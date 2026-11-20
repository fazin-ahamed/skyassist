"""Translate OAG observations into the journey agent's flight evidence contract."""

import sqlite3
import time
from collections.abc import Callable
from contextlib import closing, nullcontext
from datetime import datetime, timedelta

import httpx

from skyassist import oag
from skyassist.evidence import get_leg
from skyassist.models import (
    FieldEvidence, FlightLeg, GetFlightAndGateStatusInput, GetFlightAndGateStatusOutput,
    Observation, ObservationField, ObservationState, PolicyPack, ReasonCode, Success, ToolError,
    ToolErrorClass, ToolResult,
)
from skyassist.network import offline_forced

SOURCE = "oag"
AUTHORITY_RANK = 1
F = ObservationField


def _failure(reason: ReasonCode, kind: ToolErrorClass) -> ToolError:
    return ToolError(error_class=kind, diagnostic=reason)


def _unavailable() -> ToolError:
    return _failure(ReasonCode.SOURCE_UNAVAILABLE, ToolErrorClass.UNAVAILABLE)


def _evidence(row: oag.FlightInstance, leg: FlightLeg, airport: str, now: datetime, ttl: timedelta) -> list[FieldEvidence]:
    outbound = airport == leg.origin
    side = row["origin" if outbound else "destination"]
    status_time = oag.instant(row["observed_at"])
    if status_time and status_time > now:
        status_time = None
    facts: list[FieldEvidence] = []

    def add(field: ObservationField, observed_at: datetime, **values) -> None:
        expiry = observed_at + ttl
        observation = Observation(
            source=SOURCE, leg_id=leg.id, field=field, authority_rank=AUTHORITY_RANK, observed_at=observed_at,
            received_at=now, valid_until=expiry, **values,
        )
        state = (ObservationState.UNKNOWN if observation.withdrawn else
                 ObservationState.STALE if now > expiry else ObservationState.FRESH)
        facts.append(FieldEvidence(leg_id=leg.id, field=field, state=state, observation=observation))

    if status_time is not None:
        for field, value in ((F.GATE, side["gate"]), (F.TERMINAL, side["actual_terminal"])):
            add(field, status_time, value=value, withdrawn=value is None)
        if row["status"]:
            add(F.STATUS, status_time, value=row["status"])

    if scheduled := oag.instant(side["scheduled"]):
        estimated, actual = None, None
        if status_time and now <= status_time + ttl:
            estimated = oag.instant(side["estimated"])
            actual = oag.instant(side["actual"])
            if actual and actual > status_time:
                actual = None
        observation_time = status_time if status_time and (estimated or actual) else now
        add(F.DEPARTURE if outbound else F.ARRIVAL, observation_time, scheduled=scheduled,
            estimated=estimated, actual=actual)
    return facts


class OagFlightProvider:
    """Read known legs from SQLite and return observations for the agent to apply."""

    def __init__(self, conn: sqlite3.Connection | Callable[[], sqlite3.Connection],
                 policy: PolicyPack, client: httpx.Client) -> None:
        self.conn, self.policy, self.client = conn, policy, client

    def _instances(self, leg: FlightLeg, key: str, remaining: Callable[[], float]) -> list[oag.FlightInstance] | ToolError:
        flight = f"{leg.operating_carrier}{leg.operating_number}"
        params = oag.flight_info_params(flight, leg.scheduled_operation_date, origin=leg.origin, destination=leg.destination)
        if params is None:
            return []
        params["Codeshare"] = "Operating"
        endpoint = f"{oag.base_url()}/flight-instances/"
        rows: list[dict] = []
        query = params.copy()
        try:
            for page_number in range(1, oag.MAX_PAGES + 1):
                left = remaining()
                if left <= 0:
                    return _failure(ReasonCode.TIMEOUT, ToolErrorClass.TIMEOUT)
                response = self.client.get(endpoint, headers={"Subscription-Key": key}, params=query,
                                           timeout=min(left, 5), follow_redirects=False)
                if response.status_code == 429:
                    retry = response.headers.get("Retry-After", "")
                    seconds = int(retry) if retry.isascii() and retry.isdecimal() and len(retry) <= 6 else None
                    return ToolError(error_class=ToolErrorClass.RATE_LIMITED, diagnostic=ReasonCode.RATE_LIMITED,
                                     retry_after_seconds=seconds)
                response.raise_for_status()
                page, cursor = oag.unpack_page(response.json(), received=len(rows), page_number=page_number)
                rows.extend(page)
                if not cursor:
                    break
                query["After"] = cursor
            else:
                return _unavailable()
        except httpx.TimeoutException:
            return _failure(ReasonCode.TIMEOUT, ToolErrorClass.TIMEOUT)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            return _unavailable()
        matches = oag.match_instances(rows, flight, params)
        if leg.provider_leg_id and leg.provider_leg_id.startswith("oag:"):
            schedule_key = leg.provider_leg_id.removeprefix("oag:")
            matches = [row for row in matches if row["schedule_instance_key"] == schedule_key]
        return matches

    def flight_status(self, request: GetFlightAndGateStatusInput, *, deadline: datetime) -> ToolResult[GetFlightAndGateStatusOutput]:
        if offline_forced() or not (key := oag.flight_info_key()):
            return _unavailable()
        end = time.monotonic() + (deadline - request.as_of).total_seconds()

        def remaining() -> float:
            return end - time.monotonic()

        legs: list[FlightLeg] = []
        with (nullcontext(self.conn) if isinstance(self.conn, sqlite3.Connection) else closing(self.conn())) as conn:
            for ref in request.refs:
                leg = get_leg(conn, ref)
                if leg is None:
                    return _failure(ReasonCode.FLIGHT_UNKNOWN, ToolErrorClass.MISSING)
                if request.airport not in (leg.origin, leg.destination):
                    return _failure(ReasonCode.WRONG_AIRPORT, ToolErrorClass.MISSING)
                legs.append(leg)
        facts: list[FieldEvidence] = []
        ttl = timedelta(seconds=self.policy.stale_seconds)
        for leg in legs:
            rows = self._instances(leg, key, remaining)
            if isinstance(rows, ToolError):
                return rows
            if len(rows) > 1:
                return _failure(ReasonCode.AMBIGUOUS_FLIGHT, ToolErrorClass.AMBIGUOUS)
            if not rows:
                return _failure(ReasonCode.FLIGHT_UNKNOWN, ToolErrorClass.MISSING)
            facts.extend(_evidence(rows[0], leg, request.airport, request.as_of, ttl))
        return Success(value=GetFlightAndGateStatusOutput(legs=tuple(legs), facts=tuple(facts)))
