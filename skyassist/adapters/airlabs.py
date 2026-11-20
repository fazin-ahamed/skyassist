import os
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import ValidationError

from skyassist.db import transaction
from skyassist.evidence import current_field, get_leg, ingest
from skyassist.models import (
    AircraftFreshness, AircraftPosition, AircraftReport, AircraftView, FlightLeg,
    GetAircraftPositionInput, GetAircraftPositionOutput, GetFlightAndGateStatusInput,
    GetFlightAndGateStatusOutput, Observation, ObservationField, PolicyPack, ReasonCode,
    Success, ToolError, ToolErrorClass, ToolResult,
)
from skyassist.providers import ProviderFailure, ProviderFailureKind

BASE_URL = "https://airlabs.co/api/v9"
KEY_ENV = "AIRLABS_API_KEY"
SOURCE = "airlabs"
AUTHORITY_RANK = 0
TIME_FORMAT = "%Y-%m-%d %H:%M"


def _unavailable() -> ToolError:
    return ToolError(error_class=ToolErrorClass.UNAVAILABLE, diagnostic=ReasonCode.SOURCE_UNAVAILABLE)


def _flight(leg: FlightLeg) -> str:
    return f"{leg.operating_carrier}{leg.operating_number}"


def _side(leg: FlightLeg, airport: str) -> str:
    return "dep" if airport == leg.origin else "arr"


def _status_failure(response: httpx.Response) -> ProviderFailure:
    status = response.status_code
    if status in (401, 403):
        return ProviderFailure(ProviderFailureKind.AUTH)
    if status == 402 or (status == 429 and "quota" in response.text.lower()):
        return ProviderFailure(ProviderFailureKind.QUOTA)
    if status == 429:
        retry = response.headers.get("Retry-After", "")
        return ProviderFailure(
            ProviderFailureKind.RATE_LIMIT, retry_after_seconds=int(retry) if retry.isdigit() else None,
        )
    if status >= 500:
        return ProviderFailure(ProviderFailureKind.TRANSIENT)
    return ProviderFailure(ProviderFailureKind.UNAVAILABLE)


def _get(client: httpx.Client, api_key: str, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
    try:
        response = client.get(f"{BASE_URL}/{path}", params={**params, "api_key": api_key})
    except httpx.TimeoutException:
        raise ProviderFailure(ProviderFailureKind.TIMEOUT) from None
    except httpx.TransportError:
        raise ProviderFailure(ProviderFailureKind.TRANSIENT) from None
    if response.status_code >= 400:
        raise _status_failure(response)
    try:
        body = response.json()
    except ValueError:
        raise ProviderFailure(ProviderFailureKind.UNAVAILABLE) from None
    rows = body.get("response") if isinstance(body, dict) else body
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ProviderFailure(ProviderFailureKind.UNAVAILABLE)
    return rows


def _text(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ProviderFailure(ProviderFailureKind.UNAVAILABLE)
    return value


def _instant(value: str | None) -> datetime:
    try:
        return datetime.strptime(value, TIME_FORMAT).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        raise ProviderFailure(ProviderFailureKind.UNAVAILABLE) from None


def _optional_instant(row: dict[str, Any], key: str) -> datetime | None:
    text = _text(row, key)
    return None if text is None else _instant(text)


def _real(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ProviderFailure(ProviderFailureKind.UNAVAILABLE)
    return float(value)


def _epoch(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ProviderFailure(ProviderFailureKind.UNAVAILABLE)
    return datetime.fromtimestamp(value, UTC)


def _select(rows: list[dict[str, Any]], leg: FlightLeg) -> dict[str, Any] | ToolError:
    same = [row for row in rows if row.get("flight_iata") == _flight(leg)]
    scoped = [
        row for row in same
        if row.get("dep_iata") == leg.origin and row.get("arr_iata") == leg.destination
        and _instant(_text(row, "dep_time")).date() == leg.scheduled_operation_date
    ]
    if len(scoped) > 1:
        return ToolError(error_class=ToolErrorClass.AMBIGUOUS, diagnostic=ReasonCode.AMBIGUOUS_FLIGHT)
    if scoped:
        return scoped[0]
    if any(row.get("dep_iata") != leg.origin or row.get("arr_iata") != leg.destination for row in same):
        return ToolError(error_class=ToolErrorClass.MISSING, diagnostic=ReasonCode.WRONG_AIRPORT)
    return ToolError(error_class=ToolErrorClass.MISSING, diagnostic=ReasonCode.FLIGHT_UNKNOWN)


def _observations(
    row: dict[str, Any], leg: FlightLeg, side: str, as_of: datetime, valid_until: datetime,
) -> list[Observation]:
    stamp = dict(
        source=SOURCE, leg_id=leg.id, authority_rank=AUTHORITY_RANK,
        observed_at=as_of, received_at=as_of, valid_until=valid_until,
    )
    values = {
        ObservationField.GATE: _text(row, f"{side}_gate"),
        ObservationField.TERMINAL: _text(row, f"{side}_terminal"),
        ObservationField.STATUS: _text(row, "status"),
    }
    if side == "arr":
        values[ObservationField.CAROUSEL] = _text(row, "arr_baggage")
    observations = [Observation(**stamp, field=field, value=value)
                    for field, value in values.items() if value is not None]
    timing = ObservationField.DEPARTURE if side == "dep" else ObservationField.ARRIVAL
    observations.append(Observation(
        **stamp, field=timing, scheduled=_instant(_text(row, f"{side}_time_utc")),
        estimated=_optional_instant(row, f"{side}_estimated_utc"),
        actual=_optional_instant(row, f"{side}_actual_utc"),
    ))
    return observations


class AirLabsFlightProvider:
    def __init__(self, conn: sqlite3.Connection, policy: PolicyPack, client: httpx.Client) -> None:
        self.conn = conn
        self.policy = policy
        self.client = client
        self.api_key = os.environ.get(KEY_ENV) or None

    def flight_status(
        self, request: GetFlightAndGateStatusInput, *, deadline: datetime,
    ) -> ToolResult[GetFlightAndGateStatusOutput]:
        if self.api_key is None:
            return _unavailable()
        valid_until = request.as_of + timedelta(seconds=self.policy.stale_seconds)
        legs: list[FlightLeg] = []
        observations: list[Observation] = []
        for ref in request.refs:
            leg = get_leg(self.conn, ref)
            if leg is None:
                return ToolError(error_class=ToolErrorClass.MISSING, diagnostic=ReasonCode.FLIGHT_UNKNOWN)
            if request.airport not in (leg.origin, leg.destination):
                return ToolError(error_class=ToolErrorClass.MISSING, diagnostic=ReasonCode.WRONG_AIRPORT)
            side = _side(leg, request.airport)
            rows = _get(self.client, self.api_key, "schedules",
                        {"flight_iata": _flight(leg), f"{side}_iata": request.airport})
            selected = _select(rows, leg)
            if isinstance(selected, ToolError):
                return selected
            legs.append(leg)
            observations += _observations(selected, leg, side, request.as_of, valid_until)
        with transaction(self.conn):
            for observation in observations:
                ingest(self.conn, observation)
        return Success(value=GetFlightAndGateStatusOutput(
            legs=tuple(legs),
            facts=tuple(current_field(self.conn, leg.id, field, request.as_of, self.policy)
                        for leg in legs for field in ObservationField),
        ))


class AirLabsAircraftProvider:
    def __init__(self, policy: PolicyPack, client: httpx.Client) -> None:
        self.policy = policy
        self.client = client
        self.api_key = os.environ.get(KEY_ENV) or None

    def aircraft_position(
        self, request: GetAircraftPositionInput, *, as_of: datetime, deadline: datetime,
    ) -> ToolResult[GetAircraftPositionOutput]:
        if self.api_key is None:
            return _unavailable()
        leg = request.leg
        rows = _get(self.client, self.api_key, "flights",
                    {"flight_iata": _flight(leg), "dep_iata": leg.origin, "arr_iata": leg.destination})
        matches = [row for row in rows if row.get("flight_iata") == _flight(leg)
                   and row.get("dep_iata") == leg.origin and row.get("arr_iata") == leg.destination]
        if not matches:
            return ToolError(error_class=ToolErrorClass.MISSING, diagnostic=ReasonCode.AIRCRAFT_UNMATCHED)
        if len(matches) > 1:
            return ToolError(error_class=ToolErrorClass.AMBIGUOUS, diagnostic=ReasonCode.AIRCRAFT_UNMATCHED)
        row = matches[0]
        observed_at = _epoch(row.get("updated"))
        try:
            report = AircraftReport(
                source=SOURCE, observed_at=observed_at,
                position=AircraftPosition(
                    latitude=_real(row.get("lat")), longitude=_real(row.get("lng")),
                    altitude_m=_real(row.get("alt")),
                ),
                matched_leg_id=leg.id, provider_leg_id=leg.provider_leg_id,
                aircraft_id=_text(row, "hex"),
            )
        except ValidationError:
            return ToolError(error_class=ToolErrorClass.UNAVAILABLE, diagnostic=ReasonCode.POSITION_UNAVAILABLE)
        view = AircraftView.from_report(report, as_of=as_of, policy=self.policy)
        if view.freshness is AircraftFreshness.HIDDEN:
            return Success(value=GetAircraftPositionOutput(
                freshness=view.freshness, reason_code=ReasonCode.POSITION_STALE,
            ))
        if view.position is None:
            return ToolError(error_class=ToolErrorClass.UNAVAILABLE, diagnostic=ReasonCode.POSITION_UNAVAILABLE)
        return Success(value=GetAircraftPositionOutput(report=report, freshness=view.freshness))
