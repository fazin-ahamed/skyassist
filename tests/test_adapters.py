import json
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from skyassist.adapters import AirLabsAircraftProvider, AirLabsFlightProvider, ArrivalEstimatesHook
from skyassist.clock import SimClock
from skyassist.db import connect, init_db
from skyassist.evidence import current_field
from skyassist.models import (
    Applicability, AircraftFreshness, FlightLeg, GetAircraftPositionInput, GetArrivalEstimatesInput,
    GetFlightAndGateStatusInput, ObservationField, ObservationState, PassengerFlow, PolicyPack,
    ReasonCode, Success, ToolError, ToolErrorClass,
)
from skyassist.tools import ReadRecovery

FIXTURES = Path(__file__).parent / "fixtures_airlabs"
POLICY = PolicyPack()
DAY = date(2026, 11, 19)
NOW = SimClock("demo").now()
UTC_06 = datetime(2026, 11, 19, 6, tzinfo=UTC)
BAGGAGE = {
    "applicability": "applicable", "state": "fresh", "wait": {"lower": 300, "upper": 600},
    "provenance": {"source": "ops-feed", "observed_at": "2026-11-19T06:00:00Z"},
}
SECURITY = {
    "applicability": "not_applicable", "state": "fresh",
    "provenance": {"source": "ops-feed", "observed_at": "2026-11-19T06:00:00Z"},
}


def load(name):
    return json.loads((FIXTURES / name).read_text())


def leg(leg_id="out", *, carrier="DX", number="402", origin="DXB", destination="AUH"):
    return FlightLeg(id=leg_id, operating_carrier=carrier, operating_number=number,
                     scheduled_operation_date=DAY, origin=origin, destination=destination)


def reply(payload, status=200, headers=None):
    return httpx.Response(status, json=payload, headers=headers)


def upstream(*responses):
    seen = []

    def handle(request):
        seen.append(request)
        item = responses[min(len(seen), len(responses)) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.Client(transport=httpx.MockTransport(handle)), seen


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("AIRLABS_API_KEY", "test-key-123")


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "adapters.sqlite3"
    init_db(path)
    with closing(connect(path)) as connection:
        yield connection


@pytest.fixture
def clock():
    return SimClock("demo")


def add_legs(conn, *legs):
    for item in legs:
        conn.execute(
            "INSERT INTO flight_legs (id, operating_carrier, operating_number, scheduled_operation_date,"
            " origin, destination) VALUES (?, ?, ?, ?, ?, ?)",
            (item.id, item.operating_carrier, item.operating_number, DAY.isoformat(),
             item.origin, item.destination),
        )
    conn.commit()


def count(conn, field=None):
    sql = "SELECT count(*) FROM observations" + (" WHERE field = ?" if field else "")
    return conn.execute(sql, (field,) if field else ()).fetchone()[0]


def recovery(clock):
    waits = []

    def wait(seconds):
        waits.append(seconds)
        clock.advance(int(seconds))

    return ReadRecovery(clock, wait=wait), waits


def read_flights(rec, provider, clock, refs=("out",), airport="DXB"):
    req = GetFlightAndGateStatusInput(refs=refs, airport=airport, as_of=clock.now())
    return rec.read(
        provider, lambda deadline: provider.flight_status(req, deadline=deadline),
        clock.now() + timedelta(seconds=60),
    )[0]


def flight_facts(result):
    return {fact.field: fact for fact in result.value.facts}


def flight_request(clock, refs=("out",), airport="DXB"):
    return GetFlightAndGateStatusInput(refs=refs, airport=airport, as_of=clock.now())


def test_no_key_reports_unavailable_without_request(conn, clock, monkeypatch):
    monkeypatch.delenv("AIRLABS_API_KEY")
    add_legs(conn, leg())
    client, seen = upstream(reply(load("schedules_dxb_departure.json")))
    result = AirLabsFlightProvider(conn, POLICY, client).flight_status(
        flight_request(clock), deadline=clock.now())
    assert result.error_class is ToolErrorClass.UNAVAILABLE
    assert result.diagnostic is ReasonCode.SOURCE_UNAVAILABLE
    assert seen == []


def test_aircraft_no_key_makes_no_request(monkeypatch):
    monkeypatch.delenv("AIRLABS_API_KEY")
    client, seen = upstream(reply(load("flights_dx402_en_route.json")))
    result = AirLabsAircraftProvider(POLICY, client).aircraft_position(
        GetAircraftPositionInput(leg=leg()), as_of=NOW, deadline=NOW)
    assert result.diagnostic is ReasonCode.SOURCE_UNAVAILABLE
    assert seen == []


@pytest.mark.parametrize("wrapped", [False, True])
def test_departure_fact_has_gate_and_utc_times(conn, clock, wrapped):
    add_legs(conn, leg())
    rows = load("schedules_dxb_departure.json")
    client, seen = upstream(reply({"response": rows} if wrapped else rows))
    result = AirLabsFlightProvider(conn, POLICY, client).flight_status(
        flight_request(clock), deadline=clock.now())
    assert isinstance(result, Success)
    facts = flight_facts(result)
    assert facts[ObservationField.GATE].observation.value == "B14"
    assert facts[ObservationField.GATE].state is ObservationState.FRESH
    assert facts[ObservationField.TERMINAL].observation.value == "1"
    assert facts[ObservationField.STATUS].observation.value == "scheduled"
    departure = facts[ObservationField.DEPARTURE].observation
    assert departure.scheduled == UTC_06
    assert departure.estimated == UTC_06 + timedelta(minutes=20)
    assert departure.actual is None
    assert departure.source == "airlabs" and departure.authority_rank == 0
    assert facts[ObservationField.ARRIVAL].state is ObservationState.UNKNOWN
    params = seen[0].url.params
    assert params["flight_iata"] == "DX402" and params["dep_iata"] == "DXB"


def test_boarding_cutoff_is_never_produced(conn, clock):
    add_legs(conn, leg())
    client, _ = upstream(reply(load("schedules_dxb_departure.json")))
    result = AirLabsFlightProvider(conn, POLICY, client).flight_status(
        flight_request(clock), deadline=clock.now())
    cutoff = flight_facts(result)[ObservationField.BOARDING_CUTOFF]
    assert cutoff.state is ObservationState.UNKNOWN and cutoff.observation is None
    assert count(conn, "boarding_cutoff") == 0


def test_codeshare_row_attaches_only_to_operating_flight(conn, clock):
    add_legs(conn, leg("op", carrier="BA", number="6984", destination="LHR"),
             leg("mkt", carrier="AA", number="2421", destination="LHR"))
    rows = load("schedules_codeshare.json")
    client, _ = upstream(reply(rows), reply(rows))
    provider = AirLabsFlightProvider(conn, POLICY, client)
    operating = provider.flight_status(flight_request(clock, refs=("op",)), deadline=clock.now())
    marketed = provider.flight_status(flight_request(clock, refs=("mkt",)), deadline=clock.now())
    assert flight_facts(operating)[ObservationField.GATE].observation.value == "C21"
    assert marketed.diagnostic is ReasonCode.FLIGHT_UNKNOWN


def test_missing_gate_yields_no_gate_value(conn, clock):
    add_legs(conn, leg())
    client, _ = upstream(reply(load("schedules_gate_missing.json")))
    result = AirLabsFlightProvider(conn, POLICY, client).flight_status(
        flight_request(clock), deadline=clock.now())
    gate = flight_facts(result)[ObservationField.GATE]
    assert gate.state is ObservationState.UNKNOWN and gate.observation is None
    assert flight_facts(result)[ObservationField.DEPARTURE].observation.scheduled == UTC_06


def test_cancelled_status_is_recorded_as_reported(conn, clock):
    add_legs(conn, leg())
    client, _ = upstream(reply(load("schedules_cancelled.json")))
    result = AirLabsFlightProvider(conn, POLICY, client).flight_status(
        flight_request(clock), deadline=clock.now())
    assert flight_facts(result)[ObservationField.STATUS].observation.value == "cancelled"


def test_request_airport_outside_leg_rejected_without_request(conn, clock):
    add_legs(conn, leg())
    client, seen = upstream(reply(load("schedules_dxb_departure.json")))
    result = AirLabsFlightProvider(conn, POLICY, client).flight_status(
        flight_request(clock, airport="SHJ"), deadline=clock.now())
    assert result.diagnostic is ReasonCode.WRONG_AIRPORT
    assert seen == []


def test_row_for_other_airport_rejected_without_records(conn, clock):
    add_legs(conn, leg())
    client, _ = upstream(reply(load("schedules_wrong_airport.json")))
    result = AirLabsFlightProvider(conn, POLICY, client).flight_status(
        flight_request(clock), deadline=clock.now())
    assert result.diagnostic is ReasonCode.WRONG_AIRPORT
    assert count(conn) == 0


def test_malformed_json_is_safe_error_with_no_records(conn, clock):
    add_legs(conn, leg())
    client, _ = upstream(httpx.Response(200, content=(FIXTURES / "malformed_body.json").read_bytes()))
    rec, _ = recovery(clock)
    result = read_flights(rec, AirLabsFlightProvider(conn, POLICY, client), clock)
    assert result.error_class is ToolErrorClass.UNAVAILABLE
    assert result.diagnostic is ReasonCode.SOURCE_UNAVAILABLE
    assert count(conn) == 0


def test_malformed_row_writes_no_partial_records(conn, clock):
    add_legs(conn, leg())
    rows = load("schedules_dxb_departure.json")
    rows[0]["dep_time_utc"] = "soon"
    client, _ = upstream(reply(rows))
    rec, _ = recovery(clock)
    result = read_flights(rec, AirLabsFlightProvider(conn, POLICY, client), clock)
    assert result.diagnostic is ReasonCode.SOURCE_UNAVAILABLE
    assert count(conn) == 0


def test_retry_after_seconds_honored(conn, clock):
    add_legs(conn, leg())
    limited = reply(load("error_rate_limited.json"), 429, {"Retry-After": "30"})
    client, seen = upstream(limited, limited, reply(load("schedules_dxb_departure.json")))
    rec, waits = recovery(clock)
    provider = AirLabsFlightProvider(conn, POLICY, client)
    first = read_flights(rec, provider, clock)
    assert first.error_class is ToolErrorClass.RATE_LIMITED
    assert waits == [30] and len(seen) == 2
    blocked = read_flights(rec, provider, clock)
    assert blocked.error_class is ToolErrorClass.RATE_LIMITED
    assert blocked.retry_after_seconds == 30 and len(seen) == 2
    clock.advance(30)
    assert isinstance(read_flights(rec, provider, clock), Success) and len(seen) == 3


def test_quota_exhaustion_is_not_retried(conn, clock):
    add_legs(conn, leg())
    client, seen = upstream(reply(load("error_quota.json"), 402))
    rec, _ = recovery(clock)
    result = read_flights(rec, AirLabsFlightProvider(conn, POLICY, client), clock)
    assert result.diagnostic is ReasonCode.BUDGET_EXHAUSTED
    assert len(seen) == 1


def test_auth_failure_disables_adapter_and_hides_key(conn, clock):
    add_legs(conn, leg())
    client, seen = upstream(reply(load("error_bad_key.json"), 401))
    rec, _ = recovery(clock)
    provider = AirLabsFlightProvider(conn, POLICY, client)
    first = read_flights(rec, provider, clock)
    second = read_flights(rec, provider, clock)
    assert first.diagnostic is ReasonCode.SOURCE_UNAVAILABLE
    assert second.diagnostic is ReasonCode.SOURCE_UNAVAILABLE
    assert len(seen) == 1
    assert "test-key-123" not in first.model_dump_json()


def test_timeout_retried_once_then_classified(conn, clock):
    add_legs(conn, leg())
    client, seen = upstream(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))
    rec, _ = recovery(clock)
    result = read_flights(rec, AirLabsFlightProvider(conn, POLICY, client), clock)
    assert result.error_class is ToolErrorClass.TIMEOUT
    assert len(seen) == 2


def test_cached_observation_keeps_original_age(conn, clock):
    add_legs(conn, leg())
    client, _ = upstream(reply(load("schedules_dxb_departure.json")))
    AirLabsFlightProvider(conn, POLICY, client).flight_status(
        flight_request(clock), deadline=clock.now())
    received = clock.now()
    evidence = current_field(conn, "out", ObservationField.GATE, received + timedelta(seconds=120), POLICY)
    assert evidence.state is ObservationState.STALE
    assert evidence.observation.received_at == received


@pytest.mark.parametrize(("age", "freshness"), [
    (30, AircraftFreshness.CURRENT),
    (120, AircraftFreshness.LAST_REPORTED),
    (200, AircraftFreshness.HIDDEN),
])
def test_position_freshness_follows_age(age, freshness):
    payload = load("flights_dx402_en_route.json")
    payload[0]["updated"] = int((NOW - timedelta(seconds=age)).timestamp())
    client, _ = upstream(reply(payload))
    result = AirLabsAircraftProvider(POLICY, client).aircraft_position(
        GetAircraftPositionInput(leg=leg()), as_of=NOW, deadline=NOW)
    assert result.value.freshness is freshness
    if freshness is AircraftFreshness.HIDDEN:
        assert result.value.report is None
        assert result.value.reason_code is ReasonCode.POSITION_STALE
    else:
        assert result.value.report.position.latitude == 24.8
        assert result.value.report.aircraft_id == "06A1B2"
        assert result.value.report.observed_at == NOW - timedelta(seconds=age)


def test_out_of_range_coordinates_return_no_position():
    client, _ = upstream(reply(load("flights_coordinates_out_of_range.json")))
    result = AirLabsAircraftProvider(POLICY, client).aircraft_position(
        GetAircraftPositionInput(leg=leg()), as_of=NOW, deadline=NOW)
    assert result.error_class is ToolErrorClass.UNAVAILABLE
    assert result.diagnostic is ReasonCode.POSITION_UNAVAILABLE


def test_ambiguous_aircraft_match_is_rejected():
    client, _ = upstream(reply(load("flights_ambiguous.json")))
    result = AirLabsAircraftProvider(POLICY, client).aircraft_position(
        GetAircraftPositionInput(leg=leg()), as_of=NOW, deadline=NOW)
    assert result.error_class is ToolErrorClass.AMBIGUOUS
    assert result.diagnostic is ReasonCode.AIRCRAFT_UNMATCHED


def test_unmatched_aircraft_returns_no_position():
    client, _ = upstream(reply([]))
    result = AirLabsAircraftProvider(POLICY, client).aircraft_position(
        GetAircraftPositionInput(leg=leg()), as_of=NOW, deadline=NOW)
    assert result.error_class is ToolErrorClass.MISSING
    assert result.diagnostic is ReasonCode.AIRCRAFT_UNMATCHED


def estimate_request():
    return GetArrivalEstimatesInput(
        arrival=leg(origin="AUH", destination="DXB"), airport="DXB",
        journey_type=PassengerFlow.CONNECTING,
    )


def test_unwired_arrival_hook_is_explicitly_unavailable():
    result = ArrivalEstimatesHook().arrival_estimates(estimate_request(), as_of=NOW, deadline=NOW)
    assert result.error_class is ToolErrorClass.UNAVAILABLE
    assert result.diagnostic is ReasonCode.ESTIMATE_UNAVAILABLE


def test_registered_hook_fields_are_independently_nullable():
    hook = ArrivalEstimatesHook()
    hook.register(lambda request, as_of: {"baggage": BAGGAGE, "immigration": None, "security": SECURITY})
    result = hook.arrival_estimates(estimate_request(), as_of=NOW, deadline=NOW)
    assert isinstance(result, Success)
    assert result.value.baggage.wait.upper == 600
    assert result.value.baggage.provenance.source == "ops-feed"
    assert result.value.immigration is None
    assert result.value.security.applicability is Applicability.NOT_APPLICABLE


@pytest.mark.parametrize("value", [
    "10 minutes",
    {"baggage": {**BAGGAGE, "provenance": None}},
    {"baggage": {**BAGGAGE, "applicability": "not_applicable"}},
    {"baggage": BAGGAGE, "extra": 1},
])
def test_invalid_hook_return_is_rejected_whole(value):
    hook = ArrivalEstimatesHook()
    hook.register(lambda request, as_of: value)
    result = hook.arrival_estimates(estimate_request(), as_of=NOW, deadline=NOW)
    assert result.error_class is ToolErrorClass.UNAVAILABLE
    assert result.diagnostic is ReasonCode.ESTIMATE_UNAVAILABLE


def test_hook_without_any_estimate_is_missing():
    hook = ArrivalEstimatesHook()
    hook.register(lambda request, as_of: {})
    result = hook.arrival_estimates(estimate_request(), as_of=NOW, deadline=NOW)
    assert result.error_class is ToolErrorClass.MISSING
    assert result.diagnostic is ReasonCode.ESTIMATE_UNAVAILABLE


def test_hook_exception_is_unavailable():
    def failing(request, as_of):
        raise RuntimeError("feed down")

    hook = ArrivalEstimatesHook()
    hook.register(failing)
    result = hook.arrival_estimates(estimate_request(), as_of=NOW, deadline=NOW)
    assert result.diagnostic is ReasonCode.ESTIMATE_UNAVAILABLE


def test_register_rejects_non_callable():
    with pytest.raises(TypeError):
        ArrivalEstimatesHook().register("nope")
