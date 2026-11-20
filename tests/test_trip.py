"""Plain words about a trip become airports, airlines and real flights, never guesses."""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from skyassist import network, oag, trip

MESSY = "HI , just landed from kolkata on the emirates flight , i need to get to madrid on the emirated flight , please help . idk where i am goin plz help ."


def test_the_messy_message_names_two_airports_and_one_airline_even_misspelt():
    assert trip.understand(MESSY) == {"origin": "CCU", "destination": "MAD", "carrier_in": "EK", "carrier_out": "EK"}


def test_other_ways_of_saying_it():
    assert trip.understand("we came from Calcuta and want to fly to London on British Airways")["origin"] == "CCU"
    got = trip.understand("arrived from Delhi, going to Paris")
    assert (got["origin"], got["destination"]) == ("DEL", "CDG")
    assert trip.understand("where is the toilet") == {"origin": None, "destination": None, "carrier_in": None, "carrier_out": None}


def row(number, origin, destination, local, gate=None, status="Scheduled"):
    side = lambda iata, t, g=None: {"airport": {"iata": iata}, "terminal": "3", **({"gate": g} if g else {}), "date": {"local": "2026-11-20", "utc": "2026-11-20"}, "time": {"local": t, "utc": t}}  # noqa: E731
    return {"carrier": {"iata": "EK"}, "flightNumber": number, "departure": side(origin, local, gate), "arrival": side(destination, "23:00"),
            "statusDetails": [{"state": status, "updatedAt": "2026-11-20T01:00:00Z", "departure": {"gate": gate}} ] if gate else [{"state": status, "updatedAt": "2026-11-20T01:00:00Z"}]}


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("OAG_API_KEY", "oag-test")
    monkeypatch.delenv("OAG_FLIGHT_INFO_API_KEY", raising=False)
    oag._cache.clear()
    network.set_offline(False)


def test_the_trip_is_looked_up_on_both_routes_through_dubai_with_the_free_tier_limit():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        origin = request.url.params.get("DepartureAirport")
        rows = [row(571, "CCU", "DXB", "06:00")] if origin == "CCU" else [row(141, "DXB", "MAD", "07:25")]
        return httpx.Response(200, json={"data": rows})

    out = asyncio.run(trip.trip_flights(MESSY, datetime(2026, 11, 20, 2, 0, tzinfo=UTC), transport=httpx.MockTransport(handler)))
    assert all(int(p["Limit"]) <= 10 for p in seen), "OAG's free tier rejects a larger Limit"
    assert [r["flight"] for r in out["inbound"]] == ["EK571"] and [r["flight"] for r in out["outbound"]] == ["EK141"]
    lines = trip.sentences(out, "en")
    assert any("EK141 to MAD departs 07:25" in line and "gate is not assigned yet" in line for line in lines)
    assert any("EK571 from CCU" in line for line in lines)


def test_without_a_key_it_says_so_and_invents_nothing(monkeypatch):
    monkeypatch.delenv("OAG_API_KEY", raising=False)
    monkeypatch.setattr(oag, "flight_info_key", lambda: "")
    out = asyncio.run(trip.trip_flights(MESSY))
    assert out["live"] is False and out["inbound"] == [] and out["outbound"] == []
    assert "not switched on" in " ".join(trip.sentences(out, "en"))
