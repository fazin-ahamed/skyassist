from collections.abc import Callable

from pydantic import StrictBool

from skyassist.models import (
    AirportCode, CalculateTerminalTransferTimeInput, CalculateTerminalTransferTimeOutput,
    ConnectionStatus, Contract, Id, NonNegativeInt, ObservationState, Overlay,
    PartyNeeds, PassengerFlow, PolicyPack, Profile, QueueObservation, ReasonCode,
    SecondsRange, Success, ToolError, ToolErrorClass, ToolResult, TransferResult, UTCInstant,
)
from skyassist.routing import SECOND, Graph, find_route


class TransferFacts(Contract):
    """Use the confirmed pickup as the origin when staff handoff is pending."""

    origin_id: Id
    gate_id: Id | None = None
    gate_state: ObservationState = ObservationState.UNKNOWN
    cutoff: UTCInstant | None = None
    cutoff_verified: StrictBool = False
    cutoff_estimated: StrictBool = False
    ready: SecondsRange | None = None
    party_needs: PartyNeeds = PartyNeeds()
    flow: PassengerFlow = PassengerFlow.CONNECTING
    handoff_pending: StrictBool = False
    as_of: UTCInstant
    overlays: tuple[Overlay, ...] = ()
    queues: tuple[QueueObservation, ...] = ()
    airport: AirportCode | None = None
    gate_label: str | None = None
    revision: NonNegativeInt = 0


def profile_for(needs: PartyNeeds) -> Profile:
    if needs.wheelchair or needs.step_free:
        return Profile.FAMILY_MOBILITY if needs.children else Profile.REDUCED_MOBILITY
    return Profile.FAMILY if needs.children else Profile.STANDARD


def evaluate_transfer(graph: Graph, facts: TransferFacts, policy: PolicyPack) -> TransferResult:
    reasons: list[ReasonCode] = []
    route = None
    best_lower = None
    if facts.gate_state is ObservationState.STALE:
        reasons.append(ReasonCode.GATE_STALE)
    elif facts.gate_state is ObservationState.CONFLICT:
        reasons.append(ReasonCode.GATE_CONFLICT)
    elif facts.gate_state is ObservationState.UNKNOWN or facts.gate_id is None:
        reasons.append(ReasonCode.GATE_UNKNOWN)
    elif facts.airport is not None and facts.airport != graph.airport:
        reasons.append(ReasonCode.WRONG_AIRPORT)
    else:
        request = CalculateTerminalTransferTimeInput(
            origin_id=facts.origin_id, gate_id=facts.gate_id,
            profile=profile_for(facts.party_needs), as_of=facts.as_of, flow=facts.flow,
        )
        route, best_lower, route_reasons = find_route(
            graph, request, policy, ready_upper=facts.ready.upper if facts.ready else None,
            handoff_pending=facts.handoff_pending, revision=facts.revision,
            gate_label=facts.gate_label, overlays=facts.overlays, queues=facts.queues,
        )
        reasons.extend(route_reasons)

    cutoff_unavailable = facts.cutoff is None or not (facts.cutoff_verified or facts.cutoff_estimated)
    remaining = None if cutoff_unavailable else (facts.cutoff - facts.as_of) // SECOND
    if cutoff_unavailable:
        reasons.append(ReasonCode.CUTOFF_MISSING)
    elif facts.cutoff_estimated:
        reasons.append(ReasonCode.CUTOFF_ESTIMATED)

    ready = facts.ready
    lower = upper = margin = None
    if ready is None or ready.upper is None:
        reasons.append(ReasonCode.READINESS_UNKNOWN)
    if route is not None and ready is not None:
        lower = ready.lower + route.movement.lower + route.wait.lower + route.buffer
        if ready.upper is not None and route.movement.upper is not None and route.wait.upper is not None:
            upper = ready.upper + route.movement.upper + route.wait.upper + route.buffer
            if remaining is not None:
                margin = remaining - upper
    if route is not None and route.movement.upper is None:
        reasons.append(ReasonCode.ROUTE_UNAVAILABLE)

    if facts.cutoff_verified and not facts.cutoff_estimated and remaining is not None and remaining < 0:
        status = ConnectionStatus.BOARDING_CUTOFF_PASSED
        reasons.insert(0, ReasonCode.CUTOFF_PASSED)
    elif margin is None or facts.cutoff_estimated:
        status = ConnectionStatus.TIMING_UNVERIFIED
    elif 0 <= margin <= policy.tight_margin_seconds:
        status = ConnectionStatus.TIGHT_CONNECTION
        reasons.append(ReasonCode.TIGHT_MARGIN)
    elif margin > policy.tight_margin_seconds:
        status = ConnectionStatus.TIME_AVAILABLE
    elif ready.lower + best_lower + route.buffer > remaining:
        status = ConnectionStatus.TRANSFER_UNLIKELY_BEFORE_CUTOFF
        reasons.append(ReasonCode.LOWER_BOUND_EXCEEDS_CUTOFF)
    else:
        status = ConnectionStatus.MAY_MISS_BOARDING
        reasons.append(ReasonCode.NEGATIVE_MARGIN)
    if facts.handoff_pending:
        reasons.append(ReasonCode.STAFF_HANDOFF_PENDING)

    return TransferResult(
        route=route, ready=ready, lower=lower, upper=upper, time_remaining=remaining, margin=margin,
        status=status, reason_codes=tuple(reasons), cutoff_unavailable=cutoff_unavailable,
    )


def calculate_terminal_transfer_time(
    graph: Graph, request: CalculateTerminalTransferTimeInput, *,
    facts_provider: Callable[[CalculateTerminalTransferTimeInput], TransferFacts | ToolError],
    policy: PolicyPack,
) -> ToolResult[CalculateTerminalTransferTimeOutput]:
    facts = facts_provider(request)
    if isinstance(facts, ToolError):
        return facts
    if (
        facts.origin_id != request.origin_id or facts.as_of != request.as_of
        or facts.flow != request.flow or profile_for(facts.party_needs) != request.profile
        or (facts.gate_id is not None and facts.gate_id != request.gate_id)
    ):
        return ToolError(error_class=ToolErrorClass.UNAVAILABLE, diagnostic=ReasonCode.INVALID_ARGUMENTS)

    result = evaluate_transfer(graph, facts, policy)
    if result.route is not None:
        return Success[CalculateTerminalTransferTimeOutput](value=result.route)

    for error_class, diagnostic in (
        (ToolErrorClass.STALE, ReasonCode.GATE_STALE),
        (ToolErrorClass.CONFLICT, ReasonCode.GATE_CONFLICT),
        (ToolErrorClass.MISSING, ReasonCode.GATE_UNKNOWN),
        (ToolErrorClass.MISSING, ReasonCode.LOCATION_UNKNOWN),
        (ToolErrorClass.UNAVAILABLE, ReasonCode.WRONG_AIRPORT),
        (ToolErrorClass.UNAVAILABLE, ReasonCode.NO_ACCESSIBLE_PATH),
    ):
        if diagnostic in result.reason_codes:
            return ToolError(error_class=error_class, diagnostic=diagnostic)
    return ToolError(error_class=ToolErrorClass.UNAVAILABLE, diagnostic=ReasonCode.NO_PATH)
