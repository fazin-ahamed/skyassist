"""Read-only passenger questions for OAG products. Only public codes and dates leave SkyAssist."""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from skyassist import oag, oag_products, trip
from skyassist.i18n import render
from skyassist.models import I18nText
from skyassist.network import offline_forced

type Product = Literal["flightinfo", "masterdata", "connections", "transfer"]

DUBAI = ZoneInfo("Asia/Dubai")
FLIGHT_DOCS = "https://developers.oag.com/apis/flight-info-v2"
CONNECTION_DOCS = "https://developers.oag.com/apis/flight-info-connections-v1"
_MASTER = {
    "airport": (re.compile(r"(?:\bairport(?:\s+(?:code|is|info(?:rmation)?(?:\s+for)?))?|(?:رمز\s+)?مطار)\s+([A-Z]{3,4})(?![\w-])", re.I),
                "https://developers.oag.com/apis/locations-v1"),
    "airline": (re.compile(r"(?:\b(?:airline|carrier)(?:\s+(?:code|is))?|(?:رمز\s+)?شركة الطيران)\s+([A-Z0-9]{2,3})(?![\w-])", re.I),
                "https://developers.oag.com/apis/carriers-v1"),
    "aircraft": (re.compile(r"(?:\baircraft(?:\s+(?:type|code))?|(?:نوع|رمز)\s+الطائرة)\s+([A-Z0-9]{3,4})(?![\w-])", re.I),
                 "https://developers.oag.com/apis/equipment-v1"),
}
_FLIGHT = re.compile(r"\b(?=[A-Z0-9]{2}[ -]?\d)(?:[A-Z][A-Z0-9]|[0-9][A-Z])[ -]?\d{1,4}[A-Z]?\b", re.I)
_FLIGHT_CONTEXT = re.compile(r"\b(?:flight (?:status|info(?:rmation)?)|(?:status|gate) (?:of|for)|look up flight)\b|حالة الرحلة|معلومات الرحلة", re.I)
_CONNECTION = re.compile(r"\b(?:connections\s+(?:from|between)|connecting flights|alternate flights|find connections)\b|رحلات متصلة|رحلات مواصلة", re.I)
_PAIR = re.compile(r"(?:\bfrom\s+|\bbetween\s+|من\s+)([A-Z]{3})\s+(?:to|and|إلى)\s+([A-Z]{3})\b", re.I)
_DEPARTING = re.compile(r"\bdeparting\s+([A-Z]{3})\b", re.I)
_ARRIVED = re.compile(r"\b(?:landed|arrived|flew|came|coming|flying in)\s+from\s+", re.I)
_ONWARD = re.compile(r"\b(?:get|go|going|goin|fly|flying|connect|connecting|connection|transfer|transferring|onward)\s+to\s+", re.I)
_DATE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_COMMON_WORDS = {"map", "maps", "gate", "link", "code", "type", "info", "the", "can", "all"}
_STATUS_LABEL = {
    "Scheduled": "scheduled", "Proposed": "scheduled", "OutGate": "left_gate", "InAir": "airborne",
    "Landed": "landed", "InGate": "at_gate", "Canceled": "canceled", "Diverted": "diverted",
}


@dataclass(frozen=True)
class AviationQuestion:
    product: Product
    reference: str = ""
    entity: str = ""
    route: tuple[str, str] | None = None
    date_text: str | None = None
    tomorrow: bool = False
    origin: str | None = None


@dataclass(frozen=True)
class Answer:
    text: str
    title: str | None = None
    url: str | None = None
    links: tuple[tuple[str, str], ...] = ()


def interpret(text: str, kind: str) -> AviationQuestion | None:
    if kind == "urgent":
        return None
    text = text.translate(_DIGITS)
    service_date = _DATE.search(text)
    pair = _PAIR.search(text)
    when = {
        "date_text": service_date.group(1) if service_date else None,
        "tomorrow": bool(re.search(r"\btomorrow\b|غد[ًاا]?", text, re.I)),
    }
    route = (pair.group(1).upper(), pair.group(2).upper()) if pair else None
    if _CONNECTION.search(text):
        return AviationQuestion("connections", route=route, **when)
    arrival = _ARRIVED.search(text)
    if arrival and _ONWARD.search(text, arrival.end()):
        return AviationQuestion("transfer", reference=text, **when)
    for entity, (pattern, _) in _MASTER.items():
        match = pattern.search(text)
        if match and match.group(1).lower() not in _COMMON_WORDS:
            return AviationQuestion("masterdata", reference=match.group(1).upper(), entity=entity)
    if kind == "flightinfo" or _FLIGHT_CONTEXT.search(text):
        # In ordinary questions, short English words can look like airline
        # designators when followed by a time or date.
        text = _DATE.sub("", text)
        match = next((part for part in _FLIGHT.finditer(text)
                      if not re.match(r"(?:at|is|to|on|by|of|am|pm)\s", part.group())), None)
        departing = _DEPARTING.search(text)
        return AviationQuestion("flightinfo", reference=match.group() if match else "", route=route,
                                origin=departing[1].upper() if departing else None, **when)
    return None


def _say(key: str, lang: str, **params: str) -> str:
    return render(I18nText(key="oag." + key, params=params), lang)


def _day(query: AviationQuestion, now: datetime | None) -> date | None:
    if query.date_text:
        try:
            return date.fromisoformat(query.date_text)
        except ValueError:
            return None
    if now is None:
        return None
    return now.astimezone(DUBAI).date() + timedelta(days=int(query.tomorrow))


def _public_label(value: str | None) -> str:
    return " ".join(value.split())[:100] if isinstance(value, str) else ""


def _empty_result(status: str, lang: str) -> Answer:
    key = {
        "empty": "no_results", "invalid": "invalid", "offline": "offline",
        "unconfigured": "unavailable", "unauthorized": "unavailable", "rate_limited": "busy",
    }.get(status, "unavailable")
    return Answer(_say(key, lang))


async def _master_data(query: AviationQuestion, lang: str) -> Answer:
    lookup = {
        "airport": oag_products.lookup_airport,
        "airline": oag_products.lookup_airline,
        "aircraft": oag_products.lookup_aircraft,
    }[query.entity]
    result = await lookup(query.reference)
    if not result.items:
        return _empty_result(result.status, lang)
    item = result.items[0]
    label = _public_label(item.name)
    if not label:
        return _empty_result("empty", lang)
    text = _say("reference", lang, code=query.reference, name=label)
    if query.entity == "airport":
        locality = ", ".join(dict.fromkeys(filter(None, (_public_label(item.city), _public_label(item.country)))))
        if locality:
            text += " " + _say("location", lang, place=locality)
    return Answer(text, "OAG Master Data", _MASTER[query.entity][1])


async def _connections(query: AviationQuestion, lang: str, departure: date) -> Answer:
    if query.route is None or query.route[0] == query.route[1]:
        return Answer(_say("ask_route", lang))
    origin, destination = query.route
    result = await oag_products.search_connections(origin, destination, departure)
    if not result.items:
        return _empty_result(result.status, lang)
    options = []
    for item in result.items:
        flights = " → ".join(leg.flight for leg in item.legs)
        if flights:
            via = _say("via", lang, flights=flights, place=item.via)
            options.append(via + " (" + _say("self_transfer", lang) + ")" if item.is_self_connection else via)
    options = list(dict.fromkeys(options))[:3]
    if not options:
        return _empty_result("empty", lang)
    return Answer(_say("connections", lang, origin=origin, destination=destination,
                       date=departure.isoformat(), flights=" / ".join(options)),
                  "OAG Flight Connections", CONNECTION_DOCS)


async def _flight_info(query: AviationQuestion, lang: str, departure: date) -> Answer:
    if not query.reference:
        return Answer(_say("ask_flight", lang))
    kwargs = {"origin": query.route[0], "destination": query.route[1]} if query.route else (
        {"origin": query.origin} if query.origin else {}
    )
    result = await oag.lookup_result(query.reference, departure, **kwargs)
    if result.status != "ok":
        return _empty_result(result.status, lang)
    rows = result.rows
    if len(rows) > 1:
        return Answer(_say("ask_flight_route", lang))
    if not rows:
        return _empty_result("empty", lang)
    row = rows[0]
    origin, destination = row["origin"], row["destination"]
    unknown = _say("unknown", lang)
    time_value = origin.get("local_time") or ""
    time_match = re.match(r"^\d{2}:\d{2}", time_value)
    status = _STATUS_LABEL.get(row.get("status") or "", "unknown")
    text = _say("flight", lang, flight=row["flight"], origin=origin.get("airport") or unknown,
                destination=destination.get("airport") or unknown, date=departure.isoformat(),
                time=time_match.group() if time_match else unknown,
                status=_say(status, lang))
    observed = oag.instant(row.get("observed_at"))
    if origin.get("gate") and observed:
        text += " " + _say("reported_gate", lang, gate=origin["gate"],
                           time=observed.strftime("%Y-%m-%d %H:%M UTC"))
    return Answer(text, "OAG Flight Info", FLIGHT_DOCS)


async def answer(query: AviationQuestion, lang: str, now: datetime | None) -> Answer:
    if query.product == "transfer":
        result = await trip.trip_flights(query.reference, now)
        lines = trip.sentences(result, lang)
        return Answer(" ".join([_say("transfer_signs", lang), *lines, _say("transfer_flight", lang)]), links=(
            ("Dubai Airports flight departures", "https://dubaiairports.ae/flight-status?type=departure"),
            ("DXB Express Maps", "https://dubaiairports.ae/express-maps"),
        ))
    if offline_forced():
        return _empty_result("offline", lang)
    if query.product == "masterdata":
        return await _master_data(query, lang)
    day = _day(query, now)
    if day is None:
        return Answer(_say("ask_date", lang))
    if query.product == "connections":
        return await _connections(query, lang, day)
    return await _flight_info(query, lang, day)
