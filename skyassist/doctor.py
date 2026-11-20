"""Check that everything works, one line per thing, with what to do when it does not.

    python -m skyassist.doctor            (or:  .\\run_demo.ps1 doctor)

Needs no keys to run. With keys in `.env` it also does a real round trip: Deepgram speaks a phrase and listens to it,
opens the same live socket the kiosk uses, and OpenRouter answers a passenger message. Exit code 1 means something you
turned on is not working. Things you have not set up are skipped, not failed.
"""

import asyncio
import importlib
import json
import os
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from skyassist import env, keycheck, oag, oag_products
from skyassist.env import ROOT
from skyassist.network import internet_reachable, offline_forced

BRIEF = ("Our inbound flight DXB-402 just parked at Concourse C, our connecting gate changed to B14, my grandmother needs "
         "her wheelchair at the aircraft door, and we only have 35 minutes before boarding closes!")
PHRASE = "Your gate is B fourteen. Boarding closes at ten thirty five."
MARK = {"ok": "ok  ", "fail": "FAIL", "skip": "skip", "warn": "warn"}
OAG_KEYS = ("OAG_API_KEY", "OAG_FLIGHT_INFO_API_KEY", oag_products.MASTER_KEY, oag_products.CONNECTIONS_KEY)


def mask(value: str) -> str:
    return "not set" if not value else (f"set, ends {value[-4:]}" if len(value) > 8 else "set")


class Report:
    def __init__(self, out: Callable[[str], None] = print):
        self.out, self.failed = out, 0

    def line(self, status: str, what: str, detail: str = "") -> None:
        if status == "fail":
            self.failed += 1
        self.out(f"  [{MARK[status]}] {what}" + (f"  {detail}" if detail else ""))

    def head(self, title: str) -> None:
        self.out(f"\n{title}")


def basics(r: Report) -> None:
    r.head("This machine")
    r.line("ok" if sys.version_info >= (3, 12) else "fail", "Python 3.12 or newer", sys.version.split()[0])
    for name in ("fastapi", "uvicorn", "httpx", "pydantic", "segno", "websockets"):
        try:
            importlib.import_module(name)
            r.line("ok", f"package {name}")
        except ImportError:
            r.line("fail", f"package {name}", "run: .\\run_demo.ps1  (it installs what is missing)")
    db = ROOT / "data" / "skyassist-demo.sqlite3"
    r.line("ok" if db.exists() else "warn", "demo database", "" if db.exists() else "will be built on first start, or run: .\\run_demo.ps1 reset")
    built = (ROOT / "web" / "out" / "index.html").exists()
    r.line("ok" if built else "warn", "front end build", "" if built else "will be built on first start, or run: .\\run_demo.ps1 build")


def keys(r: Report) -> dict[str, str]:
    r.head("Keys in .env")
    path = ROOT / ".env"
    r.line("ok" if path.exists() else "warn", ".env file", str(path) if path.exists() else "copy .env.example to .env, then paste your keys")
    values = {name: env.value(name) for name in ("DEEPGRAM_API_KEY", "OPENROUTER_API_KEY", "OPENSKY_CLIENT_ID",
                                                "OPENSKY_CLIENT_SECRET", "EXA_API_KEY", *OAG_KEYS)}
    for name, value in values.items():
        detail = "set" if name in OAG_KEYS and value else mask(value)
        r.line("ok" if value else "skip", name, detail)
    return values


def deepgram(r: Report, key: str, transport: httpx.BaseTransport | None, stream: bool) -> None:
    r.head("Deepgram (voice)")
    if not key:
        r.line("skip", "voice", "no key: the kiosk works by touch. Add DEEPGRAM_API_KEY to .env to turn voice on")
        return
    base = keycheck.deepgram_base()
    headers = {"Authorization": f"Token {key}"}
    with httpx.Client(timeout=20.0, transport=transport, trust_env=False) as client:
        try:
            code = client.get(f"{base}/v1/projects", headers=headers).status_code
        except httpx.HTTPError as exc:
            r.line("fail", "reach Deepgram", f"{type(exc).__name__}: check the internet connection")
            return
        if code == 401:
            r.line("fail", "key accepted", "Deepgram said 401: the key is wrong or revoked. Make a new one in the Deepgram console")
            return
        r.line("ok", "key accepted", f"HTTP {code}")
        try:
            spoken = client.post(f"{base}/v1/speak", params={"model": keycheck_model("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en"), "encoding": "mp3"},
                                 headers=headers, json={"text": PHRASE})
            spoken.raise_for_status()
            r.line("ok", "speech out (Aura)", f"{len(spoken.content)} bytes of audio")
        except httpx.HTTPError as exc:
            r.line("fail", "speech out (Aura)", f"{exc}")
            return
        try:
            heard = client.post(f"{base}/v1/listen", params={"model": keycheck_model("DEEPGRAM_STT_MODEL", "nova-3"), "smart_format": "true"},
                                headers={**headers, "Content-Type": "audio/mpeg"}, content=spoken.content)
            heard.raise_for_status()
            alt = heard.json()["results"]["channels"][0]["alternatives"][0]
            r.line("ok" if alt["transcript"] else "warn", "speech in (Nova-3)", f'heard "{alt["transcript"]}" ({alt.get("confidence", 0):.2f})')
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            r.line("fail", "speech in (Nova-3)", f"{exc}")
    if stream:
        try:
            from websockets.sync.client import connect
            url = f"{'wss' if base.startswith('https') else 'ws'}://{base.split('://', 1)[1]}/v1/listen?model=nova-3&encoding=linear16&sample_rate=16000&channels=1"
            with connect(url, additional_headers=headers, open_timeout=10) as ws:
                ws.send(json.dumps({"type": "CloseStream"}))
            r.line("ok", "live socket (what the kiosk uses)", "connected and closed cleanly")
        except Exception as exc:  # noqa: BLE001 - any failure here is the answer
            r.line("fail", "live socket (what the kiosk uses)", f"{type(exc).__name__}: {exc}")


def keycheck_model(name: str, default: str) -> str:
    return env.value(name) or default


def openrouter(r: Report, key: str, transport: httpx.BaseTransport | None, async_transport: httpx.AsyncBaseTransport | None) -> None:
    r.head("OpenRouter (hosted language model)")
    if not key:
        r.line("skip", "hosted model", "no key: the built-in rules and a local model still work. Add OPENROUTER_API_KEY to .env")
        return
    base = keycheck.openrouter_base()
    with httpx.Client(timeout=30.0, transport=transport, trust_env=False) as client:
        try:
            code = client.get(f"{base}/auth/key", headers={"Authorization": f"Bearer {key}"}).status_code
        except httpx.HTTPError as exc:
            r.line("fail", "reach OpenRouter", f"{type(exc).__name__}: check the internet connection")
            return
    if code in (401, 403):
        r.line("fail", "key accepted", f"OpenRouter said {code}: the key is wrong or disabled")
        return
    r.line("ok", "key accepted", f"HTTP {code}")
    from skyassist.model_boundary import OpenRouterExtractor
    extractor = OpenRouterExtractor(transport=async_transport)
    slots = asyncio.run(extractor.extract(BRIEF, aliases=("B14", "C22"), timeout=25))
    if extractor.last_source != "openrouter":
        r.line("fail", f"answer from {extractor.model}", "no usable answer: check the model name (OPENROUTER_MODEL), your credit, or try again")
        return
    good = slots.flight_aliases == ("DXB-402",) and slots.wheelchair
    r.line("ok" if good else "warn", f"understand the brief with {extractor.model}",
           f"flights {list(slots.flight_aliases)}, wheelchair {slots.wheelchair}, children {slots.children}")


def others(r: Report, transport: httpx.BaseTransport | None) -> None:
    r.head("Live aircraft (OpenSky) and a local model (Ollama)")
    from skyassist.opensky import BOX, STATES, parse
    try:
        with httpx.Client(timeout=12.0, transport=transport, trust_env=False) as client:
            reply = client.get(STATES, params=dict(zip(("lamin", "lomin", "lamax", "lomax"), BOX)))
            reply.raise_for_status()
            planes = parse(reply.json())
        r.line("ok", "OpenSky", f"{len(planes)} aircraft near DXB right now (anonymous: 400 requests a day)")
    except (httpx.HTTPError, ValueError) as exc:
        r.line("warn", "OpenSky", f"{type(exc).__name__}: optional, the kiosk shows its last snapshot or nothing")
    from skyassist.adsblol import URL as ADSB_URL
    try:
        with httpx.Client(timeout=8.0, transport=transport, trust_env=False) as client:
            client.get(ADSB_URL).raise_for_status()
        r.line("ok", "adsb.lol", "reachable")
    except httpx.HTTPError as exc:
        r.line("fail", "adsb.lol", f"failed: {type(exc).__name__}")
    try:
        with httpx.Client(timeout=3.0, transport=transport, trust_env=False) as client:
            models =[m["name"] for m in client.get("http://127.0.0.1:11434/api/tags").json().get("models", [])]
        r.line("ok", "Ollama", ", ".join(models) or "running, no models pulled")
    except (httpx.HTTPError, ValueError):
        r.line("skip", "Ollama", "not running: optional, the built-in rules cover offline understanding")


def run(out: Callable[[str], None] = print, *, transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None, stream: bool = True) -> int:
    r = Report(out)
    out("SkyAssist doctor")
    basics(r)
    values = keys(r)
    r.head("Network")
    if offline_forced():
        r.line("warn", "offline mode is on", "SKYASSIST_OFFLINE is set, so online checks are skipped")
        return r.failed
    online = internet_reachable(ttl=0)
    r.line("ok" if online else "warn", "internet", "reachable" if online else "not reachable: the kiosk still runs, voice and hosted models wait for it")
    if online:
        deepgram(r, values["DEEPGRAM_API_KEY"], transport, stream)
        openrouter(r, values["OPENROUTER_API_KEY"], transport, async_transport)
        others(r, transport)
        data_sources(r, values, transport)
    out("\n" + ("Everything you turned on works." if not r.failed else f"{r.failed} thing(s) need attention, see FAIL above."))
    return r.failed


def data_sources(r: Report, values: dict[str, str], transport: httpx.BaseTransport | None) -> None:
    """Check each subscription with a small public-data request and report HTTP status."""
    r.head("Web search (Exa) and aviation data (OAG)")
    today = datetime.now(ZoneInfo("Asia/Dubai")).date()
    flight_params = oag.flight_info_params("EK1", today, origin="DXB")
    assert flight_params is not None
    flight_params["Limit"] = "1"
    checks = (
        ("OAG_FLIGHT_INFO_API_KEY", "OAG Flight Info", "/flight-instances/", flight_params),
        (oag_products.MASTER_KEY, "OAG Master Data", "/locations",
         {"version": "v1", "AirportCode": "DXB", "CodeType": "IATA", "Limit": "1"}),
        (oag_products.CONNECTIONS_KEY, "OAG Connections", "/flight-connections",
         {"version": "v1", "DepartureDate": (today + timedelta(days=1)).isoformat(), "DepartureAirport": "DXB",
          "ArrivalAirport": "LHR", "Service": "p", "InterAirport": "false", "Limit": "1"}),
    )
    with httpx.Client(transport=transport, timeout=8, trust_env=False) as client:
        exa_key = values.get("EXA_API_KEY")
        if exa_key:
            _check_data_source(r, "Exa web search", lambda: client.post(
                "https://api.exa.ai/search", headers={"x-api-key": exa_key},
                json={"query": "Dubai International Airport", "numResults": 1}))
        else:
            r.line("skip", "Exa web search", "add EXA_API_KEY to .env")
        for name, label, path, params in checks:
            key = values.get(name) or values.get("OAG_API_KEY")
            if not key:
                r.line("skip", label, f"add {name} to .env")
                continue
            _check_data_source(r, label, lambda: client.get(
                oag.base_url() + path, headers={"Subscription-Key": key}, params=params))


def _check_data_source(r: Report, label: str, request: Callable[[], httpx.Response]) -> None:
    try:
        status = request().status_code
    except httpx.HTTPError as error:
        r.line("fail", label, f"could not reach it ({type(error).__name__})")
        return
    if status in (401, 403):
        r.line("fail", label, f"key or product access rejected (HTTP {status})")
    elif status == 429:
        r.line("fail", label, "request limit reached (HTTP 429)")
    elif 200 <= status < 300:
        r.line("ok", label, f"key accepted (HTTP {status})")
    else:
        r.line("fail", label, f"answered HTTP {status}")


def main() -> None:
    raise SystemExit(1 if run() else 0)


if __name__ == "__main__":
    main()
