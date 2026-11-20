import json
import sqlite3
from datetime import date, datetime
from typing import Any

from skyassist.clock import as_utc, in_dubai
from skyassist.db import timestamp
from skyassist.models import (
    FieldEvidence, FlightBoardItem, FlightDirection, FlightLeg, GetFlightsRequest,
    GetFlightsResponse, I18nText, Observation, ObservationField, ObservationState,
    PermittedChoice, PolicyPack, ReasonCode, ToolError, ToolErrorClass,
)

_OBSERVATION_COLUMNS = (
    "leg_id", "field", "value_json", "source", "authority_rank", "observed_at", "received_at",
    "valid_until", "scheduled", "estimated", "actual", "withdrawn", "cutoff_estimated",
)


def _append(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    value = data.get("value")
    if isinstance(value, datetime):
        value = timestamp(value)
    data = {"withdrawn": False, "cutoff_estimated": False} | data
    data["value_json"] = None if value is None else json.dumps(value, sort_keys=True, allow_nan=False)
    for key in ("observed_at", "received_at", "valid_until", "scheduled", "estimated", "actual"):
        if data.get(key) is not None:
            data[key] = timestamp(data[key])
    columns = ", ".join(_OBSERVATION_COLUMNS)
    parameters = ", ".join(f":{key}" for key in _OBSERVATION_COLUMNS)
    same = " AND ".join(f"{key} IS :{key}" for key in _OBSERVATION_COLUMNS)
    conn.execute(f"""
        INSERT INTO observations ({columns})
        SELECT {parameters} WHERE NOT EXISTS (SELECT 1 FROM observations WHERE {same})
    """, {key: data.get(key) for key in _OBSERVATION_COLUMNS})


def ingest(conn: sqlite3.Connection, observation: Observation) -> None:
    _append(conn, observation.model_dump())


def _latest(
    conn: sqlite3.Connection, leg_id: str, field: str, as_of: datetime, *, scheduled: bool = False,
) -> list[sqlite3.Row]:
    schedule_filter = "AND (scheduled IS NOT NULL OR withdrawn = 1)" if scheduled else ""
    instant = timestamp(as_of)
    return conn.execute(f"""
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY source
                ORDER BY observed_at DESC, received_at DESC, withdrawn DESC, id DESC
            ) AS sequence
            FROM observations
            WHERE leg_id = ? AND field = ? AND observed_at <= ? AND received_at <= ?
            {schedule_filter}
        ) WHERE sequence = 1 AND withdrawn = 0
        ORDER BY authority_rank DESC, source
    """, (leg_id, field, instant, instant)).fetchall()


def _stale(row: sqlite3.Row, as_of: datetime, stale_seconds: int) -> bool:
    age = (as_of - datetime.fromisoformat(row["received_at"])).total_seconds()
    return age >= stale_seconds or as_of > datetime.fromisoformat(row["valid_until"])


def _select(
    rows: list[sqlite3.Row], as_of: datetime, stale_seconds: int, *facts: str,
) -> tuple[ObservationState, list[sqlite3.Row]]:
    if not rows:
        return ObservationState.UNKNOWN, []
    top = [row for row in rows if row["authority_rank"] == rows[0]["authority_rank"]]
    fresh = [row for row in top if not _stale(row, as_of, stale_seconds)]

    def value(row: sqlite3.Row) -> tuple:
        return tuple(row[key] for key in facts)

    if fresh and any(value(row) != value(fresh[0]) for row in fresh):
        return ObservationState.CONFLICT, fresh
    candidates = fresh or top
    equivalent = [row for row in candidates if value(row) == value(candidates[0])]
    newest = max(equivalent, key=lambda row: (row["observed_at"], row["received_at"]))
    return (ObservationState.FRESH if fresh else ObservationState.STALE), [newest]


def _observation(row: sqlite3.Row) -> Observation:
    data = {key: row[key] for key in Observation.model_fields if key != "value"}
    data["value"] = json.loads(row["value_json"]) if row["value_json"] is not None else None
    data["withdrawn"] = bool(data["withdrawn"])
    data["cutoff_estimated"] = bool(data["cutoff_estimated"])
    return Observation.model_validate(data)


def current_field(
    conn: sqlite3.Connection, leg_id: str, field: ObservationField | str,
    as_of: datetime, policy: PolicyPack,
) -> FieldEvidence:
    field = ObservationField(field)
    keys = ("scheduled", "estimated", "actual") if field in (
        ObservationField.ARRIVAL, ObservationField.DEPARTURE,
    ) else ("value_json", "cutoff_estimated")
    rows = [row for row in _latest(conn, leg_id, field, as_of)
            if any(row[key] is not None for key in keys if key != "cutoff_estimated")]
    state, selected = _select(rows, as_of, policy.stale_seconds, *keys)
    observations = tuple(map(_observation, selected))
    return FieldEvidence(
        leg_id=leg_id, field=field, state=state,
        observation=observations[0] if state in (ObservationState.FRESH, ObservationState.STALE) else None,
        alternatives=observations if state is ObservationState.CONFLICT else (),
    )


def get_leg(conn: sqlite3.Connection, leg_id: str) -> FlightLeg | None:
    row = conn.execute("SELECT * FROM flight_legs WHERE id = ?", (leg_id,)).fetchone()
    return _leg(conn, row) if row is not None else None


def _leg(conn: sqlite3.Connection, row: sqlite3.Row) -> FlightLeg:
    aliases = conn.execute(
        "SELECT alias FROM flight_aliases WHERE leg_id = ? ORDER BY alias", (row["id"],),
    )
    return FlightLeg(**dict(row), aliases=tuple(alias[0] for alias in aliases))


def resolve_flight(
    conn: sqlite3.Connection, text: str, airport: str, as_of: datetime,
    selected_date: date | None = None,
) -> FlightLeg | ToolError:
    rows = conn.execute("SELECT * FROM flight_legs WHERE id = ?", (text,)).fetchall()
    code = text.strip(" \t\r\n")
    if not rows and code.isascii():
        rows = conn.execute("""
            SELECT l.* FROM flight_legs AS l
            WHERE (l.operating_carrier || l.operating_number) = ? COLLATE NOCASE
                OR EXISTS (SELECT 1 FROM flight_aliases AS a
                           WHERE a.leg_id = l.id AND a.alias = ? COLLATE NOCASE)
            ORDER BY l.scheduled_operation_date, l.id
        """, (code, code)).fetchall()
    scoped = [row for row in rows if airport in (row["origin"], row["destination"])]
    if not scoped:
        return ToolError(error_class=ToolErrorClass.MISSING,
                         diagnostic=ReasonCode.WRONG_AIRPORT if rows else ReasonCode.FLIGHT_UNKNOWN)
    dates = sorted({date.fromisoformat(row["scheduled_operation_date"]) for row in scoped})
    local_date = in_dubai(as_of).date()
    if (selected_date is None and len(dates) > 1
            or selected_date is not None and selected_date not in dates):
        dates.sort(key=lambda day: (abs(day - local_date), day))
        return ToolError(
            error_class=ToolErrorClass.AMBIGUOUS, diagnostic=ReasonCode.AMBIGUOUS_DATE,
            permitted_choices=tuple(PermittedChoice(
                id=day.isoformat(), label=I18nText(key="flight.date", params={"date": day.isoformat()}),
            ) for day in dates),
        )
    matches = [row for row in scoped
               if selected_date is None or row["scheduled_operation_date"] == selected_date.isoformat()]
    if len(matches) == 1:
        return _leg(conn, matches[0])
    return ToolError(
        error_class=ToolErrorClass.AMBIGUOUS, diagnostic=ReasonCode.AMBIGUOUS_FLIGHT,
        permitted_choices=tuple(PermittedChoice(
            id=row["id"], label=I18nText(key="flight.choice", params={
                "code": row["operating_carrier"] + row["operating_number"],
                "date": row["scheduled_operation_date"],
                "origin": row["origin"], "destination": row["destination"],
            }),
        ) for row in matches),
    )


def flight_board(
    conn: sqlite3.Connection, request: GetFlightsRequest, as_of: datetime, policy: PolicyPack,
) -> GetFlightsResponse:
    departures = request.direction is FlightDirection.DEPARTURES
    endpoint = "origin" if departures else "destination"
    field = ObservationField.DEPARTURE if departures else ObservationField.ARRIVAL
    start, end = timestamp(request.window.start), timestamp(request.window.end)
    query = (request.query or "").strip().casefold()
    rows = conn.execute(f"""
        SELECT l.* FROM flight_legs AS l
        WHERE l.{endpoint} = ? AND EXISTS (
            SELECT 1 FROM observations AS o WHERE o.leg_id = l.id AND o.field = ?
            AND o.scheduled >= ? AND o.scheduled < ?
        )
    """, (request.airport, field, start, end))
    eligible = []
    for row in rows:
        leg = _leg(conn, row)
        codes = (leg.operating_carrier + leg.operating_number, leg.destination, *leg.aliases)
        if not departures:
            codes += (leg.origin,)
        if query and not any(query in term.casefold() for term in codes):
            continue
        schedules = _latest(conn, leg.id, field, as_of, scheduled=True)
        _, selected = _select(schedules, as_of, policy.stale_seconds, "scheduled")
        instants = {item["scheduled"] for item in selected}
        if len(instants) == 1 and start <= (scheduled := instants.pop()) < end:
            eligible.append((scheduled, leg.id, leg))
    eligible.sort()
    offset = 0
    if request.cursor is not None:
        offset = next((i + 1 for i, (_, leg_id, _) in enumerate(eligible)
                       if leg_id == request.cursor), len(eligible))
    page = eligible[offset:offset + request.limit]
    return GetFlightsResponse(
        as_of=as_of,
        flights=tuple(FlightBoardItem(
            leg=leg, facts=tuple(current_field(conn, leg.id, fact, as_of, policy)
                                 for fact in ObservationField),
        ) for _, _, leg in page),
        next_cursor=page[-1][1] if offset + len(page) < len(eligible) else None,
    )
