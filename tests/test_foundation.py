import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from skyassist import models as m
from skyassist.clock import SimClock, SystemClock, in_dubai
from skyassist.config import CONFIG, PROFILES, Mode
from skyassist.db import connect, init_db, timestamp, transaction

NOW = SimClock("C01").now()
LATER = NOW + timedelta(minutes=35)
STAMP = m.SourceStamp(source="demo", observed_at=NOW)
LABEL = m.I18nText(key="landmark.c22")
LOCATION = m.Location(airport="DXB", node_id="c22", landmark=LABEL)
LEG = m.FlightLeg(
    id="out", operating_carrier="DX", operating_number="402",
    scheduled_operation_date=NOW.date(), origin="DXB", destination="AUH",
    aliases=("DXB-402", "DX402", "XX402"), provider_leg_id="provider-out",
)
GATE = m.Observation(
    leg_id=LEG.id, field="gate", value="B14", authority_rank=1,
    **STAMP.model_dump(), received_at=NOW, valid_until=LATER,
)
GATE_FACT = m.FieldEvidence(leg_id=LEG.id, field="gate", state="fresh", observation=GATE)
CUTOFF = m.Observation(
    leg_id=LEG.id, field="boarding_cutoff", value="2026-11-19T10:35:00+04:00", authority_rank=1,
    **STAMP.model_dump(), received_at=NOW, valid_until=LATER,
)
CASE = m.Case(
    id="c1", journey_id="j1", airport="DXB", service="wheelchair", state="pending_approval",
    request_revision=1, journey_revision=2, created_at=NOW, idempotency_key="case:c1",
    pickup=LOCATION, pickup_confirmed=True, consent=True, approval_expires_at=LATER,
)
DECISION = m.Decision(event_id="e1", case_id="c1", request_revision=1, decision="approve")
ROUTE = m.RouteResult(
    route_id="r1", revision=2, node_ids=("c22", "b14"), provenance=STAMP,
    steps=(m.RouteStep(number=1, floor="2", landmark=LABEL, accessible_mode="step_free",
                       text=m.I18nText(key="route.wait_at_pickup")),),
    floor_shapes=(m.FloorShape(terminal="1", concourse="C", floor="2",
                               points=(m.MapPoint(x=0, y=1), m.MapPoint(x=2, y=3))),),
    movement=m.SecondsRange(lower=420, upper=540), wait=m.SecondsRange(lower=180, upper=420),
    buffer=420,
)
TRANSFER = m.TransferResult(
    route=ROUTE, ready=m.SecondsRange(lower=240, upper=360), lower=1260, upper=1740,
    time_remaining=2100, margin=360, status=m.ConnectionStatus.TIGHT_CONNECTION,
    reason_codes=(m.ReasonCode.TIGHT_MARGIN, m.ReasonCode.STAFF_HANDOFF_PENDING),
)
AIRCRAFT = m.AircraftReport(
    matched_leg_id=LEG.id, aircraft_id="demo-icao",
    position=m.AircraftPosition(latitude=25.2, longitude=55.3), **STAMP.model_dump(),
)
ARRIVALS = m.ArrivalEstimates(
    baggage=m.ArrivalEstimate(applicability="applicable", state="fresh",
                              wait=m.SecondsRange(lower=60, upper=180), provenance=STAMP),
    security=m.ArrivalEstimate(applicability="not_applicable", state="unknown"),
)
VIEW = m.ViewModel(
    revision=2, as_of=NOW,
    gate=m.GateView(value="B14", terminal="1", concourse="B", level="2", state="fresh",
                    provenance=dict.fromkeys(("value", "terminal", "concourse", "level"), STAMP)),
    cutoff=m.CutoffView(value=LATER, unavailable=False, **STAMP.model_dump()),
    status=m.StatusView(status=TRANSFER.status, reason_codes=TRANSFER.reason_codes, provenance=STAMP),
    transfer=m.TransferView.from_result(TRANSFER, provenance=STAMP),
    route=m.RouteView.model_validate(ROUTE.model_dump(include=set(m.RouteView.model_fields))),
    assistance=m.AssistanceView(
        state=CASE.state, pickup_landmark=LABEL, case_ref=CASE.id,
        stage=m.I18nText(key="assistance.pending_approval"), provenance=STAMP,
        staffed_alternative=m.I18nText(key="action.seek_staff"),
        approval_window=m.TimeWindow(start=NOW, end=LATER),
    ),
    next_action=m.NextActionView(action="wait_for_handoff", text=m.I18nText(key="action.wait_for_handoff")),
    banners=(m.BannerView(kind="route_changed", text=m.I18nText(key="banner.route_changed")),),
    qr=m.QRView(suppressed_reason="STAFF_HANDOFF_PENDING"), arrivals=ARRIVALS,
    aircraft=m.AircraftView.from_report(AIRCRAFT, as_of=NOW, policy=m.PolicyPack()),
    i18n={"transfer": m.I18nText(key="transfer.total_with_readiness", params={"lower": 21, "upper": 29})},
)


def assert_roundtrips(*models: m.Contract) -> None:
    for model in models:
        assert type(model).model_validate_json(model.model_dump_json()) == model


def test_domain_roundtrips():
    node = m.Node(
        id="c22", airport="DXB", terminal="1", concourse="C", floor="2",
        security_zone="airside", landmark=LABEL, x=0, y=1, pickup_eligible=True,
    )
    edge = m.Edge(
        id="lift", from_node_id=node.id, to_node_id="b14", distance_m=12,
        allowed_flows=("connecting",), accessible=True, lift=True, mode="lift",
    )
    assert_roundtrips(
        LEG, GATE, CUTOFF, GATE_FACT, LOCATION, node, edge, m.PolicyPack(), ROUTE, TRANSFER,
        m.Overlay(id="lift-close", airport="DXB", edge_id=edge.id, closed=True,
                  valid_until=LATER, **STAMP.model_dump()),
        m.QueueObservation(airport="DXB", queue_id="train", flow="connecting",
                           valid_until=LATER, wait=m.SecondsRange(lower=60, upper=None),
                           **STAMP.model_dump()),
        m.AmenityObservation(airport="DXB", node_id=node.id, open=True,
                             valid_until=LATER, **STAMP.model_dump()),
        m.Journey(id="j1", airport="DXB", created_at=NOW, outbound_leg_id=LEG.id),
        m.JourneyRevision(journey_id="j1", revision=2, created_at=NOW, policy_version="demo-v1", view=VIEW),
        CASE, DECISION,
        m.OutboxItem(id="o1", case_id="c1", request_revision=1, effect="mobility_dispatch",
                     idempotency_key="dispatch:c1", due_at=NOW),
        m.AuditRecord(event_id="e1", occurred_at=NOW, action="staff_decision", outcome="applied"),
        VIEW,
    )
    assert CUTOFF.value == LATER and CUTOFF.value.tzinfo is UTC
    assert list(m.PolicyPack().buffer_floors.values()) == [180, 300, 420, 420]
    assert m.ConnectionFlag.CUTOFF_UNAVAILABLE.value == "Boarding cutoff unavailable"
    assert [status.value for status in m.ConnectionStatus] == [
        "Time available", "Tight connection", "Transfer unlikely before cutoff",
        "May miss boarding", "Boarding cutoff passed", "Timing unverified",
    ]


def test_fixture_roundtrips():
    node = m.Node(
        id="c22", airport="DXB", terminal="1", concourse="C", floor="2",
        security_zone="airside", landmark=LABEL, x=0, y=1,
    )
    graph = m.FixtureGraph(airport="DXB", graph_version="c01-v1", nodes=(node,), edges=())
    midnight_leg = m.FlightLegFixture(
        **(LEG.model_dump() | {"scheduled_operation_date": "2026-11-20"}),
        codeshare_aliases=("XX402",), scheduled_departure="2026-11-20T00:28:00+04:00",
        scheduled_arrival="2026-11-20T01:28:00+04:00",
    )
    readiness = m.ReadinessEvent(leg_id=LEG.id, state="parked", ready=TRANSFER.ready, **STAMP.model_dump())
    stream = (
        m.EventStreamItem(id="g1", timestamp=NOW, event_type="gate_changed", payload=GATE),
        m.EventStreamItem(id="r1", timestamp=NOW, event_type="flight_updated", payload=readiness),
        m.EventStreamItem(id=DECISION.event_id, timestamp=NOW, event_type="coordinator_decided",
                          payload=DECISION),
    )
    assert_roundtrips(
        graph, midnight_leg, *stream,
        m.LandmarkAliasFixture(airport="DXB", node_id=node.id, alias="C22", source="demo"),
        m.ScenarioFixture(
            id="C01", clock_start="C01", profile="family_mobility",
            party_needs=m.PartyNeeds(party_size=3, children=1, step_free=True, wheelchair=True),
            event_sequence=tuple(item.id for item in stream), expected_route_node_ids=ROUTE.node_ids,
            expected_transfer_lower_seconds=1260, expected_transfer_upper_seconds=1740,
            expected_status=TRANSFER.status, expected_margin_seconds=360,
            allowed_outbound_effects=("coordinator_notification", "mobility_dispatch"),
            required_passenger_action="wait_for_handoff",
        ),
        m.AirportMeta(
            airport="DXB", name=m.I18nText(key="airport.dxb"),
            centroid=m.AircraftPosition(latitude=25.2, longitude=55.3),
            source="demo", source_revision="1",
        ),
    )
    assert midnight_leg.scheduled_departure.date().isoformat() == "2026-11-19"
    assert in_dubai(midnight_leg.scheduled_departure).date() == midnight_leg.scheduled_operation_date
    with pytest.raises(ValidationError, match="graph nodes"):
        m.FixtureGraph(
            airport="DXB", graph_version="bad", nodes=(node,),
            edges=(m.Edge(id="bad", from_node_id=node.id, to_node_id="missing", distance_m=1,
                          allowed_flows=("connecting",), accessible=True),),
        )
    with pytest.raises(ValidationError, match="gate observation"):
        m.EventStreamItem(id="bad", timestamp=NOW, event_type="gate_changed", payload=CUTOFF)


def test_api_and_tool_roundtrips():
    assistance = m.AssistanceResponse(case_ref=CASE.id, state=CASE.state, request_revision=1,
                                      journey_revision=2)
    decision = m.DecisionResponse(event_id="e1", case_id="c1", request_revision=1,
                                  state="approved", outcome="applied")
    flight_status = m.GetFlightAndGateStatusOutput(legs=(LEG,), facts=(GATE_FACT,))
    assert_roundtrips(
        m.GetFlightsRequest(airport="DXB", direction="departures", window=m.TimeWindow(start=NOW, end=LATER)),
        m.GetFlightsResponse(as_of=NOW, flights=(m.FlightBoardItem(leg=LEG, facts=(GATE_FACT,)),)),
        m.CreateJourneyRequest(airport="DXB"), m.CreateJourneyResponse(journey_id="j1", revision=0),
        m.JourneyInputRequest(journey_id="j1", client_input_id="i1", intent="check_connection"),
        m.JourneyInputResponse(journey_id="j1", event_id="e1", revision=2),
        m.GetJourneyRequest(journey_id="j1", known_revision=2),
        m.GetJourneyResponse(revision=2, unchanged=True),
        m.GetJourneyResponse(revision=2, unchanged=False, view=VIEW),
        m.AssistanceRequest(journey_id="j1", client_input_id="i1", journey_revision=2, service="wheelchair",
                            pickup_node_id="c22", pickup_confirmed=True, consent=True),
        assistance,
        m.GetStaffCasesRequest(airport="DXB"),
        m.GetStaffCasesResponse(as_of=NOW, cases=(m.StaffCaseItem(case=CASE, gate=VIEW.gate, cutoff=VIEW.cutoff),)),
        m.DecisionRequest(**DECISION.model_dump()), decision,
        m.DispatchStatusRequest(event_id="e2", case_id="c1", request_revision=1,
                                status="dispatched", evidence=STAMP),
        m.DispatchStatusResponse(**(decision.model_dump() | {"event_id": "e2", "state": "dispatched"})),
        m.GetRouteRequest(token="demo-token"), m.GetRouteResponse(route=VIEW.route),
        m.GetRouteResponse(reason_code="ROUTE_REVOKED"),
        m.GetFlightAndGateStatusInput(refs=(LEG.id,), airport="DXB", as_of=NOW), flight_status,
        m.CalculateTerminalTransferTimeInput(origin_id="c22", gate_id="b14", profile="family_mobility", as_of=NOW),
        m.RequestMobilityDispatchAsyncInput(case=CASE),
        m.CallAssistanceInput(location=LOCATION, case_id="c1", request_revision=1, idempotency_key="dispatch:c1"),
        m.CallAssistanceOutput(accepted=True), m.ReceiveAssistanceDecisionInput(decision=DECISION),
        m.GetArrivalEstimatesInput(arrival=LEG, airport="AUH", journey_type="exiting"), ARRIVALS,
        m.GetAircraftPositionInput(leg=LEG), m.GetAircraftPositionOutput(report=AIRCRAFT, freshness="current"),
    )
    tool_result = TypeAdapter(m.ToolResult[m.GetFlightAndGateStatusOutput])
    for result in (
        m.Success[m.GetFlightAndGateStatusOutput](value=flight_status),
        m.ToolError(error_class="ambiguous", diagnostic="AMBIGUOUS_FLIGHT",
                    permitted_choices=(m.PermittedChoice(id=LEG.id, label=m.I18nText(key="flight.option")),)),
    ):
        assert tool_result.validate_json(tool_result.dump_json(result)) == result
    error = tool_result.validate_json(
        b'{"kind":"error","class":"stale","diagnostic":"GATE_STALE"}'
    )
    assert json.loads(error.model_dump_json())["class"] == "stale"
    assert error.diagnostic.i18n_key == "reason.GATE_STALE"


@pytest.mark.parametrize("naive", [datetime(2026, 11, 19, 6), "2026-11-19T06:00:00"])
def test_naive_datetime_rejected(naive):
    with pytest.raises(ValidationError, match="timezone"):
        m.SourceStamp(source="demo", observed_at=naive)
    with pytest.raises(ValidationError, match="timezone"):
        m.Observation.model_validate(CUTOFF.model_dump() | {"value": naive})


def test_closed_models_and_boundary_values():
    with pytest.raises(ValidationError, match="extra"):
        m.SecondsRange(lower=0, upper=1, minutes=1)
    with pytest.raises(ValidationError, match="frozen"):
        TRANSFER.lower = 99
    for bad_seconds in (-1, 1.5, "1", True):
        with pytest.raises(ValidationError):
            m.SecondsRange(lower=bad_seconds, upper=None)
    for epoch in (1760000000, "1760000000"):
        with pytest.raises(ValidationError):
            m.SourceStamp(source="demo", observed_at=epoch)
    with pytest.raises(ValidationError, match="expected true"):
        m.AssistanceRequest(journey_id="j1", client_input_id="i1", journey_revision=2,
                            service="wheelchair", pickup_node_id="c22", pickup_confirmed=1, consent=True)
    with pytest.raises(ValidationError, match="provenance"):
        m.GateView(value="B14", state="fresh")
    with pytest.raises(ValidationError):
        m.AircraftPosition(latitude=91, longitude=55)
    with pytest.raises(ValidationError, match="omit the view"):
        m.GetJourneyResponse(revision=2, unchanged=True, view=VIEW)


def test_view_projection_and_documented_example():
    transfer = m.TransferView.from_result(
        TRANSFER.model_copy(update={"lower": 1261, "upper": 1741, "margin": -1}), provenance=STAMP
    )
    assert (transfer.lower_min, transfer.upper_min, transfer.margin_min) == (21, 30, -1)
    assert transfer.includes_readiness and transfer.conditional
    policy = m.PolicyPack()
    for age, freshness, visible in (
        (60, m.AircraftFreshness.CURRENT, True),
        (61, m.AircraftFreshness.LAST_REPORTED, True),
        (180, m.AircraftFreshness.HIDDEN, False),
    ):
        aircraft = m.AircraftView.from_report(AIRCRAFT, as_of=NOW + timedelta(seconds=age), policy=policy)
        assert aircraft.freshness is freshness
        assert (aircraft.position is not None) is visible
    docs = (Path(__file__).resolve().parents[1] / "docs" / "CONTRACTS.md").read_text()
    example = m.ViewModel.model_validate_json(docs.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert in_dubai(example.as_of).strftime("%H:%M") == "10:00"
    assert (example.transfer.lower_min, example.transfer.upper_min, example.transfer.margin_min) == (21, 29, 6)
    assert example.transfer.includes_readiness and example.transfer.conditional
    assert example.status.status is m.ConnectionStatus.TIGHT_CONNECTION
    assert example.assistance.state is m.CaseState.PENDING_APPROVAL


def test_clock():
    clock = SimClock(starts={"later": LATER})
    assert clock.now() == NOW
    assert clock.advance(20) == NOW + timedelta(seconds=20)
    assert clock.set("later") == LATER
    assert clock.set("C09") == datetime(2026, 11, 19, 19, 50, tzinfo=UTC)
    assert in_dubai(clock.now()).hour == 23
    assert SystemClock().now().tzinfo is UTC
    with pytest.raises(ValueError, match="timezone-aware"):
        clock.set(datetime(2026, 11, 19))
    with pytest.raises(ValueError, match="non-negative"):
        clock.advance(-1)
    assert timestamp(NOW) < timestamp(NOW + timedelta(microseconds=1))


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "foundation.sqlite3"
    init_db(path, dataset_version="fixtures-v1")
    return path


def test_db_init_pragmas_and_transaction(db_path):
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert dict(conn.execute("SELECT key, value FROM meta")) == {
            "mode": CONFIG.mode, "dataset_version": "fixtures-v1",
        }
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"), transaction(conn):
            conn.execute("INSERT INTO meta VALUES ('rolled_back', 'yes')")
            conn.execute("INSERT INTO flight_aliases VALUES ('DX402', 'missing')")
        assert conn.execute("SELECT value FROM meta WHERE key = 'rolled_back'").fetchone() is None


def test_db_event_idempotency_and_equivalent_open_cases(db_path):
    now = timestamp(NOW)
    insert_case = """
        INSERT INTO cases (id, journey_id, airport, service, state, request_revision,
                           journey_revision, created_at, idempotency_key)
        VALUES (?, 'j1', 'DXB', ?, ?, 1, 0, ?, ?)
    """
    insert_outbox = """
        INSERT INTO outbox (id, case_id, request_revision, effect, due_at, idempotency_key)
        VALUES (?, 'c1', 1, 'mobility_dispatch', ?, 'dispatch:c1')
    """
    with closing(connect(db_path)) as conn, transaction(conn):
        conn.execute("INSERT INTO journeys (id, airport, profile, created_at) VALUES ('j1', 'DXB', 'standard', ?)", (now,))
        conn.execute(
            "INSERT INTO events (id, journey_id, client_input_id, event_type, occurred_at) "
            "VALUES ('e1', 'j1', 'i1', 'passenger_input', ?)", (now,),
        )
        with pytest.raises(sqlite3.IntegrityError, match=r"events\.id"):
            conn.execute("INSERT INTO events (id, event_type, occurred_at) VALUES ('e1', 'refresh_due', ?)", (now,))
        with pytest.raises(sqlite3.IntegrityError, match=r"events\.journey_id"):
            conn.execute(
                "INSERT INTO events (id, journey_id, client_input_id, event_type, occurred_at) "
                "VALUES ('e2', 'j1', 'i1', 'passenger_input', ?)", (now,),
            )
        conn.execute(insert_case, ("c1", "wheelchair", "pending_approval", now, "case:c1"))
        with pytest.raises(sqlite3.IntegrityError, match="equivalent_open_case"):
            conn.execute(insert_case, ("c2", "wheelchair", "approved", now, "case:c2"))
        with pytest.raises(sqlite3.IntegrityError, match=r"cases\.idempotency_key"):
            conn.execute(insert_case, ("c2", "mobility_escort", "approved", now, "case:c1"))
        conn.execute(insert_case, ("escort", "mobility_escort", "pending_approval", now, "case:escort"))
        conn.execute("UPDATE cases SET state = 'completed' WHERE id = 'c1'")
        conn.execute(insert_case, ("c2", "wheelchair", "pending_approval", now, "case:c2"))
        conn.execute(insert_outbox, ("o1", now))
        with pytest.raises(sqlite3.IntegrityError, match=r"outbox\.idempotency_key"):
            conn.execute(insert_outbox, ("o2", now))


def test_audit_is_append_only(db_path):
    now = timestamp(NOW)
    with closing(connect(db_path)) as conn:
        conn.execute("INSERT INTO events (id, event_type, occurred_at) VALUES ('e1', 'refresh_due', ?)", (now,))
        conn.execute(
            "INSERT INTO audit (id, event_id, occurred_at, action, outcome) VALUES (1, 'e1', ?, 'read', 'ok')",
            (now,),
        )
        for sql in (
            "UPDATE audit SET outcome = 'changed'",
            "DELETE FROM audit",
            "INSERT OR REPLACE INTO audit (id, event_id, occurred_at, action, outcome) "
            "VALUES (1, 'e1', '', 'read', 'changed')",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(sql)
        assert conn.execute("SELECT outcome FROM audit").fetchone()[0] == "ok"


def test_mode_stamp_mismatch(db_path, monkeypatch):
    other = Mode.LIVE if CONFIG.mode is Mode.DEMO else Mode.DEMO
    assert PROFILES[Mode.DEMO].db_path != PROFILES[Mode.LIVE].db_path
    monkeypatch.setenv("SKYASSIST_MODE", other)
    with closing(connect(db_path)) as conn:
        conn.execute("UPDATE meta SET value = ? WHERE key = 'mode'", (other,))
    with pytest.raises(ValueError, match="refuses database stamped"):
        connect(db_path)
    with pytest.raises(ValueError, match="refuses database stamped"):
        init_db(db_path)


def test_schema_export(tmp_path):
    expected = {
        "fixture_graph": m.FixtureGraph, "flight_leg_fixture": m.FlightLegFixture,
        "event_stream_item": m.EventStreamItem, "landmark_alias_fixture": m.LandmarkAliasFixture,
        "scenario_fixture": m.ScenarioFixture, "airport_meta": m.AirportMeta,
        "policy_pack": m.PolicyPack,
    }
    paths = m.export_schemas(tmp_path / "schema")
    assert {path.name for path in paths} == {f"{name}.schema.json" for name in expected}
    for name, model in expected.items():
        path = tmp_path / "schema" / f"{name}.schema.json"
        assert json.loads(path.read_text()) == {
            "$schema": "https://json-schema.org/draft/2020-12/schema", **model.model_json_schema(),
        }
