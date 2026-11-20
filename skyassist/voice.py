"""Voice for the kiosk: Deepgram speech-to-text through a server-side WebSocket, and Deepgram text-to-speech.

The browser never sees the API key. It opens `/voice/stream`, sends one JSON `start` message, then 16 kHz mono
16-bit PCM frames. This module opens the upstream Deepgram socket with the key, boosts the airport's own
vocabulary (flight numbers, gates, landmarks) with `keyterm`, and relays a slim, stable transcript shape back.

Noise handling is split on purpose. The browser does the cheap, accuracy-safe work: echo cancellation, a high-pass,
level control and a noise-adaptive speech gate (see web/lib/voice). Deepgram's own guidance is that heavy denoising
removes cues the recogniser needs, so recognition runs on Nova-3, which is built for noisy and far-field audio, and
the proxy relies on its model-level speech detection (`vad_events`, `endpointing`, `utterance_end_ms`) rather than
energy alone.

Set DEEPGRAM_API_KEY in the environment or in the project `.env`. Without it every endpoint answers "unavailable"
and the kiosk keeps working by touch.
"""

import asyncio
import json
import logging
import os
import time
from collections import OrderedDict, deque
from contextlib import closing
from typing import Annotated, Literal
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, Response, WebSocket
from pydantic import BaseModel, ConfigDict, Field

from skyassist import env, keycheck
from skyassist.config import Config
from skyassist.db import connect
from skyassist.network import offline_forced

try:
    import websockets
    import websockets.exceptions
except ImportError:  # the kiosk still runs by touch; voice just reports itself unavailable
    websockets = None

LOG = logging.getLogger(__name__)

DEFAULT_BASE = "https://api.deepgram.com"
STT_LANGUAGES = {"en": "en", "ar": "ar"}
TTS_LANGUAGES = {"en"}  # Aura voices do not cover Arabic; the browser speaks Arabic locally.
MAX_KEYTERMS = 100
FIXED_TERMS = {
    "en": ["SkyAssist", "step-free", "wheelchair", "boarding cutoff", "connecting flight", "Concourse B",
           "Concourse C", "Costa", "lift", "gate", "departures", "arrivals", "help", "terminal"],
    "ar": ["SkyAssist", "بوابة", "كرسي متحرك", "مصعد", "مساعدة", "رحلة"],
}


def _env(name: str, default: str = "") -> str:
    return env.value(name, default)


def api_key() -> str:
    return _env("DEEPGRAM_API_KEY")


def base_url() -> str:
    return (_env("DEEPGRAM_BASE_URL") or DEFAULT_BASE).rstrip("/")


def stt_model() -> str:
    return _env("DEEPGRAM_STT_MODEL") or "nova-3"


def tts_model() -> str:
    return _env("DEEPGRAM_TTS_MODEL") or "aura-2-thalia-en"


def max_seconds() -> int:
    try:
        return max(5, min(180, int(_env("SKYASSIST_VOICE_MAX_SECONDS") or 45)))
    except ValueError:
        return 45


def max_streams() -> int:
    try:
        return max(1, int(_env("SKYASSIST_VOICE_MAX_STREAMS") or 6))
    except ValueError:
        return 6


def ws_base() -> str:
    parsed = urlparse(base_url())
    return f"{'wss' if parsed.scheme == 'https' else 'ws'}://{parsed.netloc}{parsed.path}"


def listen_url(*, language: str, sample_rate: int, terms: list[str]) -> str:
    params: list[tuple[str, str]] = [
        ("model", stt_model()), ("language", STT_LANGUAGES.get(language, "en")),
        ("encoding", "linear16"), ("sample_rate", str(sample_rate)), ("channels", "1"),
        ("interim_results", "true"), ("smart_format", "true"), ("punctuate", "true"),
        ("endpointing", "500"), ("utterance_end_ms", "1000"), ("vad_events", "true"),
    ]
    params += [("keyterm", term) for term in terms[:MAX_KEYTERMS]]
    return f"{ws_base()}/v1/listen?{urlencode(params)}"


def normalise(raw: str) -> dict | None:
    """Deepgram message in, the kiosk's small transcript shape out. Unknown or empty messages are dropped."""
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    kind = msg.get("type")
    if kind == "Results":
        alternatives = (msg.get("channel") or {}).get("alternatives") or [{}]
        best = alternatives[0]
        text = (best.get("transcript") or "").strip()
        speech_final = bool(msg.get("speech_final"))
        if not text and not speech_final:
            return None
        words = [{"w": w.get("punctuated_word") or w.get("word") or "", "c": round(float(w.get("confidence") or 0), 3),
                  "s": w.get("start"), "e": w.get("end")} for w in best.get("words") or []]
        return {"type": "transcript", "text": text, "final": bool(msg.get("is_final")), "speech_final": speech_final,
                "confidence": round(float(best.get("confidence") or 0), 3), "words": words}
    if kind == "SpeechStarted":
        return {"type": "speech_started"}
    if kind == "UtteranceEnd":
        return {"type": "utterance_end"}
    if kind in ("Error", "Warning") or msg.get("err_code"):
        return {"type": "error", "reason": str(msg.get("description") or msg.get("err_msg") or kind)[:200]}
    return None


def vocabulary(config: Config, language: str, extra: list[str]) -> list[str]:
    """Words the recogniser should favour: the flights and gates this airport actually has."""
    terms: list[str] = list(FIXED_TERMS.get(language, FIXED_TERMS["en"]))
    try:
        with closing(connect(config.db_path)) as conn:
            for carrier, number in conn.execute(
                "SELECT DISTINCT operating_carrier, operating_number FROM flight_legs LIMIT 40"
            ):
                terms.append(f"{carrier} {number}")
            for (alias,) in conn.execute("SELECT DISTINCT alias FROM flight_aliases LIMIT 40"):
                terms.append(alias)
            for (node_id,) in conn.execute("SELECT id FROM nodes WHERE public=1"):
                if len(node_id) <= 3 and node_id[:1].isalpha() and node_id[1:].isdigit():
                    terms.append(node_id.upper())
    except Exception as exc:  # vocabulary is a boost, never a reason to fail the stream
        LOG.warning("Voice vocabulary unavailable (%s)", type(exc).__name__)
    for term in extra:
        if isinstance(term, str) and 1 < len(term) <= 40:
            terms.append(term)
    seen: dict[str, None] = {}
    for term in terms:
        seen.setdefault(term.strip(), None)
    return [t for t in seen if t][:MAX_KEYTERMS]


class SpeakBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: Annotated[str, Field(min_length=1, max_length=400)]
    language: Literal["en", "ar"] = "en"


class _Budget:
    """At most N events in the last minute. Keeps a stuck client from burning the quota."""

    def __init__(self, limit: int, window: float = 60.0):
        self.limit, self.window, self.events = limit, window, deque()

    def take(self) -> bool:
        now = time.monotonic()
        while self.events and now - self.events[0] > self.window:
            self.events.popleft()
        if len(self.events) >= self.limit:
            return False
        self.events.append(now)
        return True


def _same_origin(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:
        return True  # not a browser page, so there is no cross-site page to guard against
    allowed = {item.strip() for item in _env("SKYASSIST_ALLOWED_ORIGINS").split(",") if item.strip()}
    return origin in allowed or urlparse(origin).netloc == ws.headers.get("host", "")


def voice_router(config: Config) -> APIRouter:
    router = APIRouter()
    active = {"streams": 0}
    speak_budget = _Budget(40)
    stream_budget = _Budget(30)
    cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()

    @router.get("/voice/status")
    async def status(response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        configured = bool(api_key()) and websockets is not None
        offline = configured and offline_forced()
        rejected = configured and not offline and await keycheck.deepgram() == "rejected"
        usable = configured and not rejected and not offline
        return {"provider": "deepgram", "configured": configured, "stt": usable, "tts": usable,
                "reason": None if usable else ("offline" if offline else "key_rejected" if rejected else "not_configured"),
                "model": stt_model(), "languages": sorted(STT_LANGUAGES), "tts_languages": sorted(TTS_LANGUAGES),
                "max_seconds": max_seconds()}

    @router.get("/voice/vocabulary")
    def vocabulary_for_parser(response: Response, language: str = "en") -> dict:
        """What the kiosk should listen for: boost terms for the recogniser and known flights for the parser."""
        response.headers["Cache-Control"] = "no-store"
        flights: dict[str, dict] = {}
        try:
            with closing(connect(config.db_path)) as conn:
                codes = {}
                for leg_id, carrier, number, destination in conn.execute(
                    "SELECT id, operating_carrier, operating_number, destination FROM flight_legs"
                ):
                    code = f"{carrier}{number}"
                    codes[leg_id] = code
                    flights.setdefault(code, {"code": code, "aliases": [], "direction": "arrival" if destination == "DXB" else "departure"})
                for alias, leg_id in conn.execute("SELECT alias, leg_id FROM flight_aliases"):
                    entry = flights.get(codes.get(leg_id, ""))
                    if entry is not None and alias not in entry["aliases"]:
                        entry["aliases"].append(alias)
        except Exception as exc:
            LOG.warning("Voice vocabulary unavailable (%s)", type(exc).__name__)
        return {"terms": vocabulary(config, language if language in STT_LANGUAGES else "en", []), "flights": list(flights.values())}

    @router.post("/voice/speak")
    async def speak(body: SpeakBody) -> Response:
        if not api_key() or offline_forced() or body.language not in TTS_LANGUAGES:
            return Response(status_code=503, content=json.dumps({"detail": {"reason_code": "TTS_UNAVAILABLE"}}),
                            media_type="application/json")
        text = " ".join(body.text.split())
        hit = cache.get((tts_model(), text))
        if hit is not None:
            cache.move_to_end((tts_model(), text))
            return Response(hit, media_type="audio/mpeg", headers={"Cache-Control": "no-store", "X-Voice-Cache": "hit"})
        if not speak_budget.take():
            return Response(status_code=429, content=json.dumps({"detail": {"reason_code": "RATE_LIMITED"}}),
                            media_type="application/json")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                reply = await client.post(f"{base_url()}/v1/speak", params={"model": tts_model(), "encoding": "mp3"},
                                          headers={"Authorization": f"Token {api_key()}"}, json={"text": text})
        except httpx.HTTPError:
            return Response(status_code=503, content=json.dumps({"detail": {"reason_code": "SOURCE_UNAVAILABLE"}}),
                            media_type="application/json")
        if reply.status_code != 200 or not reply.content:
            return Response(status_code=503, content=json.dumps({"detail": {"reason_code": "SOURCE_UNAVAILABLE"}}),
                            media_type="application/json")
        cache[(tts_model(), text)] = reply.content
        while len(cache) > 48:
            cache.popitem(last=False)
        return Response(reply.content, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})

    @router.websocket("/voice/stream")
    async def stream(ws: WebSocket) -> None:
        if not _same_origin(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            start = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=5))
            if start.get("type") != "start":
                raise ValueError
        except (ValueError, TimeoutError, KeyError, RuntimeError):
            await _say(ws, {"type": "error", "reason": "bad_start"})
            await _close(ws)
            return
        key = api_key()
        if not key or websockets is None or offline_forced():
            await _say(ws, {"type": "unavailable", "reason": "offline" if offline_forced() and key else "not_configured"})
            await _close(ws)
            return
        if active["streams"] >= max_streams() or not stream_budget.take():
            await _say(ws, {"type": "unavailable", "reason": "busy"})
            await _close(ws)
            return
        language = start.get("language") if start.get("language") in STT_LANGUAGES else "en"
        try:
            sample_rate = int(start.get("sample_rate") or 16000)
        except (TypeError, ValueError):
            sample_rate = 16000
        sample_rate = sample_rate if 8000 <= sample_rate <= 48000 else 16000
        extra = start.get("terms") if isinstance(start.get("terms"), list) else []
        url = listen_url(language=language, sample_rate=sample_rate, terms=vocabulary(config, language, extra))
        active["streams"] += 1
        try:
            await _relay(ws, url, key)
        finally:
            active["streams"] -= 1
            await _close(ws)

    return router


async def _say(ws: WebSocket, message: dict) -> None:
    try:
        await ws.send_text(json.dumps(message))
    except Exception:  # the browser already left
        pass


async def _close(ws: WebSocket) -> None:
    try:
        await ws.close()
    except Exception:
        pass


async def _relay(ws: WebSocket, url: str, key: str) -> None:
    try:
        async with websockets.connect(url, additional_headers={"Authorization": f"Token {key}"},
                                      open_timeout=8, max_size=2**22, ping_interval=15) as upstream:
            await _say(ws, {"type": "ready"})
            await _pump(ws, upstream)
    except websockets.exceptions.InvalidStatus as exc:
        code = exc.response.status_code
        reason = "key_rejected" if code in (401, 403) else "upstream_error"
        LOG.error("Deepgram refused the stream (%s)", code)
        await _say(ws, {"type": "error", "reason": reason})
    except (OSError, TimeoutError, websockets.exceptions.WebSocketException) as exc:
        LOG.error("Deepgram unreachable (%s)", type(exc).__name__)
        await _say(ws, {"type": "error", "reason": "unreachable"})


async def _pump(ws: WebSocket, upstream) -> None:
    stopping = asyncio.Event()

    async def from_browser() -> None:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            if (data := message.get("bytes")) is not None:
                await upstream.send(data)
            elif (text := message.get("text")) is not None:
                try:
                    control = json.loads(text).get("type")
                except ValueError:
                    continue
                if control == "finalize":
                    await upstream.send(json.dumps({"type": "Finalize"}))
                elif control == "keepalive":
                    await upstream.send(json.dumps({"type": "KeepAlive"}))
                elif control == "stop":
                    await upstream.send(json.dumps({"type": "CloseStream"}))
                    stopping.set()
                    return

    async def to_browser() -> None:
        async for raw in upstream:
            if isinstance(raw, bytes):
                continue
            if (out := normalise(raw)) is not None:
                await _say(ws, out)

    async def deadline() -> None:
        await asyncio.sleep(max_seconds())
        await _say(ws, {"type": "limit"})

    down = asyncio.create_task(to_browser())
    up = asyncio.create_task(from_browser())
    timer = asyncio.create_task(deadline())
    try:
        done, _ = await asyncio.wait({down, up, timer}, return_when=asyncio.FIRST_COMPLETED)
        if stopping.is_set() and down not in done:
            # The browser asked to stop: let Deepgram flush the last words, then finish.
            await asyncio.wait({down}, timeout=3.0)
    finally:
        for task in (down, up, timer):
            task.cancel()
        await asyncio.gather(down, up, timer, return_exceptions=True)
