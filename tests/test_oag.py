"""OAG Flight Info v2 contract, exact matching and operational evidence."""

import asyncio
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from functools import partial

import httpx
import pytest

from skyassist import network, oag
from skyassist.adapters import OagFlightProvider
from skyassist.concierge import respond
from skyassist.db import connect, init_db
from skyassist.evals import fixture_view
from skyassist.models import (
    GetFlightAndGateStatusInput, ObservationField, ObservationState, PolicyPack, ReasonCode, Success, ToolError,
)

DAY = "2026-11-19"
NOW = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)
UPDATE = "2026-11-19T05:59:30Z"
ROW = {
    "carrier": {"iata": "EK", "icao": "UAE"},
    "flightNumber": 202,
    "scheduleInstanceKey": "departure-dxb-202-a",
    "statusKey": "status-dxb-202-a",
    "departure": {
        "airport": {"iata": "DXB"}, "terminal": "1", "gate": "C22",
        "date": {"local": DAY, "utc": DAY}, "time": {"local": "10:35", "utc": "06:35"},
    },
    "arrival": {
        "airport": {"iata": "LHR"}, "terminal": "3",
        "date": {"local": DAY, "utc": DAY}, "time": {"local": "14:10", "utc": "14:10"},
    },
    "statusDetails": [{
        "state": "Scheduled", "updatedAt": UPDATE,
        "departure": {
            "airport": {"iata": "DXB"}, "actualTerminal": "3", "gate": "B14",
            "estimatedTime": {"outGate": {"utc": "2026-11-19T06:45:00Z"}},
        },
        "arrival": {"airport": {"iata": "LHR"}, "gate": "07"},
    }],
}


@pytest.fixture(autouse=True)
def key(monkeypatch, tmp_path):
    monkeypatch.setattr(oag.env, "DEFAULT", tmp_path / "env.absent")
    monkeypatch.setattr(oag.env, "_state", {"mtime": None, "applied": {}, "checked": 0.0})
    monkeypatch.setenv("OAG_FLIGHT_INFO_API_KEY", "test-flight-info")
    monkeypatch.setenv("OAG_API_KEY", "test-common")
    monkeypatch.delenv("OAG_BASE_URL", raising=False)
    oag._cache.clear()
    was_offline = network.offline_forced()
    network.set_offline(False)
    yield
    network.set_offline(was_offline)


@pytest.fixture
def flight_db(tmp_path):
    path = tmp_path / "oag.sqlite3"
    init_db(path)
    with closing(connect(path)) as conn:
        conn.execute("INSERT INTO flight_legs VALUES ('out', 'EK', '202', ?, 'DXB', 'LHR', NULL)", (DAY,))
    return path


def test_flight_designator_preserves_suffix():
    assert oag.split_flight("EK 202") == ("EK", "202")
    assert oag.split_flight("ek-0202a") == ("EK", "202A")
    assert oag.split_flight("EK000") is None
    assert oag.split_flight("nonsense") is None


def test_v2_parser_preserves_status_provenance_and_uses_operational_gates():
    row = oag.parse_instance(ROW)
    assert row["flight"] == "EK202"
    assert row["status"] == "Scheduled"
    assert oag.instant(row["observed_at"]) == oag.instant(UPDATE)
    assert row["schedule_instance_key"] == "departure-dxb-202-a"
    assert row["status_key"] == "status-dxb-202-a"
    assert row["origin"] == {
        "airport": "DXB", "terminal": "3", "actual_terminal": "3", "gate": "B14",
        "scheduled": "2026-11-19T06:35:00+00:00", "estimated": "2026-11-19T06:45:00+00:00",
        "actual": None, "local_time": "10:35", "local_date": DAY,
    }

    scheduled = oag.parse_instance({**ROW, "statusDetails": []})
    assert scheduled["status"] is None
    assert scheduled["origin"]["terminal"] == "1"
    assert scheduled["origin"]["actual_terminal"] is None
    assert scheduled["origin"]["gate"] is None


@pytest.mark.parametrize("change", [
    {"state": "Canceled"},
    {"updatedAt": "2026-11-19T05:59:30"},
    {"departure": {"airport": {"iata": "AUH"}, "gate": "B14"}},
    {"departure": {"actualTerminal": "N/A", "gate": "N/A"}},
])
def test_parser_drops_unverified_gate_and_terminal_fields(change):
    instance = {**ROW, "statusDetails": [ROW["statusDetails"][0] | change]}
    assert oag.parse_instance(instance)["origin"]["gate"] is None
    assert oag.parse_instance(instance)["origin"]["actual_terminal"] is None
    assert oag.parse_instance(instance)["origin"]["terminal"] == "1"


def test_parser_uses_latest_snapshot_and_withholds_conflicting_same_time_reports():
    original = ROW["statusDetails"][0]
    revised = {**original, "updatedAt": "2026-11-19T05:59:40Z", "departure": {"gate": "B18"}}
    assert oag.parse_instance({**ROW, "statusDetails": [revised, original]})["origin"]["gate"] == "B18"
    revised["updatedAt"] = UPDATE
    assert oag.parse_instance({**ROW, "statusDetails": [revised, original]})["origin"]["gate"] is None


def test_parser_withholds_scheduled_airport_gate_after_diversion():
    status = ROW["statusDetails"][0] | {"irregularOperationType": "Diverted"}
    row = oag.parse_instance({**ROW, "statusDetails": [status]})
    assert row["status"] == "Diverted"
    assert row["destination"]["gate"] is None


def test_clock_only_operational_time_uses_reported_variation_to_cross_midnight():
    instance = deepcopy(ROW)
    instance["departure"]["time"]["utc"] = "23:45"
    update = instance["statusDetails"][0]["departure"]["estimatedTime"]
    update["outGate"] = {"utc": "00:10"}
    update["outGateVariation"] = "00:25:00"
    assert oag.parse_instance(instance)["origin"]["estimated"] == "2026-11-20T00:10:00+00:00"
    del update["outGateVariation"]
    assert oag.parse_instance(instance)["origin"]["estimated"] is None


def test_lookup_sends_flight_info_key_caches_and_respects_offline(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [ROW]})

    transport = httpx.MockTransport(handler)

    def lookup(code):
        return asyncio.run(oag.lookup(code, DAY, origin="DXB", destination="LHR", transport=transport))

    found = lookup("EK 202")
    assert found[0]["flight"] == "EK202"
    assert calls[0].headers["Subscription-Key"] == "test-flight-info"
    assert calls[0].url.host == "api.oag.com"
    assert calls[0].url.path == "/flight-instances/"
    assert dict(calls[0].url.params) == {
        "version": "v2", "CodeType": "IATA", "Content": "Status", "FlightType": "Scheduled",
        "CarrierCode": "EK", "FlightNumber": "202", "DepartureDateTime": DAY, "Limit": "10",
        "DepartureAirport": "DXB", "ArrivalAirport": "LHR",
    }
    found[0]["origin"]["gate"] = "C99"
    assert lookup("ek-0202")[0]["origin"]["gate"] == "B14"
    assert len(calls) == 1
    network.set_offline(True)
    assert lookup("EK202") == []
    network.set_offline(False)
    monkeypatch.delenv("OAG_FLIGHT_INFO_API_KEY")
    assert lookup("EK202")
    assert calls[-1].headers["Subscription-Key"] == "test-common"
    monkeypatch.delenv("OAG_API_KEY")
    assert lookup("EK202") == []
    assert len(calls) == 2


def test_lookup_matches_flight_suffix_route_and_airport_local_arrival_date():
    match = deepcopy(ROW)
    match["serviceSuffix"] = "A"
    match["arrival"]["date"]["local"] = "2026-11-20"
    wrong_date = deepcopy(match)
    wrong_date["arrival"]["date"]["local"] = DAY
    wrong_route = deepcopy(match)
    wrong_route["departure"]["airport"]["iata"] = "AUH"
    wrong_number = match | {"flightNumber": 203}
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [ROW, wrong_date, wrong_route, wrong_number, match, match]})

    rows = asyncio.run(oag.lookup("EK 0202A", "2026-11-20", arrival=True, origin="DXB", destination="LHR",
                                  transport=httpx.MockTransport(handler)))
    assert [row["flight"] for row in rows] == ["EK202A"]
    params = calls[0].url.params
    assert params["ArrivalDateTime"] == "2026-11-20"
    assert params["FlightNumber"] == "202"
    assert "DepartureDateTime" not in params


def test_lookup_uses_only_pagination_cursor_and_returns_every_exact_match():
    calls = []

    def handler(request):
        calls.append(request)
        if "After" in request.url.params:
            return httpx.Response(200, json={"data": [ROW | {"scheduleInstanceKey": "departure-dxb-202-b"}]})
        return httpx.Response(200, json={
            "data": [ROW],
            "paging": {"next": "https://example.invalid/other-endpoint?After=cursor-token&CarrierCode=XX"},
        })

    found = asyncio.run(oag.lookup("EK202", DAY, transport=httpx.MockTransport(handler)))
    assert [row["schedule_instance_key"] for row in found] == ["departure-dxb-202-a", "departure-dxb-202-b"]
    assert calls[1].url.host == "api.oag.com"
    assert calls[1].url.path == "/flight-instances/"
    assert calls[1].url.params["After"] == "cursor-token"
    assert calls[1].url.params["CarrierCode"] == "EK"


def test_lookup_refuses_incomplete_search_results():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [ROW], "paging": {"next": "/?After=repeat-cursor"}})

    assert asyncio.run(oag.lookup("EK202", DAY, transport=httpx.MockTransport(handler))) == []
    assert len(calls) == oag.MAX_PAGES


@pytest.mark.parametrize(("status", "body", "expected"), [
    (401, {}, "unauthorized"),
    (403, {}, "unauthorized"),
    (429, {}, "rate_limited"),
    (500, {}, "unavailable"),
    (200, {"data": {}}, "unavailable"),
    (200, {"data": []}, "empty"),
    (422, {}, "invalid"),
])
def test_lookup_distinguishes_unavailable_from_no_matching_flight(status, body, expected):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    result = asyncio.run(oag.lookup_result("EK202", DAY, transport=transport))
    assert result.status == expected
    assert result.rows == []


def test_lookup_inactive_and_invalid_queries_skip_the_network(monkeypatch):
    def handler(request):
        pytest.fail("Flight Info should skip the network")

    transport = httpx.MockTransport(handler)

    def status(day=DAY):
        return asyncio.run(oag.lookup_result("EK202", day, transport=transport)).status

    assert status("2026-11-31") == "invalid"
    network.set_offline(True)
    assert status() == "offline"
    network.set_offline(False)
    monkeypatch.delenv("OAG_FLIGHT_INFO_API_KEY")
    monkeypatch.delenv("OAG_API_KEY")
    assert status() == "unconfigured"


def test_route_search_uses_dubai_arrival_date_and_filters_operating_carrier():
    inbound = deepcopy(ROW)
    inbound["flightNumber"] = 571
    inbound["departure"]["airport"]["iata"] = "CCU"
    inbound["departure"]["date"]["local"] = "2026-11-18"
    inbound["arrival"]["airport"]["iata"] = "DXB"
    inbound["statusDetails"][0]["arrival"] = {"airport": {"iata": "DXB"}, "gate": "B16"}
    wrong_day = deepcopy(inbound)
    wrong_day["arrival"]["date"]["local"] = "2026-11-20"
    wrong_airport = deepcopy(inbound)
    wrong_airport["departure"]["airport"]["iata"] = "DEL"
    marketed = inbound | {
        "flightNumber": 6789, "codeshare": {"operatingFlight": {"code": "EK", "serviceNumber": "571"}},
    }
    other_carrier = inbound | {"carrier": {"iata": "FZ"}}

    def handler(request):
        assert dict(request.url.params) == {
            "version": "v2", "CodeType": "IATA", "Content": "Status", "FlightType": "Scheduled",
            "DepartureAirport": "CCU", "ArrivalAirport": "DXB", "ArrivalDateTime": DAY,
            "Codeshare": "Operating", "CarrierCode": "EK", "Limit": "10",
        }
        return httpx.Response(200, json={
            "data": [wrong_day, wrong_airport, marketed, other_carrier, inbound, inbound],
        })

    result = asyncio.run(oag.search_route("ccu", "DXB", DAY, carrier="ek", arrival=True,
                                        transport=httpx.MockTransport(handler)))
    assert result.status == "ok"
    assert [row["flight"] for row in result.rows] == ["EK571"]
    assert result.rows[0]["destination"]["gate"] == "B16"


def test_route_search_without_carrier_keeps_all_operating_choices():
    outbound = deepcopy(ROW)
    outbound["flightNumber"] = 141
    outbound["arrival"]["airport"]["iata"] = "MAD"
    other = outbound | {"carrier": {"iata": "IB"}, "flightNumber": 166,
                        "scheduleInstanceKey": "another-madrid-flight"}
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.params["DepartureDateTime"] == DAY
        assert request.url.params["DepartureAirport"] == "DXB"
        assert request.url.params["ArrivalAirport"] == "MAD"
        assert "CarrierCode" not in request.url.params
        if "After" not in request.url.params:
            return httpx.Response(200, json={"data": [outbound], "paging": {"next": "/?After=madrid-cursor"}})
        assert request.url.params["After"] == "madrid-cursor"
        return httpx.Response(200, json={"data": [other]})

    transport = httpx.MockTransport(handler)
    result = asyncio.run(oag.search_route("DXB", "MAD", DAY, transport=transport))
    assert result.status == "ok"
    assert [row["flight"] for row in result.rows] == ["EK141", "IB166"]
    assert asyncio.run(oag.search_route("dxb", "mad", DAY, transport=transport)) == result
    assert len(calls) == 2


@pytest.mark.parametrize("paging", [{"next": "/?After=cursor"}, {"next": None, "totalCount": 2}])
def test_route_search_withholds_single_match_when_remaining_rows_are_unread(paging):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [ROW], "paging": paging}))
    result = asyncio.run(oag.search_route("DXB", "LHR", DAY, carrier="EK", transport=transport))
    assert result.status == "unavailable"
    assert result.rows == []


@pytest.mark.parametrize(("origin", "destination", "carrier"), [("", "DXB", "EK"), ("CCU", "Dubai", "EK"),
                                                                ("CCU", "DXB", "Emirates")])
def test_route_search_rejects_invalid_codes_without_network(origin, destination, carrier):
    def handler(request):
        pytest.fail("Invalid route should skip the network")

    result = asyncio.run(oag.search_route(origin, destination, DAY, carrier=carrier,
                                        transport=httpx.MockTransport(handler)))
    assert result.status == "invalid"


def _read_flight(path, handler, *, now=NOW, airport="DXB", timeout=5):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return OagFlightProvider(partial(connect, path), PolicyPack(), client).flight_status(
            GetFlightAndGateStatusInput(refs=("out",), airport=airport, as_of=now),
            deadline=now + timedelta(seconds=timeout),
        )


def test_adapter_returns_dated_observations_for_agent_ingestion(flight_db):
    def handler(request):
        assert request.url.params["Codeshare"] == "Operating"
        market_only = ROW | {
            "scheduleInstanceKey": "marketing",
            "codeshare": {"operatingFlight": {"code": "QF", "serviceNumber": "8100"}},
        }
        return httpx.Response(200, json={"data": [market_only, ROW]})

    result = _read_flight(flight_db, handler)
    assert isinstance(result, Success)
    facts = {fact.field: fact for fact in result.value.facts}
    gate = facts[ObservationField.GATE]
    assert gate.state is ObservationState.FRESH
    assert gate.observation.source == "oag"
    assert gate.observation.value == "B14"
    assert gate.observation.observed_at == oag.instant(UPDATE)
    assert gate.observation.received_at == NOW
    departure = facts[ObservationField.DEPARTURE].observation
    assert departure.observed_at == oag.instant(UPDATE)
    assert departure.estimated == oag.instant("2026-11-19T06:45:00Z")
    assert ObservationField.BOARDING_CUTOFF not in facts
    with closing(connect(flight_db)) as conn:
        assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 0


def test_adapter_reports_stale_and_future_gate_sources_safely(flight_db):
    read = lambda now: _read_flight(flight_db, lambda request: httpx.Response(200, json={"data": [ROW]}), now=now)
    stale = read(NOW + timedelta(minutes=3))
    assert isinstance(stale, Success)
    facts = {fact.field: fact for fact in stale.value.facts}
    assert facts[ObservationField.GATE].state is ObservationState.STALE
    assert facts[ObservationField.DEPARTURE].observation.estimated is None
    future = read(NOW - timedelta(minutes=3))
    assert isinstance(future, Success)
    assert all(fact.field != ObservationField.GATE for fact in future.value.facts)


def test_adapter_uses_stored_schedule_instance_key_to_resolve_matching_rows(flight_db):
    other = ROW | {"scheduleInstanceKey": "departure-dxb-202-b"}
    handler = lambda request: httpx.Response(200, json={"data": [ROW, other]})
    ambiguous = _read_flight(flight_db, handler)
    assert isinstance(ambiguous, ToolError)
    assert ambiguous.diagnostic is ReasonCode.AMBIGUOUS_FLIGHT
    with closing(connect(flight_db)) as conn:
        conn.execute("UPDATE flight_legs SET provider_leg_id='oag:departure-dxb-202-b' WHERE id='out'")
    assert isinstance(_read_flight(flight_db, handler), Success)
    missing = _read_flight(flight_db, lambda request: httpx.Response(200, json={"data": [ROW]}))
    assert isinstance(missing, ToolError)
    assert missing.diagnostic is ReasonCode.FLIGHT_UNKNOWN


def test_adapter_distinguishes_unavailable_and_rate_limited(flight_db):
    denied = _read_flight(flight_db, lambda request: httpx.Response(401))
    assert isinstance(denied, ToolError)
    assert denied.diagnostic is ReasonCode.SOURCE_UNAVAILABLE
    limited = _read_flight(flight_db, lambda request: httpx.Response(429, headers={"Retry-After": "30"}))
    assert isinstance(limited, ToolError)
    assert limited.diagnostic is ReasonCode.RATE_LIMITED
    assert limited.retry_after_seconds == 30


def test_named_flight_answer_uses_and_attributes_oag(monkeypatch):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [ROW]}))
    real = oag.lookup_result

    async def lookup(code, day, **filters):
        filters["transport"] = transport
        return await real(code, day, **filters)

    monkeypatch.setattr(oag, "lookup_result", lookup)
    out = asyncio.run(respond("what is the status of EK 202?", fixture_view(), "en", None, now=NOW))
    assert out["kind"] == "flightinfo"
    assert "EK202" in out["answer"] and "OAG" in out["answer"] and "10:35" in out["answer"]
