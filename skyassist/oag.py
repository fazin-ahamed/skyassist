"""Read-only OAG Flight Info v2 integration.

The search date is local to the selected airport's side of the flight. Schedule
fields and operational updates have different provenance. Only status-details
records can supply gates and their observation time.
"""

import asyncio
import re
import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, time as wall_time
from hashlib import sha256
from typing import Literal, TypedDict

import httpx

from skyassist import env
from skyassist.network import offline_forced

TTL = 60.0
MAX_PAGES = 3
MAX_CACHE = 256
_CODE = re.compile(r"([A-Z0-9]{2})\s*-?\s*([0-9]{1,4})([A-Z]?)", re.ASCII)
_CARRIER = re.compile(r"[A-Z0-9]{2}", re.ASCII | re.IGNORECASE)
_AIRPORT = re.compile(r"[A-Z]{3}", re.ASCII)
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
_TERMINAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .-]{0,23}", re.ASCII)
_GATE = re.compile(r"(?:[A-Z]{1,4}[- ]?)?[0-9]{1,4}[A-Z]?", re.ASCII)
_VARIATION = re.compile(r"(-)?(?:([0-9]{1,3})\.)?([0-9]{2}):([0-9]{2}):([0-9]{2})", re.ASCII)
_STATUS = {value.casefold(): value for value in (
    "Scheduled", "Proposed", "OutGate", "InAir", "Landed", "InGate", "Canceled", "Diverted",
)}


class FlightSide(TypedDict):
    airport: str | None
    terminal: str | None
    actual_terminal: str | None
    gate: str | None
    scheduled: str | None
    estimated: str | None
    actual: str | None
    local_time: str | None
    local_date: str | None


class FlightInstance(TypedDict):
    flight: str
    carrier: str
    number: str
    origin: FlightSide
    destination: FlightSide
    status: str | None
    observed_at: str | None
    schedule_instance_key: str | None
    status_key: str | None
    source: str


type LookupStatus = Literal["ok", "empty", "unconfigured", "offline", "unauthorized", "rate_limited", "unavailable", "invalid"]


@dataclass(frozen=True, slots=True)
class FlightLookupResult:
    status: LookupStatus
    rows: list[FlightInstance] = field(default_factory=list)


_cache: OrderedDict[tuple, tuple[float, FlightLookupResult]] = OrderedDict()
_ERROR_STATUS: dict[int, LookupStatus] = {
    400: "invalid", 401: "unauthorized", 403: "unauthorized", 422: "invalid", 429: "rate_limited",
}


def flight_info_key() -> str:
    return env.value("OAG_FLIGHT_INFO_API_KEY") or env.value("OAG_API_KEY")


def configured() -> bool:
    return not offline_forced() and bool(flight_info_key())


def base_url() -> str:
    return (env.value("OAG_BASE_URL") or "https://api.oag.com").rstrip("/")


def split_flight(code: str) -> tuple[str, str] | None:
    """Normalize common flight designators without dropping a service suffix."""
    match = _CODE.fullmatch(code.strip().upper())
    if not match or not int(match[2]):
        return None
    return match[1], str(int(match[2])) + match[3]


def _text(value: object) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _dig(row: dict, *path: str):
    value: object = row
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _day(value: object) -> str | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and _DATE.fullmatch(value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    return None


def _airport(value: object) -> str | None:
    code = _text(value)
    return code.upper() if code and _AIRPORT.fullmatch(code.upper()) else None


def _carrier(value: object) -> str | None:
    code = _text(value)
    return code.upper() if code and _CARRIER.fullmatch(code) else None


def _terminal(value: object) -> str | None:
    terminal = _text(value)
    return terminal if terminal and _TERMINAL.fullmatch(terminal) else None


def _schedule_params(on: date | str, *, origin: str | None, destination: str | None,
                     arrival: bool) -> dict[str, str] | None:
    day, origin_code, destination_code = _day(on), _airport(origin), _airport(destination)
    if not day or (origin is not None and not origin_code) or (destination is not None and not destination_code):
        return None
    return {
        "version": "v2", "CodeType": "IATA", "Content": "Status", "FlightType": "Scheduled",
        "ArrivalDateTime" if arrival else "DepartureDateTime": day, "Limit": "10",
        **({"DepartureAirport": origin_code} if origin_code else {}),
        **({"ArrivalAirport": destination_code} if destination_code else {}),
    }


def flight_info_params(flight: str, on: date | str, *, origin: str | None = None,
                       destination: str | None = None, arrival: bool = False) -> dict[str, str] | None:
    parts = split_flight(flight)
    params = _schedule_params(on, origin=origin, destination=destination, arrival=arrival)
    if not parts or params is None:
        return None
    params["CarrierCode"] = parts[0]
    params["FlightNumber"] = parts[1].rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return params


def instant(value: object) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _clock(value: object) -> wall_time | None:
    text = _text(value)
    if not text or not re.fullmatch(r"[0-9]{2}:?[0-9]{2}(?::[0-9]{2})?", text):
        return None
    try:
        return wall_time.fromisoformat(text)
    except ValueError:
        return None


def _operational_time(update: object, event: str, scheduled: datetime | None) -> str | None:
    if not isinstance(update, dict) or not (utc := _text(_dig(update, event, "utc"))):
        return None
    if len(utc) > 10 and _day(utc[:10]) and utc[10] in ("T", " "):
        try:
            parsed = datetime.fromisoformat(utc)
            return (parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)).isoformat()
        except (ValueError, OverflowError):
            return None
    clock = _clock(utc)
    variation = _VARIATION.fullmatch(_text(update.get(f"{event}Variation")) or "")
    if not scheduled or not clock or not variation:
        return None
    days = int(variation[2] or "0")
    hours, minutes, seconds = map(int, variation.group(3, 4, 5))
    if hours >= 24 or minutes >= 60 or seconds >= 60:
        return None
    offset = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    try:
        adjusted = scheduled - offset if variation[1] else scheduled + offset
    except OverflowError:
        return None
    if (clock.hour, clock.minute) != (adjusted.hour, adjusted.minute):
        return None
    return adjusted.isoformat()


def _latest_status(row: dict) -> tuple[datetime | None, dict]:
    details = row.get("statusDetails")
    latest_at = None
    latest: dict = {}
    for item in details if isinstance(details, list) else ():
        when = instant(item.get("updatedAt")) if isinstance(item, dict) else None
        if when and (latest_at is None or when > latest_at):
            latest_at, latest = when, item
        elif when and when == latest_at and item != latest:
            latest = {}  # conflicting snapshots at the same instant
    return (latest_at, latest) if latest else (None, {})


def _side(row: dict, details: dict, name: str, status: str | None) -> FlightSide:
    part = row.get(name)
    part = part if isinstance(part, dict) else {}
    operational = details.get(name)
    operational = operational if isinstance(operational, dict) else {}
    airport = _airport(_dig(part, "airport", "iata"))
    operational_airport = operational.get("airport")
    if (status == "Canceled" or status == "Diverted" and name == "arrival"
            or operational_airport is not None and _airport(_dig(operational, "airport", "iata")) != airport):
        operational = {}
    scheduled_day = _day(_dig(part, "date", "utc"))
    scheduled_time = _clock(_dig(part, "time", "utc"))
    scheduled = datetime.combine(date.fromisoformat(scheduled_day), scheduled_time, UTC) if scheduled_day and scheduled_time else None
    local = _clock(_dig(part, "time", "local"))
    terminal = _terminal(part.get("terminal"))
    actual_terminal = _terminal(operational.get("actualTerminal"))
    gate = (_text(operational.get("gate")) or "").upper()
    time_field = "outGate" if name == "departure" else "inGate"
    return {
        "airport": airport, "terminal": actual_terminal or terminal, "actual_terminal": actual_terminal,
        "gate": gate if _GATE.fullmatch(gate) else None,
        "scheduled": scheduled.isoformat() if scheduled else None,
        "estimated": _operational_time(operational.get("estimatedTime"), time_field, scheduled),
        "actual": _operational_time(operational.get("actualTime"), time_field, scheduled),
        "local_time": local.strftime("%H:%M") if local else None, "local_date": _day(_dig(part, "date", "local")),
    }


def parse_instance(row: dict) -> FlightInstance:
    carrier = _carrier(_dig(row, "carrier", "iata"))
    number = row.get("flightNumber")
    numeric = str(number) if isinstance(number, (int, str)) and not isinstance(number, bool) else ""
    suffix = row.get("serviceSuffix")
    suffix = "" if suffix is None else suffix
    parts = None
    if (carrier and re.fullmatch(r"[0-9]{1,4}", numeric)
            and isinstance(suffix, str) and re.fullmatch(r"[A-Z]?", suffix, re.IGNORECASE | re.ASCII)):
        parts = split_flight(carrier + numeric + suffix)
    carrier, number = parts or ("", "")
    observed, details = _latest_status(row)
    state = (_text(details.get("state")) or "").casefold()
    irregular = (_text(details.get("irregularOperationType")) or "").casefold()
    diverted_to = _airport(_dig(details, "diversionAirport", "iata"))
    scheduled_destination = _airport(_dig(row, "arrival", "airport", "iata"))
    status = _STATUS.get("canceled" if state == "cancelled" else state)
    if status != "Canceled" and (irregular in ("diversion", "diverted") or diverted_to and diverted_to != scheduled_destination):
        status = "Diverted"
    return {
        "flight": carrier + number, "carrier": carrier, "number": number,
        "origin": _side(row, details, "departure", status), "destination": _side(row, details, "arrival", status),
        "status": status, "source": "OAG", "observed_at": observed.isoformat() if observed else None,
        "schedule_instance_key": _text(row.get("scheduleInstanceKey")), "status_key": _text(row.get("statusKey")),
    }


def unpack_page(payload: object, *, received: int = 0, page_number: int = 1) -> tuple[list[dict], str | None]:
    """Extract only the cursor. Subsequent requests keep the configured OAG origin."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("invalid Flight Info response")
    raw_rows = payload["data"]
    link = _dig(payload, "paging", "next")
    cursor = None
    if link:
        if not isinstance(link, str):
            raise ValueError("invalid Flight Info cursor")
        cursor = httpx.URL(link).params.get("After")
        if not cursor:
            raise ValueError("missing Flight Info cursor")
    else:
        total_count = _dig(payload, "paging", "totalCount")
        total_pages = _dig(payload, "paging", "totalPages")
        if (type(total_count) is int and total_count > received + len(raw_rows)
                or type(total_pages) is int and total_pages > page_number):
            raise ValueError("incomplete Flight Info response")
    return [row for row in raw_rows if isinstance(row, dict)], cursor


def match_instances(rows: list[dict], flight: str | None, params: dict[str, str]) -> list[FlightInstance]:
    parts = split_flight(flight) if flight is not None else None
    if flight is not None and parts is None:
        return []
    code = "".join(parts) if parts else None
    side = "destination" if "ArrivalDateTime" in params else "origin"
    day = params.get("ArrivalDateTime") or params.get("DepartureDateTime")
    origin, destination = params.get("DepartureAirport"), params.get("ArrivalAirport")
    carrier = params.get("CarrierCode")
    matches: list[FlightInstance] = []
    operating_only = params.get("Codeshare") == "Operating"
    for raw in rows:
        if operating_only and _dig(raw, "codeshare", "operatingFlight"):
            continue
        row = parse_instance(raw)
        departure, arrival = row["origin"]["airport"], row["destination"]["airport"]
        if (row["flight"] and (code is None or row["flight"] == code)
                and (carrier is None or row["carrier"] == carrier)
                and row[side]["local_date"] == day and departure and arrival
                and (not origin or departure == origin) and (not destination or arrival == destination)
                and row not in matches):
            matches.append(row)
    return matches


async def _query_results(params: dict[str, str] | None, *, flight: str | None = None,
                         transport: httpx.AsyncBaseTransport | None = None) -> FlightLookupResult:
    if offline_forced():
        return FlightLookupResult("offline")
    if not (key := flight_info_key()):
        return FlightLookupResult("unconfigured")
    if params is None:
        return FlightLookupResult("invalid")
    endpoint = f"{base_url()}/flight-instances/"
    designator = split_flight(flight) if flight is not None else None
    cache_key = (endpoint, sha256(key.encode()).digest(), designator, tuple(params.items()))
    cached = _cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < TTL:
        return deepcopy(cached[1])
    rows: list[dict] = []
    query = params.copy()
    try:
        async with asyncio.timeout(8), httpx.AsyncClient(
            transport=transport, trust_env=False, timeout=5, follow_redirects=False,
        ) as client:
            for page_number in range(1, MAX_PAGES + 1):
                response = await client.get(endpoint, params=query, headers={"Subscription-Key": key})
                response.raise_for_status()
                page, cursor = unpack_page(response.json(), received=len(rows), page_number=page_number)
                rows.extend(page)
                if not cursor:
                    break
                query["After"] = cursor
            else:
                return FlightLookupResult("unavailable")
    except httpx.HTTPStatusError as error:
        return FlightLookupResult(_ERROR_STATUS.get(error.response.status_code, "unavailable"))
    except (httpx.HTTPError, httpx.InvalidURL, ValueError, TimeoutError):
        return FlightLookupResult("unavailable")
    found = match_instances(rows, flight, params)
    result = FlightLookupResult("ok" if found else "empty", found)
    _cache[cache_key] = (time.monotonic(), result)
    _cache.move_to_end(cache_key)
    if len(_cache) > MAX_CACHE:
        _cache.popitem(last=False)
    return deepcopy(result)


async def lookup_result(flight: str, on: date | str, *, origin: str | None = None, destination: str | None = None,
                        arrival: bool = False, transport: httpx.AsyncBaseTransport | None = None) -> FlightLookupResult:
    """Return exact matches or a typed reason why flight details are unavailable."""
    params = flight_info_params(flight, on, origin=origin, destination=destination, arrival=arrival)
    return await _query_results(params, flight=flight, transport=transport)


async def search_route(origin: str, destination: str, on: date | str, *, carrier: str | None = None,
                       arrival: bool = False, transport: httpx.AsyncBaseTransport | None = None) -> FlightLookupResult:
    """Find operating flights by route on the selected airport's local date."""
    params = _schedule_params(on, origin=origin, destination=destination, arrival=arrival)
    airline = _carrier(carrier)
    if (params is None or "DepartureAirport" not in params or "ArrivalAirport" not in params
            or (carrier is not None and airline is None)):
        return FlightLookupResult("invalid")
    params.update(Codeshare="Operating", Limit="10")  # the free tier rejects anything above 10
    if airline:
        params["CarrierCode"] = airline
    return await _query_results(params, transport=transport)


async def lookup(flight: str, on: date | str, *, origin: str | None = None, destination: str | None = None,
                 arrival: bool = False, transport: httpx.AsyncBaseTransport | None = None) -> list[FlightInstance]:
    """Return the rows from a Flight Info search."""
    result = await lookup_result(flight, on, origin=origin, destination=destination, arrival=arrival, transport=transport)
    return result.rows
