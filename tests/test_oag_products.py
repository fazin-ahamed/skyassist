from datetime import date

import httpx
import pytest

from skyassist import oag_products as products


def airport(code):
    return {"iata": code}


def leg(carrier, flight_number, origin, destination, departure_time, arrival_time, **extra):
    return {
        "carrierCode": {"iata": carrier}, "flightNumber": flight_number,
        "departure": {"airport": airport(origin), "localTime": departure_time, "terminalCode": "3"},
        "arrival": {"airport": airport(destination), "localTime": arrival_time, "terminalCode": "2"},
        **extra,
    }


@pytest.fixture(autouse=True)
def configure_products(monkeypatch):
    keys = {
        "OAG_API_KEY": "fallback-test-key",
        "OAG_MASTER_DATA_API_KEY": "catalog-test-key",
        "OAG_CONNECTIONS_API_KEY": "connections-test-key",
    }
    monkeypatch.setattr(products.env, "value", lambda name: keys.get(name, ""))
    monkeypatch.setattr(products, "offline_forced", lambda: False)
    return keys


async def test_locations_v1_uses_precise_icao_filter_and_normalizes_reference_data():
    def endpoint(request):
        assert request.url.host == "api.oag.com"
        assert request.url.path == "/locations"
        assert dict(request.url.params) == {"version": "v1", "AirportCode": "OMDB", "CodeType": "ICAO", "Limit": "1"}
        assert request.headers["Subscription-Key"] == "catalog-test-key"
        return httpx.Response(200, json={"data": [{
            "code": {"iata": "DXB", "icao": "OMDB"}, "type": "AIRPORT", "name": "Dubai International",
            "place": {"city": {"name": "Dubai"}, "country": {"code": "AE", "name": "United Arab Emirates"}},
            "terminals": [{"code": "0", "name": "NOT PRE-DETERMINED"}, {"code": "3", "name": "Terminal 3"}],
            "timeZone": {"code": {"iata": "AE-0"}},
        }]})

    lookup = await products.lookup_airport(" omdb ", transport=httpx.MockTransport(endpoint))
    assert lookup.status == "ok"
    assert lookup.items == (products.Airport(
        "DXB", "OMDB", "Dubai International", "Dubai", "United Arab Emirates", "AE-0",
        (products.Terminal("3", "Terminal 3"),),
    ),)


async def test_carriers_and_equipment_use_the_master_data_product_and_separate_filters(configure_products):
    configure_products.pop(products.MASTER_KEY)

    def endpoint(request):
        assert request.headers["Subscription-Key"] == "fallback-test-key"
        assert request.url.params["version"] == "v1"
        assert request.url.params["Limit"] == "1"
        if request.url.path == "/carriers":
            assert request.url.params["CarrierCode"] == "EK" and request.url.params["CodeType"] == "IATA"
            # Master Data documents both single-object and paged responses.
            return httpx.Response(200, json={
                "code": {"iata": "EK", "icao": "UAE"}, "name": "EMIRATES",
                "domicile": {"country": {"code": {"iso": "AE"}}},
            })
        assert request.url.path == "/equipment"
        assert request.url.params["AircraftCode"] == "A320" and request.url.params["CodeType"] == "ICAO"
        return httpx.Response(200, json={"data": [{
            "code": {"iata": "320", "icao": "A320"},
            "specification": {"iata": {"model": "A320", "manufacturer": {"name": "Airbus"}}},
        }]})

    transport = httpx.MockTransport(endpoint)
    airline = await products.lookup_airline("EK", transport=transport)
    aircraft = await products.lookup_aircraft("A320", transport=transport)
    assert airline.items == (products.Airline("EK", "UAE", "EMIRATES", "AE"),)
    assert aircraft.items == (products.Aircraft("320", "A320", "A320", "Airbus"),)


async def test_connections_v1_preserves_scheduled_local_times_and_does_not_join_airports():
    first = leg("EK", 2, "LHR", "DXB", "1410", "01:05:00", connectionTime=130)
    second = leg("EK", 562, "DXB", "BLR", "0315", "0805")
    route = {"leg1": first, "leg2": second, "elapsedTime": 750, "isSelfConnection": True}
    wrong_hub = {
        **route, "leg1": leg("EK", 2, "LHR", "DWC", "1410", "0105"),
        "leg2": leg("EK", 562, "DWC", "BLR", "0315", "0805"),
    }
    wrong_airport = {**route, "leg2": leg("EK", 562, "DWC", "BLR", "0315", "0805")}
    wrong_destination = {**route, "leg2": leg("EK", 561, "DXB", "DEL", "0315", "0805")}

    def endpoint(request):
        assert request.url.path == "/flight-connections"
        assert request.headers["Subscription-Key"] == "connections-test-key"
        assert dict(request.url.params) == {
            "version": "v1", "DepartureDate": "2026-11-20", "DepartureAirport": "LHR", "ArrivalAirport": "BLR",
            "Service": "p", "InterAirport": "false", "Limit": "5", "ViaPoint": "DXB",
        }
        return httpx.Response(200, json={"data": [wrong_airport, wrong_hub, wrong_destination, route]})

    lookup = await products.search_connections("lhr", "BLR", date(2026, 11, 20), via="dxb", transport=httpx.MockTransport(endpoint))
    assert lookup.status == "ok" and len(lookup.items) == 1
    connection = lookup.items[0]
    assert (connection.origin, connection.via, connection.destination) == ("LHR", "DXB", "BLR")
    assert (connection.duration_minutes, connection.layover_minutes, connection.is_self_connection) == (750, 130, True)
    assert connection.legs[0] == products.ConnectionLeg("EK2", "LHR", "DXB", "14:10", "01:05", "3", "2")
    assert connection.legs[1].departure_time == "03:15"


@pytest.mark.parametrize(("status", "payload", "expected"), [
    (200, {"data": []}, "empty"),
    (200, {"data": ["not an airport record"]}, "unavailable"),
    (200, {"message": "unknown response"}, "unavailable"),
    (401, {}, "unauthorized"),
    (403, {}, "unauthorized"),
    (429, {}, "rate_limited"),
    (503, {}, "unavailable"),
])
async def test_empty_results_are_distinct_from_provider_failures(status, payload, expected):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=payload))
    lookup = await products.lookup_airport("DXB", transport=transport)
    assert (lookup.status, lookup.items) == (expected, ())


async def test_invalid_inputs_offline_mode_and_missing_keys_never_make_a_request(monkeypatch, configure_products):
    def fail(request):
        pytest.fail("unexpected provider request")

    transport = httpx.MockTransport(fail)
    assert (await products.lookup_airport("DXB or DWC", transport=transport)).status == "invalid"
    assert (await products.search_connections("DXB", "DXB", "2026-11-20", transport=transport)).status == "invalid"
    assert (await products.search_connections("DXB", "LHR", "2026-02-31", transport=transport)).status == "invalid"
    assert (await products.search_connections("DXB", "LHR", "20261120", transport=transport)).status == "invalid"
    assert (await products.search_connections("DXB", "LHR", "2026-11-20", via="LHR", transport=transport)).status == "invalid"
    configure_products.clear()
    assert (await products.lookup_airline("UAE", transport=transport)).status == "unconfigured"
    monkeypatch.setattr(products, "offline_forced", lambda: True)
    assert (await products.lookup_aircraft("A320", transport=transport)).status == "offline"
