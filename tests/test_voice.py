import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
import websockets.asyncio.server as wss
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from skyassist import voice
from skyassist.agent import JourneyAgent
from skyassist.service_read import create_integrated_app
from test_agent import fixture

DEAD_END = "http://127.0.0.1:9"  # nothing listens here, so connections are refused


def make_client(tmp_path):
    clock, config = fixture(tmp_path)
    return TestClient(create_integrated_app(clock, config, agent=JourneyAgent(clock, config)))


class FakeDeepgram:
    """Enough of Deepgram's live endpoint to watch what the kiosk sends and answer like the real one."""

    def __init__(self):
        self.requests, self.controls, self.frames = [], [], 0
        self.port = self.loop = self.stop = self.thread = None
        self.ready = threading.Event()

    async def handler(self, ws):
        self.requests.append((ws.request.path, ws.request.headers.get("Authorization")))
        async for message in ws:
            if isinstance(message, bytes):
                self.frames += 1
                if self.frames == 2:
                    await ws.send(json.dumps({"type": "SpeechStarted", "channel": [0, 1], "timestamp": 0.1}))
                    await ws.send(json.dumps({"type": "Results", "is_final": False, "speech_final": False,
                                              "channel": {"alternatives": [{"transcript": "zed x five", "confidence": 0.8, "words": []}]}}))
                    await ws.send(json.dumps({"type": "Results", "is_final": True, "speech_final": True, "channel": {"alternatives": [{
                        "transcript": "ZX 515", "confidence": 0.97, "words": [
                            {"word": "zx", "punctuated_word": "ZX", "confidence": 0.98, "start": 0.1, "end": 0.4},
                            {"word": "515", "punctuated_word": "515", "confidence": 0.96, "start": 0.4, "end": 0.9}]}]}}))
                    await ws.send(json.dumps({"type": "Results", "is_final": False, "speech_final": False,
                                              "channel": {"alternatives": [{"transcript": "", "confidence": 0, "words": []}]}}))
            else:
                kind = json.loads(message).get("type")
                self.controls.append(kind)
                if kind == "CloseStream":
                    await ws.close()
                    return

    def __enter__(self):
        def run():
            async def main():
                self.stop = asyncio.Event()
                async with wss.serve(self.handler, "127.0.0.1", 0) as server:
                    self.port = server.sockets[0].getsockname()[1]
                    self.loop = asyncio.get_running_loop()
                    self.ready.set()
                    await self.stop.wait()
            asyncio.run(main())
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        assert self.ready.wait(5)
        return self

    def __exit__(self, *exc):
        self.loop.call_soon_threadsafe(self.stop.set)
        self.thread.join(5)

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    monkeypatch.setenv("SKYASSIST_VOICE_MAX_STREAMS", "6")


def test_results_are_reshaped_and_noise_is_dropped():
    final = json.dumps({"type": "Results", "is_final": True, "speech_final": True, "channel": {"alternatives": [{
        "transcript": " Gate B14 ", "confidence": 0.91234, "words": [{"word": "gate", "confidence": 0.9, "start": 0, "end": 1}]}]}})
    assert voice.normalise(final) == {"type": "transcript", "text": "Gate B14", "final": True, "speech_final": True, "confidence": 0.912,
                                      "words": [{"w": "gate", "c": 0.9, "s": 0, "e": 1}]}
    empty = json.dumps({"type": "Results", "is_final": False, "speech_final": False, "channel": {"alternatives": [{"transcript": ""}]}})
    assert voice.normalise(empty) is None
    assert voice.normalise(json.dumps({"type": "Results", "speech_final": True, "channel": {"alternatives": [{"transcript": ""}]}}))["speech_final"]
    assert voice.normalise('{"type": "SpeechStarted"}') == {"type": "speech_started"}
    assert voice.normalise('{"type": "UtteranceEnd"}') == {"type": "utterance_end"}
    assert voice.normalise('{"type": "Metadata"}') is None and voice.normalise("not json") is None
    assert voice.normalise('{"type": "Error", "description": "bad audio"}') == {"type": "error", "reason": "bad audio"}


def test_listen_url_asks_for_model_level_speech_detection_and_caps_terms(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_BASE_URL", "https://example.test/dg")
    query = parse_qs(urlparse(voice.listen_url(language="ar", sample_rate=16000, terms=[f"t{i}" for i in range(150)])).query)
    assert query["model"] == ["nova-3"] and query["language"] == ["ar"] and query["encoding"] == ["linear16"]
    assert query["vad_events"] == ["true"] and query["interim_results"] == ["true"] and query["utterance_end_ms"] == ["1000"]
    assert len(query["keyterm"]) == 100
    assert voice.listen_url(language="en", sample_rate=16000, terms=[]).startswith("wss://example.test/dg/v1/listen?")


def test_status_says_why_voice_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    with make_client(tmp_path) as client:
        off = client.get("/voice/status").json()
        assert off["configured"] is False and off["stt"] is False and off["reason"] == "not_configured"
        monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
        monkeypatch.setenv("DEEPGRAM_BASE_URL", DEAD_END)
        on = client.get("/voice/status").json()
        assert on["configured"] and on["stt"] and on["tts"] and on["reason"] is None, "no network is not a rejected key"
        assert "test-key" not in json.dumps(on)


def test_vocabulary_lists_this_airports_flights_and_gates(tmp_path):
    with make_client(tmp_path) as client:
        body = client.get("/voice/vocabulary?language=en").json()
        flights = {f["code"]: f for f in body["flights"]}
        assert flights["EK101"]["direction"] == "arrival" and flights["EK202"]["direction"] == "departure"
        assert "QF202" in flights["EK202"]["aliases"]
        assert {"EK 202", "B14", "SkyAssist"} <= set(body["terms"]) and len(body["terms"]) <= 100


def test_stream_without_a_key_says_so_and_closes(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    with make_client(tmp_path) as client, client.websocket_connect("/voice/stream") as ws:
        ws.send_text(json.dumps({"type": "start"}))
        assert ws.receive_json() == {"type": "unavailable", "reason": "not_configured"}


def test_stream_refuses_pages_from_other_sites(tmp_path, key):
    with make_client(tmp_path) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/voice/stream", headers={"origin": "http://evil.example"}):
                pass


def test_stream_needs_a_start_message_first(tmp_path, key):
    with make_client(tmp_path) as client, client.websocket_connect("/voice/stream") as ws:
        ws.send_text(json.dumps({"type": "audio"}))
        assert ws.receive_json() == {"type": "error", "reason": "bad_start"}


def test_stream_reports_an_unreachable_service(tmp_path, key, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_BASE_URL", DEAD_END)
    with make_client(tmp_path) as client, client.websocket_connect("/voice/stream") as ws:
        ws.send_text(json.dumps({"type": "start", "language": "en", "sample_rate": 16000}))
        assert ws.receive_json() == {"type": "error", "reason": "unreachable"}


def test_stream_relays_speech_with_the_key_and_vocabulary_kept_server_side(tmp_path, key, monkeypatch):
    with FakeDeepgram() as fake:
        monkeypatch.setenv("DEEPGRAM_BASE_URL", fake.base)
        with make_client(tmp_path) as client, client.websocket_connect("/voice/stream") as ws:
            ws.send_text(json.dumps({"type": "start", "language": "en", "sample_rate": 16000, "terms": ["Zephyr Air"]}))
            assert ws.receive_json() == {"type": "ready"}
            ws.send_bytes(b"\x00\x01" * 480)
            ws.send_bytes(b"\x00\x01" * 480)
            heard = [ws.receive_json() for _ in range(3)]
            assert heard[0] == {"type": "speech_started"}
            assert heard[1]["type"] == "transcript" and heard[1]["final"] is False and heard[1]["text"] == "zed x five"
            assert heard[2]["final"] and heard[2]["speech_final"] and heard[2]["text"] == "ZX 515"
            assert heard[2]["words"][0] == {"w": "ZX", "c": 0.98, "s": 0.1, "e": 0.4}
            ws.send_text(json.dumps({"type": "finalize"}))
            ws.send_text(json.dumps({"type": "stop"}))
        path, authorization = fake.requests[0]
        query = parse_qs(urlparse(path).query)
        assert authorization == "Token test-key", "the proxy, not the browser, holds the key"
        assert query["model"] == ["nova-3"] and query["sample_rate"] == ["16000"]
        assert {"Zephyr Air", "B14", "EK 202"} <= set(query["keyterm"]), "airport vocabulary and page terms both boost"
        assert fake.frames == 2 and "Finalize" in fake.controls and "CloseStream" in fake.controls


class FakeSpeak(BaseHTTPRequestHandler):
    hits: list = []

    def do_POST(self):
        FakeSpeak.hits.append((self.path, self.headers.get("Authorization"), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.end_headers()
        self.wfile.write(b"ID3-fake-audio")

    def log_message(self, *args):
        pass


def test_speech_is_cached_and_arabic_is_left_to_the_device(tmp_path, key, monkeypatch):
    FakeSpeak.hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSpeak)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("DEEPGRAM_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}")
        with make_client(tmp_path) as client:
            first = client.post("/voice/speak", json={"text": "Your gate is B14.", "language": "en"})
            again = client.post("/voice/speak", json={"text": "Your  gate is B14.", "language": "en"})
            assert first.status_code == 200 and first.content == b"ID3-fake-audio" and first.headers["content-type"] == "audio/mpeg"
            assert again.headers["x-voice-cache"] == "hit" and len(FakeSpeak.hits) == 1
            path, authorization, body = FakeSpeak.hits[0]
            assert "model=aura-2-thalia-en" in path and authorization == "Token test-key" and body == {"text": "Your gate is B14."}
            arabic = client.post("/voice/speak", json={"text": "بوابتك B14", "language": "ar"})
            assert arabic.status_code == 503 and len(FakeSpeak.hits) == 1
            assert client.post("/voice/speak", json={"text": "", "language": "en"}).status_code == 422
            assert client.post("/voice/speak", json={"text": "x" * 401, "language": "en"}).status_code == 422
    finally:
        server.shutdown()


def test_speech_without_a_key_is_unavailable_not_an_error_page(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    with make_client(tmp_path) as client:
        reply = client.post("/voice/speak", json={"text": "hello", "language": "en"})
        assert reply.status_code == 503 and reply.json()["detail"]["reason_code"] == "TTS_UNAVAILABLE"
