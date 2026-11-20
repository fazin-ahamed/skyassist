import json
import sqlite3
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import ValidationError

from skyassist.evidence import _append, _latest, _select, current_field, get_leg
from skyassist.models import (
    AircraftFreshness, AircraftReport, AircraftView, Applicability, ArrivalEstimate,
    ArrivalEstimates, ExpiringSourceStamp, FlightLeg, GetAircraftPositionInput,
    GetAircraftPositionOutput, GetArrivalEstimatesInput, GetFlightAndGateStatusInput,
    GetFlightAndGateStatusOutput, ObservationField, ObservationState, PassengerFlow,
    PolicyPack, ReasonCode, SecondsRange, SourceStamp, Success, ToolError, ToolErrorClass,
    ToolResult,
)


class FlightProvider(Protocol):
    def flight_status(
        self, request: GetFlightAndGateStatusInput, *, deadline: datetime,
    ) -> ToolResult[GetFlightAndGateStatusOutput]: ...


class ArrivalEstimateProvider(Protocol):
    def arrival_estimates(
        self, request: GetArrivalEstimatesInput, *, as_of: datetime, deadline: datetime,
    ) -> ToolResult[ArrivalEstimates]: ...


class AircraftProvider(Protocol):
    def aircraft_position(
        self, request: GetAircraftPositionInput, *, as_of: datetime, deadline: datetime,
    ) -> ToolResult[GetAircraftPositionOutput]: ...


class ProviderFailureKind(StrEnum):
    TRANSIENT = "transient"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"
    AUTH = "auth"
    UNAVAILABLE = "unavailable"


class ProviderFailure(Exception):
    def __init__(self, kind: ProviderFailureKind, *, retry_after_seconds: int | None = None) -> None:
        self.kind = ProviderFailureKind(kind)
        if retry_after_seconds is not None and (
            type(retry_after_seconds) is not int or retry_after_seconds < 0
        ):
            raise ValueError("invalid retry delay")
        self.retry_after_seconds = retry_after_seconds
        super().__init__(self.kind.value)


def matched_leg(conn: sqlite3.Connection, supplied: FlightLeg) -> FlightLeg | None:
    leg = get_leg(conn, supplied.id)
    fields = ("operating_carrier", "operating_number", "scheduled_operation_date",
              "origin", "destination")
    if leg is None or any(getattr(leg, name) != getattr(supplied, name) for name in fields):
        return None
    if supplied.provider_leg_id not in (None, leg.provider_leg_id):
        return None
    return leg if set(supplied.aliases).issubset(leg.aliases) else None


class MockFlightProvider:
    def __init__(self, conn: sqlite3.Connection, policy: PolicyPack) -> None:
        self.conn = conn
        self.policy = policy

    def flight_status(
        self, request: GetFlightAndGateStatusInput, *, deadline: datetime,
    ) -> ToolResult[GetFlightAndGateStatusOutput]:
        legs = []
        for ref in request.refs:
            leg = get_leg(self.conn, ref)
            if leg is None:
                return ToolError(error_class=ToolErrorClass.MISSING, diagnostic=ReasonCode.FLIGHT_UNKNOWN)
            if request.airport not in (leg.origin, leg.destination):
                return ToolError(error_class=ToolErrorClass.MISSING, diagnostic=ReasonCode.WRONG_AIRPORT)
            legs.append(leg)
        return Success(value=GetFlightAndGateStatusOutput(
            legs=tuple(legs),
            facts=tuple(current_field(self.conn, leg.id, field, request.as_of, self.policy)
                        for leg in legs for field in ObservationField),
        ))


type EstimateKind = Literal["baggage", "immigration", "security"]
_ESTIMATES = ("baggage", "immigration", "security")
_AIRCRAFT_FIELD = "aircraft_position"


class MockArrivalEstimateProvider:
    def __init__(self, conn: sqlite3.Connection, policy: PolicyPack) -> None:
        self.conn = conn
        self.policy = policy

    def ingest(
        self, leg_id: str, flow: PassengerFlow, kind: EstimateKind, *,
        stamp: ExpiringSourceStamp, received_at: datetime, applicability: Applicability,
        wait: SecondsRange | None = None, authority_rank: int = 0,
    ) -> None:
        if kind not in _ESTIMATES:
            raise ValueError("unknown arrival estimate")
        if wait is not None and applicability != Applicability.APPLICABLE:
            raise ValueError("wait requires an applicable estimate")
        estimate = ArrivalEstimate(
            applicability=applicability, state=ObservationState.FRESH, wait=wait, provenance=stamp,
        )
        _append(self.conn, dict(
            stamp.model_dump(), leg_id=leg_id, field=f"arrival_estimates:{PassengerFlow(flow)}:{kind}",
            received_at=received_at, authority_rank=authority_rank,
            value=estimate.model_dump(mode="json", include={"applicability", "wait"}),
        ))

    def arrival_estimates(
        self, request: GetArrivalEstimatesInput, *, as_of: datetime, deadline: datetime,
    ) -> ToolResult[ArrivalEstimates]:
        leg = matched_leg(self.conn, request.arrival)
        if leg is None:
            return ToolError(error_class=ToolErrorClass.MISSING, diagnostic=ReasonCode.FLIGHT_UNKNOWN)
        if request.airport != leg.destination:
            return ToolError(error_class=ToolErrorClass.MISSING, diagnostic=ReasonCode.WRONG_AIRPORT)
        estimates = {}
        try:
            for kind in _ESTIMATES:
                field = f"arrival_estimates:{request.journey_type}:{kind}"
                rows = _latest(self.conn, leg.id, field, as_of)
                state, selected = _select(rows, as_of, self.policy.stale_seconds, "value_json")
                if state is ObservationState.CONFLICT:
                    estimates[kind] = ArrivalEstimate(applicability=Applicability.UNKNOWN, state=state)
                elif selected:
                    row = selected[0]
                    estimates[kind] = ArrivalEstimate(
                        **json.loads(row["value_json"]), state=state,
                        provenance=SourceStamp(source=row["source"], observed_at=row["observed_at"]),
                    )
        except (ValidationError, TypeError, ValueError):
            return ToolError(error_class=ToolErrorClass.UNAVAILABLE, diagnostic=ReasonCode.ESTIMATE_UNAVAILABLE)
        if not estimates:
            return ToolError(error_class=ToolErrorClass.MISSING, diagnostic=ReasonCode.ESTIMATE_UNAVAILABLE)
        return Success(value=ArrivalEstimates(**estimates))


class MockAircraftProvider:
    def __init__(self, conn: sqlite3.Connection, policy: PolicyPack) -> None:
        self.conn = conn
        self.policy = policy

    def ingest(
        self, report: AircraftReport, *, received_at: datetime, valid_until: datetime,
        authority_rank: int = 0,
    ) -> None:
        stamp = ExpiringSourceStamp(
            source=report.source, observed_at=report.observed_at, valid_until=valid_until,
        )
        _append(self.conn, dict(
            stamp.model_dump(), leg_id=report.matched_leg_id, field=_AIRCRAFT_FIELD,
            received_at=received_at, authority_rank=authority_rank,
            value=report.model_dump(mode="json", exclude={"source", "observed_at"}),
        ))

    def aircraft_position(
        self, request: GetAircraftPositionInput, *, as_of: datetime, deadline: datetime,
    ) -> ToolResult[GetAircraftPositionOutput]:
        leg = matched_leg(self.conn, request.leg)
        if leg is None:
            return ToolError(error_class=ToolErrorClass.MISSING, diagnostic=ReasonCode.AIRCRAFT_UNMATCHED)
        rows = _latest(self.conn, leg.id, _AIRCRAFT_FIELD, as_of)
        state, selected = _select(rows, as_of, self.policy.aircraft_hide_seconds, "value_json")
        if state in (ObservationState.UNKNOWN, ObservationState.CONFLICT):
            return ToolError(
                error_class=ToolErrorClass.CONFLICT if rows else ToolErrorClass.MISSING,
                diagnostic=ReasonCode.POSITION_UNAVAILABLE,
            )
        row = selected[0]
        try:
            report = AircraftReport(
                **json.loads(row["value_json"]), source=row["source"], observed_at=row["observed_at"],
            )
        except (ValidationError, TypeError, ValueError):
            return ToolError(error_class=ToolErrorClass.UNAVAILABLE, diagnostic=ReasonCode.POSITION_UNAVAILABLE)
        if report.matched_leg_id != leg.id or report.provider_leg_id != leg.provider_leg_id:
            return ToolError(error_class=ToolErrorClass.MISSING, diagnostic=ReasonCode.AIRCRAFT_UNMATCHED)
        freshness = AircraftView.from_report(report, as_of=as_of, policy=self.policy).freshness
        if state is ObservationState.STALE or freshness is AircraftFreshness.HIDDEN:
            return Success(value=GetAircraftPositionOutput(
                freshness=AircraftFreshness.HIDDEN, reason_code=ReasonCode.POSITION_STALE,
            ))
        return Success(value=GetAircraftPositionOutput(report=report, freshness=freshness))
