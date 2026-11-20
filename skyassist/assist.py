"""Durable assistance state and outbox operations."""

import asyncio
import json
import sqlite3
from collections.abc import Callable
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

from skyassist.clock import Clock
from skyassist.db import timestamp, transaction
from skyassist.models import (
    AssistanceResponse, AssistanceService, AssistanceView, CallbackOutcome,
    CallAssistanceInput, CallAssistanceOutput, Case, CaseState, ConnectionStatus,
    Decision, DecisionKind, DecisionResponse, DispatchState, DispatchStatusRequest,
    DispatchStatusResponse, EventType, I18nText, Location, NextActionView,
    OutboundEffect, PassengerAction, QRView, ReasonCode, Role, SourceStamp,
    StatusView, TimeWindow, ViewModel,
)
from skyassist.models import Notice, RequestView


R = ReasonCode
S = CaseState
E = OutboundEffect
OPEN = (S.DRAFT, S.PENDING_APPROVAL, S.APPROVED, S.DISPATCH_PENDING_VERIFICATION, S.DISPATCHED)
TRANSITIONS = {
    S.DRAFT: {S.PENDING_APPROVAL, S.CANCELLED},
    S.PENDING_APPROVAL: {S.APPROVED, S.DECLINED, S.CANCELLED, S.EXPIRED},
    S.APPROVED: {S.CANCELLED, S.DISPATCH_PENDING_VERIFICATION, S.DISPATCHED},
    S.DISPATCH_PENDING_VERIFICATION: {S.DISPATCHED},
    S.DISPATCHED: {S.COMPLETED},
}
type OpenDB = Callable[[], sqlite3.Connection]
type Handoff = Callable[[Location], CallAssistanceOutput]
_handoff: ContextVar[CallAssistanceInput] = ContextVar("approved_handoff")


class AssistanceError(ValueError):
    def __init__(self, reason: ReasonCode):
        self.reason = reason
        super().__init__(reason)


@contextmanager
def _tx(conn):
    """Join the caller's transaction, or open one. Lets journey code stage a request in the same commit."""
    if conn.in_transaction:
        yield conn
    else:
        with transaction(conn):
            yield conn


_notice: ContextVar[tuple[str, I18nText] | None] = ContextVar("assist_notice", default=None)


@contextmanager
def announcing(kind: str, text: I18nText):
    """Whatever revision the wrapped assist call publishes carries this one-off message for the passenger."""
    token = _notice.set((kind, text))
    try:
        yield
    finally:
        _notice.reset(token)


def retarget_note(conn, case_id):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (f"retarget:{case_id}",)).fetchone()
    return json.loads(row[0]) if row else None


def group_role(conn, case_id):
    """Whether a shared-buggy plan seats this request or leaves it waiting, from the newest plans."""
    for row in conn.execute("SELECT plan_json FROM buggy_groups ORDER BY created_at DESC, rowid DESC LIMIT 20"):
        plan = json.loads(row[0])
        for rider in plan.get("riders", ()):
            if rider["case_id"] == case_id:
                return "rides", rider.get("seats")
        for waiting in plan.get("leftover", ()):
            if waiting["case_id"] == case_id:
                return "waits", waiting.get("seats")
    return None, None


def request_views(conn, journey_id, gate=None):
    """Every staff request on the journey, so a family with a wheelchair, a buggy seat and a gate hold sees all three."""
    views = []
    for row in conn.execute("SELECT * FROM cases WHERE journey_id=? ORDER BY created_at, rowid", (journey_id,)):
        case = case_from_row(conn, row)
        held = case.service == AssistanceService.GATE_HOLD and case.state in (S.PENDING_APPROVAL, S.APPROVED)
        note = retarget_note(conn, case.id)
        role, seats = group_role(conn, case.id)
        views.append(RequestView(
            case_ref=case.id, service=case.service, state=case.state,
            stage=I18nText(key="assistance.gate_hold_submitted" if held else f"assistance.{case.state}"),
            destination=(note or {}).get("to") or gate, retargeted_from=(note or {}).get("from"),
            group_role=role, seats=seats,
        ))
    return tuple(views[-6:])


def retarget(conn, case, *, old_gate, new_gate, now):
    """The gate moved. Point the existing request at the new gate instead of making the family ask again.

    The request keeps its id, its approval and its place in the queue. Only its destination and the facts it was
    approved against are brought up to date. If a dispatch was already sent, staff are told the destination changed.
    """
    journey = journey_row(conn, case.journey_id)
    row = conn.execute("SELECT payload_json FROM events WHERE id=?", (case.idempotency_key,)).fetchone()
    if row and row[0]:
        payload = json.loads(row[0])
        payload["scope"] = _scope(conn, journey, case.target_leg_id, case.service, now)
        conn.execute("UPDATE events SET payload_json=? WHERE id=?", (json.dumps(payload), case.idempotency_key))
    original = (retarget_note(conn, case.id) or {}).get("from") or old_gate
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)",
                 (f"retarget:{case.id}", json.dumps({"from": original, "to": new_gate})))
    event_id = enqueue_event(conn, case.journey_id, "case_retargeted", now, processed=True)
    audit(conn, event_id, now, "case_retargeted", f"{original}->{new_gate}", case=case, reason=R.GATE_CHANGED)
    item = _dispatch_item(conn, case)
    if case.state in (S.DISPATCH_PENDING_VERIFICATION, S.DISPATCHED) or (
        case.state == S.APPROVED and item and (item["in_flight_at"] or item["status"] == "sent")
    ):
        _staff_followup(conn, case, now, "destination_amended", R.GATE_CHANGED)


def republish_notice(conn, journey_id, clock, kind, text):
    """Publish a new revision that only adds one message for the passenger, with the current list of requests."""
    journey = journey_row(conn, journey_id)
    previous = latest_revision(conn, journey_id)
    view = ViewModel.model_validate_json(previous["view_json"]) if previous else initial_view(journey, clock)
    if view.notice and view.notice.kind == kind and view.notice.text == text:
        return  # the same message is never published twice in a row: watching must not turn into nagging
    revision = journey["revision"] + 1
    now = timestamp(clock.now())
    event_id = enqueue_event(conn, journey_id, "notice", now, processed=True)
    assistance = view.assistance
    if assistance and assistance.case_ref:
        note = retarget_note(conn, assistance.case_ref) or {}
        assistance = assistance.model_copy(update={"destination": note.get("to") or view.gate.value,
                                                   "retargeted_from": note.get("from")})
    view = view.model_copy(update={
        "revision": revision, "as_of": clock.now(), "requests": request_views(conn, journey_id, view.gate.value),
        "assistance": assistance, "notice": Notice(kind=kind, text=text, seq=revision),
    })
    conn.execute("UPDATE journeys SET revision=? WHERE id=?", (revision, journey_id))
    conn.execute(
        """INSERT INTO journey_revisions (journey_id, revision, created_at, event_id, policy_version, graph_version,
                                          overlay_version, view_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (journey_id, revision, now, event_id, previous["policy_version"] if previous else "service",
         previous["graph_version"] if previous else None, previous["overlay_version"] if previous else None,
         view.model_dump_json()),
    )


def dispatch_context() -> CallAssistanceInput:
    """Return the approved request revision and deduplication key inside a hook."""
    return _handoff.get()


def enqueue_event(conn, journey_id, kind, now, *, event_id=None, payload=None,
                  client_input_id=None, processed=False):
    event_id = event_id or uuid4().hex
    conn.execute(
        """INSERT INTO events (id, journey_id, event_type, occurred_at, due_at,
                               payload_json, client_input_id, processed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_id, journey_id, kind, now, None if processed else now, payload,
         client_input_id, now if processed else None),
    )
    return event_id


def audit(conn, event_id, now, action, outcome, *, case=None, journey_id=None,
          role=None, reason=None, attempts=0):
    conn.execute(
        """INSERT INTO audit (event_id, occurred_at, action, outcome, journey_id,
                              case_id, role, reason_codes_json, attempts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_id, now, action, outcome, case.journey_id if case else journey_id,
         case.id if case else None, role, json.dumps([reason] if reason else []), attempts),
    )


def journey_row(conn, journey_id):
    return conn.execute("SELECT * FROM journeys WHERE id = ?", (journey_id,)).fetchone()


def latest_revision(conn, journey_id):
    return conn.execute(
        "SELECT * FROM journey_revisions WHERE journey_id=? ORDER BY revision DESC LIMIT 1",
        (journey_id,),
    ).fetchone()


def initial_view(journey, clock: Clock) -> ViewModel:
    now = clock.now()
    return ViewModel(
        revision=journey["revision"], as_of=now, language=journey["language"],
        status=StatusView(status=ConnectionStatus.TIMING_UNVERIFIED,
                          provenance=SourceStamp(source="journey", observed_at=now)),
        next_action=NextActionView(action=PassengerAction.CHOOSE_FLIGHT,
                                   text=I18nText(key="action.choose_flight")),
    )


def pickup_location(conn, airport, node_id):
    row = conn.execute(
        "SELECT * FROM nodes WHERE id=? AND airport=? AND pickup_eligible=1",
        (node_id, airport),
    ).fetchone()
    if row:
        return Location(node_id=row["id"], airport=row["airport"],
                        landmark=I18nText.model_validate_json(row["landmark_json"]))
    return None


def case_from_row(conn, row) -> Case:
    data = dict(row)
    node_id = data.pop("pickup_node_id")
    data["pickup"] = pickup_location(conn, data["airport"], node_id)
    data["pickup_confirmed"] = bool(data["pickup_confirmed"])
    data["consent"] = bool(data["consent"])
    data["reason_codes"] = json.loads(data.pop("reason_codes_json"))
    return Case.model_validate(data)


def _case(conn, case_id):
    row = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    return case_from_row(conn, row) if row else None


def _scope(conn, journey, target_leg_id, service, now):
    revision = latest_revision(conn, journey["id"])
    view = ViewModel.model_validate_json(revision["view_json"]) if revision else None
    gate = None
    if view and target_leg_id:
        gate = [view.gate.value, view.gate.terminal, view.gate.concourse, view.gate.level, view.gate.state]
    fields = ("gate", "terminal", "concourse", "level", "status")
    cutoff = None
    if service == AssistanceService.GATE_HOLD:
        fields += ("boarding_cutoff",)
        if view and target_leg_id:
            cutoff = [view.cutoff.value and timestamp(view.cutoff.value), view.cutoff.estimated]
    observations = conn.execute(
        """SELECT field, source, value_json, scheduled, estimated, actual, withdrawn
           FROM (
               SELECT *, row_number() OVER (
                   PARTITION BY field, source ORDER BY observed_at DESC, received_at DESC, id DESC
               ) AS latest
               FROM observations WHERE leg_id=? AND observed_at<=? AND received_at<=?
           )
           WHERE latest=1 ORDER BY field, source""",
        (target_leg_id, now, now),
    )
    return [journey["origin_node_id"], journey["outbound_leg_id"], gate, cutoff,
            [list(row) for row in observations if row["field"] in fields]]


def _scope_reason(conn, case, now):
    if not case.consent:
        return R.CONSENT_MISSING
    if not case.pickup_confirmed or case.pickup is None:
        return R.PICKUP_INELIGIBLE
    journey = journey_row(conn, case.journey_id)
    latest = latest_revision(conn, case.journey_id)
    if latest is None or latest["revision"] != journey["revision"]:
        return R.STALE_REVISION
    snapshot = conn.execute("SELECT payload_json FROM events WHERE id=?",
                            (case.idempotency_key,)).fetchone()
    if snapshot is None:
        return R.CASE_SCOPE_CHANGED
    old = json.loads(snapshot[0])
    if old["handoff"] != [case.service, case.pickup.node_id, case.target_leg_id, case.target_revision]:
        return R.CASE_SCOPE_CHANGED
    current = _scope(conn, journey, case.target_leg_id, case.service, now)
    if current != old["scope"]:
        return R.CASE_SCOPE_CHANGED
    if case.service is AssistanceService.GATE_HOLD and current[3]:
        cutoff = current[3][0]
        if cutoff is not None and cutoff <= now:
            return R.CUTOFF_PASSED
    return None


def _publish(conn, case, event_id, clock):
    journey = journey_row(conn, case.journey_id)
    previous = latest_revision(conn, case.journey_id)
    view = ViewModel.model_validate_json(previous["view_json"]) if previous else initial_view(journey, clock)
    revision = journey["revision"] + 1
    fallback = case.state in (S.CANCELLED, S.DECLINED, S.EXPIRED)
    active = case.state in OPEN
    action = view.next_action
    if active or fallback:
        action_kind = PassengerAction.SEEK_STAFF if fallback else PassengerAction.WAIT_FOR_HANDOFF
        if R.URGENT_SAFETY in case.reason_codes:
            action_kind = PassengerAction.SEEK_EMERGENCY_HELP
        action = NextActionView(action=action_kind, text=I18nText(key=f"action.{action_kind}"))
    elif action.action == PassengerAction.WAIT_FOR_HANDOFF:
        action_kind = PassengerAction.FOLLOW_ROUTE if view.route else PassengerAction.SEEK_STAFF
        action = NextActionView(action=action_kind, text=I18nText(key=f"action.{action_kind}"))
    stage = I18nText(key=f"assistance.{case.state}")
    if case.service is AssistanceService.GATE_HOLD and case.state in (S.PENDING_APPROVAL, S.APPROVED):
        stage = I18nText(key="assistance.gate_hold_submitted")
    assistance = AssistanceView(
        state=case.state, stage=stage, case_ref=case.id,
        pickup_landmark=case.pickup.landmark if case.pickup else None,
        staffed_alternative=I18nText(key="action.seek_staff"),
        approval_window=(TimeWindow(start=case.created_at, end=case.approval_expires_at)
                         if case.approval_expires_at else None),
        provenance=SourceStamp(source="assistance", observed_at=clock.now()),
        service=case.service, destination=(retarget_note(conn, case.id) or {}).get("to") or view.gate.value,
        retargeted_from=(retarget_note(conn, case.id) or {}).get("from"),
    )
    announce = _notice.get()
    view = view.model_copy(update={
        "requests": request_views(conn, case.journey_id, view.gate.value),
        "notice": Notice(kind=announce[0], text=announce[1], seq=revision) if announce else view.notice,
        "revision": revision, "as_of": clock.now(), "assistance": assistance, "next_action": action,
        "route": None if set(case.reason_codes) & {R.CASE_SCOPE_CHANGED, R.STALE_REVISION} else view.route,
        "qr": QRView(suppressed_reason=R.STAFF_HANDOFF_PENDING if active else R.ROUTE_UNAVAILABLE),
    })
    now = timestamp(view.as_of)
    conn.execute("UPDATE journey_revisions SET route_revoked_at=? WHERE journey_id=? "
                 "AND route_token_hash IS NOT NULL AND route_revoked_at IS NULL", (now, case.journey_id))
    conn.execute("UPDATE journeys SET revision=? WHERE id=?", (revision, case.journey_id))
    conn.execute(
        """INSERT INTO journey_revisions (journey_id, revision, created_at, event_id,
                                          policy_version, graph_version, overlay_version, view_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (case.journey_id, revision, now, event_id, previous["policy_version"] if previous else "service",
         previous["graph_version"] if previous else None,
         previous["overlay_version"] if previous else None, view.model_dump_json()),
    )


def _transition(conn, case, state, event_id, clock, reason=None):
    if state not in TRANSITIONS.get(case.state, ()):
        raise RuntimeError(f"Illegal assistance transition: {case.state} to {state}")
    updated = conn.execute(
        """UPDATE cases SET state=?, reason_codes_json=?
           WHERE id=? AND state=? AND request_revision=?""",
        (state, json.dumps([reason] if reason else []), case.id, case.state, case.request_revision),
    )
    if updated.rowcount != 1:
        raise RuntimeError("Assistance case changed inside transaction")
    case = case.model_copy(update={"state": state, "reason_codes": (reason,) if reason else ()})
    _publish(conn, case, event_id, clock)
    return case


def _outbox(conn, case, effect, now, *, suffix=""):
    key = f"{effect}:{case.id}:{case.request_revision}{suffix}"
    return conn.execute(
        """INSERT INTO outbox (id, case_id, request_revision, effect, idempotency_key, due_at)
           VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(idempotency_key) DO NOTHING""",
        (uuid4().hex, case.id, case.request_revision, effect, key, now),
    ).rowcount == 1


def _continuation(conn, case, now):
    return enqueue_event(conn, case.journey_id, EventType.REFRESH_DUE, now)


def _assistance_response(conn, case):
    return AssistanceResponse(case_ref=case.id, state=case.state,
                              request_revision=case.request_revision,
                              journey_revision=journey_row(conn, case.journey_id)["revision"])


def create_case(conn: sqlite3.Connection, *, clock: Clock, journey_id: str, client_input_id: str,
                journey_revision: int, service: AssistanceService, pickup_node_id: str | None,
                pickup_confirmed: bool, consent: bool, target_leg_id: str | None = None,
                target_revision: int | None = None, bind_to_outbound: bool = True) -> AssistanceResponse:
    if consent is not True:
        raise AssistanceError(R.CONSENT_MISSING)
    service = AssistanceService(service)
    key = "assistance:" + sha256(json.dumps([journey_id, client_input_id]).encode()).hexdigest()
    with _tx(conn):
        instant = clock.now()
        now = timestamp(instant)
        journey = journey_row(conn, journey_id)
        if journey is None or journey["closed_at"] is not None:
            raise AssistanceError(R.SESSION_EXPIRED)
        prior = conn.execute("SELECT event_type, outcome_json FROM events WHERE id=?", (key,)).fetchone()
        if prior:
            if prior["event_type"] != "assistance_submission":
                raise AssistanceError(R.DUPLICATE_EVENT)
            return _assistance_response(conn, _case(conn, json.loads(prior["outcome_json"])["case_id"]))
        location = pickup_location(conn, journey["airport"], pickup_node_id) if pickup_confirmed else None
        reason = None
        if not pickup_confirmed:
            reason = R.PICKUP_UNCONFIRMED
        elif location is None:
            reason = R.PICKUP_INELIGIBLE
        if target_leg_id is not None and target_leg_id != journey["outbound_leg_id"]:
            raise AssistanceError(R.CASE_SCOPE_CHANGED)
        if bind_to_outbound and target_leg_id is None:
            target_leg_id = journey["outbound_leg_id"]
        existing = None
        if reason is None:
            existing = conn.execute(
                """SELECT * FROM cases WHERE journey_id=? AND service=? AND pickup_node_id IS ?
                   AND target_leg_id IS ? AND state IN (?, ?, ?, ?, ?)""",
                (journey_id, service, location.node_id, target_leg_id, *OPEN),
            ).fetchone()
        scope = _scope(conn, journey, target_leg_id, service, now)
        if existing:
            case = case_from_row(conn, existing)
            if journey_revision not in (case.journey_revision, journey["revision"]):
                raise AssistanceError(R.STALE_REVISION)
            expired = (case.state == S.PENDING_APPROVAL and case.approval_expires_at
                       and timestamp(case.approval_expires_at) <= now)
            if not expired and _scope_reason(conn, case, now) is None and (
                target_revision is None or target_revision == case.target_revision
            ):
                enqueue_event(conn, journey_id, "assistance_submission", now, event_id=key, processed=True)
                conn.execute("UPDATE events SET outcome_json=? WHERE id=?",
                             (json.dumps({"case_id": case.id}), key))
                return _assistance_response(conn, case)
            if expired and journey_revision == journey["revision"]:
                _expire(conn, case, now, clock)
            else:
                raise AssistanceError(R.APPROVAL_EXPIRED if expired else R.CASE_SCOPE_CHANGED)
        if journey_revision != journey["revision"]:
            raise AssistanceError(R.STALE_REVISION)
        if service is AssistanceService.GATE_HOLD and (
            target_leg_id is None or target_revision != journey_revision
        ):
            raise AssistanceError(R.STALE_REVISION)
        if service is AssistanceService.GATE_HOLD and scope[3] and scope[3][0] and scope[3][0] <= now:
            raise AssistanceError(R.CUTOFF_PASSED)
        case = Case(
            id=uuid4().hex, journey_id=journey_id, airport=journey["airport"], service=service,
            request_revision=1, journey_revision=journey_revision, created_at=instant,
            idempotency_key=key, pickup=location, pickup_confirmed=location is not None, consent=True,
            approval_expires_at=instant + timedelta(minutes=5) if reason is None else None,
            target_leg_id=target_leg_id, target_revision=target_revision,
        )
        conn.execute(
            """INSERT INTO cases (id, journey_id, airport, service, state, request_revision,
                                  journey_revision, created_at, idempotency_key, pickup_node_id,
                                  pickup_confirmed, consent, approval_expires_at, target_leg_id,
                                  target_revision)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (case.id, journey_id, case.airport, service, case.state, case.request_revision,
             journey_revision, now, key, location.node_id if location else None,
             case.pickup_confirmed, case.consent,
             timestamp(case.approval_expires_at) if case.approval_expires_at else None,
             target_leg_id, target_revision),
        )
        handoff = [case.service, location.node_id if location else None, target_leg_id, target_revision]
        enqueue_event(conn, journey_id, "assistance_submission", now, event_id=key,
                      payload=json.dumps({"scope": scope, "handoff": handoff}), processed=True)
        conn.execute("UPDATE events SET outcome_json=? WHERE id=?", (json.dumps({"case_id": case.id}), key))
        event_id = _continuation(conn, case, now)
        case = _transition(conn, case, S.CANCELLED if reason else S.PENDING_APPROVAL, event_id, clock, reason)
        _outbox(conn, case, E.COORDINATOR_NOTIFICATION, now)
        audit(conn, event_id, now, "assistance_requested", case.state, case=case, role=Role.PASSENGER,
              reason=reason)
        if case.state == S.PENDING_APPROVAL:
            from skyassist import replan  # late import: replan builds on this module
            replan.after_request(conn, case, clock)
        return _assistance_response(conn, case)


def _callback(response_type, request, case, reason=None):
    return response_type(event_id=request.event_id, case_id=request.case_id,
                         request_revision=request.request_revision,
                         state=case.state if case else S.DRAFT, reason_code=reason,
                         outcome=CallbackOutcome.REJECTED if reason else CallbackOutcome.APPLIED)


def _prior_callback(conn, request, case, kind, response_type):
    if request.event_id.startswith(("handoff:", "assistance:")):
        return _callback(response_type, request, case, R.DUPLICATE_EVENT)
    row = conn.execute("SELECT * FROM events WHERE id=?", (request.event_id,)).fetchone()
    if row is None:
        return None
    if (row["event_type"] != kind or row["payload_json"] is None or row["outcome_json"] is None
            or json.loads(row["payload_json"]) != request.model_dump(mode="json")):
        return _callback(response_type, request, case, R.DUPLICATE_EVENT)
    return response_type.model_validate_json(row["outcome_json"]).model_copy(
        update={"outcome": CallbackOutcome.REPLAYED},
    )


def _expire(conn, case, now, clock):
    event_id = _continuation(conn, case, now)
    case = _transition(conn, case, S.EXPIRED, event_id, clock, R.APPROVAL_EXPIRED)
    audit(conn, event_id, now, "approval_expired", case.state, case=case, reason=R.APPROVAL_EXPIRED)
    return case


def expire_due(conn: sqlite3.Connection, *, clock: Clock) -> int:
    with transaction(conn):
        now = timestamp(clock.now())
        rows = conn.execute(
            "SELECT * FROM cases WHERE state='pending_approval' AND approval_expires_at<=?",
            (now,),
        ).fetchall()
        for row in rows:
            _expire(conn, case_from_row(conn, row), now, clock)
        return len(rows)


def decide(conn: sqlite3.Connection, decision: Decision, *, clock: Clock, role: Role,
           airport: str | None = None) -> DecisionResponse:
    if role not in (Role.COORDINATOR, Role.REVIEWER):
        return _callback(DecisionResponse, decision, None, R.WRONG_ROLE)
    with _tx(conn):
        now = timestamp(clock.now())
        case = _case(conn, decision.case_id)
        if case is None or (airport is not None and case.airport != airport):
            return _callback(DecisionResponse, decision, None, R.CASE_NOT_PENDING)
        prior = _prior_callback(conn, decision, case, EventType.COORDINATOR_DECIDED, DecisionResponse)
        if prior:
            return prior
        if (case.state == S.PENDING_APPROVAL and case.approval_expires_at is not None
                and timestamp(case.approval_expires_at) <= now):
            case = _expire(conn, case, now, clock)
        first = conn.execute(
            """SELECT outcome_json FROM staff_decisions WHERE case_id=? AND request_revision=?
               AND json_extract(outcome_json, '$.outcome')='applied' LIMIT 1""",
            (case.id, case.request_revision),
        ).fetchone()
        reason = None
        scope_reason = None
        if case.request_revision != decision.request_revision:
            reason = R.STALE_REVISION
        elif case.state == S.CANCELLED:
            reason = R.CASE_CANCELLED
        elif case.state == S.EXPIRED:
            reason = R.APPROVAL_EXPIRED
        elif first:
            case = case.model_copy(update={"state": DecisionResponse.model_validate_json(first[0]).state})
            reason = R.ALREADY_DECIDED
        elif case.state != S.PENDING_APPROVAL:
            reason = R.CASE_NOT_PENDING
        elif case.approval_expires_at is None:
            reason = R.APPROVAL_EXPIRED
        elif decision.decision == DecisionKind.APPROVE:
            scope_reason = reason = _scope_reason(conn, case, now)

        enqueue_event(conn, case.journey_id, EventType.COORDINATOR_DECIDED, now,
                      event_id=decision.event_id, payload=decision.model_dump_json(), processed=reason is not None)
        if scope_reason:
            event_id = _continuation(conn, case, now)
            case = _transition(conn, case, S.CANCELLED, event_id, clock, scope_reason)
            audit(conn, event_id, now, "approval_scope_rejected", case.state, case=case, reason=scope_reason)
        if reason is None:
            state = S.APPROVED if decision.decision == DecisionKind.APPROVE else S.DECLINED
            decline_reason = (decision.reason_code or R.APPROVAL_DECLINED) if state == S.DECLINED else None
            case = _transition(conn, case, state, decision.event_id, clock, decline_reason)
            if state == S.APPROVED:
                effect = E.GATE_HOLD_REVIEW if case.service == AssistanceService.GATE_HOLD else E.MOBILITY_DISPATCH
                _outbox(conn, case, effect, now)
            from skyassist import replan  # late import: replan builds on this module
            if state == S.DECLINED:
                replan.after_decline(conn, case, decision, clock)
            elif case.service == AssistanceService.BUGGY:
                replan.after_group_approved(conn, case, clock)
        response = _callback(DecisionResponse, decision, case, reason)
        conn.execute(
            """INSERT INTO staff_decisions (event_id, case_id, request_revision, decision, reason_code, outcome_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (decision.event_id, case.id, decision.request_revision, decision.decision,
             response.reason_code or decision.reason_code, response.model_dump_json()),
        )
        conn.execute("UPDATE events SET outcome_json=? WHERE id=?", (response.model_dump_json(), decision.event_id))
        audit(conn, decision.event_id, now, f"decision_{decision.decision}", response.outcome,
              case=case, role=role, reason=reason)
        return response


def _staff_followup(conn, case, now, action, reason=None):
    if _outbox(conn, case, E.COORDINATOR_NOTIFICATION, now, suffix=f":{action}"):
        event_id = enqueue_event(conn, case.journey_id, action, now, processed=True)
        audit(conn, event_id, now, action, "staff_review", case=case, reason=reason)


def _dispatch_item(conn, case):
    return conn.execute(
        "SELECT * FROM outbox WHERE case_id=? AND request_revision=? AND effect IN (?, ?)",
        (case.id, case.request_revision, E.MOBILITY_DISPATCH, E.GATE_HOLD_REVIEW),
    ).fetchone()


def cancel(conn: sqlite3.Connection, case_id: str, *, clock: Clock, journey_id: str) -> AssistanceResponse:
    with _tx(conn):
        now = timestamp(clock.now())
        case = _case(conn, case_id)
        if case is None or case.journey_id != journey_id:
            raise AssistanceError(R.CASE_NOT_PENDING)
        item = _dispatch_item(conn, case)
        if case.state in (S.DISPATCH_PENDING_VERIFICATION, S.DISPATCHED) or (
            case.state == S.APPROVED and item and (item["in_flight_at"] or item["status"] == "sent")
        ):
            _staff_followup(conn, case, now, "cancellation_requested", R.CASE_CANCELLED)
        elif case.state in (S.DRAFT, S.PENDING_APPROVAL, S.APPROVED):
            event_id = _continuation(conn, case, now)
            case = _transition(conn, case, S.CANCELLED, event_id, clock, R.CASE_CANCELLED)
            if item:
                conn.execute("UPDATE outbox SET status='done' WHERE id=?", (item["id"],))
            audit(conn, event_id, now, "case_cancelled", case.state, case=case, role=Role.PASSENGER,
                  reason=R.CASE_CANCELLED)
        return _assistance_response(conn, case)


def report_dispatch(conn: sqlite3.Connection, request: DispatchStatusRequest, *, clock: Clock,
                    role: Role, airport: str | None = None, demo_staff: bool = False) -> DispatchStatusResponse:
    if role != Role.INTEGRATION and not (demo_staff and role == Role.COORDINATOR):
        return _callback(DispatchStatusResponse, request, None, R.WRONG_ROLE)
    with transaction(conn):
        now = timestamp(clock.now())
        case = _case(conn, request.case_id)
        if case is None or (airport is not None and case.airport != airport):
            return _callback(DispatchStatusResponse, request, None, R.CASE_NOT_PENDING)
        prior = _prior_callback(conn, request, case, EventType.DISPATCH_STATUS, DispatchStatusResponse)
        if prior:
            return prior
        item = _dispatch_item(conn, case)
        state = S.DISPATCH_PENDING_VERIFICATION if request.status == DispatchState.UNKNOWN else S(request.status)
        observed = timestamp(request.evidence.observed_at)
        reason = None
        if case.request_revision != request.request_revision:
            reason = R.STALE_REVISION
        elif case.service == AssistanceService.GATE_HOLD:
            reason = R.CASE_SCOPE_CHANGED
        elif state not in TRANSITIONS.get(case.state, ()):
            reason = R.CASE_NOT_PENDING
        elif not item or not item["in_flight_at"] or not item["in_flight_at"] <= observed <= now:
            reason = R.DISPATCH_UNCONFIRMED
        else:
            acceptance = conn.execute(
                "SELECT payload_json FROM events WHERE id=? AND event_type='handoff_accepted'",
                (_effect_event(item["idempotency_key"]),),
            ).fetchone()
            if acceptance and request.provider_ref:
                provider_ref = json.loads(acceptance[0])["provider_ref"]
                if provider_ref and request.provider_ref != provider_ref:
                    reason = R.DISPATCH_UNCONFIRMED
            if state == S.COMPLETED:
                previous = conn.execute(
                    """SELECT payload_json FROM events WHERE journey_id=? AND event_type='dispatch_status'
                       AND json_extract(outcome_json, '$.case_id')=?
                       AND json_extract(outcome_json, '$.state')='dispatched'
                       AND json_extract(outcome_json, '$.outcome')='applied'""",
                    (case.journey_id, case.id),
                ).fetchone()
                prior_status = DispatchStatusRequest.model_validate_json(previous[0]) if previous else None
                if prior_status and (
                    request.evidence.observed_at < prior_status.evidence.observed_at
                    or (prior_status.provider_ref and request.provider_ref
                        and prior_status.provider_ref != request.provider_ref)
                ):
                    reason = R.DISPATCH_UNCONFIRMED
        enqueue_event(conn, case.journey_id, EventType.DISPATCH_STATUS, now, event_id=request.event_id,
                      payload=request.model_dump_json(), processed=reason is not None)
        unknown = None
        if reason is None:
            scope_reason = _scope_reason(conn, case, now)
            if scope_reason:
                _staff_followup(conn, case, now, "scope_amendment", scope_reason)
            unknown = R.DISPATCH_OUTCOME_UNKNOWN if state == S.DISPATCH_PENDING_VERIFICATION else None
            case = _transition(conn, case, state, request.event_id, clock, unknown)
            if case.service == AssistanceService.BUGGY:
                from skyassist import buggy  # late import: buggy builds on this module
                buggy.sync_riders(conn, case, clock)
            conn.execute("UPDATE outbox SET status=?, claim_expires_at=NULL WHERE id=?",
                         ("unknown" if unknown else "done", item["id"]))
        response = _callback(DispatchStatusResponse, request, case, reason)
        conn.execute("UPDATE events SET outcome_json=? WHERE id=?", (response.model_dump_json(), request.event_id))
        audit(conn, request.event_id, now, f"dispatch_{request.status}", response.outcome,
              case=case, role=role, reason=reason)
        if unknown:
            _staff_followup(conn, case, now, "dispatch_reconciliation", unknown)
        return response


def _reconcile(conn, case, item, now, clock):
    event_id = _continuation(conn, case, now)
    reason = R.DISPATCH_OUTCOME_UNKNOWN
    case = _transition(conn, case, S.DISPATCH_PENDING_VERIFICATION, event_id, clock, reason)
    conn.execute("UPDATE outbox SET status='unknown', claim_expires_at=NULL WHERE id=?", (item["id"],))
    audit(conn, event_id, now, "dispatch_outcome", "unknown", case=case, reason=reason,
          attempts=item["attempts"])
    _staff_followup(conn, case, now, "dispatch_reconciliation", reason)


def _effect_event(idempotency_key):
    return f"handoff:{idempotency_key}"


def _record_handoff(conn, case, key, provider_ref, now):
    event_id = _effect_event(key)
    existing = conn.execute("SELECT event_type FROM events WHERE id=?", (event_id,)).fetchone()
    if existing:
        if existing[0] != "handoff_accepted":
            raise ValueError("Handoff event identity is already in use")
        return
    enqueue_event(conn, case.journey_id, "handoff_accepted", now, event_id=event_id,
                  payload=json.dumps({"provider_ref": provider_ref}), processed=True)
    audit(conn, event_id, now, "mobility_dispatch", "accepted", case=case)


def _mock_call_assistance(open_db, clock, location):
    context = dispatch_context()
    if location != context.location:
        raise ValueError("Pickup does not match approved handoff")
    with closing(open_db()) as conn, transaction(conn):
        case = _case(conn, context.case_id)
        _record_handoff(conn, case, context.idempotency_key, context.idempotency_key, timestamp(clock.now()))
    return CallAssistanceOutput(accepted=True, provider_ref=context.idempotency_key)


def _claim(open_db, clock, deadline, can_send, mock):
    with closing(open_db()) as conn, transaction(conn):
        instant = clock.now()
        now = timestamp(instant)
        for row in conn.execute(
            """SELECT c.* FROM cases c JOIN outbox o ON o.case_id=c.id
               AND o.request_revision=c.request_revision AND o.effect IN (?, ?)
               WHERE c.state IN ('approved', 'dispatch_pending_verification', 'dispatched')
                 AND (o.in_flight_at IS NOT NULL OR o.status='sent')""",
            (E.MOBILITY_DISPATCH, E.GATE_HOLD_REVIEW),
        ).fetchall():
            case = case_from_row(conn, row)
            reason = _scope_reason(conn, case, now)
            if reason:
                _staff_followup(conn, case, now, "scope_amendment", reason)
        item = conn.execute(
            """SELECT * FROM outbox
               WHERE (status='pending' AND due_at<=? AND (? OR effect!=?))
                  OR (status='claimed' AND claim_expires_at<=?)
               ORDER BY due_at, rowid LIMIT 1""",
            (now, can_send, E.MOBILITY_DISPATCH, now),
        ).fetchone()
        if item is None:
            return False, None, None
        case = _case(conn, item["case_id"])
        effect = item["effect"]
        if effect == E.COORDINATOR_NOTIFICATION:
            event_id = enqueue_event(conn, case.journey_id, effect, now, processed=True)
            audit(conn, event_id, now, effect, "available", case=case)
            conn.execute("UPDATE outbox SET status='done' WHERE id=?", (item["id"],))
            return True, None, None
        if case.state != S.APPROVED or case.request_revision != item["request_revision"]:
            conn.execute("UPDATE outbox SET status='done', claim_expires_at=NULL WHERE id=?", (item["id"],))
            return True, None, None
        if item["in_flight_at"]:
            if mock and conn.execute("SELECT 1 FROM events WHERE id=? AND event_type='handoff_accepted'",
                                     (_effect_event(item["idempotency_key"]),)).fetchone():
                conn.execute("UPDATE outbox SET status='sent', claim_expires_at=NULL WHERE id=?", (item["id"],))
                event_id = _continuation(conn, case, now)
                audit(conn, event_id, now, "dispatch_outcome", "recovered", case=case,
                      attempts=item["attempts"])
            else:
                _reconcile(conn, case, item, now, clock)
            return True, None, None
        reason = _scope_reason(conn, case, now)
        if reason:
            event_id = _continuation(conn, case, now)
            case = _transition(conn, case, S.CANCELLED, event_id, clock, reason)
            conn.execute("UPDATE outbox SET status='done', claim_expires_at=NULL WHERE id=?", (item["id"],))
            audit(conn, event_id, now, "dispatch_scope_rejected", case.state, case=case, reason=reason)
            _staff_followup(conn, case, now, "scope_amendment", reason)
            return True, None, None
        if effect == E.GATE_HOLD_REVIEW:
            event_id = enqueue_event(conn, case.journey_id, effect, now, processed=True)
            audit(conn, event_id, now, effect, "submitted_for_review", case=case)
            conn.execute("UPDATE outbox SET status='sent', attempts=attempts+1 WHERE id=?", (item["id"],))
            return True, None, None
        if not can_send:
            conn.execute("UPDATE outbox SET status='pending', claim_expires_at=NULL WHERE id=?", (item["id"],))
            return True, None, None
        conn.execute(
            """UPDATE outbox SET status='claimed', attempts=attempts+1, in_flight_at=?, claim_expires_at=?
               WHERE id=?""",
            (now, timestamp(instant + timedelta(seconds=deadline + 1)), item["id"]),
        )
        event_id = enqueue_event(conn, case.journey_id, "dispatch_claimed", now, processed=True)
        audit(conn, event_id, now, "dispatch_claimed", "in_flight", case=case, attempts=item["attempts"] + 1)
        return True, item["id"], CallAssistanceInput(
            location=case.pickup, case_id=case.id, request_revision=case.request_revision,
            idempotency_key=item["idempotency_key"],
        )


def _finish(open_db, clock, item_id, result):
    with closing(open_db()) as conn, transaction(conn):
        item = conn.execute("SELECT * FROM outbox WHERE id=?", (item_id,)).fetchone()
        case = _case(conn, item["case_id"])
        now = timestamp(clock.now())
        reason = _scope_reason(conn, case, now)
        if reason:
            _staff_followup(conn, case, now, "scope_amendment", reason)
        if item["status"] != "claimed":
            return
        if result is not None and result.accepted:
            _record_handoff(conn, case, item["idempotency_key"], result.provider_ref, now)
            conn.execute("UPDATE outbox SET status='sent', claim_expires_at=NULL WHERE id=?", (item_id,))
            event_id = _continuation(conn, case, now)
            audit(conn, event_id, now, "dispatch_outcome", "accepted", case=case,
                  attempts=item["attempts"])
        else:
            _reconcile(conn, case, item, now, clock)


async def claim_and_dispatch(open_db: OpenDB, *, clock: Clock, call_assistance: Handoff | None = None,
                             deadline: float = 5, demo: bool = True) -> bool:
    """Process one eligible item. A missing live hook leaves mobility work queued."""
    if deadline <= 0:
        raise ValueError("Dispatch deadline must be positive")
    mock = demo and call_assistance is None
    worked, item_id, context = await asyncio.to_thread(
        _claim, open_db, clock, deadline, mock or call_assistance is not None, mock,
    )
    if context is None:
        return worked
    token = _handoff.set(context)
    try:
        if mock:
            task = asyncio.to_thread(_mock_call_assistance, open_db, clock, context.location)
        else:
            task = asyncio.to_thread(call_assistance, context.location)
        result = CallAssistanceOutput.model_validate(await asyncio.wait_for(task, timeout=deadline))
    except asyncio.CancelledError:
        # A restart owns reconciliation if shutdown interrupts a send.
        raise
    except Exception:
        result = None
    finally:
        _handoff.reset(token)
    await asyncio.to_thread(_finish, open_db, clock, item_id, result)
    return True
