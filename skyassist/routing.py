from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from heapq import heappop, heappush
from math import ceil
from sqlite3 import Connection
from typing import Literal

from pydantic import TypeAdapter

from skyassist.config import CONFIG, Mode
from skyassist.models import (
    AccessibleMode, CalculateTerminalTransferTimeInput, Edge, ExpiringSourceStamp,
    FloorShape, I18nText, MapPoint, Node, Overlay, PolicyPack, Profile,
    QueueObservation, ReasonCode, RouteMapPoint, RouteResult, RouteStep, SecondsRange, SourceStamp,
    SpeedBand, TimeWindow,
)

SECOND = timedelta(seconds=1)
_STEP_FREE = {Profile.REDUCED_MOBILITY, Profile.FAMILY_MOBILITY}
_NON_PASSENGER = {"aircraft", "aircraft_door", "apron"}
_OUTLINES = TypeAdapter(tuple[FloorShape, ...])


@dataclass(frozen=True)
class Graph:
    airport: str
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    overlays: tuple[Overlay, ...] = ()
    queues: tuple[QueueObservation, ...] = ()
    graph_version: str = "terminal-graph"
    floor_outlines: tuple[FloorShape, ...] = ()


def load_graph(conn: Connection, airport: str) -> Graph:
    nodes = tuple(
        Node(id=node_id, airport=airport, terminal=terminal, concourse=concourse,
             floor=floor, security_zone=zone, landmark=I18nText.model_validate_json(landmark),
             x=x, y=y, public=bool(public), pickup_eligible=bool(pickup))
        for node_id, terminal, concourse, floor, zone, landmark, x, y, public, pickup
        in conn.execute(
            """SELECT id, terminal, concourse, floor, security_zone, landmark_json,
                      x, y, public, pickup_eligible FROM nodes WHERE airport = ?""",
            (airport,),
        )
    )
    edges = tuple(
        Edge.model_validate_json(payload)
        for (payload,) in conn.execute(
            """SELECT e.edge_json FROM edges e JOIN nodes n ON n.id = e.from_node_id
               WHERE n.airport = ?""",
            (airport,),
        )
    )
    overlays = tuple(
        Overlay(id=key, airport=airport, edge_id=edge_id, source=source,
                observed_at=observed, valid_until=expires,
                closed=None if closed is None else bool(closed),
                accessible=None if accessible is None else bool(accessible))
        for key, edge_id, source, observed, expires, closed, accessible in conn.execute(
            """SELECT id, edge_id, source, observed_at, valid_until, closed, accessible
               FROM overlays WHERE airport = ? ORDER BY rowid""",
            (airport,),
        )
    )
    queues = tuple(
        QueueObservation(airport=airport, queue_id=key, flow=flow, source=source,
                         observed_at=observed, valid_until=expires,
                         wait=None if lower is None else SecondsRange(lower=lower, upper=upper))
        for key, flow, source, observed, expires, lower, upper in conn.execute(
            """SELECT queue_id, flow, source, observed_at, valid_until, lower_seconds,
                      upper_seconds FROM queue_observations WHERE airport = ? ORDER BY rowid""",
            (airport,),
        )
    )
    stored = conn.execute("SELECT value FROM meta WHERE key=?", (f"floor_outlines:{airport}",)).fetchone()
    outlines = _OUTLINES.validate_json(stored[0]) if stored else ()
    return Graph(airport, nodes, edges, overlays, queues, floor_outlines=outlines)


@dataclass(frozen=True)
class _Segment:
    edge: Edge
    movement: SecondsRange
    wait: SecondsRange
    queue_reason: ReasonCode | None

    @property
    def upper(self) -> int | None:
        if self.movement.upper is None or self.wait.upper is None:
            return None
        return self.movement.upper + self.wait.upper


def _latest[T: ExpiringSourceStamp](
    observations: Iterable[tuple[str, T]], as_of: datetime,
) -> dict[str, list[T]]:
    latest: dict[str, list[T]] = {}
    for key, observation in observations:
        if observation.observed_at > as_of:
            continue
        current = latest.get(key)
        if not current or observation.observed_at > current[0].observed_at:
            latest[key] = [observation]
        elif observation.observed_at == current[0].observed_at:
            latest[key] = [seen for seen in current if seen.source != observation.source] + [observation]
    return latest


def _public(node: Node) -> bool:
    return (
        node.public
        and node.security_zone.lower().replace("-", "_").replace(" ", "_") not in _NON_PASSENGER
        and node.landmark.key.rsplit(".", 1)[-1].lower() not in _NON_PASSENGER
    )


def _edge_exclusion(
    edge: Edge, origin: Node, target: Node, airport: str,
    request: CalculateTerminalTransferTimeInput, overlays: list[Overlay],
) -> ReasonCode | None:
    if origin.airport != airport or target.airport != airport:
        return ReasonCode.WRONG_AIRPORT
    if edge.restricted or not _public(origin) or not _public(target):
        return ReasonCode.EDGE_RESTRICTED
    is_lift = edge.lift or edge.mode is AccessibleMode.LIFT
    fresh = [overlay for overlay in overlays if request.as_of < overlay.valid_until]
    closures = {overlay.closed for overlay in fresh}
    access = {overlay.accessible for overlay in fresh}
    if True in closures or (edge.closed and False not in closures):
        return ReasonCode.LIFT_CLOSED if is_lift else ReasonCode.FACILITY_CLOSED
    if edge.requires_access_evidence and (
        False in access
        or not any(overlay.closed is False and overlay.accessible is True for overlay in fresh)
    ):
        return ReasonCode.LIFT_UNVERIFIED if is_lift else ReasonCode.ACCESS_UNVERIFIED
    if request.flow not in edge.allowed_flows or (
        origin.security_zone != target.security_zone and edge.mode is not AccessibleMode.CHECKPOINT
    ):
        return ReasonCode.CHECKPOINT_INELIGIBLE if (
            edge.mode is AccessibleMode.CHECKPOINT or origin.security_zone != target.security_zone
        ) else ReasonCode.NO_PATH
    if request.profile in _STEP_FREE and (
        edge.steps or False in access or not (edge.accessible or is_lift or True in access)
    ):
        return ReasonCode.NO_ACCESSIBLE_PATH
    return None


def _segment(
    edge: Edge, speed: SpeedBand, queues: list[QueueObservation], as_of: datetime,
) -> _Segment:
    movement = edge.fixed_movement or SecondsRange(
        lower=ceil(edge.distance_m / speed.fast_mps),
        upper=ceil(edge.distance_m / speed.slow_mps),
    )
    if edge.queue_id is None:
        reason = ReasonCode.QUEUE_BOUND_MISSING if edge.wait.upper is None else None
        return _Segment(edge, movement, edge.wait, reason)

    fresh = {observation.wait for observation in queues if as_of < observation.valid_until}
    if len(fresh) == 1:
        wait = next(iter(fresh))
        if wait is not None:
            reason = ReasonCode.QUEUE_BOUND_MISSING if wait.upper is None else None
            return _Segment(edge, movement, wait, reason)

    reason = ReasonCode.QUEUE_BOUND_STALE if queues and not fresh else ReasonCode.QUEUE_BOUND_MISSING
    return _Segment(edge, movement, SecondsRange(lower=edge.wait.lower, upper=None), reason)


def _open_during(
    windows: tuple[TimeWindow, ...], as_of: datetime, start: int | None, end: int | None,
) -> bool:
    if start is None or end is None:
        return False
    covered_until = None
    for window in sorted(windows, key=lambda window: window.start):
        if covered_until is None:
            if -((as_of - window.start) // SECOND) > start:
                return False
            if (window.end - as_of) // SECOND < start:
                continue
            covered_until = window.end
        elif window.start > covered_until:
            return False
        else:
            covered_until = max(covered_until, window.end)
        if end <= (covered_until - as_of) // SECOND:
            return True
    return False


def _search(
    segments: dict[str, list[_Segment]], origin: str, gate: str, as_of: datetime,
    ready_upper: int | None, weight: Literal["upper", "lower", "fallback"],
) -> tuple[tuple[str, ...] | None, int | None, set[str], bool]:
    has_windows = any(segment.edge.open_windows for edges in segments.values() for segment in edges)
    pending = [(0, (), (origin,), ready_upper)]
    settled = set()
    reached = set()
    window_blocked = False
    while pending:
        cost, path, nodes, elapsed = heappop(pending)
        node = nodes[-1]
        # A later arrival can use a passage that was closed at the earliest arrival.
        state = (node, elapsed, frozenset(nodes)) if has_windows else node
        if state in settled:
            continue
        settled.add(state)
        reached.add(node)
        if node == gate:
            return path, cost, reached, window_blocked

        for segment in segments.get(node, ()):
            edge = segment.edge
            if edge.to_node_id in nodes:
                continue
            upper = segment.upper
            edge_cost = 0
            if weight == "upper":
                if upper is None:
                    continue
                edge_cost = upper
            elif weight == "lower":
                edge_cost = segment.movement.lower + segment.wait.lower
            end = None if elapsed is None or upper is None else elapsed + upper
            if edge.open_windows and not _open_during(edge.open_windows, as_of, elapsed, end):
                window_blocked = True
                continue
            heappush(pending, (cost + edge_cost, (*path, edge.id), (*nodes, edge.to_node_id), end))
    return None, None, reached, window_blocked


def _sum_ranges(ranges: Iterable[SecondsRange]) -> SecondsRange:
    parts = tuple(ranges)
    return SecondsRange(
        lower=sum(part.lower for part in parts),
        upper=None if any(part.upper is None for part in parts) else sum(part.upper for part in parts),
    )


def _map_key(node: Node) -> tuple[str, str, str]:
    return node.terminal, node.concourse, node.floor


def _floor_shapes(nodes: tuple[Node, ...], edges: tuple[Edge, ...]) -> tuple[FloorShape, ...]:
    shapes: list[FloorShape] = []
    points = [MapPoint(x=nodes[0].x, y=nodes[0].y)]
    for origin, target, edge in zip(nodes, nodes[1:], edges):
        if _map_key(origin) == _map_key(target):
            for point in (*edge.shape, MapPoint(x=target.x, y=target.y)):
                if point != points[-1]:
                    points.append(point)
            continue
        # Cross-map edges have no floor tags on their individual shape points.
        if len(points) > 1:
            shapes.append(FloorShape(terminal=origin.terminal, concourse=origin.concourse,
                                     floor=origin.floor, points=tuple(points)))
        points = [MapPoint(x=target.x, y=target.y)]
    if len(points) > 1:
        target = nodes[-1]
        shapes.append(FloorShape(terminal=target.terminal, concourse=target.concourse,
                                 floor=target.floor, points=tuple(points)))
    return tuple(shapes)


def _steps(
    nodes: tuple[Node, ...], edges: tuple[Edge, ...], handoff_pending: bool, gate_label: str,
) -> tuple[RouteStep, ...]:
    steps: list[RouteStep] = []

    def add(node: Node, mode: AccessibleMode, key: str, **params: str) -> None:
        steps.append(RouteStep(number=len(steps) + 1, floor=node.floor, landmark=node.landmark,
                               accessible_mode=mode, text=I18nText(key=f"route.{key}", params=params),
                               map_position=RouteMapPoint(x=node.x, y=node.y, terminal=node.terminal,
                                                          concourse=node.concourse)))

    if handoff_pending:
        add(nodes[0], AccessibleMode.STAFF_HANDOFF, "wait_at_pickup")
    gate_mode = AccessibleMode.WALK
    for index, (origin, target, edge) in enumerate(zip(nodes, nodes[1:], edges)):
        mode = AccessibleMode.LIFT if edge.lift else edge.mode
        next_mode = edges[index + 1].mode if index + 1 < len(edges) else None
        if mode is AccessibleMode.STAFF_HANDOFF:
            if index or not handoff_pending:
                add(origin, mode, "wait_at_pickup")
        elif mode is AccessibleMode.LIFT:
            add(target, mode, "take_lift")
        elif mode is AccessibleMode.TRANSIT:
            if index == 0 or edges[index - 1].mode is not AccessibleMode.TRANSIT:
                add(origin, mode, "board_train")
            if next_mode is not AccessibleMode.TRANSIT:
                add(target, mode, "exit_train")
        elif mode is AccessibleMode.CHECKPOINT:
            add(target, mode, "cross_checkpoint")
        elif next_mode is None and mode is not AccessibleMode.STAIRS:
            gate_mode = mode
        elif mode is AccessibleMode.STAIRS or (
            next_mode is not AccessibleMode.TRANSIT and origin.floor != target.floor
        ):
            add(target, mode, "change_floor")
        elif next_mode is not AccessibleMode.TRANSIT:
            add(target, mode, "walk_to")
    add(nodes[-1], gate_mode, "approach_gate", gate=gate_label)
    return tuple(steps)


def find_route(
    graph: Graph, request: CalculateTerminalTransferTimeInput, policy: PolicyPack, *,
    ready_upper: int | None = 0, handoff_pending: bool = False, revision: int = 0,
    gate_label: str | None = None, overlays: tuple[Overlay, ...] = (),
    queues: tuple[QueueObservation, ...] = (),
) -> tuple[RouteResult | None, int | None, tuple[ReasonCode, ...]]:
    """Return the conservative route, best travel lower bound, and route diagnostics."""
    nodes = {node.id: node for node in graph.nodes}
    no_path = (
        ReasonCode.NO_ACCESSIBLE_PATH if request.profile in _STEP_FREE else ReasonCode.NO_PATH
    )
    for node_id, missing in (
        (request.origin_id, ReasonCode.LOCATION_UNKNOWN),
        (request.gate_id, ReasonCode.GATE_UNKNOWN),
    ):
        node = nodes.get(node_id)
        if node is None:
            return None, None, (no_path, missing)
        if node.airport != graph.airport:
            return None, None, (no_path, ReasonCode.WRONG_AIRPORT)
        if not _public(node):
            return None, None, (no_path, ReasonCode.EDGE_RESTRICTED)

    scoped_overlays = _latest(
        ((value.edge_id, value) for value in (*graph.overlays, *overlays)
         if value.airport == graph.airport), request.as_of,
    )
    scoped_queues = _latest(
        ((value.queue_id, value) for value in (*graph.queues, *queues)
         if value.airport == graph.airport and value.flow == request.flow), request.as_of,
    )
    adjacent: dict[str, list[_Segment]] = defaultdict(list)
    segments = {}
    blocked: dict[str, set[ReasonCode]] = defaultdict(set)
    speed = policy.speed_bands[request.profile]
    for edge in graph.edges:
        origin = nodes.get(edge.from_node_id)
        target = nodes.get(edge.to_node_id)
        if origin is None or target is None:
            continue
        excluded = _edge_exclusion(
            edge, origin, target, graph.airport, request, scoped_overlays.get(edge.id, []),
        )
        if excluded is not None:
            blocked[origin.id].add(excluded)
            continue
        segment = _segment(edge, speed, scoped_queues.get(edge.queue_id, []), request.as_of)
        adjacent[origin.id].append(segment)
        segments[edge.id] = segment

    path, _, reached, window_blocked = _search(
        adjacent, request.origin_id, request.gate_id, request.as_of, ready_upper, "upper",
    )
    if path is None:
        path, _, reached, window_blocked = _search(
            adjacent, request.origin_id, request.gate_id, request.as_of, ready_upper, "fallback",
        )
    if path is None:
        reasons = {reason for node in reached for reason in blocked.get(node, ())}
        if window_blocked:
            reasons.add(ReasonCode.FACILITY_CLOSED)
        reasons.discard(no_path)
        return None, None, (no_path, *sorted(reasons))

    _, best_lower, _, _ = _search(
        adjacent, request.origin_id, request.gate_id, request.as_of, ready_upper, "lower",
    )
    selected = tuple(segments[edge_id] for edge_id in path)
    edges = tuple(segment.edge for segment in selected)
    route_nodes = (nodes[request.origin_id], *(nodes[edge.to_node_id] for edge in edges))
    gate = gate_label or request.gate_id
    identity = (graph.airport, graph.graph_version, revision, request.origin_id, request.gate_id,
                handoff_pending, gate, path)
    reasons = tuple(sorted({segment.queue_reason for segment in selected if segment.queue_reason}))
    traversed_levels = {_map_key(node) for node in route_nodes}
    route = RouteResult(
        route_id=sha256(repr(identity).encode()).hexdigest(),
        revision=revision,
        node_ids=tuple(node.id for node in route_nodes),
        movement=_sum_ranges(segment.movement for segment in selected),
        wait=_sum_ranges(segment.wait for segment in selected),
        buffer=policy.buffer_floors[request.profile],
        reason_codes=reasons,
        steps=_steps(route_nodes, edges, handoff_pending, gate),
        floor_shapes=_floor_shapes(route_nodes, edges),
        floor_outlines=tuple(shape for shape in graph.floor_outlines
                             if (shape.terminal, shape.concourse, shape.floor) in traversed_levels),
        provenance=SourceStamp(source=graph.graph_version, observed_at=request.as_of),
    )
    return route, best_lower, reasons
