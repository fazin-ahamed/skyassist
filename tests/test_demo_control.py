from contextlib import closing
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from skyassist import clock as clock_module, service, service_read
from skyassist.agent import JourneyAgent
from skyassist.clock import DEMO_STARTS, ReplayClock, SimClock, SystemClock
from skyassist.config import Config, Mode
from skyassist.db import connect, init_db, timestamp
from skyassist.evidence import current_field, ingest
from skyassist.models import Edge, Observation, ObservationField, ObservationState, ReadinessEvent, SecondsRange
from skyassist.seed import build_demo_db, load
from skyassist.service import DEMO_COORDINATOR_TOKEN, DEMO_INTEGRATION_TOKEN
from skyassist.service_read import create_integrated_app

from test_agent import await_view, create, submit

COORDINATOR = {"Authorization": f"Bearer {DEMO_COORDINATOR_TOKEN}"}
GATE = "ev-c02-gate-b18"
APPLY_GATE = f"/demo/events/{GATE}/apply"
C02_OUTBOUND = "zx730-1119-out"


@pytest.fixture
def demo(tmp_path):
    config = Config(Mode.DEMO, tmp_path / "demo.sqlite3")
    init_db(config.db_path)
    clock = SimClock()
    agent = JourneyAgent(clock, config)
    return clock, config, agent


def test_auth_rejects_without_side_effects(demo):
    clock, config, agent = demo
    with TestClient(create_integrated_app(clock, config, agent=agent)) as client:
        _, passenger = create(client)
        for headers in ({}, {"Authorization": "Bearer wrong"}, passenger,
                        {"Authorization": f"Bearer {DEMO_INTEGRATION_TOKEN}"}):
            for response in (client.get("/demo/state", headers=headers),
                             client.post("/demo/clock", headers=headers, json={"advance_seconds": 10}),
                             client.post(APPLY_GATE, headers=headers)):
                assert response.status_code == 403
                assert response.json()["detail"]["reason_code"] == "WRONG_ROLE"
        assert clock.now() == DEMO_STARTS["C01"]
    with closing(connect(config.db_path)) as conn:
        assert conn.execute("SELECT count(*) FROM events WHERE id=? OR event_type='demo_clock'", (GATE,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM audit WHERE action LIKE 'demo_%'").fetchone()[0] == 0


def test_live_has_no_demo_routes(monkeypatch, tmp_path):
    config = Config(Mode.LIVE, tmp_path / "live.sqlite3")
    monkeypatch.setattr(service, "CONFIG", config)
    monkeypatch.setattr(service_read, "CONFIG", config)
    monkeypatch.setenv("SKYASSIST_COORDINATOR_TOKEN", "live-test-coordinator")
    monkeypatch.setenv("SKYASSIST_INTEGRATION_TOKEN", "live-test-integration")
    monkeypatch.setenv("SKYASSIST_CLOCK_START", "not-a-start")
    monkeypatch.setenv("SKYASSIST_CLOCK_SPEED", "not-a-speed")
    app = service_read.app_from_env()
    assert isinstance(app.state.journey_agent.clock, SystemClock)
    assert not any(path.startswith("/demo") for path in app.openapi()["paths"])
    client = TestClient(app)
    assert client.get("/demo/state").status_code == 404
    assert client.get("/clock").status_code == 200


def test_demo_env_clock(monkeypatch):
    monkeypatch.setenv("SKYASSIST_CLOCK_START", "C02")
    monkeypatch.setenv("SKYASSIST_CLOCK_SPEED", "0")
    app = service_read.app_from_env()
    assert isinstance(app.state.journey_agent.clock, ReplayClock)
    assert app.state.journey_agent.clock.now() == DEMO_STARTS["C01"]


def test_replay_clock_rate_and_moves(monkeypatch):
    tick = 100.0
    monkeypatch.setattr(clock_module, "monotonic", lambda: tick)
    start = datetime.fromisoformat("2026-11-19T12:00:00+04:00")
    clock = ReplayClock("custom", speed=2.5, starts={"custom": start})
    tick += 2
    assert clock.now() == start + timedelta(seconds=5)
    assert clock.advance(5) == start + timedelta(seconds=10)
    tick += 2
    assert clock.now() == start + timedelta(seconds=15)
    assert clock.set("2026-11-19T06:30:00Z") == datetime.fromisoformat("2026-11-19T06:30:00Z")
    assert clock.set("C09") == DEMO_STARTS["C09"]
    with pytest.raises(ValueError):
        clock.advance(True)
    with pytest.raises(ValueError):
        ReplayClock("C01", speed=float("nan"))


def test_state_and_clock_moves(demo):
    clock, config, agent = demo
    with TestClient(create_integrated_app(clock, config, agent=agent), headers=COORDINATOR) as client:
        response = client.get("/demo/state")
        assert response.headers["Cache-Control"] == "no-store"
        state = response.raise_for_status().json()
        assert set(state["clock_starts"]) == {"demo", *(f"C{index:02d}" for index in range(1, 11))}
        assert {event["kind"] for event in state["events"]} == {
            "flight_updated", "gate_changed", "facility_changed", "refresh_due",
        }
        assert next(event for event in state["events"] if event["id"] == GATE) == {
            "id": GATE, "kind": "gate_changed", "label": "Change gate to B18", "scenario_ids": ["C02"], "applied": False,
        }
        for event_id in ("ev-c01-assist", "ev-c01-approve", "ev-c01-dispatched", "unknown"):
            assert client.post(f"/demo/events/{event_id}/apply").status_code == 404
        for body in ({}, {"name": "missing"}, {"name": "C01", "set": None}, {"set": "2026-11-19"},
                     {"advance_seconds": -1}, {"advance_seconds": True}, {"advance_seconds": 1.5}):
            assert client.post("/demo/clock", json=body).status_code == 422
        assert clock.now() == DEMO_STARTS["C01"]
        for body, expected in (
            ({"name": "C02"}, "2026-11-19T06:00:00Z"),
            ({"name": "C09"}, "2026-11-19T19:50:00Z"),
            ({"set": "2026-11-19T10:15:00+04:00"}, "2026-11-19T06:15:00Z"),
            ({"advance_seconds": 15}, "2026-11-19T06:15:15Z"),
        ):
            response = client.post("/demo/clock", json=body).raise_for_status()
            assert datetime.fromisoformat(response.json()["now"]) == datetime.fromisoformat(expected)
            assert client.get("/clock").json() == response.json()
    with closing(connect(config.db_path)) as conn:
        rows = conn.execute("SELECT outcome, role FROM audit WHERE action='demo_clock'").fetchall()
        assert [tuple(row) for row in rows] == [("applied", "coordinator")] * 4


def test_system_clock_cannot_be_moved(demo):
    _, config, _ = demo
    client = TestClient(create_integrated_app(SystemClock(), config), headers=COORDINATOR)
    assert client.post("/demo/clock", json={"name": "C01"}).status_code == 422


def test_gate_apply_revokes_route_and_replays_first_outcome(demo):
    clock, config, agent = demo
    clock.advance(120)
    with closing(connect(config.db_path)) as conn:
        conn.executemany("INSERT INTO flight_legs VALUES (?, ?, ?, '2026-11-19', ?, ?, NULL)", (
            ("in", "EK", "101", "AUH", "DXB"), (C02_OUTBOUND, "ZX", "730", "DXB", "SIN"),
        ))
        for index, node in enumerate(("c22", "b14", "b18")):
            conn.execute("INSERT INTO nodes VALUES (?, 'DXB', '1', ?, '2', 'airside', ?, ?, 0, 1, ?)",
                         (node, node[0].upper(), f'{{"key":"demo.landmark.{node}"}}', index, node == "c22"))
        for gate in ("b14", "b18"):
            edge = Edge(id=gate, from_node_id="c22", to_node_id=gate, distance_m=0,
                        allowed_flows=("connecting",), accessible=True, mode="step_free",
                        fixed_movement=SecondsRange(lower=420, upper=540), wait=SecondsRange(lower=180, upper=420))
            conn.execute("INSERT INTO edges VALUES (?, 'c22', ?, ?)", (gate, gate, edge.model_dump_json()))
        for field, value in (("gate", "B14"), ("terminal", "1"), ("concourse", "B"), ("level", "2"),
                             ("boarding_cutoff", clock.now() + timedelta(minutes=35))):
            ingest(conn, Observation(leg_id=C02_OUTBOUND, field=field, value=value, source="ops-desk",
                                     authority_rank=0, observed_at=clock.now(), received_at=clock.now(),
                                     valid_until=clock.now() + timedelta(minutes=5)))

    with TestClient(create_integrated_app(clock, config, agent=agent)) as client:
        session = create(client)
        submit(client, session, flight_aliases=["EK101", "ZX730"])
        original = await_view(client, session, lambda current: current["qr"]["token"] is not None)
        token = original["qr"]["token"]
        assert original["gate"]["value"] == "B14"
        assert client.get(f"/route/{token}").json()["route"]["steps"]
        clock.advance(1)
        applied_at = clock.now()
        applied = client.post(APPLY_GATE, headers=COORDINATOR).raise_for_status().json()
        assert applied["applied"] and applied["outcome"] == "ingested"
        assert client.get(f"/route/{token}").json()["reason_code"] == "ROUTE_REVOKED"
        updated = await_view(client, session, lambda current: current["gate"]["value"] == "B18" and current["route"])
        assert updated["route"]["steps"][-1]["text"]["params"] == {"gate": "B18"}
        assert updated["qr"]["token"] not in (token, None)
        clock.advance(1)
        assert client.post(APPLY_GATE, headers=COORDINATOR).json() == applied
        state = client.get("/demo/state", headers=COORDINATOR).json()
        assert next(event for event in state["events"] if event["id"] == GATE)["applied"]

    with closing(connect(config.db_path)) as conn:
        fact = current_field(conn, C02_OUTBOUND, ObservationField.GATE, clock.now(), agent.policy)
        fixture = next(event.payload for event in load()["events"] if event.id == GATE)
        assert applied_at - fixture.observed_at > timedelta(seconds=agent.policy.stale_seconds)
        assert fact.state == ObservationState.FRESH
        assert fact.observation.observed_at == fact.observation.received_at == applied_at
        assert fact.observation.valid_until == applied_at + (fixture.valid_until - fixture.observed_at)
        assert conn.execute("SELECT count(*) FROM observations WHERE field='gate'").fetchone()[0] == 2
        actions = [row[0] for row in conn.execute("SELECT action FROM audit WHERE event_id=?", (GATE,))]
        assert actions.count("demo_event") == actions.count("route_revoked") == 1


def test_facility_readiness_and_refresh(demo):
    clock, config, agent = demo
    build_demo_db(config.db_path)
    clock.advance(120)
    app = create_integrated_app(clock, config, agent=agent)
    client = TestClient(app, headers=COORDINATOR)
    journey, _ = create(client)
    for event_id in ("ev-c01-lift-closed", "ev-c10-queue-stale", "ev-c02-ready", "ev-c02-refresh"):
        endpoint = f"/demo/events/{event_id}/apply"
        first = client.post(endpoint).raise_for_status().json()
        assert first["applied"]
        assert client.post(endpoint).json() == first
    with closing(connect(config.db_path)) as conn:
        for table in ("overlays", "queue_observations"):
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE observed_at=?", (timestamp(clock.now()),)).fetchone()[0] == 1
        ready = conn.execute("SELECT value FROM meta WHERE key LIKE 'readiness:%'").fetchone()[0]
        assert ReadinessEvent.model_validate_json(ready).observed_at == clock.now()
        assert conn.execute("SELECT count(*) FROM events WHERE event_type='refresh_due' AND journey_id=?",
                            (journey,)).fetchone()[0] == 1
