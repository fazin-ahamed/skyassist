import json
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from skyassist import oag, oag_products
from skyassist.agent import JourneyAgent
from skyassist.concierge import respond
from skyassist.evals import fixture_view
from skyassist.service_read import create_integrated_app
from test_agent import create, fixture


async def test_master_data_uses_public_code_and_skips_hosted_model_and_search(monkeypatch):
    async def airport(code):
        assert code == "DXB"
        return oag_products.LookupResult("ok", (oag_products.Airport(
            "DXB", "OMDB", "Dubai International Airport", "Dubai", "United Arab Emirates", None, (),
        ),))

    async def unexpected(*args, **kwargs):
        raise AssertionError("passenger wording should stay out of hosted model and search")

    class Writer:
        draft = unexpected

    monkeypatch.setattr(oag_products, "lookup_airport", airport)
    reply = await respond("What is airport code DXB? My booking reference is private.", fixture_view(), "en",
                          Writer(), search=unexpected)
    assert reply["kind"] == "masterdata"
    assert "Dubai International Airport" in reply["answer"]
    assert reply["citations"][0]["title"] == "OAG Master Data"


async def test_arabic_connection_search_uses_dubai_date_and_shows_via_airport(monkeypatch):
    async def connections(origin, destination, on):
        assert (origin, destination, on) == ("DXB", "LHR", date(2026, 11, 21))
        first = oag_products.ConnectionLeg("QR1003", origin, "DOH", "10:50", "11:10", None, None)
        second = oag_products.ConnectionLeg("QR7", "DOH", destination, "13:10", "17:30", None, None)
        return oag_products.LookupResult("ok", (
            oag_products.Connection(origin, destination, "DOH", (first, second), 640, 120, True),
        ))

    monkeypatch.setattr(oag_products, "search_connections", connections)
    reply = await respond("رحلات متصلة من DXB إلى LHR غدًا", fixture_view(), "ar", None,
                          now=datetime(2026, 11, 19, 22, tzinfo=UTC))
    assert reply["kind"] == "connections"
    assert all(value in reply["answer"] for value in ("QR1003", "QR7", "DOH", "انتقال مستقل"))
    assert reply["citations"][0]["title"] == "OAG Flight Connections"


async def test_flight_info_distinguishes_key_rejection_from_no_matching_flight(monkeypatch):
    for status, expected in (("unauthorized", "unavailable"), ("empty", "no matching results")):
        async def lookup(flight, on, **kwargs):
            assert (flight, on) == ("EK202", date(2026, 11, 20))
            assert kwargs == {"origin": "JFK", "destination": "DXB"}
            return oag.FlightLookupResult(status, [])

        monkeypatch.setattr(oag, "lookup_result", lookup)
        reply = await respond("Flight status EK202 from JFK to DXB on 2026-11-20", fixture_view(), "en", None)
        assert reply["kind"] == "flightinfo" and expected in reply["answer"]
        assert reply["citations"] == []


def test_flight_info_stream_works_before_submitting_a_journey(tmp_path, monkeypatch):
    async def lookup(flight, on, **kwargs):
        assert flight == "EK202" and on == date(2026, 11, 20)
        return oag.FlightLookupResult("ok", [{
            "flight": "EK202", "origin": {"airport": "JFK", "local_time": "23:55", "gate": None},
            "destination": {"airport": "DXB"}, "status": "Scheduled", "observed_at": None,
        }])

    monkeypatch.setattr(oag, "lookup_result", lookup)
    clock, config = fixture(tmp_path)
    with TestClient(create_integrated_app(clock, config, agent=JourneyAgent(clock, config))) as client:
        journey, headers = create(client)
        url = f"/journeys/{journey}/ask/stream"
        body = {"question": "Flight status EK202 on 2026-11-20"}
        assert client.post(url, json=body).status_code == 403
        result = client.post(url, json=body, headers=headers)
    assert result.status_code == 200
    assert result.headers["Cache-Control"] == "no-store"
    final = result.text.rsplit("event: final\ndata: ", 1)[1].strip()
    assert '"id": "search"' in result.text
    reply = json.loads(final)
    assert "Scheduled departure at origin is 23:55" in reply["answer"]
    assert reply["citations"][0]["title"] == "OAG Flight Info"
