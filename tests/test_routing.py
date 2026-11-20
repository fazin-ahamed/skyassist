import sqlite3
from datetime import datetime, timedelta

import pytest
from hypothesis import given, strategies as st

from skyassist.db import _SCHEMA
from skyassist.models import (
    AccessibleMode, CalculateTerminalTransferTimeInput, Edge, I18nText, Node,
    Overlay, PassengerFlow, PolicyPack, Profile, QueueObservation, ReasonCode,
    SecondsRange, TimeWindow,
)
from skyassist.routing import Graph, find_route, load_graph

AS_OF = datetime.fromisoformat("2026-11-19T10:00:00+04:00")
POLICY = PolicyPack()


def seconds(lower, upper):
    return SecondsRange(lower=lower, upper=upper)


def at(offset):
    return AS_OF + timedelta(seconds=offset)


def node(node_id, **changes):
    return Node(**{
        "id": node_id, "airport": "DXB", "terminal": "1", "concourse": "C", "floor": "2",
        "security_zone": "airside", "landmark": I18nText(key=f"landmark.{node_id.lower()}"),
        "x": 0, "y": 0, **changes,
    })


def edge(edge_id, origin, target, **changes):
    return Edge(**{
        "id": edge_id, "from_node_id": origin, "to_node_id": target, "distance_m": 0,
        "accessible": True, "allowed_flows": (PassengerFlow.CONNECTING,),
        "fixed_movement": seconds(30, 60), **changes,
    })


def overlay(edge_id, **changes):
    return Overlay(**{
        "id": f"state-{edge_id}", "edge_id": edge_id, "airport": "DXB", "source": "operations",
        "observed_at": AS_OF, "valid_until": at(60), "closed": False, "accessible": True,
        **changes,
    })


def queue(queue_id, wait, **changes):
    return QueueObservation(**{
        "queue_id": queue_id, "wait": wait, "airport": "DXB", "flow": PassengerFlow.CONNECTING,
        "source": "operations", "observed_at": AS_OF, "valid_until": at(60), **changes,
    })


def route_for(graph, start="A", end="G", profile=Profile.STANDARD, **options):
    request = CalculateTerminalTransferTimeInput(
        origin_id=start, gate_id=end, profile=profile, as_of=AS_OF,
    )
    return find_route(graph, request, POLICY, **options)


@pytest.mark.parametrize(
    "target, changes, reason",
    [
        (node("G", airport="AUH"), {}, ReasonCode.WRONG_AIRPORT),
        (node("G"), {"restricted": True}, ReasonCode.EDGE_RESTRICTED),
        (node("G", public=False), {}, ReasonCode.EDGE_RESTRICTED),
        (node("G", security_zone="apron"), {}, ReasonCode.EDGE_RESTRICTED),
        (node("G", security_zone="landside"), {}, ReasonCode.CHECKPOINT_INELIGIBLE),
        (node("G", security_zone="landside"), {
            "mode": AccessibleMode.CHECKPOINT, "allowed_flows": (PassengerFlow.EXITING,),
        }, ReasonCode.CHECKPOINT_INELIGIBLE),
    ],
)
def test_scope_and_security(target, changes, reason):
    graph = Graph("DXB", (node("A"), target), (edge("edge", "A", "G", **changes),))
    route, best, reasons = route_for(graph)
    assert route is best is None
    assert reasons == (ReasonCode.NO_PATH, reason)


def test_no_backtracking_on_one_way_edge():
    graph = Graph("DXB", (node("A"), node("G")), (edge("back", "G", "A"),))
    assert route_for(graph) == (None, None, (ReasonCode.NO_PATH,))


def test_step_free_detour_and_lift_require_fresh_positive_evidence():
    lift = edge("lift", "A", "G", mode=AccessibleMode.LIFT, lift=True,
                requires_access_evidence=True, closed=True, accessible=False)
    stairs = edge("stairs", "A", "G", mode=AccessibleMode.STAIRS, steps=4,
                  fixed_movement=seconds(1, 2))
    detour = (edge("around-1", "A", "D"), edge("around-2", "D", "G"))
    old = overlay("lift", observed_at=at(-100), valid_until=AS_OF)
    graph = Graph("DXB", (node("A"), node("D", floor="1"), node("G")), (lift, stairs, *detour), (old,))
    assert route_for(graph, profile=Profile.REDUCED_MOBILITY)[0].node_ids == ("A", "D", "G")
    assert route_for(graph)[0].steps[0].text.key == "route.change_floor"
    fresh = overlay("lift")
    route, best, _ = route_for(graph, profile=Profile.REDUCED_MOBILITY, overlays=(fresh,))
    assert route.movement == seconds(30, 60)
    assert best == 30
    assert route.steps[0].text.key == "route.take_lift"

    unverified = lift.model_copy(update={"closed": False})
    graph = Graph("DXB", graph.nodes, (unverified, stairs), (old,))
    assert route_for(graph, profile=Profile.REDUCED_MOBILITY)[2] == (
        ReasonCode.NO_ACCESSIBLE_PATH, ReasonCode.LIFT_UNVERIFIED,
    )


def test_distance_rounding_and_map_shapes_are_ordered():
    graph = Graph(
        "DXB", (node("A"), node("B", x=2), node("G", x=3)),
        (edge("first", "A", "B", distance_m=3.1, fixed_movement=None, shape=({"x": 1, "y": 1},)),
         edge("last", "B", "G", distance_m=1.1, fixed_movement=None)),
    )
    route, best, _ = route_for(graph, gate_label="B14")
    assert route.movement == seconds(4, 6)
    assert best == 4
    assert [step.text.key for step in route.steps] == ["route.walk_to", "route.approach_gate"]
    assert route.steps[-1].text.params == {"gate": "B14"}
    assert [(p.x, p.y) for p in route.floor_shapes[0].points] == [(0, 0), (1, 1), (2, 0), (3, 0)]


def test_later_arrival_can_use_window_and_readiness_counts_once():
    def window(start, end):
        return (TimeWindow(start=at(start), end=at(end)),)

    graph = Graph(
        "DXB", (node("A"), node("B"), node("D"), node("G")),
        (edge("direct", "A", "B", fixed_movement=seconds(1, 5)),
         edge("detour-1", "A", "D", fixed_movement=seconds(3, 10)),
         edge("detour-2", "D", "B", fixed_movement=seconds(3, 10)),
         edge("windowed", "B", "G", open_windows=window(25, 100))),
    )
    route, best, _ = route_for(graph, ready_upper=5)
    assert route.node_ids == ("A", "D", "B", "G")
    assert route.movement == seconds(36, 80)
    assert best == 36
    assert route_for(graph, ready_upper=21)[0].node_ids == ("A", "B", "G")
    assert route_for(graph, ready_upper=40)[0] is None
    assert route_for(graph, ready_upper=None)[0] is None


@pytest.mark.parametrize("next_start, expected", [(30, True), (31, False)])
def test_windows_must_cover_the_full_traversal_without_gaps(next_start, expected):
    graph = Graph("DXB", (node("A"), node("G")), (
        edge("windowed", "A", "G", open_windows=(
            TimeWindow(start=at(next_start), end=at(60)),
            TimeWindow(start=AS_OF, end=at(30)),
        )),
    ))
    assert (route_for(graph)[0] is not None) is expected


def test_bounded_route_preferred_and_fallback_tie_breaks_by_entire_edge_sequence():
    graph = Graph(
        "DXB", (node("A"), node("B"), node("D"), node("G")),
        (edge("a-1", "A", "D", fixed_movement=seconds(60, 90)),
         edge("a-2", "D", "B"), edge("b", "A", "B", fixed_movement=seconds(0, 0)),
         edge("queue", "B", "G", queue_id="checkpoint"),
         edge("safe", "A", "G", fixed_movement=seconds(100, 100))),
    )
    bounded, best, _ = route_for(graph)
    assert bounded.node_ids == ("A", "G")
    assert best == 30
    fallback = Graph("DXB", graph.nodes, graph.edges[:-1])
    chosen, _, reasons = route_for(fallback)
    assert chosen.node_ids == ("A", "D", "B", "G")
    assert reasons == (ReasonCode.QUEUE_BOUND_MISSING,)
    assert chosen.wait.upper is None


def test_equal_upper_uses_edge_ids_and_queue_override_uses_correct_scope():
    graph = Graph(
        "DXB", (node("A"), node("B"), node("D"), node("G")),
        (edge("z", "A", "B"), edge("b-end", "B", "G"),
         edge("a", "A", "D", queue_id="train", wait=seconds(40, 80)),
         edge("d-end", "D", "G")),
        queues=(queue("train", seconds(5, 7), airport="AUH"),
                queue("train", seconds(6, 9), flow=PassengerFlow.EXITING),
                queue("train", seconds(0, 0)),
                queue("train", seconds(3, 30), observed_at=at(1))),
    )
    route, _, reasons = route_for(graph)
    assert route.node_ids == ("A", "D", "G")
    assert route.wait == seconds(0, 0)
    assert not reasons
    reversed_edges = Graph("DXB", graph.nodes, graph.edges[::-1], queues=graph.queues)
    assert route_for(reversed_edges)[0].route_id == route.route_id


def test_sqlite_loader_without_row_factory():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(_SCHEMA)
        for item in (node("A", pickup_eligible=True), node("G"), node("U", airport="AUH")):
            conn.execute(
                "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item.id, item.airport, item.terminal, item.concourse, item.floor, item.security_zone,
                 item.landmark.model_dump_json(), item.x, item.y, item.public, item.pickup_eligible),
            )
        passage = edge("gate-edge", "A", "G", requires_access_evidence=True, queue_id="q")
        conn.execute("INSERT INTO edges VALUES (?, ?, ?, ?)",
                     (passage.id, passage.from_node_id, passage.to_node_id, passage.model_dump_json()))
        conn.execute("INSERT INTO overlays VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     ("overlay", passage.id, "DXB", "test", AS_OF.isoformat(), at(20).isoformat(), 0, 1))
        conn.execute("INSERT INTO queue_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (1, "DXB", "q", "connecting", "test", AS_OF.isoformat(), at(20).isoformat(), 5, 10))
        graph = load_graph(conn, "DXB")
    finally:
        conn.close()
    assert {item.id for item in graph.nodes} == {"A", "G"}
    assert next(item for item in graph.nodes if item.id == "A").pickup_eligible
    assert route_for(graph)[0].wait == seconds(5, 10)


@given(st.lists(st.tuples(st.booleans(), st.booleans(), st.booleans(), st.booleans()), min_size=4, max_size=4),
       st.sampled_from(tuple(Profile)))
def test_every_selected_edge_is_eligible(attributes, profile):
    links = (("A", "B"), ("B", "G"), ("A", "D"), ("D", "G"))
    edges = tuple(
        edge(str(i), origin, target, restricted=restricted, closed=closed,
             steps=int(steps), allowed_flows=(PassengerFlow.CONNECTING if connecting else PassengerFlow.EXITING,))
        for i, ((origin, target), (restricted, closed, steps, connecting)) in enumerate(zip(links, attributes))
    )
    graph = Graph("DXB", tuple(map(node, ("A", "B", "D", "G"))), edges)
    found, _, _ = route_for(graph, profile=profile)
    eligible = {
        link for link, (restricted, closed, steps, connecting) in zip(links, attributes)
        if not restricted and not closed and connecting
        and (not steps or profile in (Profile.STANDARD, Profile.FAMILY))
    }
    possible = all(link in eligible for link in links[:2]) or all(link in eligible for link in links[2:])
    assert (found is not None) == possible
    if found is not None:
        assert all(link in eligible for link in zip(found.node_ids, found.node_ids[1:]))


@given(st.integers(0, 300), st.integers(0, 300), st.integers(0, 300), st.booleans(), st.booleans())
def test_same_wait_on_all_alternatives_preserves_choice(first, second, extra, first_closed, second_closed):
    nodes = tuple(map(node, ("A", "B", "D", "G")))

    def build(increase):
        return Graph("DXB", nodes, (
            edge("b", "A", "B", closed=first_closed, wait=seconds(first + increase, first + increase)),
            edge("b-end", "B", "G"),
            edge("d", "A", "D", closed=second_closed, wait=seconds(second + increase, second + increase)),
            edge("d-end", "D", "G"),
        ))

    before, _, _ = route_for(build(0))
    after, _, _ = route_for(build(extra))
    if before is None:
        assert after is None
    else:
        assert after.route_id == before.route_id
        assert after.wait.upper == before.wait.upper + extra
