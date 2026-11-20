"""Live aircraft from adsb.lol: no key, a point query around DXB (100 nautical miles).

Used as the primary feed when OpenSky is unavailable, rate limited or empty, and merged with OpenSky when both answer.
Rows come out in the same shape as skyassist.opensky.parse, so the kiosk never needs to know which feed it came from.
"""

import math

import httpx

URL = "https://api.adsb.lol/v2/point/25.2532/55.3657/100"
CENTRE = (25.2528, 55.3644)  # DXB, the reference for every distance
FT_TO_M = 0.3048
KT_TO_MS = 0.514444
FPM_TO_MS = 0.00508
KEEP = 40


def _km(lat: float, lon: float) -> float:
    p1, p2 = math.radians(CENTRE[0]), math.radians(lat)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon - CENTRE[1]) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse(payload: dict) -> list[dict]:
    """adsb.lol 'ac' list in, aircraft dicts out (nearest to DXB first)."""
    out = []
    for ac in payload.get("ac") or []:
        lat, lon = _number(ac.get("lat")), _number(ac.get("lon"))
        hex_code = ac.get("hex")
        if lat is None or lon is None or not isinstance(hex_code, str) or not hex_code:
            continue
        alt = ac.get("alt_baro")
        on_ground = alt == "ground"
        alt_ft = _number(alt)
        speed_kt, track, rate_fpm = _number(ac.get("gs")), _number(ac.get("track")), _number(ac.get("baro_rate"))
        out.append({
            "icao24": hex_code.lower(), "callsign": (ac.get("flight") or "").strip() or None, "country": None,
            "lon": round(lon, 4), "lat": round(lat, 4),
            "altitude_m": None if alt_ft is None else round(alt_ft * FT_TO_M),
            "on_ground": on_ground,
            "speed_ms": None if speed_kt is None else round(speed_kt * KT_TO_MS),
            "heading": None if track is None else round(track),
            "climb_ms": None if rate_fpm is None else round(rate_fpm * FPM_TO_MS, 1),
            "km": round(_km(lat, lon), 1), "source": "adsb.lol",
        })
    out.sort(key=lambda a: a["km"])
    return out[:KEEP]


async def fetch(client: httpx.AsyncClient) -> list[dict]:
    reply = await client.get(URL)
    reply.raise_for_status()
    return parse(reply.json())
