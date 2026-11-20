"""Live aircraft: rate limit, fallback, merge, ETA and offline behaviour. No real network: httpx.MockTransport only."""

import asyncio

import httpx
import pytest

from skyassist import adsblol, opensky
from skyassist.opensky import OpenSky, estimate_arrivals, merge, parse


def sky_row(icao, lat, lon, alt=3000, ground=False, speed=150, track=0, climb=-5, call="UAE1  "):
    return [icao, call, "United Arab Emirates", None, None, lon, lat, alt, ground, speed, track, climb, None, None, False, 0]


def adsb_ac(hex_code, lat, lon, alt=9000, gs=300, track=0, rate=-1000, flight="ETD123  "):
    return {"hex": hex_code, "flight": flight, "lat": lat, "lon": lon, "alt_baro": alt, "gs": gs, "track": track, "baro_rate": rate}


class Feeds:
    """A fake upstream: records every request and answers from the fields below."""

    def __init__(self):
        self.hosts: list[str] = []
        self.sky_status = 200
        self.sky_headers: dict = {}
        self.sky_states: list = []
        self.adsb: list = []

    def transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.hosts.append(request.url.host)
            if request.url.host == "opensky-network.org":
                if self.sky_status == 429:
                    return httpx.Response(429, headers=self.sky_headers)
                return httpx.Response(self.sky_status, json={"states": self.sky_states}, headers=self.sky_headers)
            if request.url.host == "api.adsb.lol":
                return httpx.Response(200, json={"ac": self.adsb})
            raise AssertionError(f"unexpected host {request.url.host}")
        return httpx.MockTransport(handler)

    def count(self, host: str) -> int:
        return sum(1 for h in self.hosts if h == host)


@pytest.fixture
def feeds(monkeypatch):
    monkeypatch.setattr(OpenSky, "credentials", staticmethod(lambda: None))
    monkeypatch.setattr(opensky, "offline_forced", lambda: False)
    return Feeds()


def make(feeds, clock):
    return OpenSky(transport=feeds.transport(), clock=lambda: clock[0])


def test_second_call_within_a_minute_makes_no_upstream_request(feeds):
    clock = [1000.0]
    sky = make(feeds, clock)
    first = asyncio.run(sky.nearby())
    assert first["status"] == "live"
    assert len(feeds.hosts) == 2  # OpenSky and adsb.lol, once each
    clock[0] += 10
    second = asyncio.run(sky.nearby())
    assert len(feeds.hosts) == 2
    assert second["status"] == "cached" and second["age_s"] == 10


def test_a_call_after_the_interval_refreshes_once(feeds):
    clock = [1000.0]
    sky = make(feeds, clock)
    asyncio.run(sky.nearby())
    clock[0] += 61
    reply = asyncio.run(sky.nearby())
    assert len(feeds.hosts) == 4
    assert reply["status"] == "live"


def test_many_clients_share_one_refresh(feeds):
    clock = [1000.0]
    sky = make(feeds, clock)

    async def burst():
        return await asyncio.gather(*(sky.nearby() for _ in range(5)))

    replies = asyncio.run(burst())
    assert len(feeds.hosts) == 2
    assert all(r["status"] in ("live", "cached") for r in replies)


def test_429_backs_off_opensky_for_ten_minutes_and_adsb_still_answers(feeds):
    clock = [1000.0]
    feeds.sky_status = 429
    feeds.adsb = [adsb_ac("4d2a01", 25.0, 55.4)]
    sky = make(feeds, clock)
    reply = asyncio.run(sky.nearby())
    assert reply["sources"] == ["adsb.lol"]
    assert feeds.count("opensky-network.org") == 1
    clock[0] += 61
    asyncio.run(sky.nearby())
    assert feeds.count("opensky-network.org") == 1  # still backing off
    assert feeds.count("api.adsb.lol") == 2
    clock[0] = 1000.0 + 601
    asyncio.run(sky.nearby())
    assert feeds.count("opensky-network.org") == 2


def test_retry_after_sets_the_backoff(feeds):
    clock = [1000.0]
    feeds.sky_status = 429
    feeds.sky_headers = {"Retry-After": "120"}
    sky = make(feeds, clock)
    asyncio.run(sky.nearby())
    clock[0] += 121
    asyncio.run(sky.nearby())
    assert feeds.count("opensky-network.org") == 2


def test_low_credits_serve_the_cache_and_back_off(feeds):
    clock = [1000.0]
    feeds.sky_states = [sky_row("abc123", 25.0, 55.4)]
    feeds.sky_headers = {"X-Rate-Limit-Remaining": "5"}
    sky = make(feeds, clock)
    asyncio.run(sky.nearby())
    clock[0] += 120
    asyncio.run(sky.nearby())
    assert feeds.count("opensky-network.org") == 1


def test_adsb_parses_ground_and_units():
    rows = adsb_parse({"ac": [
        {"hex": "ABC123", "flight": " ETD9 ", "lat": 25.1, "lon": 55.3, "alt_baro": "ground", "gs": 10, "track": 90},
        {"hex": "def456", "flight": None, "lat": 25.0, "lon": 55.4, "alt_baro": 1000, "gs": 200, "track": 180, "baro_rate": -600},
        {"hex": "bad", "lat": None, "lon": 55.4},
    ]})
    ground = next(r for r in rows if r["icao24"] == "abc123")
    assert ground["on_ground"] is True and ground["altitude_m"] is None
    assert ground["callsign"] == "ETD9" and ground["source"] == "adsb.lol"
    air = next(r for r in rows if r["icao24"] == "def456")
    assert air["altitude_m"] == 305 and air["speed_ms"] == 103 and air["climb_ms"] == -3.0 and air["callsign"] is None
    assert len(rows) == 2


def adsb_parse(payload):
    return adsblol.parse(payload)


def test_merge_dedupes_by_icao24_and_prefers_opensky():
    sky_rows = parse({"states": [sky_row("abc123", 25.0, 55.4, call="UAE7  ")]})
    adsb_rows = adsb_parse({"ac": [
        {"hex": "ABC123", "flight": "XYZ1", "lat": 25.0, "lon": 55.4, "alt_baro": 900, "gs": 150, "track": 0},
        {"hex": "fff000", "lat": 25.2, "lon": 55.3, "alt_baro": 900, "gs": 150, "track": 0},
    ]})
    merged = merge(sky_rows, adsb_rows)
    assert [r["icao24"] for r in merged].count("abc123") == 1
    assert len(merged) == 2
    assert next(r for r in merged if r["icao24"] == "abc123")["source"] == "OpenSky Network"


def test_eta_for_an_inbound_descending_aircraft():
    # about 30 km due south of DXB, heading north, 150 m/s (540 km/h): 30 / 540 h = 3.3 min
    a = {"icao24": "a1", "callsign": "UAE1", "lat": 25.2528 - 0.27, "lon": 55.3644, "altitude_m": 3000, "on_ground": False,
         "speed_ms": 150, "heading": 0, "climb_ms": -5.0, "km": 30.0}
    arrivals = estimate_arrivals([a])
    assert a["eta_min"] == 3
    assert arrivals == [{"callsign": "UAE1", "eta_min": 3, "altitude_m": 3000}]


def test_eta_is_null_when_not_inbound_or_not_descending_or_too_far():
    base = {"icao24": "a1", "callsign": "X", "lat": 25.2528 - 0.27, "lon": 55.3644, "altitude_m": 5000, "on_ground": False,
            "speed_ms": 150, "heading": 0, "climb_ms": 5.0, "km": 30.0}
    climbing = dict(base)
    wrong_way = dict(base, heading=90, climb_ms=-5.0)
    far = dict(base, km=75.0, climb_ms=-5.0)
    ground = dict(base, on_ground=True, climb_ms=-5.0)
    estimate_arrivals([climbing, wrong_way, far, ground])
    assert climbing["eta_min"] is None and wrong_way["eta_min"] is None
    assert far["eta_min"] is None and ground["eta_min"] is None


def test_eta_is_clamped_to_sixty_minutes():
    slow = {"icao24": "s", "callsign": "SLOW", "lat": 25.2528 - 0.5, "lon": 55.3644, "altitude_m": 1000, "on_ground": False,
            "speed_ms": 5, "heading": 0, "climb_ms": -2.0, "km": 55.0}
    estimate_arrivals([slow])
    assert slow["eta_min"] == 60


def test_arrivals_are_capped_at_five_and_sorted(feeds):
    clock = [1000.0]
    feeds.adsb = [adsb_ac(f"c0{i:04x}", 25.2528 - 0.05 * (i + 1), 55.3644, alt=3000, gs=200, track=0, rate=-800) for i in range(7)]
    reply = asyncio.run(make(feeds, clock).nearby())
    assert len(reply["arrivals"]) == 5
    etas = [a["eta_min"] for a in reply["arrivals"]]
    assert etas == sorted(etas)


def test_offline_makes_zero_calls(feeds, monkeypatch):
    monkeypatch.setattr(opensky, "offline_forced", lambda: True)
    clock = [1000.0]
    reply = asyncio.run(make(feeds, clock).nearby())
    assert feeds.hosts == []
    assert reply["status"] == "offline" and reply["aircraft"] == []
