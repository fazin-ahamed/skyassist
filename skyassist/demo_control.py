import json
import secrets
from collections.abc import Callable
from contextlib import closing
from datetime import datetime

from fastapi import APIRouter, Depends, Response
from pydantic import model_validator

from skyassist.agent import UPDATE_KINDS, JourneyAgent
from skyassist.assist import audit, enqueue_event
from skyassist.clock import DEMO_STARTS, Clock, ReplayClock, SimClock, as_utc
from skyassist.config import Config, Mode
from skyassist.db import connect, timestamp, transaction
from skyassist.demo_feed import DemoFeed
from skyassist.models import (
    Contract, EventStreamItem, EventType, ExpiringSourceStamp, Id, Observation, ObservationField, Overlay,
    QueueObservation, ReadinessEvent, ReasonCode, Role, Seconds, UTCInstant,
)
from skyassist.seed import Bundle
from skyassist.service import (
    DEMO_COORDINATOR_TOKEN, SESSION_HORIZON, Auth, ResourceId, _bearer, _credential, _digest, _error,
)


class ClockMove(Contract):
    name: Id | None = None
    set: UTCInstant | None = None
    advance_seconds: Seconds | None = None

    @model_validator(mode="after")
    def _one_operation(self):
        if len(self.model_fields_set) != 1 or all(
            value is None for value in (self.name, self.set, self.advance_seconds)
        ):
            raise ValueError("specify exactly one clock operation")
        return self


class FeedPair(Contract):
    leg_id: Id
    field: ObservationField


def _label(event: EventStreamItem) -> str:
    payload = event.payload
    if event.event_type == EventType.GATE_CHANGED:
        return f"Change gate to {payload.value}" if payload.value else "Withdraw gate"
    if isinstance(payload, ReadinessEvent):
        return f"Readiness: {payload.state.replace('_', ' ')}"
    if isinstance(payload, Overlay):
        return "Close lift" if payload.closed else "Reopen lift"
    if isinstance(payload, QueueObservation):
        return "Update queue wait"
    return event.event_type.replace("_", " ").capitalize()


def _restamp(event: EventStreamItem, now: datetime) -> EventStreamItem:
    payload = event.payload
    stamps = {"observed_at": now}
    if isinstance(payload, ExpiringSourceStamp):
        stamps["valid_until"] = now + (payload.valid_until - payload.observed_at)
    if isinstance(payload, Observation):
        stamps["received_at"] = now
    return event.model_copy(update={"timestamp": now, "payload": payload.model_copy(update=stamps)})


def demo_router(clock: Clock, config: Config, agent: JourneyAgent,
                bundle_or_loader: Bundle | Callable[[], Bundle], feed: DemoFeed) -> APIRouter:
    if config.mode != Mode.DEMO:
        return APIRouter()

    coordinator = _digest(_credential("SKYASSIST_COORDINATOR_TOKEN", DEMO_COORDINATOR_TOKEN))

    def require_coordinator(authorization: Auth = None) -> None:
        if not secrets.compare_digest(_digest(_bearer(authorization)), coordinator):
            raise _error(403, ReasonCode.WRONG_ROLE)

    router = APIRouter(prefix="/demo", dependencies=[Depends(require_coordinator)])
    bundle = bundle_or_loader() if callable(bundle_or_loader) else bundle_or_loader
    starts = {name: as_utc(datetime.fromisoformat(start) if isinstance(start, str) else start)
              for name, start in (DEMO_STARTS | bundle["clocks"]).items()}
    events = {event.id: event for event in bundle["events"]
              if event.event_type in (*UPDATE_KINDS, EventType.REFRESH_DUE)}
    catalog = [{"id": event.id, "label": _label(event), "kind": event.event_type,
                "scenario_ids": [scenario.id for scenario in bundle["scenarios"] if event.id in scenario.event_sequence]}
               for event in events.values()]
    lock = feed.lock

    @router.get("/state")
    def state(response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        with lock, closing(connect(config.db_path)) as conn:
            applied = {row[0] for row in conn.execute(
                "SELECT event_id FROM audit JOIN events ON events.id=audit.event_id "
                "WHERE action='demo_event' AND events.outcome_json IS NOT NULL "
                "AND json_extract(events.outcome_json, '$.reason_code') IS NULL",
            )}
            return {"now": clock.now(), "clock_starts": starts,
                    "held": [{"leg_id": leg_id, "field": field} for leg_id, field in sorted(feed.held)],
                    "events": [entry | {"applied": entry["id"] in applied} for entry in catalog]}

    def audit_feed(conn, now, action, body=None):
        stamp = timestamp(now)
        event_id = enqueue_event(conn, None, action, stamp, processed=True,
                                 payload=body.model_dump_json() if body is not None else None)
        audit(conn, event_id, stamp, action, "applied", role=Role.COORDINATOR)

    def set_hold(body: FeedPair, hold: bool) -> dict:
        pair = (body.leg_id, body.field)
        with lock, closing(connect(config.db_path)) as conn:
            if not conn.execute("SELECT 1 FROM flight_legs WHERE id=?", (body.leg_id,)).fetchone():
                raise _error(404, ReasonCode.INVALID_ARGUMENTS)
            if (pair in feed.held) != hold:
                with transaction(conn):
                    now = clock.now()
                    if not hold:
                        feed._tick(conn, now, pair)
                    audit_feed(conn, now, "demo_feed_hold" if hold else "demo_feed_resume", body)
                (feed.held.add if hold else feed.held.remove)(pair)
        return body.model_dump() | {"held": hold}

    @router.post("/feed/hold")
    def hold_feed(body: FeedPair) -> dict:
        return set_hold(body, True)

    @router.post("/feed/resume")
    def resume_feed(body: FeedPair) -> dict:
        return set_hold(body, False)

    @router.post("/feed/tick")
    def tick_feed() -> dict[str, datetime]:
        with lock, closing(connect(config.db_path)) as conn, transaction(conn):
            now = clock.now()
            if not conn.execute("SELECT 1 FROM events WHERE event_type='demo_feed_tick' AND occurred_at=?",
                                (timestamp(now),)).fetchone():
                feed._tick(conn, now)
                audit_feed(conn, now, "demo_feed_tick")
            return {"now": now}

    @router.post("/clock")
    def move_clock(body: ClockMove) -> dict[str, datetime]:
        if not isinstance(clock, (ReplayClock, SimClock)):
            raise _error(422, ReasonCode.INVALID_ARGUMENTS)
        with lock, closing(connect(config.db_path)) as conn, transaction(conn):
            try:
                if body.advance_seconds is not None:
                    now = clock.advance(body.advance_seconds)
                else:
                    now = clock.set(starts[body.name] if body.name is not None else body.set)
            except (KeyError, ValueError, OverflowError) as exc:
                raise _error(422, ReasonCode.INVALID_ARGUMENTS) from exc
            stamp = timestamp(now)
            event_id = enqueue_event(conn, None, "demo_clock", stamp, processed=True)
            audit(conn, event_id, stamp, "demo_clock", "applied", role=Role.COORDINATOR)
            return {"now": now}

    @router.post("/events/{event_id}/apply")
    def apply_event(event_id: ResourceId) -> dict:
        event = events.get(event_id)
        if event is None:
            raise _error(404, ReasonCode.INVALID_ARGUMENTS)
        refresh = event.event_type == EventType.REFRESH_DUE
        with lock, closing(connect(config.db_path)) as conn:
            with transaction(conn):
                row = conn.execute(
                    "SELECT outcome_json, EXISTS (SELECT 1 FROM audit WHERE event_id=events.id AND action='demo_event') "
                    "FROM events WHERE id=?", (event_id,),
                ).fetchone()
                if row is not None and not row[1]:
                    raise _error(409, ReasonCode.INVALID_ARGUMENTS)
                if row is None:
                    now = clock.now()
                    stamp = timestamp(now)
                    if not refresh:
                        event = _restamp(event, now)
                    payload = event.payload.model_dump_json() if event.payload is not None else None
                    enqueue_event(conn, None, event.event_type, stamp, event_id=event_id,
                                  payload=payload, processed=refresh)
                    audit(conn, event_id, stamp, "demo_event", "queued", role=Role.COORDINATOR)
                    if refresh:
                        for (airport,) in conn.execute("SELECT DISTINCT airport FROM journeys WHERE closed_at IS NULL"):
                            agent._fanout(conn, event_id, stamp, EventType.REFRESH_DUE, airport=airport)
                        conn.execute("UPDATE events SET outcome_json=? WHERE id=?",
                                     (json.dumps({"outcome": "queued"}), event_id))
            if not refresh and (row is None or row[0] is None):
                agent.enqueue_update(event)
            stored = conn.execute("SELECT outcome_json FROM events WHERE id=?", (event_id,)).fetchone()[0]
            if stored is None:
                raise _error(409, ReasonCode.INVALID_ARGUMENTS)
            result = json.loads(stored)
            return {"id": event_id, "kind": event.event_type, "applied": "reason_code" not in result} | result

    @router.post("/resources")
    def set_resource(body: dict, response: Response) -> dict:
        """Set how many of an equipment kind are free (wheelchair, escort, buggy). Used to rehearse a refusal."""
        kind, total = body.get("kind"), body.get("total")
        if kind not in ("wheelchair", "escort", "buggy") or not isinstance(total, int) or isinstance(total, bool) or not 0 <= total <= 20:
            raise _error(422, ReasonCode.INVALID_ARGUMENTS)
        response.headers["Cache-Control"] = "no-store"
        with closing(connect(config.db_path)) as conn, transaction(conn):
            seats = conn.execute("SELECT seats FROM resources WHERE kind=?", (kind,)).fetchone()
            conn.execute("INSERT OR REPLACE INTO resources (kind, total, seats) VALUES (?, ?, ?)",
                         (kind, total, seats[0] if seats else (4 if kind == "buggy" else 1)))
        return {"kind": kind, "total": total}

    @router.get("/buggy/plans")
    def buggy_plans(response: Response) -> dict:
        from skyassist import buggy  # late import: buggy builds on the assistance module
        response.headers["Cache-Control"] = "no-store"
        with closing(connect(config.db_path)) as conn:
            return {"plans": buggy.plans(conn)}

    return router
