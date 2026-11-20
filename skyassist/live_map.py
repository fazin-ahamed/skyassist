"""The live airport map.

Three read models and one write, all server-owned so the screen only draws what it is given:

  GET  /map/layout                  the terminal graph as drawable shapes (static for a dataset)
  GET  /map/live                    live layer for a kiosk with no journey yet: closures, queue waits, the kiosk pin
  GET  /journeys/{id}/live          the same plus this journey's gate, route progress, assistance pin and position
  POST /journeys/{id}/position      set where the passenger is. A point is snapped to the nearest walkable corridor.

Position is deliberately source-agnostic. A deployment feeds it from whatever positioning it has (beacons,
Wi-Fi RTT, staff handoff); the demo feeds it from the on-screen controls. Nothing here claims a sensor type.
"""

import json
import math
import os
import re
import secrets
from collections import defaultdict, deque
from contextlib import closing
from datetime import datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from skyassist.assist import journey_row, latest_revision
from skyassist.clock import Clock
from skyassist.config import DATA_DIR, Config
from skyassist.db import connect, timestamp, transaction
from skyassist.models import ReasonCode, ViewModel
from skyassist.routing import Graph, _latest, _public, load_graph
from skyassist.service import SESSION_HORIZON, Auth, ResourceId, _bearer, _digest, _error

AIRPORT = "DXB"
NODE_SNAP = 7.0        # within this many map units of a node, the position becomes that node
CORRIDOR_SNAP = 16.0   # within this of a walkable edge, the position is pulled onto it
ON_ROUTE = 12.0        # farther than this from the route line counts as off route
DEFAULT_KIOSK_NODE = "c22"

_GATE = re.compile(r"^[a-z]\d{1,2}$")


def node_kind(node_id: str) -> str:
    """What a node is, from its id. The map draws gates, lifts and platforms; junctions stay unlabelled."""
    if _GATE.match(node_id):
        return "gate"
    if "lift" in node_id:
        return "lift"
    if "stairs" in node_id:
        return "stairs"
    if node_id.startswith("train"):
        return "train"
    if "costa" in node_id:
        return "cafe"
    if node_id.endswith("_door"):
        return "door"
    if node_id == "public_exit":
        return "exit"
    if node_id == "public_hall":
        return "hall"
    if "hall" in node_id or "walk" in node_id:
        return "junction"
    return "point"


def kiosk_node(graph: Graph) -> str | None:
    wanted = os.environ.get("SKYASSIST_KIOSK_NODE", DEFAULT_KIOSK_NODE)
    return wanted if any(n.id == wanted for n in graph.nodes) else None


@lru_cache(maxsize=4)
def _outline_shapes(airport: str) -> tuple[dict, ...]:
    """Concourse outlines. They live in the fixture file, not the database, so read them from there."""
    try:
        data = json.loads((DATA_DIR / "graph.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple(data.get("floor_shapes", ())) if data.get("airport") == airport else ()


def layout(graph: Graph) -> dict:
    nodes = {n.id: n for n in graph.nodes if _public(n)}
    edges = [e for e in graph.edges if e.from_node_id in nodes and e.to_node_id in nodes and not e.restricted]
    seen: set[frozenset[str]] = set()
    unique = []
    for edge in edges:  # train and some corridors are listed both ways; the map draws each pair once
        pair = frozenset((edge.from_node_id, edge.to_node_id))
        if pair in seen:
            continue
        seen.add(pair)
        unique.append(edge)
    floors = sorted({n.floor for n in nodes.values()})
    return {
        "airport": graph.airport, "version": graph.graph_version, "floors": floors,
        "kiosk_node": kiosk_node(graph),
        "nodes": [
            {"id": n.id, "kind": node_kind(n.id), "terminal": n.terminal, "concourse": n.concourse,
             "floor": n.floor, "x": n.x, "y": n.y, "landmark": n.landmark.model_dump(mode="json"),
             "pickup": n.pickup_eligible}
            for n in nodes.values() if node_kind(n.id) != "door"
        ],
        "edges": [
            {"id": e.id, "from": e.from_node_id, "to": e.to_node_id, "mode": e.mode.value if hasattr(e.mode, "value") else str(e.mode),
             "lift": e.lift, "steps": e.steps, "accessible": e.accessible, "queue_id": e.queue_id,
             "cross_floor": nodes[e.from_node_id].floor != nodes[e.to_node_id].floor}
            for e in unique if node_kind(e.from_node_id) != "door" and node_kind(e.to_node_id) != "door"
        ],
        "shapes": [
            {"terminal": s["terminal"], "concourse": s["concourse"], "floor": s["floor"],
             "points": [[p["x"], p["y"]] for p in s["points"]]}
            for s in _outline_shapes(graph.airport)
        ],
    }


def _segment_projection(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    t = 0.0 if span == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
    qx, qy = ax + t * dx, ay + t * dy
    return t, qx, qy, math.hypot(px - qx, py - qy)


def snap(graph: Graph, *, x: float, y: float, floor: str | None) -> dict:
    """Pull a raw point onto the walkable graph on its floor. Returns the stored position shape."""
    nodes = {n.id: n for n in graph.nodes if _public(n) and node_kind(n.id) != "door"}
    floors = {n.floor for n in nodes.values()}
    if floor is None or floor not in floors:
        floor = min(nodes.values(), key=lambda n: math.hypot(n.x - x, n.y - y)).floor
    here = [n for n in nodes.values() if n.floor == floor]
    nearest = min(here, key=lambda n: math.hypot(n.x - x, n.y - y))
    if math.hypot(nearest.x - x, nearest.y - y) <= NODE_SNAP:
        return _from_node(nearest)
    best = None
    for edge in graph.edges:
        a, b = nodes.get(edge.from_node_id), nodes.get(edge.to_node_id)
        if a is None or b is None or a.floor != floor or b.floor != floor or edge.restricted:
            continue
        t, qx, qy, distance = _segment_projection(x, y, a.x, a.y, b.x, b.y)
        if best is None or distance < best[0]:
            best = (distance, edge.id, t, qx, qy, a if t < 0.5 else b)
    if best and best[0] <= CORRIDOR_SNAP:
        _, edge_id, _, qx, qy, owner = best
        return {"x": round(qx, 2), "y": round(qy, 2), "floor": floor, "concourse": owner.concourse,
                "terminal": owner.terminal, "node_id": None, "edge_id": edge_id}
    return {"x": round(x, 2), "y": round(y, 2), "floor": floor, "concourse": nearest.concourse,
            "terminal": nearest.terminal, "node_id": None, "edge_id": None}


def _from_node(node) -> dict:
    return {"x": node.x, "y": node.y, "floor": node.floor, "concourse": node.concourse,
            "terminal": node.terminal, "node_id": node.id, "edge_id": None}


class PositionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    x: Annotated[float, Field(ge=-1000, le=1000)] | None = None
    y: Annotated[float, Field(ge=-1000, le=1000)] | None = None
    floor: Annotated[str, Field(min_length=1, max_length=8)] | None = None

    @model_validator(mode="after")
    def _one_form(self):
        has_point = self.x is not None and self.y is not None
        if (self.node_id is None) == (not has_point):
            raise ValueError("send a node_id, or x and y")
        return self


def _position_key(journey_id: str) -> str:
    return f"position:{journey_id}"


def stored_position(conn, journey_id: str) -> dict | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (_position_key(journey_id),)).fetchone()
    return json.loads(row[0]) if row else None


def _store_position(conn, journey_id: str, position: dict) -> None:
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (_position_key(journey_id), json.dumps(position)))


def _node_from_landmark(key: str | None, nodes: dict) -> str | None:
    node_id = (key or "").rsplit(".", 1)[-1]  # "landmark.b14" and "demo.landmark.b14" both name node b14
    return node_id if node_id in nodes else None


def _expand_path(graph: Graph, nodes: dict, ids: list[str]) -> list[str]:
    """Join consecutive route stops into the node-by-node path the passenger would actually walk."""
    adjacent: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if edge.from_node_id in nodes and edge.to_node_id in nodes and not edge.restricted:
            adjacent[edge.from_node_id].add(edge.to_node_id)
            adjacent[edge.to_node_id].add(edge.from_node_id)
    path: list[str] = []
    for a, b in zip(ids, ids[1:]):
        if a == b:
            continue
        hops = _hops(adjacent, a, b) or [a, b]
        path.extend(hops if not path else hops[1:])
    return path or list(ids[:1])


def _hops(adjacent, start: str, goal: str) -> list[str] | None:
    seen, queue = {start: None}, deque([start])
    while queue:
        here = queue.popleft()
        if here == goal:
            out = []
            while here is not None:
                out.append(here)
                here = seen[here]
            return out[::-1]
        for there in sorted(adjacent.get(here, ())):
            if there not in seen:
                seen[there] = here
                queue.append(there)
    return None


def _route_progress(path: list[str], step_nodes: list[str | None], position: dict | None, nodes: dict) -> dict | None:
    """Where along the route the passenger is. Geometry only; timing stays with the agent."""
    if len(path) < 2:
        return None
    points = [nodes[i] for i in path]
    cumulative = [0.0]
    for a, b in zip(points, points[1:]):
        cumulative.append(cumulative[-1] + math.hypot(b.x - a.x, b.y - a.y))
    total = cumulative[-1]
    out = {"total_steps": len(step_nodes), "progress": None, "on_route": None, "reached_steps": 0, "next_step": 1}
    if position is None or total == 0:
        return out
    travelled, distance = None, None
    if position.get("node_id") in path:
        travelled, distance = cumulative[path.index(position["node_id"])], 0.0
    else:
        for index, (a, b) in enumerate(zip(points, points[1:])):
            if a.floor != b.floor or a.floor != position.get("floor"):
                continue  # a lift or train leg has no floor-plan line to be near
            t, _, _, gap = _segment_projection(position["x"], position["y"], a.x, a.y, b.x, b.y)
            if distance is None or gap < distance:
                distance, travelled = gap, cumulative[index] + t * (cumulative[index + 1] - cumulative[index])
    if travelled is None:
        return out
    marks, cursor = [], 0
    for stop in step_nodes:
        if stop is not None and stop in path[cursor:]:
            cursor += path[cursor:].index(stop)
            marks.append(cumulative[cursor])
        else:
            marks.append(None)
    if distance > ON_ROUTE:
        out["on_route"] = False  # near no part of the route: say so rather than claim a fraction
        return out
    reached = sum(1 for mark in marks if mark is not None and mark <= travelled + 1e-6)
    out.update(progress=round(travelled / total, 4), on_route=True, reached_steps=reached,
               next_step=reached + 1 if reached < len(step_nodes) else None)
    return out


def _with_nearest(graph: Graph, position: dict | None) -> dict | None:
    """Add the closest named place, so the kiosk can offer 'use my location' without guessing."""
    if position is None:
        return None
    candidates = [n for n in graph.nodes if _public(n) and node_kind(n.id) not in ("door", "junction", "point")
                  and n.floor == position.get("floor")]
    if not candidates:
        return position
    near = min(candidates, key=lambda n: math.hypot(n.x - position["x"], n.y - position["y"]))
    return position | {"near_node": near.id, "near_distance": round(math.hypot(near.x - position["x"], near.y - position["y"]), 1)}


def _closures_and_queues(graph: Graph, now: datetime) -> tuple[list[dict], list[dict]]:
    overlays = _latest(((o.edge_id, o) for o in graph.overlays if o.airport == graph.airport), now)
    closures = []
    for edge in graph.edges:
        fresh = [o for o in overlays.get(edge.id, []) if now < o.valid_until]
        states = {o.closed for o in fresh}
        if True in states or (edge.closed and False not in states):
            lift = edge.lift or str(getattr(edge.mode, "value", edge.mode)) == "lift"
            observed = max((o.observed_at for o in fresh), default=None)
            closures.append({"edge_id": edge.id, "kind": "lift" if lift else "passage",
                             "observed_at": timestamp(observed) if observed else None})
    queues = _latest(((q.queue_id, q) for q in graph.queues if q.airport == graph.airport and q.flow == "connecting"), now)
    waits = []
    for edge in graph.edges:
        if edge.queue_id is None:
            continue
        fresh = {q.wait for q in queues.get(edge.queue_id, []) if now < q.valid_until}
        if len(fresh) == 1 and (wait := next(iter(fresh))) is not None:
            observed = max(q.observed_at for q in queues[edge.queue_id])
            waits.append({"edge_id": edge.id, "queue_id": edge.queue_id, "lower_min": wait.lower // 60,
                          "upper_min": None if wait.upper is None else math.ceil(wait.upper / 60),
                          "observed_at": timestamp(observed)})
    return closures, waits


def build_live(conn, clock: Clock, config: Config, journey_id: str | None = None) -> dict:
    now = clock.now()
    graph = load_graph(conn, AIRPORT)
    nodes = {n.id: n for n in graph.nodes}
    closures, queues = _closures_and_queues(graph, now)
    live: dict = {"as_of": timestamp(now), "closures": closures, "queues": queues,
                  "kiosk_node": kiosk_node(graph), "position": None, "gate": None, "route": None,
                  "assistance": None}
    if journey_id is None:
        if live["kiosk_node"] in nodes:
            live["position"] = _with_nearest(graph, _from_node(nodes[live["kiosk_node"]]) | {"observed_at": timestamp(now)})
        return live
    journey = journey_row(conn, journey_id)
    stored = stored_position(conn, journey_id)
    if stored is None:
        start = journey["origin_node_id"] if journey is not None and journey["origin_node_id"] in nodes else live["kiosk_node"]
        stored = (_from_node(nodes[start]) | {"observed_at": journey["created_at"] if journey else timestamp(now)}) if start in nodes else None
    live["position"] = _with_nearest(graph, stored)
    latest = latest_revision(conn, journey_id)
    if latest is None:
        return live
    view = ViewModel.model_validate_json(latest["view_json"])
    gate_id = (view.gate.value or "").lower()
    if gate_id in nodes:
        live["gate"] = {"node_id": gate_id, "label": view.gate.value, "state": str(getattr(view.gate.state, "value", view.gate.state))}
    if view.route:
        steps = [_node_from_landmark(step.landmark.key, nodes) for step in view.route.steps]
        origin = journey["origin_node_id"] if journey is not None and journey["origin_node_id"] in nodes else None
        stops = [i for i in ([origin] if origin else []) + steps if i]
        path = _expand_path(graph, nodes, stops)
        live["route"] = {"route_id": view.route.route_id, "revision": view.route.revision, "step_nodes": steps,
                         "path": path, "progress": _route_progress(path, steps, stored, nodes)}
    if view.assistance:
        pickup = _node_from_landmark(view.assistance.pickup_landmark.key if view.assistance.pickup_landmark else None, nodes)
        live["assistance"] = {"state": str(getattr(view.assistance.state, "value", view.assistance.state)), "pickup_node": pickup}
    return live


def map_router(clock: Clock, config: Config) -> APIRouter:
    router = APIRouter()

    def authorise(conn, journey_id: str, authorization: str | None):
        row = conn.execute("SELECT value FROM meta WHERE key=?", (f"session:{journey_id}",)).fetchone()
        digest = _digest(_bearer(authorization))
        if row is None or not secrets.compare_digest(digest, row[0]):
            raise _error(403, ReasonCode.WRONG_ROLE)
        journey = journey_row(conn, journey_id)
        if journey is None or journey["closed_at"] is not None or journey["created_at"] <= timestamp(clock.now() - SESSION_HORIZON):
            raise _error(403, ReasonCode.SESSION_EXPIRED)
        return journey

    @router.get("/map/layout")
    def map_layout(response: Response) -> dict:
        response.headers["Cache-Control"] = "public, max-age=60"
        with closing(connect(config.db_path)) as conn:
            return layout(load_graph(conn, AIRPORT))

    @router.get("/map/live")
    def map_live(response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        with closing(connect(config.db_path)) as conn:
            return build_live(conn, clock, config)

    @router.get("/journeys/{journey_id}/live")
    def journey_live(journey_id: ResourceId, response: Response, authorization: Auth = None) -> dict:
        response.headers["Cache-Control"] = "no-store"
        with closing(connect(config.db_path)) as conn:
            authorise(conn, journey_id, authorization)
            return build_live(conn, clock, config, journey_id)

    @router.post("/journeys/{journey_id}/position")
    def set_position(journey_id: ResourceId, body: PositionBody, response: Response, authorization: Auth = None) -> dict:
        response.headers["Cache-Control"] = "no-store"
        with closing(connect(config.db_path)) as conn:
            authorise(conn, journey_id, authorization)
            graph = load_graph(conn, AIRPORT)
            nodes = {n.id: n for n in graph.nodes if _public(n) and node_kind(n.id) != "door"}
            if body.node_id is not None:
                if body.node_id not in nodes:
                    raise _error(422, ReasonCode.INVALID_ARGUMENTS)
                position = _from_node(nodes[body.node_id])
            else:
                position = snap(graph, x=body.x, y=body.y, floor=body.floor)
            position["observed_at"] = timestamp(clock.now())
            with transaction(conn):
                _store_position(conn, journey_id, position)
            return build_live(conn, clock, config, journey_id)

    return router
