"""Thin HTTP client for the SkyAssist service, plus board-row mapping. The kiosk never computes facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from skyassist.clock import in_dubai
from skyassist.models import (
    AssistanceRequest, AssistanceResponse, CreateJourneyRequest, FlightBoardItem, FlightDirection,
    GetFlightsResponse, GetJourneyResponse, JourneyInputRequest, JourneyInputResponse, ObservationField,
    ObservationState,
)


class ServiceDown(Exception):
    pass


class ServiceRefused(Exception):
    def __init__(self, status: int, reason_code: str | None):
        super().__init__(f"{status} {reason_code}")
        self.status, self.reason_code = status, reason_code


@dataclass(frozen=True)
class LiveRow:
    leg_id: str
    code: str
    city: str
    time: str
    gate: str | None
    gate_state: ObservationState


class Service:
    def __init__(self, base_url: str | None = None, *, client: httpx.Client | None = None) -> None:
        self.http = client or httpx.Client(base_url=base_url, timeout=5)
        self.tokens: dict[str, str] = {}

    def _call(self, method: str, url: str, *, token: str | None = None, **kw) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = self.http.request(method, url, headers=headers, **kw)
        except httpx.TransportError as exc:
            raise ServiceDown(type(exc).__name__) from exc
        if response.status_code >= 400:
            detail = response.json().get("detail") if response.headers.get("content-type", "").startswith("application/json") else None
            code = detail.get("reason_code") if isinstance(detail, dict) else None
            raise ServiceRefused(response.status_code, code)
        return response

    def now(self) -> datetime:
        return datetime.fromisoformat(self._call("GET", "/clock").json()["now"])

    def flights(self, airport: str, direction: FlightDirection, query: str | None = None) -> GetFlightsResponse:
        now = self.now()
        params = {
            "airport": airport, "direction": direction.value, "limit": 50,
            "start": (now - timedelta(hours=6)).isoformat(), "end": (now + timedelta(hours=42)).isoformat(),
        }
        if query:
            params["query"] = query
        return GetFlightsResponse.model_validate_json(self._call("GET", "/flights", params=params).content)

    def create_journey(self, request: CreateJourneyRequest) -> str:
        response = self._call("POST", "/journeys", json=request.model_dump(mode="json"))
        journey_id = response.json()["journey_id"]
        self.tokens[journey_id] = response.headers["Authorization"].removeprefix("Bearer ")
        return journey_id

    def send(self, request: JourneyInputRequest) -> JourneyInputResponse:
        r = self._call("POST", f"/journeys/{request.journey_id}/inputs", token=self.tokens[request.journey_id],
                       json=request.model_dump(mode="json", exclude_none=True))
        return JourneyInputResponse.model_validate_json(r.content)

    def poll(self, journey_id: str, known_revision: int | None) -> GetJourneyResponse:
        params = {} if known_revision is None else {"known_revision": known_revision}
        r = self._call("GET", f"/journeys/{journey_id}", token=self.tokens[journey_id], params=params)
        return GetJourneyResponse.model_validate_json(r.content)

    def assist(self, request: AssistanceRequest) -> AssistanceResponse:
        r = self._call("POST", f"/journeys/{request.journey_id}/assistance", token=self.tokens[request.journey_id],
                       json=request.model_dump(mode="json", exclude_none=True))
        return AssistanceResponse.model_validate_json(r.content)


def board_rows(response: GetFlightsResponse, direction: FlightDirection) -> list[LiveRow]:
    rows = []
    for item in response.flights:
        rows.append(_row(item, direction))
    return rows


def _row(item: FlightBoardItem, direction: FlightDirection) -> LiveRow:
    leg = item.leg
    time_field = ObservationField.DEPARTURE if direction is FlightDirection.DEPARTURES else ObservationField.ARRIVAL
    facts = {f.field: f for f in item.facts}
    when = ""
    fact = facts.get(time_field)
    if fact and fact.observation:
        o = fact.observation
        instant = o.estimated or o.scheduled
        when = in_dubai(instant).strftime("%H:%M") if instant else ""
    gate_fact = facts.get(ObservationField.GATE)
    gate = None
    state = gate_fact.state if gate_fact else ObservationState.UNKNOWN
    if gate_fact and gate_fact.observation and state in (ObservationState.FRESH, ObservationState.STALE):
        gate = str(gate_fact.observation.value) if gate_fact.observation.value is not None else None
    other = leg.destination if direction is FlightDirection.DEPARTURES else leg.origin
    return LiveRow(leg.id, f"{leg.operating_carrier} {leg.operating_number}", other, when, gate, state)
