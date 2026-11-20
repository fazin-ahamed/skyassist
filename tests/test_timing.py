from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, strategies as st

from skyassist.models import (
    AccessibleMode, CalculateTerminalTransferTimeInput, ConnectionStatus, ObservationState,
    PartyNeeds, Profile, ReasonCode, Success, ToolError, ToolErrorClass, TransferView,
)
from skyassist.routing import Graph
from skyassist.timing import (
    TransferFacts, calculate_terminal_transfer_time, evaluate_transfer, profile_for,
)
from test_routing import AS_OF, POLICY, at, edge, node, overlay, queue, seconds


def facts(**changes):
    return TransferFacts(**{
        "origin_id": "A", "gate_id": "G", "gate_state": ObservationState.FRESH,
        "cutoff": at(40 * 60), "cutoff_verified": True, "ready": seconds(0, 0),
        "as_of": AS_OF, **changes,
    })


def standard_graph():
    return Graph("DXB", (node("A"), node("G")), (
        edge("corridor", "A", "G", fixed_movement=seconds(300, 480)),
    ))


@pytest.fixture
def mobility_graph():
    return Graph("DXB", (
        node("C22", pickup_eligible=True), node("Lift_C", x=10), node("Train_C", floor="1"),
        node("Train_B", floor="1", concourse="B"), node("B14", concourse="B"),
        node("B18", concourse="B"),
    ), (
        edge("lift", "C22", "Lift_C", mode=AccessibleMode.LIFT, requires_access_evidence=True,
             fixed_movement=seconds(60, 90)),
        edge("to_train", "Lift_C", "Train_C", fixed_movement=seconds(120, 150)),
        edge("train", "Train_C", "Train_B", mode=AccessibleMode.TRANSIT,
             fixed_movement=seconds(120, 180), wait=seconds(180, 420)),
        edge("to_b14", "Train_B", "B14", mode=AccessibleMode.STEP_FREE,
             fixed_movement=seconds(120, 120)),
        edge("to_b18", "C22", "B18", mode=AccessibleMode.STEP_FREE,
             fixed_movement=seconds(360, 480)),
    ), overlays=(overlay("lift", valid_until=at(300)),))


def test_c01_pending_handoff(mobility_graph):
    result = evaluate_transfer(mobility_graph, facts(
        origin_id="C22", gate_id="B14", cutoff=at(2100), ready=seconds(240, 360),
        party_needs=PartyNeeds(wheelchair=True), handoff_pending=True, revision=2,
    ), POLICY)
    assert (result.time_remaining, result.lower, result.upper, result.margin) == (2100, 1260, 1740, 360)
    assert result.status is ConnectionStatus.TIGHT_CONNECTION
    assert result.reason_codes == (ReasonCode.TIGHT_MARGIN, ReasonCode.STAFF_HANDOFF_PENDING)
    route = result.route
    assert route.node_ids == ("C22", "Lift_C", "Train_C", "Train_B", "B14")
    assert (route.movement, route.wait, route.buffer) == (seconds(420, 540), seconds(180, 420), 420)
    assert [step.number for step in route.steps] == [1, 2, 3, 4, 5]
    assert [step.text.key for step in route.steps] == [
        "route.wait_at_pickup", "route.take_lift", "route.board_train",
        "route.exit_train", "route.approach_gate",
    ]
    assert [step.landmark.key for step in route.steps] == [
        "landmark.c22", "landmark.lift_c", "landmark.train_c", "landmark.train_b", "landmark.b14",
    ]
    assert route.steps[-1].text.params == {"gate": "B14"}
    view = TransferView.from_result(result, provenance=route.provenance)
    assert (view.lower_min, view.upper_min, view.margin_min, view.conditional) == (21, 29, 6, True)


def test_c02_reroutes_to_b18_with_new_identity(mobility_graph):
    old = evaluate_transfer(mobility_graph, facts(
        origin_id="C22", gate_id="B14", cutoff=at(2100), ready=seconds(240, 360),
        party_needs=PartyNeeds(step_free=True), revision=2,
    ), POLICY)
    updated = evaluate_transfer(mobility_graph, facts(
        origin_id="C22", gate_id="B18", as_of=at(180), cutoff=at(2100),
        ready=seconds(0, 60), party_needs=PartyNeeds(step_free=True), revision=3,
    ), POLICY)
    assert (updated.time_remaining, updated.lower, updated.upper, updated.margin) == (1920, 780, 960, 960)
    assert updated.status is ConnectionStatus.TIME_AVAILABLE
    assert updated.route.node_ids == ("C22", "B18")
    assert updated.route.steps[-1].text.params == {"gate": "B18"}
    assert old.route.route_id != updated.route.route_id
    assert (old.route.revision, updated.route.revision) == (2, 3)


def test_c03_standard():
    result = evaluate_transfer(standard_graph(), facts(), POLICY)
    assert (result.time_remaining, result.lower, result.upper, result.margin) == (2400, 480, 660, 1740)
    assert result.status is ConnectionStatus.TIME_AVAILABLE
    assert result.reason_codes == ()


def test_c04_family_lower_bound_exceeds_cutoff():
    graph = Graph("DXB", (node("A"), node("G")), (
        edge("slow", "A", "G", fixed_movement=seconds(240, 300), wait=seconds(180, 720)),
    ))
    result = evaluate_transfer(graph, facts(cutoff=at(480), party_needs=PartyNeeds(children=1)), POLICY)
    assert (result.time_remaining, result.lower, result.upper, result.margin) == (480, 720, 1320, -840)
    assert result.status is ConnectionStatus.TRANSFER_UNLIKELY_BEFORE_CUTOFF
    assert result.reason_codes == (ReasonCode.LOWER_BOUND_EXCEEDS_CUTOFF,)


def test_c05_cutoff_unavailable_preserves_route():
    result = evaluate_transfer(standard_graph(), facts(cutoff=None), POLICY)
    assert result.route.node_ids == ("A", "G")
    assert (result.lower, result.upper, result.time_remaining, result.margin) == (480, 660, None, None)
    assert result.status is ConnectionStatus.TIMING_UNVERIFIED
    assert result.cutoff_unavailable
    assert result.reason_codes == (ReasonCode.CUTOFF_MISSING,)


@pytest.mark.parametrize(
    "state,reason", [(ObservationState.STALE, ReasonCode.GATE_STALE),
                     (ObservationState.CONFLICT, ReasonCode.GATE_CONFLICT),
                     (ObservationState.UNKNOWN, ReasonCode.GATE_UNKNOWN)],
)
def test_c06_gate_evidence_suppresses_route_then_resolves(state, reason):
    graph = standard_graph()
    result = evaluate_transfer(graph, facts(gate_state=state), POLICY)
    assert result.route is result.lower is result.upper is result.margin is None
    assert result.time_remaining == 2400
    assert result.status is ConnectionStatus.TIMING_UNVERIFIED
    assert result.reason_codes == (reason,)
    resolved = evaluate_transfer(graph, facts(gate_state=ObservationState.FRESH), POLICY)
    assert resolved.route.node_ids == ("A", "G")
    assert (resolved.lower, resolved.upper, resolved.margin) == (480, 660, 1740)
    assert resolved.status is ConnectionStatus.TIME_AVAILABLE


def test_c08_lift_closure_leaves_only_stairs():
    graph = Graph("DXB", (node("A"), node("G")), (
        edge("lift", "A", "G", lift=True, requires_access_evidence=True),
        edge("stairs", "A", "G", mode=AccessibleMode.STAIRS, steps=18),
    ))
    result = evaluate_transfer(graph, facts(
        party_needs=PartyNeeds(wheelchair=True, children=1),
        overlays=(overlay("lift", closed=True),),
    ), POLICY)
    assert result.route is result.lower is result.upper is result.margin is None
    assert result.time_remaining == 2400
    assert result.status is ConnectionStatus.TIMING_UNVERIFIED
    assert result.reason_codes == (ReasonCode.NO_ACCESSIBLE_PATH, ReasonCode.LIFT_CLOSED)


def test_c09_cutoff_crosses_midnight_dubai():
    dubai = ZoneInfo("Asia/Dubai")
    as_of = datetime(2026, 11, 19, 23, 50, tzinfo=dubai)
    cutoff = datetime(2026, 11, 20, 0, 18, tzinfo=dubai)
    graph = Graph("DXB", (node("A"), node("G")), (
        edge("path", "A", "G", fixed_movement=seconds(420, 540), wait=seconds(60, 60)),
    ))
    result = evaluate_transfer(graph, facts(as_of=as_of, cutoff=cutoff, ready=seconds(60, 120)), POLICY)
    assert (result.time_remaining, result.lower, result.upper, result.margin) == (1680, 720, 900, 780)
    assert result.status is ConnectionStatus.TIME_AVAILABLE


@pytest.mark.parametrize(
    "observations, reason",
    [
        ((queue("security", seconds(60, 900), observed_at=at(-180), valid_until=at(-1)),),
         ReasonCode.QUEUE_BOUND_STALE),
        ((), ReasonCode.QUEUE_BOUND_MISSING),
        ((queue("security", None),), ReasonCode.QUEUE_BOUND_MISSING),
    ],
)
def test_c10_unbounded_checkpoint_wait_withholds_margin(observations, reason):
    graph = Graph("DXB", (node("A"), node("G", security_zone="post_checkpoint")), (
        edge("checkpoint", "A", "G", mode=AccessibleMode.CHECKPOINT, queue_id="security",
             fixed_movement=seconds(60, 90), wait=seconds(120, 180)),
    ))
    result = evaluate_transfer(graph, facts(cutoff=at(1800), queues=observations), POLICY)
    assert result.route.node_ids == ("A", "G")
    assert result.route.steps[0].text.key == "route.cross_checkpoint"
    assert result.route.wait == seconds(120, None)
    assert (result.time_remaining, result.lower, result.upper, result.margin) == (1800, 360, None, None)
    assert result.status is ConnectionStatus.TIMING_UNVERIFIED
    assert result.reason_codes == (reason,)


@pytest.mark.parametrize(
    "margin, status", [(0, ConnectionStatus.TIGHT_CONNECTION),
                       (600, ConnectionStatus.TIGHT_CONNECTION),
                       (601, ConnectionStatus.TIME_AVAILABLE)],
)
def test_margin_boundaries(margin, status):
    result = evaluate_transfer(standard_graph(), facts(cutoff=at(660 + margin)), POLICY)
    assert result.status is status
    assert result.margin == margin


def test_cutoff_passed_beats_no_route():
    graph = Graph("DXB", (node("A"), node("G")), ())
    result = evaluate_transfer(graph, facts(cutoff=at(-1)), POLICY)
    assert result.route is None
    assert result.time_remaining == -1
    assert result.status is ConnectionStatus.BOARDING_CUTOFF_PASSED
    assert result.reason_codes[0] is ReasonCode.CUTOFF_PASSED


def test_estimated_cutoff_and_unknown_readiness_leave_route_available():
    graph = standard_graph()
    estimated = evaluate_transfer(graph, facts(
        cutoff=at(-1), cutoff_verified=False, cutoff_estimated=True,
    ), POLICY)
    assert estimated.route is not None
    assert estimated.status is ConnectionStatus.TIMING_UNVERIFIED
    assert estimated.reason_codes == (ReasonCode.CUTOFF_ESTIMATED,)
    assert not estimated.cutoff_unavailable
    result = evaluate_transfer(graph, facts(ready=None), POLICY)
    assert result.route is not None
    assert result.status is ConnectionStatus.TIMING_UNVERIFIED
    assert result.reason_codes == (ReasonCode.READINESS_UNKNOWN,)
    assert result.lower is result.upper is result.margin is None


def test_negative_margin_compares_against_second_pass_instead_of_chosen_route():
    graph = Graph("DXB", (node("A"), node("G")), (
        edge("steady", "A", "G", fixed_movement=seconds(200, 300)),
        edge("variable", "A", "G", fixed_movement=seconds(100, 400)),
    ))
    result = evaluate_transfer(graph, facts(cutoff=at(280)), POLICY)
    assert (result.lower, result.upper, result.margin) == (380, 480, -200)
    assert result.status is ConnectionStatus.MAY_MISS_BOARDING
    assert result.reason_codes == (ReasonCode.NEGATIVE_MARGIN,)


@pytest.mark.parametrize("needs,profile", [
    (PartyNeeds(), Profile.STANDARD),
    (PartyNeeds(children=1), Profile.FAMILY),
    (PartyNeeds(wheelchair=True), Profile.REDUCED_MOBILITY),
    (PartyNeeds(children=1, step_free=True), Profile.FAMILY_MOBILITY),
])
def test_profile_for(needs, profile):
    assert profile_for(needs) is profile


def test_tool_returns_route_or_typed_gate_error():
    graph = standard_graph()
    request = CalculateTerminalTransferTimeInput(
        origin_id="A", gate_id="G", as_of=AS_OF, profile=Profile.STANDARD,
    )
    result = calculate_terminal_transfer_time(
        graph, request, facts_provider=lambda _: facts(cutoff=None), policy=POLICY,
    )
    assert isinstance(result, Success)
    assert result.value.node_ids == ("A", "G")
    stale = calculate_terminal_transfer_time(
        graph, request, facts_provider=lambda _: facts(gate_state=ObservationState.STALE),
        policy=POLICY,
    )
    assert isinstance(stale, ToolError)
    assert stale.error_class is ToolErrorClass.STALE
    assert stale.diagnostic is ReasonCode.GATE_STALE


@given(st.integers(0, 300), st.integers(0, 300), st.integers(0, 300))
def test_increasing_mandatory_wait_never_lowers_total(first, alternative, extra):
    nodes = tuple(map(node, ("A", "B", "D", "G")))

    def transfer(increase):
        graph = Graph("DXB", nodes, (
            edge("direct-1", "A", "B"), edge("direct-2", "B", "G", queue_id="first"),
            edge("other-1", "A", "D"),
            edge("other-2", "D", "G", wait=seconds(alternative, alternative)),
        ))
        wait = seconds(first + increase, first + increase)
        return evaluate_transfer(graph, facts(queues=(queue("first", wait),)), POLICY)

    assert transfer(extra).upper >= transfer(0).upper
