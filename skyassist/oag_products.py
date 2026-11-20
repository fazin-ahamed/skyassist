"""Read-only OAG airport, airline, aircraft and scheduled connection lookups."""

import re
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Literal

import httpx

from skyassist import env, oag
from skyassist.network import offline_forced

MASTER_SPEC = "https://subscriptions.oag.com/openapi/locations-v1"
CONNECTIONS_SPEC = "https://subscriptions.oag.com/openapi/flight-info-connections-v1"
MASTER_KEY = "OAG_MASTER_DATA_API_KEY"
CONNECTIONS_KEY = "OAG_CONNECTIONS_API_KEY"

type LookupStatus = Literal[
    "ok", "empty", "unconfigured", "offline", "invalid", "unauthorized", "rate_limited", "unavailable",
]
type Record = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class LookupResult[T]:
    status: LookupStatus
    items: tuple[T, ...] = ()


@dataclass(frozen=True, slots=True)
class Terminal:
    code: str
    name: str | None


@dataclass(frozen=True, slots=True)
class Airport:
    iata: str | None
    icao: str | None
    name: str | None
    city: str | None
    country: str | None
    timezone: str | None
    terminals: tuple[Terminal, ...]


@dataclass(frozen=True, slots=True)
class Airline:
    iata: str | None
    icao: str | None
    name: str | None
    country: str | None


@dataclass(frozen=True, slots=True)
class Aircraft:
    iata: str | None
    icao: str | None
    name: str | None
    manufacturer: str | None


@dataclass(frozen=True, slots=True)
class ConnectionLeg:
    flight: str
    departure_airport: str
    arrival_airport: str
    departure_time: str | None
    arrival_time: str | None
    departure_terminal: str | None
    arrival_terminal: str | None


@dataclass(frozen=True, slots=True)
class Connection:
    origin: str
    destination: str
    via: str
    legs: tuple[ConnectionLeg, ConnectionLeg]
    duration_minutes: int | None
    layover_minutes: int | None
    is_self_connection: bool | None


_AIRPORT = re.compile(r"[A-Z]{3}")
_AIRPORT_ICAO = re.compile(r"[A-Z]{4}")
_AIRLINE_IATA = re.compile(r"[A-Z0-9]{2}")
_AIRLINE_ICAO = re.compile(r"[A-Z]{3}")
_AIRCRAFT = re.compile(r"[A-Z0-9]{2,4}")
_LOCAL_TIME = re.compile(r"([01][0-9]|2[0-3]):?([0-5][0-9])(?::[0-5][0-9])?")
_CACHE_LIMIT = 128
_CONNECTIONS_LIMIT = 5
_cache: OrderedDict[tuple, tuple[float, LookupResult]] = OrderedDict()


def _object(value: object) -> Record:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(value.split())[:160] or None


def _code(value: object, pattern: re.Pattern[str]) -> str | None:
    cleaned = _text(value)
    return cleaned.upper() if cleaned and pattern.fullmatch(cleaned.upper()) else None


def _airport(row: Record) -> Airport | None:
    codes = _object(row.get("code"))
    iata = _code(codes.get("iata"), _AIRPORT)
    icao = _code(codes.get("icao"), _AIRPORT_ICAO)
    if not (iata or icao) or row.get("type") not in (None, "AIRPORT"):
        return None
    place = _object(row.get("place"))
    country = _object(place.get("country"))
    timezone = _object(row.get("timeZone"))
    terminals = []
    if isinstance(raw_terminals := row.get("terminals"), list):
        for raw in raw_terminals:
            item = _object(raw)
            code = _text(item.get("code"))
            if code and code != "0":
                terminals.append(Terminal(code, _text(item.get("name"))))
    return Airport(iata, icao, _text(row.get("name")), _text(_object(place.get("city")).get("name")),
                   _text(country.get("name")) or _text(country.get("code")),
                   _text(_object(timezone.get("code")).get("iata")), tuple(terminals))


def _airline(row: Record) -> Airline | None:
    codes = _object(row.get("code"))
    iata = _code(codes.get("iata"), _AIRLINE_IATA)
    icao = _code(codes.get("icao"), _AIRLINE_ICAO)
    if not (iata or icao):
        return None
    country = _object(_object(row.get("domicile")).get("country"))
    display = _object(_object(row.get("alternativeName")).get("iata"))
    return Airline(iata, icao, _text(display.get("display")) or _text(row.get("name")),
                   _text(country.get("name")) or _text(_object(country.get("code")).get("iso")))


def _aircraft(row: Record) -> Aircraft | None:
    codes = _object(row.get("code"))
    iata = _code(codes.get("iata"), _AIRCRAFT)
    icao = _code(codes.get("icao"), _AIRCRAFT)
    if not (iata or icao):
        return None
    specification = _object(row.get("specification"))
    primary = _object(specification.get("iata"))
    name = _text(primary.get("model"))
    manufacturer = _text(_object(primary.get("manufacturer")).get("name"))
    if not (name and manufacturer) and isinstance(alternatives := specification.get("icao"), list):
        icao_spec = next((_object(item) for item in alternatives if _object(item).get("model")), {})
        name = name or _text(_object(icao_spec.get("model")).get("name"))
        manufacturer = manufacturer or _text(_object(icao_spec.get("manufacturer")).get("name"))
    return Aircraft(iata, icao, name, manufacturer)


def _minutes(value: object) -> int | None:
    if type(value) is int and value >= 0:
        return value
    return None


def _local_time(value: object) -> str | None:
    match = _LOCAL_TIME.fullmatch(value.strip()) if isinstance(value, str) else None
    return f"{match[1]}:{match[2]}" if match else None


def _leg(row: Record) -> ConnectionLeg | None:
    departure = _object(row.get("departure"))
    arrival = _object(row.get("arrival"))
    origin = _code(_object(departure.get("airport")).get("iata"), _AIRPORT)
    destination = _code(_object(arrival.get("airport")).get("iata"), _AIRPORT)
    carrier = _code(_object(row.get("carrierCode")).get("iata"), _AIRLINE_IATA)
    number = row.get("flightNumber")
    if not (origin and destination and carrier) or type(number) is not int or not (1 <= number <= 9999):
        return None
    return ConnectionLeg(f"{carrier}{number}", origin, destination, _local_time(departure.get("localTime")),
                         _local_time(arrival.get("localTime")), _text(departure.get("terminalCode")),
                         _text(arrival.get("terminalCode")))


def _connection(row: Record) -> Connection | None:
    inbound = _object(row.get("leg1"))
    first, second = _leg(inbound), _leg(_object(row.get("leg2")))
    if not first or not second or first.arrival_airport != second.departure_airport:
        return None
    self_connection = row.get("isSelfConnection")
    return Connection(first.departure_airport, second.arrival_airport, first.arrival_airport, (first, second),
                      _minutes(row.get("elapsedTime")), _minutes(inbound.get("connectionTime")),
                      self_connection if isinstance(self_connection, bool) else None)


def _iso_day(value: date | str) -> str | None:
    if type(value) is date:
        return value.isoformat()
    if not isinstance(value, str):
        return None
    try:
        day = value.strip()
        return day if date.fromisoformat(day).isoformat() == day else None
    except ValueError:
        return None


async def _request[T](
    path: str, key_name: str, params: dict[str, str | int], parse: Callable[[Record], T | None],
    accept: Callable[[T], bool], ttl: float, transport: httpx.AsyncBaseTransport | None,
) -> LookupResult[T]:
    if offline_forced():
        return LookupResult("offline")
    key = env.value(key_name) or env.value("OAG_API_KEY")
    if not key:
        return LookupResult("unconfigured")
    url = oag.base_url() + path
    cache_key = (url, tuple(params.items()), sha256(key.encode()).digest())
    if transport is None and (cached := _cache.get(cache_key)) and time.monotonic() < cached[0]:
        _cache.move_to_end(cache_key)
        return cached[1]
    try:
        async with httpx.AsyncClient(transport=transport, trust_env=False, timeout=6) as client:
            response = await client.get(url, params=params, headers={"Subscription-Key": key, "Accept": "application/json"})
        if response.status_code in {401, 403}:
            return LookupResult("unauthorized")
        if response.status_code == 429:
            return LookupResult("rate_limited")
        if response.status_code == 204:
            return LookupResult("empty")
        response.raise_for_status()
        payload = _object(response.json())
    except (httpx.HTTPError, ValueError):
        return LookupResult("unavailable")
    rows = payload.get("data")
    # The Master Data OpenAPI also documents an individual object response.
    if key_name == MASTER_KEY and rows is None and "code" in payload:
        rows = [payload]
    if not isinstance(rows, list):
        return LookupResult("unavailable")
    parsed = tuple(item for row in rows if (item := parse(_object(row))) is not None)
    if rows and not parsed:
        return LookupResult("unavailable")
    items = tuple(item for item in parsed if accept(item))
    status: LookupStatus = "ok" if items else "empty"
    result = LookupResult(status, items)
    if transport is None:
        cache_ttl = ttl if items else min(ttl, 60)
        _cache[cache_key] = (time.monotonic() + cache_ttl, result)
        _cache.move_to_end(cache_key)
        if len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)
    return result


async def lookup_airport(code: str, *, transport: httpx.AsyncBaseTransport | None = None) -> LookupResult[Airport]:
    """Look up one IATA or ICAO airport designator using Master Data Locations."""
    normalized = _code(code, _AIRPORT) or _code(code, _AIRPORT_ICAO)
    if not normalized:
        return LookupResult("invalid")
    kind = "IATA" if len(normalized) == 3 else "ICAO"
    return await _request("/locations", MASTER_KEY, {"version": "v1", "AirportCode": normalized, "CodeType": kind, "Limit": 1},
                          _airport, lambda row: normalized in (row.iata, row.icao), 3600, transport)


async def lookup_airline(code: str, *, transport: httpx.AsyncBaseTransport | None = None) -> LookupResult[Airline]:
    """Look up one IATA or ICAO airline designator using Master Data Carriers."""
    normalized = _code(code, _AIRLINE_IATA) or _code(code, _AIRLINE_ICAO)
    if not normalized:
        return LookupResult("invalid")
    kind = "IATA" if len(normalized) == 2 else "ICAO"
    return await _request("/carriers", MASTER_KEY, {"version": "v1", "CarrierCode": normalized, "CodeType": kind, "Limit": 1},
                          _airline, lambda row: normalized in (row.iata, row.icao), 3600, transport)


async def lookup_aircraft(code: str, *, transport: httpx.AsyncBaseTransport | None = None) -> LookupResult[Aircraft]:
    """Look up an IATA or ICAO aircraft designator using Master Data Equipment."""
    normalized = _code(code, _AIRCRAFT)
    if not normalized:
        return LookupResult("invalid")
    kind = "IATA,ICAO" if len(normalized) == 3 else "ICAO"
    return await _request("/equipment", MASTER_KEY, {"version": "v1", "AircraftCode": normalized, "CodeType": kind, "Limit": 1},
                          _aircraft, lambda row: normalized in (row.iata, row.icao), 3600, transport)


async def search_connections(
    origin: str, destination: str, on: date | str, *, via: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LookupResult[Connection]:
    """Find scheduled two-leg, same-airport passenger connections on a specific local departure date."""
    departure, arrival, day = _code(origin, _AIRPORT), _code(destination, _AIRPORT), _iso_day(on)
    connection_airport = _code(via, _AIRPORT) if via is not None else None
    if not (departure and arrival and day) or departure == arrival:
        return LookupResult("invalid")
    if via is not None and (connection_airport is None or connection_airport in (departure, arrival)):
        return LookupResult("invalid")
    params = {"version": "v1", "DepartureDate": day, "DepartureAirport": departure, "ArrivalAirport": arrival,
              "Service": "p", "InterAirport": "false", "Limit": _CONNECTIONS_LIMIT}
    if connection_airport:
        params["ViaPoint"] = connection_airport
    return await _request("/flight-connections", CONNECTIONS_KEY, params, _connection, lambda row: (
        (row.origin, row.destination) == (departure, arrival) and (not connection_airport or row.via == connection_airport)
    ), 300, transport)
