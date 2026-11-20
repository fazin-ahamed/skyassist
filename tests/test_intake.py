"""Passenger intake: the exact sentence from the brief, the hosted-model path, and keeping going with no network."""

import json
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from skyassist import network
from skyassist.agent import JourneyAgent
from skyassist.db import connect
from skyassist.model_boundary import AutoExtractor, OllamaExtractor, OpenRouterExtractor, TemplateExtractor, default_extractor, flight_candidates
from skyassist.opensky import OpenSky, parse
from skyassist.service_read import create_integrated_app
from test_agent import await_view, create, fixture

BRIEF = ("Our inbound flight DXB-402 just parked at Concourse C, our connecting gate changed to B14, my grandmother needs her "
         "wheelchair at the aircraft door, and we only have 35 minutes before boarding closes!")


@pytest.fixture(autouse=True)
def online(monkeypatch):
    network.set_offline(False)
    for name in ("OPENROUTER_API_KEY", "SKYASSIST_LLM", "OPENSKY_CLIENT_ID", "OPENSKY_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    yield
    network.set_offline(False)


def test_gates_times_and_filler_are_never_flights():
    places = ("B14", "C22", "Lift C")
    assert flight_candidates(BRIEF, places) == ("DXB-402",)
    assert flight_candidates("we have 35 minutes in B14, gate C22 and seat 12A", places) == ()
    assert flight_candidates("ZX 515 and zx515, then EK 303", places) == ("ZX515", "EK303")
    assert flight_candidates("dxb 402", ()) == ("DXB402",)
    assert flight_candidates("flight B6 123 to JFK", ()) == ("B6123",)


async def test_the_brief_keeps_the_inbound_flight_the_wheelchair_and_does_not_invent_a_location():
    slots = await TemplateExtractor().extract(BRIEF, aliases=("B14", "B18", "C22", "Lift C"))
    assert slots.flight_aliases == ("DXB-402",)
    assert slots.wheelchair and not slots.children
    assert slots.landmark_text is None, "B14 is where they are going, not where they are"
    assert (await TemplateExtractor().extract("we are at B14", aliases=("B14",))).landmark_text == "B14"


async def test_prams_toddlers_and_grandmothers_are_heard():
    slots = await TemplateExtractor().extract("I have a pram and a toddler, my mother needs a wheel chair")
    assert slots.children and slots.wheelchair
    assert not (await TemplateExtractor().extract("just me, flight EK 303")).children


def test_the_brief_lands_on_the_inbound_leg_then_asks_for_the_onward_flight(tmp_path):
    clock, config = fixture(tmp_path)
    with closing(connect(config.db_path)) as conn:
        conn.execute("INSERT INTO flight_aliases VALUES ('DXB-101', 'in')")
    app = create_integrated_app(clock, config, agent=JourneyAgent(clock, config, extractor=TemplateExtractor()))
    with TestClient(app) as client:
        session = create(client)
        journey, headers = session

        def say(text, input_id):
            reply = client.post(f"/journeys/{journey}/inputs", headers=headers, json={
                "journey_id": journey, "client_input_id": input_id, "text": text})
            assert reply.status_code == 202
            return reply.json()["event_id"]

        say(BRIEF.replace("DXB-402", "DXB-101"), "brief")
        asked = await_view(client, session, lambda v: "OUTBOUND_MISSING" in v["status"]["reason_codes"])
        assert asked["next_action"]["action"] == "choose_flight" and asked["next_action"]["choices"] == []
        assert asked["assistance"] is None, "nothing is staged before the family has said where they are"
        say("my connecting flight is QF202", "onward")
        moved_on = await_view(client, session, lambda v: "OUTBOUND_MISSING" not in v["status"]["reason_codes"]
                              and v["revision"] > asked["revision"])
        assert "FLIGHT_UNKNOWN" not in moved_on["status"]["reason_codes"]
        say("hello there", "noise")
        stable = await_view(client, session, lambda v: v["revision"] > moved_on["revision"])
        assert "FLIGHT_UNKNOWN" not in stable["status"]["reason_codes"], "chatter must not undo what was already understood"


async def test_openrouter_answers_are_validated_like_any_other():
    seen = {}

    def reply(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        content = json.dumps({"intent": "check_connection", "flight_aliases": ["DXB-402"], "wheelchair": True, "children": True})
        return httpx.Response(200, json={"choices": [{"message": {"content": "```json\n" + content + "\n```"}}]})

    import os
    os.environ["OPENROUTER_API_KEY"] = "or-test"
    extractor = OpenRouterExtractor(transport=httpx.MockTransport(reply))
    slots = await extractor.extract(BRIEF, aliases=("B14",))
    assert extractor.last_source == "openrouter" and slots.children and slots.wheelchair
    assert seen["auth"] == "Bearer or-test" and seen["body"]["messages"][1] == {"role": "user", "content": BRIEF}
    assert seen["body"]["response_format"] == {"type": "json_object"}

    def invents(request):
        content = json.dumps({"intent": "find_gate", "flight_aliases": ["QQ999"]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    guarded = OpenRouterExtractor(transport=httpx.MockTransport(invents))
    slots = await guarded.extract("gate for DXB-402", aliases=())
    assert guarded.last_source == "template" and slots.flight_aliases == ("DXB-402",), "a code the text never contained is refused"

    del os.environ["OPENROUTER_API_KEY"]
    quiet = OpenRouterExtractor(transport=httpx.MockTransport(reply))
    assert (await quiet.extract(BRIEF)).intent == "request_assistance" and quiet.last_source == "template"


async def test_the_chain_prefers_the_hosted_model_then_the_local_one_then_rules(monkeypatch):
    calls = []

    def hosted_down(request):
        calls.append("openrouter")
        raise httpx.ConnectError("no network", request=request)

    def local_ok(request):
        calls.append("ollama")
        return httpx.Response(200, json={"message": {"content": '{"intent":"check_connection","flight_aliases":["EK202"]}'}})

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    chain = AutoExtractor([("openrouter", OpenRouterExtractor(transport=httpx.MockTransport(hosted_down))),
                           ("ollama", OllamaExtractor(transport=httpx.MockTransport(local_ok)))])
    assert (await chain.extract("transfer EK202")).intent == "check_connection"
    assert calls == ["openrouter", "openrouter", "ollama"], "primary model, then the fallback model, then the local one"
    assert chain.state()["last_used"] == "ollama"
    assert chain.state()["providers"][0]["cooling_down"] is True
    await chain.extract("transfer EK202")
    assert calls == ["openrouter", "openrouter", "ollama", "ollama"], "a failed provider is skipped for a minute, so offline costs no time"

    network.set_offline(True)
    fresh = AutoExtractor([("openrouter", OpenRouterExtractor(transport=httpx.MockTransport(hosted_down)))])
    await fresh.extract("transfer EK202")
    assert fresh.state()["last_used"] == "template" and calls.count("openrouter") == 2, "offline mode never calls the hosted model"


def test_default_chain_follows_the_environment(monkeypatch):
    chain = default_extractor()
    assert [n for n, _ in chain.providers] == ["openrouter", "ollama"]
    assert [p["configured"] for p in chain.state()["providers"]] == [False, True], "no key yet: the hosted model sits out"
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    assert [p["configured"] for p in chain.state()["providers"]] == [True, True], "a key added later is picked up without a restart"
    monkeypatch.setenv("SKYASSIST_LLM", "off")
    assert default_extractor().providers == ()
    monkeypatch.setenv("SKYASSIST_LLM", "local")
    assert [n for n, _ in default_extractor().providers] == ["ollama"]


STATES = {"time": 1, "states": [
    ["a1", "UAE402  ", "United Arab Emirates", 1, 1, 55.40, 25.30, 1200.4, False, 120.2, 271.0, -4.2],
    ["a2", "", "Oman", 1, 1, 55.10, 25.00, None, True, 0.0, None, None],
    ["a3", "FDB91", "UAE", 1, 1, None, None, None, False, None, None, None],
]}


async def test_live_aircraft_are_cached_for_the_next_outage():
    hits = []

    def sky(request):
        if "adsb.lol" in str(request.url):
            return httpx.Response(200, json={"ac": []})
        hits.append(str(request.url))
        if len(hits) > 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json=STATES)

    source = OpenSky(transport=httpx.MockTransport(sky))
    first = await source.nearby()
    assert first["status"] == "live" and first["airborne"] == 1
    assert [a["callsign"] for a in first["aircraft"]] == ["UAE402", None], "no position, no dot; nearest first"
    assert "lamin=24.55" in hits[0] and len(hits) == 1
    again = await source.nearby()
    assert again["status"] in ("live", "cached") and len(hits) == 1, "inside the refresh window nothing is fetched"
    source.fetched_at -= 600
    later = await source.nearby()
    assert later["status"] == "cached" and later["age_s"] >= 600 and later["aircraft"], "last good snapshot, with its age"
    assert parse({"states": None}) == []

    network.set_offline(True)
    offline = await OpenSky(transport=httpx.MockTransport(sky)).nearby()
    assert offline["status"] == "offline" and offline["aircraft"] == [] and offline["age_s"] is None
    assert offline["centre"] == (25.2528, 55.3644) and offline["arrivals"] == []


def test_status_tells_the_truth_and_offline_can_be_rehearsed(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg")
    clock, config = fixture(tmp_path)
    with TestClient(create_integrated_app(clock, config, agent=JourneyAgent(clock, config, extractor=default_extractor()))) as client:
        auth = {"Authorization": "Bearer demo-coordinator"}
        assert client.post("/system/offline", json={"offline": True}).status_code == 403
        assert client.post("/system/offline", headers=auth, json={"offline": True}).json() == {"offline_forced": True}
        status = client.get("/system/status").json()
        assert status["internet"] is False and status["offline_forced"] and status["core"] == "local" and status["simulated"]
        assert status["understanding"]["last_used"] == "template" and set(status["unavailable_online_features"]) >= {"voice", "live_aircraft"}
        assert client.get("/voice/status").json()["reason"] == "offline"
        assert client.get("/traffic/nearby").json()["status"] == "offline"
        assert client.post("/voice/speak", json={"text": "hi", "language": "en"}).status_code == 503
        assert client.post("/system/offline", headers=auth, json={"offline": False}).json() == {"offline_forced": False}


def test_typed_flight_codes_match_the_schedule_with_or_without_spacing():
    from skyassist.agent import _as_listed
    from skyassist.db import connect  # noqa: F401  (import check only)
