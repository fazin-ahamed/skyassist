from contextlib import closing
from datetime import date, timedelta

import pytest

from skyassist.clock import SimClock, in_dubai
from skyassist.db import connect, init_db, timestamp
from skyassist.evidence import current_field, flight_board, ingest, resolve_flight
from skyassist.models import (
    FlightLeg, GetFlightsRequest, Observation, ObservationState, PolicyPack, ReasonCode,
    TimeWindow, ToolError,
)

NOW = SimClock().now()
POLICY = PolicyPack()
DAY = date(2026, 11, 19)


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "test.sqlite3"
    init_db(path)
    with closing(connect(path)) as connection:
        yield connection


def add_leg(conn, leg_id="out", *, day=DAY, number="402", origin="DXB", destination="AUH", aliases=()):
    leg = FlightLeg(
        id=leg_id, operating_carrier="DX", operating_number=number,
        scheduled_operation_date=day, origin=origin, destination=destination,
        aliases=aliases, provider_leg_id=f"feed-{leg_id}",
    )
    conn.execute("""
        INSERT INTO flight_legs (id, operating_carrier, operating_number, scheduled_operation_date,
                                 origin, destination, provider_leg_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (leg.id, leg.operating_carrier, leg.operating_number, day.isoformat(),
          origin, destination, leg.provider_leg_id))
    conn.executemany("INSERT INTO flight_aliases VALUES (?, ?)", ((alias, leg_id) for alias in aliases))
    return leg


def fact(leg_id="out", field="gate", value="B14", **kwargs):
    return Observation.model_validate(dict(
        leg_id=leg_id, field=field, value=value, **({
            "source": "airport", "authority_rank": 1, "observed_at": NOW,
            "received_at": NOW, "valid_until": NOW + timedelta(hours=1),
        } | kwargs),
    ))


def test_idempotent_ingest_and_age_boundary(conn):
    add_leg(conn)
    original = fact(observed_at=NOW - timedelta(seconds=30))
    ingest(conn, original)
    ingest(conn, original)
    assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
    for age, state in ((-1, ObservationState.UNKNOWN), (89, ObservationState.FRESH),
                       (90, ObservationState.STALE), (105, ObservationState.STALE)):
        cached = current_field(conn, "out", "gate", NOW + timedelta(seconds=age), POLICY)
        assert cached.state is state
        assert cached.observation == (None if age < 0 else original)


def test_valid_until_boundary_and_future_source(conn):
    add_leg(conn)
    expiry = NOW + timedelta(seconds=10)
    original = fact(valid_until=expiry)
    ingest(conn, original)
    ingest(conn, fact(value="B18", observed_at=NOW + timedelta(minutes=1)))
    assert current_field(conn, "out", "gate", expiry, POLICY).state is ObservationState.FRESH
    expired = current_field(conn, "out", "gate", expiry + timedelta(microseconds=1), POLICY)
    assert expired.state is ObservationState.STALE
    assert expired.observation == original


def test_conflict_higher_authority_and_per_field_precedence(conn):
    add_leg(conn)
    old = fact()
    newer = fact(value="B18", source="airline", observed_at=NOW + timedelta(seconds=4))
    lower = fact(value="B27", source="tracker", authority_rank=0)
    terminal = fact(field="terminal", value="2", source="tracker", authority_rank=3)
    for observation in (old, newer, lower, terminal):
        ingest(conn, observation)
    later = NOW + timedelta(seconds=5)
    conflict = current_field(conn, "out", "gate", later, POLICY)
    assert conflict.state is ObservationState.CONFLICT
    assert conflict.observation is None
    assert set(item.value for item in conflict.alternatives) == {"B14", "B18"}
    assert current_field(conn, "out", "terminal", later, POLICY).observation == terminal
    winner = fact(value="B20", source="operations", authority_rank=2, observed_at=later, received_at=later)
    ingest(conn, winner)
    assert current_field(conn, "out", "gate", later, POLICY).observation == winner


def test_agreeing_sources_and_same_source_update(conn):
    add_leg(conn)
    first = fact()
    newest = fact(source="airline", observed_at=NOW + timedelta(seconds=2))
    ingest(conn, first)
    ingest(conn, newest)
    later = NOW + timedelta(seconds=3)
    assert current_field(conn, "out", "gate", later, POLICY).observation == newest
    updated = fact(source="airport", value="B18", observed_at=later)
    ingest(conn, updated)
    assert current_field(conn, "out", "gate", later, POLICY).state is ObservationState.CONFLICT
    ingest(conn, fact(source="airline", value="B18", observed_at=later))
    assert current_field(conn, "out", "gate", later, POLICY).state is ObservationState.FRESH


def test_withdrawal_masks_source_and_preserves_history(conn):
    add_leg(conn)
    original = fact()
    backup = fact(source="airline", value="B18", authority_rank=0)
    withdrawn = fact(value=None, withdrawn=True, received_at=NOW + timedelta(seconds=2))
    for obs in (original, withdrawn, backup, withdrawn, original):
        ingest(conn, obs)
    assert current_field(conn, "out", "gate", NOW, POLICY).observation == original
    later = NOW + timedelta(seconds=2)
    assert current_field(conn, "out", "gate", later, POLICY).observation == backup
    ingest(conn, fact(source="airline", value=None, withdrawn=True, received_at=later))
    assert current_field(conn, "out", "gate", later, POLICY).state is ObservationState.UNKNOWN
    assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 4


def test_cutoff_separate_from_departure_and_estimate_flag(conn):
    add_leg(conn)
    departure = NOW + timedelta(minutes=45)
    schedule = fact(field="departure", value=None, scheduled=departure, estimated=departure + timedelta(minutes=5))
    ingest(conn, schedule)
    assert current_field(conn, "out", "departure", NOW, POLICY).observation == schedule
    assert current_field(conn, "out", "boarding_cutoff", NOW, POLICY).state is ObservationState.UNKNOWN
    cutoff = fact(field="boarding_cutoff", value=NOW + timedelta(minutes=20), cutoff_estimated=True)
    ingest(conn, cutoff)
    result = current_field(conn, "out", "boarding_cutoff", NOW, POLICY)
    assert result.observation == cutoff
    assert result.observation.cutoff_estimated


def test_board_local_midnight_search_page_and_provenance(conn):
    as_of = SimClock("C09").now()
    next_day = DAY + timedelta(days=1)
    add_leg(conn, "before", day=DAY, number="201")
    add_leg(conn, "after", day=next_day, number="202", aliases=("XY123",))
    add_leg(conn, "end", day=next_day, number="203")
    for leg_id, minutes in (("before", 5), ("after", 15), ("end", 40)):
        ingest(conn, fact(leg_id, "departure", None, scheduled=as_of + timedelta(minutes=minutes)))
    gate = fact("after", value="B22", observed_at=as_of, received_at=as_of,
                valid_until=as_of + timedelta(minutes=1))
    ingest(conn, gate)
    request = GetFlightsRequest(
        airport="DXB", direction="departures", limit=1,
        window=TimeWindow(start="2026-11-19T23:50:00+04:00", end="2026-11-20T00:30:00+04:00"),
    )
    first = flight_board(conn, request, as_of, POLICY)
    assert first.as_of == as_of
    assert [item.leg.id for item in first.flights] == ["before"]
    second = flight_board(conn, request.model_copy(update={"cursor": first.next_cursor}), as_of, POLICY)
    assert [item.leg.id for item in second.flights] == ["after"]
    assert second.next_cursor is None
    facts = {item.field: item for item in second.flights[0].facts}
    assert facts["gate"].observation == gate
    assert facts["gate"].state is ObservationState.FRESH
    assert facts["departure"].state is ObservationState.STALE
    assert facts["departure"].observation.scheduled.date() == DAY
    assert in_dubai(facts["departure"].observation.scheduled).date() == next_day
    for query in (" DX202 ", "xy123"):
        searched = flight_board(conn, request.model_copy(update={"query": query}), as_of, POLICY)
        assert [item.leg.id for item in searched.flights] == ["after"]
    assert flight_board(conn, request.model_copy(update={"query": "auh"}), as_of, POLICY).next_cursor
    assert not flight_board(conn, request.model_copy(update={"query": "end"}), as_of, POLICY).flights


def test_arrival_board_keeps_schedule_after_estimate_only_update(conn):
    add_leg(conn, "in", origin="LHR", destination="DXB")
    arrival = NOW + timedelta(minutes=20)
    ingest(conn, fact("in", "arrival", None, scheduled=arrival))
    ingest(conn, fact("in", "arrival", None, estimated=arrival + timedelta(minutes=80),
                      observed_at=NOW + timedelta(seconds=1)))
    first = flight_board(conn, GetFlightsRequest(
        airport="DXB", direction="arrivals", query="LHR",
        window=TimeWindow(start=NOW, end=NOW + timedelta(hours=1)),
    ), NOW + timedelta(seconds=1), POLICY)
    assert [item.leg.id for item in first.flights] == ["in"]


def test_resolve_alias_codeshare_date_and_endpoints(conn):
    add_leg(conn, aliases=("DXB-402", "XY402", "ZZ402"))
    add_leg(conn, "tomorrow", day=DAY + timedelta(days=1), aliases=("XY402",))
    add_leg(conn, "same-date", number="104", destination="LHR", aliases=("ZZ402",))
    assert resolve_flight(conn, " dxb-402 ", "DXB", NOW).id == "out"
    assert resolve_flight(conn, "dx402", "DXB", NOW, DAY).id == "out"
    dates = resolve_flight(conn, "XY402", "DXB", NOW)
    assert isinstance(dates, ToolError) and dates.diagnostic is ReasonCode.AMBIGUOUS_DATE
    assert [choice.id for choice in dates.permitted_choices] == ["2026-11-19", "2026-11-20"]
    assert resolve_flight(conn, "XY402", "DXB", NOW, DAY).id == "out"
    ambiguous = resolve_flight(conn, "ZZ402", "DXB", NOW, DAY)
    assert ambiguous.diagnostic is ReasonCode.AMBIGUOUS_FLIGHT
    assert {choice.id for choice in ambiguous.permitted_choices} == {"out", "same-date"}
    assert resolve_flight(conn, "DXB-402", "LHR", NOW).diagnostic is ReasonCode.WRONG_AIRPORT
