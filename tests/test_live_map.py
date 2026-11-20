from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from skyassist.agent import JourneyAgent
from skyassist.db import connect
from skyassist.live_map import _expand_path, _route_progress, layout, node_kind, snap
from skyassist.routing import load_graph
from skyassist.seed import build_demo_db
from skyassist.service_read import create_integrated_app
from test_agent import await_view, create, fixture, submit


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    path = build_demo_db(tmp_path_factory.mktemp("map") / "demo.sqlite3")
    with closing(connect(path)) as conn:
        return load_graph(conn, "DXB")


def test_node_kinds_come_from_ids():
    kinds = {node: node_kind(node) for node in ("b14", "c22", "lift_c", "c_stairs", "train_b", "c_costa", "b_hall", "public_exit", "b14_door")}
    assert kinds == {"b14": "gate", "c22": "gate", "lift_c": "lift", "c_stairs": "stairs", "train_b": "train",
                     "c_costa": "cafe", "b_hall": "junction", "public_exit": "exit", "b14_door": "door"}


def test_layout_is_drawable_and_hides_what_passengers_cannot_reach(graph):
    plan = layout(graph)
    ids = {n["id"] for n in plan["nodes"]}
    assert plan["floors"] == ["1", "2"] and plan["kiosk_node"] == "c22"
    assert {"c22", "b14", "lift_c", "train_b"} <= ids
    assert not {i for i in ids if i.endswith("_door")}, "aircraft doors are not for passengers"
    pairs = [frozenset((e["from"], e["to"])) for e in plan["edges"]]
    assert len(pairs) == len(set(pairs)), "a corridor listed both ways is drawn once"
    assert any(e["cross_floor"] for e in plan["edges"])
    assert {(s["concourse"], s["floor"]) for s in plan["shapes"]} >= {("B", "2"), ("C", "1"), ("C", "2")}


def test_a_point_snaps_to_a_node_then_a_corridor_and_stays_on_its_level(graph):
    at_node = snap(graph, x=21, y=61, floor="2")
    assert at_node["node_id"] == "c22" and (at_node["x"], at_node["y"]) == (20.0, 60.0)
    on_corridor = snap(graph, x=100, y=78, floor="2")
    assert on_corridor["node_id"] is None and on_corridor["edge_id"] == "e_c_hall_w_c_hall" and on_corridor["y"] == 80.0
    nowhere = snap(graph, x=5, y=5, floor="2")
    assert nowhere["edge_id"] is None and nowhere["node_id"] is None and (nowhere["x"], nowhere["y"]) == (5, 5)
    beside_the_station = snap(graph, x=60, y=60, floor="2")
    assert beside_the_station["floor"] == "2" and beside_the_station["node_id"] != "train_c", "train_c is on level 1"
    assert snap(graph, x=60, y=60, floor=None)["floor"] in {"1", "2"}


def test_route_progress_counts_stops_reached_and_says_when_off_route(graph):
    nodes = {n.id: n for n in graph.nodes}
    stops = ["lift_c", "train_c", "train_c", "train_b", "b14"]
    path = _expand_path(graph, nodes, ["c22", *stops])
    assert path == ["c22", "lift_c", "train_c", "train_b", "b14"]

    def at(**where):
        return _route_progress(path, stops, {"floor": "2", "node_id": None} | where, nodes)

    start = at(node_id="c22", x=20, y=60)
    assert start["progress"] == 0.0 and start["next_step"] == 1 and start["on_route"] is True
    middle = at(node_id="train_c", x=60, y=60, floor="1")
    assert middle["reached_steps"] == 3 and middle["next_step"] == 4 and 0 < middle["progress"] < 1
    end = at(node_id="b14", x=180, y=40)
    assert end["next_step"] is None and end["progress"] == 1.0
    lost = at(x=100, y=78)
    assert lost["on_route"] is False and lost["progress"] is None and lost["reached_steps"] == 0
    assert _route_progress(path, stops, None, nodes)["progress"] is None


def test_journey_live_layer_and_position_are_scoped_to_the_session(tmp_path):
    clock, config = fixture(tmp_path)
    with TestClient(create_integrated_app(clock, config, agent=JourneyAgent(clock, config))) as client:
        plan = client.get("/map/layout").json()
        assert {n["id"] for n in plan["nodes"]} == {"c22", "b14", "b18"}
        kiosk = client.get("/map/live").json()
        assert kiosk["position"]["node_id"] == "c22" and kiosk["route"] is None

        session = create(client)
        journey, headers = session
        live = f"/journeys/{journey}/live"
        assert client.get(live).status_code == 403
        assert client.get(live, headers=headers).json()["position"]["node_id"] == "c22"

        moved = client.post(f"/journeys/{journey}/position", headers=headers, json={"node_id": "b14"})
        assert moved.status_code == 200 and moved.json()["position"]["node_id"] == "b14"
        assert client.get(live, headers=headers).json()["position"]["node_id"] == "b14"

        for bad in ({"node_id": "nope"}, {"x": 1}, {}, {"node_id": "b14", "x": 1, "y": 2}, {"node_id": "b14", "extra": 1}):
            assert client.post(f"/journeys/{journey}/position", headers=headers, json=bad).status_code == 422, bad
        stranger = create(client)[1]
        assert client.post(f"/journeys/{journey}/position", headers=stranger, json={"node_id": "c22"}).status_code == 403

        submit(client, session)
        ready = await_view(client, session, lambda v: v["route"] is not None)
        layer = client.get(live, headers=headers).json()
        assert layer["gate"] == {"node_id": "b14", "label": "B14", "state": "fresh"}
        assert layer["route"]["step_nodes"][-1] == "b14" and layer["route"]["route_id"] == ready["route"]["route_id"]
        assert layer["route"]["progress"]["total_steps"] == len(ready["route"]["steps"])
