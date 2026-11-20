"""Plugging keys in: .env edits take effect without a restart, each key is checked, and the doctor reports it all."""

import json
import os

import httpx
import pytest

from skyassist import doctor, env, keycheck, network
from skyassist.opensky import STATES

NAMES = ("SA_T_ONE", "SA_T_TWO", "SA_T_THREE", "SA_T_REAL")


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(env, "DEFAULT", tmp_path / ".env")
    monkeypatch.setattr(env, "_state", {"mtime": None, "applied": {}, "checked": 0.0})
    network.set_offline(False)
    keycheck.reset()
    keycheck.transport = None
    for name in (*NAMES, "DEEPGRAM_API_KEY", "OPENROUTER_API_KEY", "EXA_API_KEY", "OAG_BASE_URL", *doctor.OAG_KEYS):
        monkeypatch.delenv(name, raising=False)
    yield
    for name in NAMES:
        os.environ.pop(name, None)
    keycheck.transport = None
    keycheck.reset()
    network.set_offline(False)


def bump(path):
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 5))


def test_saving_env_changes_the_running_process(tmp_path, monkeypatch):
    target = tmp_path / ".env"
    monkeypatch.setattr(env, "DEFAULT", target)
    monkeypatch.setattr(env, "_state", {"mtime": None, "applied": {}, "checked": 0.0})
    monkeypatch.setenv("SA_T_REAL", "from-env")
    target.write_text("SA_T_ONE=a\nSA_T_TWO=b\nSA_T_REAL=from-file\n", encoding="utf-8")
    assert set(env.refresh(min_interval=0)) == {"SA_T_ONE", "SA_T_TWO"}
    assert env.value("SA_T_ONE") == "a" and os.environ["SA_T_REAL"] == "from-env", "a real variable always wins"

    target.write_text("SA_T_ONE=changed\nSA_T_THREE=new\n", encoding="utf-8")
    bump(target)
    assert set(env.refresh(min_interval=0)) == {"SA_T_ONE", "SA_T_TWO", "SA_T_THREE"}
    assert os.environ["SA_T_ONE"] == "changed" and os.environ["SA_T_THREE"] == "new" and "SA_T_TWO" not in os.environ
    assert os.environ["SA_T_REAL"] == "from-env"

    target.write_text("SA_T_ONE=\n", encoding="utf-8")
    bump(target)
    env.refresh(min_interval=0)
    assert "SA_T_ONE" not in os.environ, "blanking a key in the file turns the feature off"

    target.unlink()
    assert env.refresh(min_interval=0) == [] or "SA_T_THREE" not in os.environ
    assert env.refresh(min_interval=60) == [], "checked recently: no work"


def respond(code):
    return httpx.MockTransport(lambda request: httpx.Response(code, json={}))


async def test_each_key_is_checked_once_and_only_a_401_means_rejected(monkeypatch):
    assert await keycheck.deepgram("") == "missing"
    seen = []

    def spy(request):
        seen.append((str(request.url), request.headers["authorization"]))
        return httpx.Response(200, json={})

    keycheck.transport = httpx.MockTransport(spy)
    assert await keycheck.deepgram("dg-key") == "ok" and await keycheck.deepgram("dg-key") == "ok"
    assert seen == [("https://api.deepgram.com/v1/projects", "Token dg-key")], "cached: one request for two checks"
    assert await keycheck.openrouter("or-key") == "ok" and seen[-1] == ("https://openrouter.ai/api/v1/auth/key", "Bearer or-key")

    keycheck.transport = respond(401)
    assert await keycheck.deepgram("other-key") == "rejected"
    keycheck.transport = respond(403)
    assert await keycheck.deepgram("narrow-key") == "ok", "a key without that permission still works for voice"
    keycheck.transport = respond(503)
    assert await keycheck.openrouter("busy-key") == "unreachable"

    def down(request):
        raise httpx.ConnectError("no network", request=request)

    keycheck.transport = httpx.MockTransport(down)
    assert await keycheck.deepgram("lost-key") == "unreachable"
    network.set_offline(True)
    assert await keycheck.deepgram("dg-key") == "offline"


def fake_services(good=True):
    def handler(request):
        url = str(request.url)
        if url.startswith(STATES):
            return httpx.Response(200, json={"time": 1, "states": [["a1", "UAE402", "UAE", 1, 1, 55.4, 25.3, 900, False, 100, 270, 0]]})
        if "adsb.lol" in url:
            return httpx.Response(200, json={"ac": []})
        if "api.exa.ai" in url:
            return httpx.Response(200, json={"results": []})
        if "api.oag.com" in url:
            return httpx.Response(200, json={"data": []})
        if "11434" in url:
            raise httpx.ConnectError("no ollama", request=request)
        if url.endswith("/v1/projects") or url.endswith("/auth/key"):
            return httpx.Response(200 if good else 401, json={})
        if url.endswith("/v1/speak") or "/v1/speak?" in url:
            return httpx.Response(200, content=b"ID3 audio bytes")
        if "/v1/listen" in url:
            return httpx.Response(200, json={"results": {"channels": [{"alternatives": [{"transcript": "Your gate is B14.", "confidence": 0.98}]}]}})
        return httpx.Response(404)
    return handler


def hosted_model(request):
    content = json.dumps({"intent": "request_assistance", "flight_aliases": ["DXB-402"], "wheelchair": True})
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_doctor_with_no_keys_skips_what_is_off_and_passes(monkeypatch):
    lines = []
    monkeypatch.setattr(doctor, "internet_reachable", lambda ttl=0: True)
    failed = doctor.run(lines.append, transport=httpx.MockTransport(fake_services()), stream=False)
    text = "\n".join(lines)
    assert failed == 0 and "[skip] DEEPGRAM_API_KEY  not set" in text and "no key: the kiosk works by touch" in text
    assert "OpenSky" in text and "1 aircraft" in text and "Everything you turned on works." in text


def test_doctor_runs_a_real_round_trip_for_each_key(monkeypatch):
    lines = []
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test-1234")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-5678")
    monkeypatch.setattr(doctor, "internet_reachable", lambda ttl=0: True)
    failed = doctor.run(lines.append, transport=httpx.MockTransport(fake_services()),
                        async_transport=httpx.MockTransport(hosted_model), stream=False)
    text = "\n".join(lines)
    assert failed == 0, text
    assert "set, ends 1234" in text and "[ok  ] speech out (Aura)" in text and 'heard "Your gate is B14."' in text
    assert "flights ['DXB-402'], wheelchair True" in text and "dg-test" not in text, "keys are masked"


def test_doctor_says_exactly_what_to_do_when_a_key_is_refused(monkeypatch):
    lines = []
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-wrong")
    monkeypatch.setattr(doctor, "internet_reachable", lambda ttl=0: True)
    failed = doctor.run(lines.append, transport=httpx.MockTransport(fake_services(good=False)), stream=False)
    text = "\n".join(lines)
    assert failed == 1 and "[FAIL] key accepted" in text and "wrong or revoked" in text and "need attention" in text


def test_doctor_stays_quiet_online_when_offline_mode_is_on(monkeypatch):
    lines = []
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test-1234")
    network.set_offline(True)
    assert doctor.run(lines.append, stream=False) == 0
    assert any("offline mode is on" in line for line in lines) and not any("Deepgram (voice)" in line for line in lines)


def test_doctor_probes_each_oag_subscription_with_the_correct_key():
    requests = {}
    statuses = {"/flight-instances/": 401, "/locations": 200, "/flight-connections": 429}

    def handler(request):
        requests[request.url.path] = (request.headers["Subscription-Key"], dict(request.url.params))
        return httpx.Response(statuses[request.url.path], json={})

    lines = []
    report = doctor.Report(lines.append)
    doctor.data_sources(report, {"OAG_API_KEY": "shared", "OAG_MASTER_DATA_API_KEY": "master"},
                        httpx.MockTransport(handler))
    assert requests["/locations"] == ("master", {"version": "v1", "AirportCode": "DXB", "CodeType": "IATA", "Limit": "1"})
    key, flight = requests["/flight-instances/"]
    assert key == "shared" and flight["version"] == "v2" and flight["Content"] == "Status"
    key, connections = requests["/flight-connections"]
    assert key == "shared" and connections["DepartureAirport"] == "DXB" and connections["ArrivalAirport"] == "LHR"
    assert report.failed == 2
    assert "OAG Flight Info" in lines[-3] and "product access rejected" in lines[-3]
    assert "OAG Master Data" in lines[-2] and "key accepted" in lines[-2]
    assert "OAG Connections" in lines[-1] and "request limit reached" in lines[-1]
