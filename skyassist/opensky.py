"""Live aircraft near the airport: OpenSky Network, with adsb.lol alongside it, and a cache that outlives the connection.

OpenSky is open data, rate limited (anonymous: 400 credits a day). adsb.lol needs no key. Both are context for the kiosk,
never an input to a decision: gates, cutoffs and routes come from the airport's own feeds. The whole upstream refresh
runs at most once a minute no matter how many clients ask, OpenSky backs off for ten minutes when its credits run low
or it answers 429, and offline mode makes no calls at all. With no network the last good snapshot is returned with its
age, and with none at all the answer is "unavailable". Nothing is invented.

Optional credentials (OpenSky now accepts only OAuth2 client credentials): OPENSKY_CLIENT_ID, OPENSKY_CLIENT_SECRET.
"""

import asyncio
import math
import time

import httpx
from fastapi import APIRouter, Response

from skyassist import adsblol, env
from skyassist.network import offline_forced

STATES = "https://opensky-network.org/api/states/all"
TOKEN = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
CENTRE = (25.2528, 55.3644)  # DXB
BOX = (24.55, 54.85, 25.95, 56.05)  # lamin, lomin, lamax, lomax: about 150 km across, one credit
KEEP = 40
MIN_INTERVAL = 60.0  # one upstream refresh a minute in total, whoever asks
LOW_CREDITS = 5  # X-Rate-Limit-Remaining at or below this: stop calling OpenSky for a while
BACKOFF = 600.0  # seconds, or Retry-After when OpenSky sends it
ARRIVAL_KM = 60.0
ARRIVAL_ALT_M = 8000 * 0.3048  # 8000 ft
ARRIVAL_HEADING_DEG = 45.0
ARRIVALS_MAX = 5
OPENSKY_NAME, ADSB_NAME = "OpenSky Network", "adsb.lol"


def _km(lat: float, lon: float) -> float:
    p1, p2 = math.radians(CENTRE[0]), math.radians(lat)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon - CENTRE[1]) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def _bearing_to_centre(lat: float, lon: float) -> float:
    """Initial bearing in degrees from an aircraft to DXB."""
    p1, p2 = math.radians(lat), math.radians(CENTRE[0])
    dl = math.radians(CENTRE[1] - lon)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def parse(payload: dict) -> list[dict]:
    """State vectors in, a short list of aircraft out, nearest the airport first."""
    out = []
    for row in payload.get("states") or []:
        if len(row) < 11 or row[5] is None or row[6] is None:
            continue
        out.append({
            "icao24": row[0], "callsign": (row[1] or "").strip() or None, "country": row[2],
            "lon": round(row[5], 4), "lat": round(row[6], 4),
            "altitude_m": None if row[7] is None else round(row[7]), "on_ground": bool(row[8]),
            "speed_ms": None if row[9] is None else round(row[9]), "heading": None if row[10] is None else round(row[10]),
            "climb_ms": None if len(row) < 12 or row[11] is None else round(row[11], 1),
            "km": round(_km(row[6], row[5]), 1), "source": OPENSKY_NAME,
        })
    out.sort(key=lambda a: a["km"])
    return out[:KEEP]


def merge(*feeds: list[dict]) -> list[dict]:
    """One row per icao24, the first feed to report it wins; nearest the airport first."""
    seen: dict[str, dict] = {}
    for rows in feeds:
        for row in rows:
            if row.get("icao24"):
                seen.setdefault(row["icao24"], row)
    return sorted(seen.values(), key=lambda a: a["km"])[:KEEP]


def estimate_arrivals(aircraft: list[dict]) -> list[dict]:
    """Sets eta_min on each aircraft (None unless it is inbound and descending) and returns the arrivals, soonest first.

    Inbound: airborne, within 60 km, descending (baro_rate below 0, or below 8000 ft), and heading within 45 degrees
    of the bearing to DXB. ETA is the distance over the ground speed, clamped to 0..60 minutes.
    """
    arrivals = []
    for a in aircraft:
        a["eta_min"] = None
        if a.get("on_ground") or a.get("km") is None or a["km"] > ARRIVAL_KM:
            continue
        if a.get("lat") is None or a.get("lon") is None or not a.get("speed_ms") or a.get("heading") is None:
            continue
        falling = (a.get("climb_ms") is not None and a["climb_ms"] < 0) or \
            (a.get("altitude_m") is not None and a["altitude_m"] < ARRIVAL_ALT_M)
        if not falling:
            continue
        off = abs((a["heading"] - _bearing_to_centre(a["lat"], a["lon"]) + 180) % 360 - 180)
        if off > ARRIVAL_HEADING_DEG:
            continue
        speed_kmh = a["speed_ms"] * 3.6
        eta = max(0, min(60, round(a["km"] / speed_kmh * 60)))
        a["eta_min"] = eta
        arrivals.append({"callsign": a.get("callsign"), "eta_min": eta, "altitude_m": a.get("altitude_m")})
    arrivals.sort(key=lambda x: x["eta_min"])
    return arrivals[:ARRIVALS_MAX]


def _retry_after(reply: httpx.Response) -> float:
    try:
        return max(0.0, float(reply.headers.get("Retry-After")))
    except (TypeError, ValueError):
        return BACKOFF


class OpenSky:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None, clock=time.monotonic):
        self.transport = transport
        self.clock = clock
        self.aircraft: list[dict] | None = None
        self.fetched_at: float | None = None  # when the snapshot was taken
        self.attempted_at: float | None = None  # when the last upstream refresh started, success or not
        self.sources: list[str] = []
        self.error: str | None = None
        self.opensky_retry_at = 0.0  # OpenSky is not called before this clock time
        self._token: tuple[str, float] | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def credentials() -> tuple[str, str] | None:
        cid, secret = env.value("OPENSKY_CLIENT_ID"), env.value("OPENSKY_CLIENT_SECRET")
        return (cid, secret) if cid and secret else None

    def refresh_seconds(self) -> float:
        try:
            return max(5.0, float(env.value("OPENSKY_REFRESH_SECONDS")))
        except ValueError:
            return 30.0 if self.credentials() else 240.0

    async def _headers(self, client: httpx.AsyncClient) -> dict:
        creds = self.credentials()
        if not creds:
            return {}
        if not self._token or self._token[1] < time.monotonic() + 60:
            reply = await client.post(TOKEN, data={"grant_type": "client_credentials", "client_id": creds[0], "client_secret": creds[1]})
            reply.raise_for_status()
            body = reply.json()
            self._token = (body["access_token"], time.monotonic() + float(body.get("expires_in", 1800)))
        return {"Authorization": f"Bearer {self._token[0]}"}

    def state(self) -> dict:
        age = None if self.fetched_at is None else round(self.clock() - self.fetched_at)
        return {"authenticated": self.credentials() is not None, "last_fetch_age_s": age, "last_error": self.error,
                "refresh_seconds": MIN_INTERVAL, "sources": self.sources}

    async def _opensky(self, client: httpx.AsyncClient, now: float) -> list[dict]:
        reply = await client.get(STATES, params=dict(zip(("lamin", "lomin", "lamax", "lomax"), BOX)),
                                 headers=await self._headers(client))
        if reply.status_code == 429:
            self.opensky_retry_at = now + _retry_after(reply)
            raise _RateLimited()
        reply.raise_for_status()
        rows = parse(reply.json())
        remaining = reply.headers.get("X-Rate-Limit-Remaining")
        if remaining is not None and int(remaining) <= LOW_CREDITS:
            self.opensky_retry_at = now + BACKOFF
        return rows

    async def _refresh(self, now: float) -> bool:
        """One upstream round. Returns True when at least one feed answered and the snapshot was replaced."""
        sky_rows: list[dict] | None = None
        adsb_rows: list[dict] | None = None
        errors: list[str] = []
        async with httpx.AsyncClient(transport=self.transport, trust_env=False, timeout=6.0) as client:
            if now >= self.opensky_retry_at:
                try:
                    sky_rows = await self._opensky(client, now)
                except _RateLimited:
                    errors.append("rate_limited")
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    errors.append(type(exc).__name__)
            else:
                errors.append("opensky_backoff")
            try:
                adsb_rows = await adsblol.fetch(client)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                errors.append(f"adsb_{type(exc).__name__}")
        if sky_rows is None and adsb_rows is None:
            self.error = ", ".join(errors) or "unavailable"
            return False
        self.aircraft = merge(sky_rows or [], adsb_rows or [])
        self.sources = [name for name, rows in ((OPENSKY_NAME, sky_rows), (ADSB_NAME, adsb_rows)) if rows]
        self.fetched_at = now
        self.error = ", ".join(errors) or None
        return True

    async def nearby(self) -> dict:
        async with self._lock:
            now = self.clock()
            refreshed = False
            due = self.attempted_at is None or now - self.attempted_at >= MIN_INTERVAL
            if due and not offline_forced():
                self.attempted_at = now
                refreshed = await self._refresh(now)
        offline = offline_forced()
        if self.aircraft is None:
            return {"status": "offline" if offline else "unavailable", "aircraft": [], "arrivals": [], "age_s": None,
                    "sources": [], "source": f"{OPENSKY_NAME}, {ADSB_NAME}", "centre": CENTRE}
        aircraft = [dict(a) for a in self.aircraft]
        arrivals = estimate_arrivals(aircraft)
        status = "offline" if offline else ("live" if refreshed else "cached")
        return {"status": status, "age_s": round(now - self.fetched_at), "aircraft": aircraft, "arrivals": arrivals,
                "sources": list(self.sources), "source": f"{OPENSKY_NAME}, {ADSB_NAME}", "centre": CENTRE,
                "airborne": sum(1 for a in aircraft if not a["on_ground"])}


class _RateLimited(Exception):
    pass


def traffic_router(source: OpenSky | None = None) -> APIRouter:
    router = APIRouter()
    sky = source or OpenSky()
    router.sky = sky  # type: ignore[attr-defined]

    @router.get("/traffic/nearby")
    async def nearby(response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        return await sky.nearby()

    return router
