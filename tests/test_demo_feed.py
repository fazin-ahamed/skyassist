import asyncio
from contextlib import closing, suppress
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from skyassist import service
from skyassist.agent import JourneyAgent
from skyassist.clock import SimClock
from skyassist.config import Config, Mode
from skyassist.db import timestamp
from skyassist.demo_feed import DemoFeed
from skyassist.evidence import current_field, ingest
from skyassist.models import AmenityObservation, ObservationField, Overlay, QueueObservation, ReadinessEvent
from skyassist.service_read import create_integrated_app

from test_agent import await_view, create, fixture, observation, readiness, submit, update

COORDINATOR = {"Authorization": f"Bearer {service.DEMO_COORDINATOR_TOKEN}"}
GATE = {"leg_id": "out", "field": "gate"}


@pytest.fixture
def demo(tmp_path):
    clock, config = fixture(tmp_path)
    agent = JourneyAgent(clock, config)
    with closing(agent.open_db()) as conn:
        for item in (
            observation(clock, "gate", "A12", leg_id="in"),
            observation(clock, "status", "scheduled"),
            observation(clock, "arrival", scheduled=clock.now(), actual=clock.now()),
            observation(clock, "carousel", "12"),
        ):
            ingest(conn, item)
    return clock, config, agent


def fact(clock, agent, field="gate", leg_id="out"):
    with closing(agent.open_db()) as conn:
        return current_field(conn, leg_id, field, clock.now(), agent.policy)


def test_startup_and_tick_preserve_values_and_validity(demo):
    clock, config, agent = demo
    original = {field: fact(clock, agent, field).observation for field in ObservationField}
    assert all(original.values())
    clock.advance(100)
    assert fact(clock, agent).state == "stale"
    assert fact(clock, agent, "boarding_cutoff").state == "stale"
    app = create_integrated_app(clock, config, agent=agent)
    with TestClient(app):
        for field, prior in original.items():
            current = fact(clock, agent, field)
            assert current.state == "fresh"
            assert current.observation == prior.model_copy(update={
                "observed_at": clock.now(), "received_at": clock.now(),
                "valid_until": clock.now() + (prior.valid_until - prior.observed_at),
            })
    clock.advance(100)
    assert fact(clock, agent).state == "stale"
    app.state.demo_feed.tick()
    assert fact(clock, agent).state == "fresh"


async def test_run_uses_app_clock_interval(demo, monkeypatch):
    clock, config, agent = demo
    feed = DemoFeed(clock, config, agent, agent.policy)
    ticked = asyncio.Event()
    tick = feed.tick

    def record_tick():
        tick()
        ticked.set()

    monkeypatch.setattr(feed, "tick", record_tick)
    task = asyncio.create_task(feed.run())
    try:
        await asyncio.sleep(0)
        clock.advance(19)
        await asyncio.sleep(0.15)
        assert not ticked.is_set()
        clock.advance(1)
        await asyncio.wait_for(ticked.wait(), 2)
        assert fact(clock, agent).observation.observed_at == clock.now()
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def test_hold_resume_scope_auth_and_audit(demo):
    clock, config, agent = demo
    app = create_integrated_app(clock, config, agent=agent)
    with TestClient(app) as client:
        for action in ("hold", "resume", "tick"):
            assert client.post(f"/demo/feed/{action}", json=GATE).status_code == 403

    client = TestClient(app, headers=COORDINATOR)
    assert client.post("/demo/feed/hold", json=GATE | {"leg_id": "missing"}).status_code == 404
    assert client.post("/demo/feed/hold", json=GATE | {"field": "queue"}).status_code == 422
    assert client.post("/demo/feed/hold", json=GATE | {"passenger": "not allowed"}).status_code == 422
    first = client.post("/demo/feed/hold", json=GATE).raise_for_status().json()
    assert first == GATE | {"held": True}
    assert client.post("/demo/feed/hold", json=GATE).json() == first
    assert client.get("/demo/state").json()["held"] == [GATE]
    clock.advance(100)
    tick = client.post("/demo/feed/tick").raise_for_status().json()
    assert client.post("/demo/feed/tick").json() == tick
    assert fact(clock, agent).state == "stale"
    assert fact(clock, agent, leg_id="in").state == "fresh"
    terminal = fact(clock, agent, "terminal")
    assert terminal.state == "fresh"
    clock.advance(1)
    resumed = client.post("/demo/feed/resume", json=GATE).raise_for_status().json()
    assert resumed == GATE | {"held": False}
    assert client.get("/demo/state").json()["held"] == []
    restored = fact(clock, agent)
    assert restored.state == "fresh" and restored.observation.observed_at == clock.now()
    assert fact(clock, agent, "terminal") == terminal
    clock.advance(1)
    assert client.post("/demo/feed/resume", json=GATE).json() == resumed
    assert fact(clock, agent) == restored
    with closing(agent.open_db()) as conn:
        rows = conn.execute("SELECT action, role, audit.journey_id, payload_json FROM audit JOIN events "
                            "ON events.id=audit.event_id WHERE action LIKE 'demo_feed_%'").fetchall()
        assert sorted(tuple(row) for row in rows) == [
            ("demo_feed_hold", "coordinator", None, '{"leg_id":"out","field":"gate"}'),
            ("demo_feed_resume", "coordinator", None, '{"leg_id":"out","field":"gate"}'),
            ("demo_feed_tick", "coordinator", None, None),
        ]


def test_conflict_and_withdrawal_survive_ticks(demo):
    clock, config, agent = demo
    feed = DemoFeed(clock, config, agent, agent.policy)
    clock.advance(20)
    feed.tick()
    clock.advance(1)
    with closing(agent.open_db()) as conn:
        ingest(conn, observation(clock, "gate", "B18", source="peer"))
        ingest(conn, observation(clock, "gate", None, withdrawn=True, leg_id="in"))
    conflict = fact(clock, agent)
    assert conflict.state == "conflict"
    clock.advance(20)
    feed.tick()
    assert fact(clock, agent) == conflict
    assert fact(clock, agent, leg_id="in").state == "unknown"
    clock.advance(100)
    feed.tick()
    assert fact(clock, agent).state == "stale"
    assert fact(clock, agent, leg_id="in").state == "unknown"
    with closing(agent.open_db()) as conn:
        ingest(conn, observation(clock, "gate", "B18", source="ops", authority_rank=1))
    clock.advance(20)
    feed.tick()
    resolved = fact(clock, agent).observation
    assert (resolved.value, resolved.source, resolved.observed_at) == ("B18", "ops", clock.now())


def test_non_flight_evidence_keeps_original_clocks(demo):
    clock, config, agent = demo
    stamp = {"airport": "DXB", "source": "ops", "observed_at": clock.now(),
             "valid_until": clock.now() + timedelta(seconds=30)}
    update(agent, clock, "queue", "facility_changed", QueueObservation(
        **stamp, queue_id="security", flow="connecting", wait={"lower": 30, "upper": 60},
    ))
    update(agent, clock, "amenity", "facility_changed", AmenityObservation(**stamp, node_id="c22", open=False))
    readiness(agent, clock)
    clock.advance(35)
    update(agent, clock, "lift", "facility_changed", Overlay(
        **(stamp | {"observed_at": clock.now(), "valid_until": clock.now() + timedelta(minutes=5)}),
        id="lift", edge_id="b14", closed=True,
    ))
    clock.advance(20)
    DemoFeed(clock, config, agent, agent.policy).tick()
    with closing(agent.open_db()) as conn:
        assert tuple(conn.execute("SELECT closed, observed_at FROM overlays").fetchone()) == (
            1, timestamp(clock.now() - timedelta(seconds=20)),
        )
        assert tuple(conn.execute("SELECT observed_at, valid_until FROM queue_observations").fetchone()) == (
            timestamp(stamp["observed_at"]), timestamp(stamp["valid_until"]),
        )
        assert tuple(conn.execute("SELECT open, observed_at FROM amenity_observations").fetchone()) == (
            0, timestamp(stamp["observed_at"]),
        )
        stored = conn.execute("SELECT value FROM meta WHERE key='readiness:in'").fetchone()[0]
        assert ReadinessEvent.model_validate_json(stored).observed_at == stamp["observed_at"]


def test_ticks_are_bounded_across_feed_restarts_and_clock_rewind(demo):
    clock, config, agent = demo
    start = clock.now()
    feed = DemoFeed(clock, config, agent, agent.policy)
    for _ in range(100):
        clock.advance(20)
        feed.tick()
    feed = DemoFeed(clock, config, agent, agent.policy)
    for _ in range(100):
        clock.advance(20)
        feed.tick()
    with closing(agent.open_db()) as conn:
        assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 20
    clock.set(start)
    assert fact(clock, agent).observation.value == "B14"
    clock.advance(20)
    feed.tick()
    assert fact(clock, agent).observation.observed_at == clock.now()


def test_resume_recomputes_stale_journey_route(demo):
    clock, config, agent = demo
    readiness(agent, clock)
    with TestClient(create_integrated_app(clock, config, agent=agent)) as client:
        session = create(client)
        submit(client, session)
        original = await_view(client, session, lambda current: current["qr"]["token"] is not None)
        client.post("/demo/feed/hold", headers=COORDINATOR, json=GATE).raise_for_status()
        clock.advance(100)
        client.post("/demo/feed/tick", headers=COORDINATOR).raise_for_status()
        stale = await_view(client, session, lambda current: current["gate"]["state"] == "stale")
        assert stale["route"] is None and stale["qr"]["token"] is None
        client.post("/demo/feed/resume", headers=COORDINATOR, json=GATE).raise_for_status()
        restored = await_view(client, session, lambda current: current["qr"]["token"] is not None)
        assert restored["gate"]["state"] == "fresh"
        assert restored["route"]["route_id"] == original["route"]["route_id"]


def test_live_has_no_feed_or_routes(tmp_path, monkeypatch):
    config = Config(Mode.LIVE, tmp_path / "live.sqlite3")
    monkeypatch.setattr(service, "CONFIG", config)
    monkeypatch.setenv("SKYASSIST_COORDINATOR_TOKEN", "live-feed-test-coordinator")
    monkeypatch.setenv("SKYASSIST_INTEGRATION_TOKEN", "live-feed-test-integration")
    app = create_integrated_app(SimClock(), config)
    assert not hasattr(app.state, "demo_feed")
    assert not any(path.startswith("/demo") for path in app.openapi()["paths"])
