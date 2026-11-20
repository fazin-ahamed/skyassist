"""Understand a trip described in plain words, and look its flights up.

"just landed from kolkata on the emirates flight, i need to get to madrid on the emirated flight" has no flight number in
it, but it names two airports and an airline. Code reads those (misspellings included), asks OAG Flight Info for the
flights on those routes through Dubai, and reports what is known: time, terminal, gate and status. Nothing is guessed:
a gate OAG has not published yet is reported as not assigned yet.
"""

import re
from datetime import datetime, timedelta
from difflib import get_close_matches
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated

from skyassist import oag
from skyassist.i18n import render
from skyassist.models import I18nText

DUBAI = ZoneInfo("Asia/Dubai")
HUB = "DXB"

CITIES = {
    "dubai": "DXB", "kolkata": "CCU", "calcutta": "CCU", "madrid": "MAD", "london": "LHR", "heathrow": "LHR", "gatwick": "LGW",
    "delhi": "DEL", "new delhi": "DEL", "mumbai": "BOM", "bombay": "BOM", "bangalore": "BLR", "bengaluru": "BLR", "chennai": "MAA",
    "hyderabad": "HYD", "kochi": "COK", "cochin": "COK", "ahmedabad": "AMD", "goa": "GOI", "karachi": "KHI", "lahore": "LHE",
    "islamabad": "ISB", "dhaka": "DAC", "colombo": "CMB", "kathmandu": "KTM", "male": "MLE", "manila": "MNL", "singapore": "SIN",
    "bangkok": "BKK", "kuala lumpur": "KUL", "jakarta": "CGK", "hong kong": "HKG", "tokyo": "HND", "seoul": "ICN", "sydney": "SYD",
    "melbourne": "MEL", "auckland": "AKL", "paris": "CDG", "frankfurt": "FRA", "munich": "MUC", "amsterdam": "AMS", "rome": "FCO",
    "milan": "MXP", "barcelona": "BCN", "lisbon": "LIS", "istanbul": "IST", "athens": "ATH", "vienna": "VIE", "zurich": "ZRH",
    "dublin": "DUB", "manchester": "MAN", "glasgow": "GLA", "birmingham": "BHX", "cairo": "CAI", "johannesburg": "JNB",
    "cape town": "CPT", "nairobi": "NBO", "addis ababa": "ADD", "lagos": "LOS", "casablanca": "CMN", "new york": "JFK",
    "toronto": "YYZ", "los angeles": "LAX", "san francisco": "SFO", "chicago": "ORD", "riyadh": "RUH", "jeddah": "JED",
    "doha": "DOH", "abu dhabi": "AUH", "muscat": "MCT", "kuwait": "KWI", "bahrain": "BAH", "amman": "AMM", "beirut": "BEY",
    "tehran": "IKA", "moscow": "SVO", "shanghai": "PVG", "beijing": "PEK", "guangzhou": "CAN",
}
AIRLINES = {
    "emirates": "EK", "flydubai": "FZ", "fly dubai": "FZ", "etihad": "EY", "qatar": "QR", "qatar airways": "QR", "air india": "AI",
    "indigo": "6E", "spicejet": "SG", "lufthansa": "LH", "british airways": "BA", "turkish": "TK", "turkish airlines": "TK",
    "singapore airlines": "SQ", "saudia": "SV", "air arabia": "G9", "iberia": "IB", "air france": "AF", "klm": "KL",
    "virgin atlantic": "VS", "ethiopian": "ET", "oman air": "WY", "gulf air": "GF", "kuwait airways": "KU", "pakistan international": "PK",
    "srilankan": "UL", "kenya airways": "KQ", "qantas": "QF", "cathay pacific": "CX", "thai": "TG", "malaysia airlines": "MH",
}
_NAMES = sorted(CITIES, key=len, reverse=True)
_AIRLINE_NAMES = sorted(AIRLINES, key=len, reverse=True)
_FROM = re.compile(r"\b(?:from|landed from|arrived from|came from|flew from|coming from)\s+$")
_TO = re.compile(r"\b(?:to|towards|for|get to|going to|heading to|fly to|flying to|connect to|connecting to|headed to)\s+$")


def _find(text: str, names: list[str], table: dict[str, str]) -> list[tuple[int, int, str]]:
    """(start, end, code) for each place or airline named, longest names first, one close-spelling repair per word."""
    lowered = text.lower()
    found: list[tuple[int, int, str]] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    for name in names:
        for match in re.finditer(rf"\b{re.escape(name)}\b", lowered):
            if free(match.start(), match.end()):
                found.append((match.start(), match.end(), table[name]))
                taken.append((match.start(), match.end()))
    for match in re.finditer(r"[a-z]{4,}", lowered):  # misspellings: "emirated", "calcuta"
        if not free(match.start(), match.end()):
            continue
        close = get_close_matches(match.group(0), [n for n in names if " " not in n], n=1, cutoff=0.84)
        if close:
            found.append((match.start(), match.end(), table[close[0]]))
            taken.append((match.start(), match.end()))
    return sorted(found)


def understand(text: str) -> dict:
    """{origin, destination, carrier_in, carrier_out}: airport codes and airline codes named in the text, or None."""
    lowered = text.lower()
    places = _find(text, _NAMES, CITIES)
    airlines = _find(text, _AIRLINE_NAMES, AIRLINES)
    origin = destination = None
    spans: dict[str, tuple[int, int]] = {}
    for start, end, code in places:
        before = lowered[max(0, start - 22):start]
        if _FROM.search(before) and origin is None and code != HUB:
            origin, spans["origin"] = code, (start, end)
        elif _TO.search(before) and destination is None and code != HUB:
            destination, spans["destination"] = code, (start, end)
    if origin is None and destination is None:
        others = [p for p in places if p[2] != HUB]
        if len(others) == 2:
            (a, _, ca), (b, _, cb) = others
            origin, destination, spans = ca, cb, {"origin": (others[0][0], others[0][1]), "destination": (others[1][0], others[1][1])}

    def airline_after(span: tuple[int, int] | None) -> str | None:
        if span is None:
            return None
        near = [a for a in airlines if 0 <= a[0] - span[1] <= 40]
        return near[0][2] if near else None

    carrier_in, carrier_out = airline_after(spans.get("origin")), airline_after(spans.get("destination"))
    named = {a[2] for a in airlines}
    if len(named) == 1:  # "the emirates flight" once: the same airline for both
        carrier_in, carrier_out = carrier_in or next(iter(named)), carrier_out or next(iter(named))
    return {"origin": origin, "destination": destination, "carrier_in": carrier_in, "carrier_out": carrier_out}


def _row(row: dict, side: str) -> dict:
    end = row["origin"] if side == "origin" else row["destination"]
    return {"flight": row["flight"], "origin": row["origin"]["airport"], "destination": row["destination"]["airport"],
            "time": (end["local_time"] or "")[:5], "terminal": end["actual_terminal"] or end["terminal"], "gate": end["gate"],
            "status": row["status"], "scheduled": end["scheduled"]}


async def trip_flights(text: str, now: datetime | None = None, *, transport=None) -> dict:
    trip = understand(text)
    if not (trip["origin"] or trip["destination"]):
        return {"understood": False, "trip": trip, "inbound": [], "outbound": [], "live": oag.configured(), "lookup": {}}
    today = (now or datetime.now(DUBAI)).astimezone(DUBAI).date()
    inbound, outbound, lookup = [], [], {}
    if oag.configured():
        if trip["origin"]:
            found = await oag.search_route(trip["origin"], HUB, today, carrier=trip["carrier_in"], arrival=True, transport=transport)
            lookup["inbound"] = found.status
            inbound = sorted((_row(r, "destination") for r in found.rows), key=lambda r: r["scheduled"] or "")
        if trip["destination"]:
            found = await oag.search_route(HUB, trip["destination"], today, carrier=trip["carrier_out"], transport=transport)
            lookup["outbound"] = found.status
            cutoff = ((now or datetime.now(DUBAI)).astimezone(DUBAI) - timedelta(minutes=30)).isoformat()
            outbound = sorted((_row(r, "origin") for r in found.rows), key=lambda r: r["scheduled"] or "")
            upcoming = [r for r in outbound if (r["scheduled"] or "") >= cutoff[:16]]
            outbound = upcoming or outbound
    return {"understood": True, "trip": trip, "inbound": inbound, "outbound": outbound, "live": oag.configured(), "lookup": lookup}


def sentences(result: dict, lang: str) -> list[str]:
    """What the kiosk says about the trip, in the passenger's language. Every number comes from the lookup."""
    if not result["understood"]:
        return []
    trip = result["trip"]
    say = lambda key, **params: render(I18nText(key=key, params=params), lang)  # noqa: E731
    out = [say("trip.understood", origin=trip["origin"] or "?", destination=trip["destination"] or "?")]
    if not result["live"]:
        return out + [say("trip.not_live")]
    for rows, key_found, key_none, place in ((result["inbound"], "trip.inbound", "trip.none_in", trip["origin"]),
                                              (result["outbound"], "trip.outbound", "trip.none_out", trip["destination"])):
        if not place:
            continue
        if not rows:
            out.append(say(key_none, origin=trip["origin"] or "?", destination=trip["destination"] or "?"))
            continue
        for row in rows[:3]:
            gate = row["gate"] or ""
            out.append(say(key_found + ("_gate" if gate else ""), flight=row["flight"], origin=row["origin"] or "?", destination=row["destination"] or "?",
                           time=row["time"] or "?", terminal=row["terminal"] or "?", gate=gate or "?", status=row["status"] or say("trip.status_unknown")))
    return out


class UnderstandBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: Annotated[str, Field(min_length=1, max_length=600)]
    language: Annotated[str, Field(pattern=r"^(en|ar)$")] = "en"


def trip_router() -> APIRouter:
    router = APIRouter()

    @router.post("/flights/understand")
    async def understand_trip(body: UnderstandBody, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        result = await trip_flights(body.text)
        return {**result, "lines": sentences(result, body.language)}

    return router
