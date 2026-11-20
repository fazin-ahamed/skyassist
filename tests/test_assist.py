import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from functools import partial
from threading import Barrier, Event

import pytest

from skyassist import assist
from skyassist.clock import SimClock
from skyassist.db import connect, init_db, timestamp
from skyassist.models import (
    AssistanceService, CallAssistanceOutput, CallbackOutcome, CaseState, Decision,
    DecisionKind, DispatchState, DispatchStatusRequest, ReasonCode, Role, SourceStamp,
)


def seed(conn):
    conn.executemany(
        """INSERT INTO nodes (id, airport, terminal, concourse, floor, security_zone,
                               landmark_json, x, y, pickup_eligible)
           VALUES (?, ?, '1', 'C', '2', 'airside', ?, 12.75, 45.25, ?)""",
        [("c22", "DXB", '{"key":"landmark.c22"}', 1),
         ("c23", "DXB", '{"key":"landmark.c23"}', 1),
         ("c24", "DXB", '{"key":"landmark.c24"}', 0),
         ("a01", "AUH", '{"key":"landmark.a01"}', 1)],
    )
    conn.execute(
        """INSERT INTO flight_legs (id, operating_carrier, operating_number,
                                     scheduled_operation_date, origin, destination)
           VALUES ('outbound', 'EK', '100', '2026-11-19', 'DXB', 'LHR')""",
    )


def trace(conn):
    return [(row["action"], row["outcome"]) for row in conn.execute("SELECT * FROM audit ORDER BY id")]


def new_case(conn, clock, **overrides):
    fields = dict(journey_id="journey", client_input_id="tap-1", journey_revision=0,
                  service=AssistanceService.WHEELCHAIR, pickup_node_id="c22",
                  pickup_confirmed=True, consent=True)
    return assist.create_case(conn, clock=clock, **(fields | overrides))


def decision(case, event_id="decision", kind=DecisionKind.APPROVE, revision=None):
    return Decision(event_id=event_id, case_id=case.case_ref, decision=kind,
                    request_revision=case.request_revision if revision is None else revision)


def evidence(case, clock, status=DispatchState.DISPATCHED, event_id=None):
    return DispatchStatusRequest(event_id=event_id or f"status-{status}", case_id=case.case_ref,
                                 request_revision=case.request_revision, status=status,
                                 evidence=SourceStamp(source="demo", observed_at=clock.now()))


def change_gate(conn, clock, gate="B18"):
    now = timestamp(clock.now())
    conn.execute(
        """INSERT INTO observations (leg_id, field, source, authority_rank, value_json,
                                      observed_at, received_at, valid_until)
           VALUES ('outbound', 'gate', 'demo', 1, ?, ?, ?, ?)""",
        (f'"{gate}"', now, now, now),
    )


@pytest.fixture
def setup(tmp_path):
    db = tmp_path / "workflow.sqlite3"
    init_db(db)
    open_db = partial(connect, db)
    clock = SimClock()
    with closing(open_db()) as conn:
        seed(conn)
        now = timestamp(clock.now())
        conn.execute(
            """INSERT INTO journeys (id, airport, profile, created_at, outbound_leg_id)
               VALUES ('journey', 'DXB', 'standard', ?, 'outbound')""", (now,),
        )
        view = assist.initial_view(assist.journey_row(conn, "journey"), clock)
        conn.execute(
            """INSERT INTO journey_revisions (journey_id, revision, created_at, policy_version, view_json)
               VALUES ('journey', 0, ?, 'test', ?)""", (now, view.model_dump_json()),
        )
        yield conn, clock, open_db


async def drain(open_db, clock, **kwargs):
    for _ in range(10):
        if not await assist.claim_and_dispatch(open_db, clock=clock, **kwargs):
            return
    pytest.fail("Worker failed to drain")


async def test_approval_replay_and_separate_dispatch_confirmation(setup):
    conn, clock, open_db = setup
    case = new_case(conn, clock)
    assert case.state == CaseState.PENDING_APPROVAL
    assert new_case(conn, clock) == case
    assert new_case(conn, clock, client_input_id="tap-2") == case
    assert conn.execute("SELECT count(*) FROM cases").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM events WHERE processed_at IS NULL").fetchone()[0] == 1

    approve = decision(case)
    applied = assist.decide(conn, approve, clock=clock, role=Role.COORDINATOR)
    assert applied.state == CaseState.APPROVED
    assert applied.outcome == CallbackOutcome.APPLIED
    await drain(open_db, clock)
    assert conn.execute("SELECT state FROM cases").fetchone()[0] == "approved"
    assert conn.execute("SELECT attempts FROM outbox WHERE effect='mobility_dispatch'").fetchone()[0] == 1
    reported = assist.report_dispatch(conn, evidence(case, clock), clock=clock, role=Role.INTEGRATION)
    completed = assist.report_dispatch(conn, evidence(case, clock, DispatchState.COMPLETED),
                                       clock=clock, role=Role.INTEGRATION)
    assert reported.state == CaseState.DISPATCHED
    assert completed.state == CaseState.COMPLETED
    assert assist.report_dispatch(conn, evidence(case, clock), clock=clock,
                                  role=Role.INTEGRATION).outcome == CallbackOutcome.REPLAYED
    replay = assist.decide(conn, approve, clock=clock, role=Role.COORDINATOR)
    assert replay == applied.model_copy(update={"outcome": CallbackOutcome.REPLAYED})
    conflict = assist.decide(conn, decision(case, "other", DecisionKind.DECLINE),
                             clock=clock, role=Role.COORDINATOR)
    assert (conflict.state, conflict.reason_code) == (CaseState.APPROVED, ReasonCode.ALREADY_DECIDED)
    await drain(open_db, clock)
    assert trace(conn) == [
        ("assistance_requested", "pending_approval"), ("decision_approve", "applied"),
        ("coordinator_notification", "available"), ("dispatch_claimed", "in_flight"),
        ("mobility_dispatch", "accepted"), ("dispatch_outcome", "accepted"),
        ("dispatch_dispatched", "applied"), ("dispatch_completed", "applied"),
        ("decision_decline", "rejected"),
    ]


@pytest.mark.parametrize("end,expected,reason", [
    ("decline", CaseState.DECLINED, ReasonCode.ALREADY_DECIDED),
    ("expire", CaseState.EXPIRED, ReasonCode.APPROVAL_EXPIRED),
    ("cancel", CaseState.CANCELLED, ReasonCode.CASE_CANCELLED),
])
async def test_closed_cases_reject_late_approval(setup, end, expected, reason):
    conn, clock, open_db = setup
    case = new_case(conn, clock)
    if end == "decline":
        result = assist.decide(conn, decision(case, "decline", DecisionKind.DECLINE),
                               clock=clock, role=Role.COORDINATOR)
        action = ("decision_decline", "applied")
    elif end == "expire":
        clock.advance(300)
        assert assist.expire_due(conn, clock=clock) == 1
        assert assist.expire_due(conn, clock=clock) == 0
        action = ("approval_expired", "expired")
    else:
        result = assist.cancel(conn, case.case_ref, clock=clock, journey_id="journey")
        assert result.state == CaseState.CANCELLED
        action = ("case_cancelled", "cancelled")
    if end == "decline":
        assert result.state == expected
    late = assist.decide(conn, decision(case), clock=clock, role=Role.COORDINATOR)
    assert (late.state, late.reason_code) == (expected, reason)
    await drain(open_db, clock)
    assert trace(conn) == [("assistance_requested", "pending_approval"), action,
                           ("decision_approve", "rejected"), ("coordinator_notification", "available")]


@pytest.mark.parametrize("node,confirmed,reason", [
    ("unknown", True, "PICKUP_INELIGIBLE"), ("c24", True, "PICKUP_INELIGIBLE"),
    ("a01", True, "PICKUP_INELIGIBLE"), ("c22", False, "PICKUP_UNCONFIRMED"),
])
async def test_pickup_fallback_and_consent(setup, node, confirmed, reason):
    conn, clock, open_db = setup
    with pytest.raises(assist.AssistanceError, match="CONSENT_MISSING"):
        new_case(conn, clock, consent=False)
    assert trace(conn) == []
    case = new_case(conn, clock, pickup_node_id=node, pickup_confirmed=confirmed)
    assert case.state == CaseState.CANCELLED
    view = assist.latest_revision(conn, "journey")["view_json"]
    assert "seek_staff" in view
    assert conn.execute("SELECT reason_codes_json FROM cases").fetchone()[0] == f'["{reason}"]'
    await drain(open_db, clock)
    assert trace(conn) == [("assistance_requested", "cancelled"), ("coordinator_notification", "available")]


async def test_wrong_role_scope_stale_revision_and_event_collision(setup):
    conn, clock, open_db = setup
    case = new_case(conn, clock)
    request = decision(case, revision=0)
    wrong = assist.decide(conn, request, clock=clock, role=Role.INTEGRATION)
    assert wrong.reason_code == ReasonCode.WRONG_ROLE
    wrong_airport = assist.decide(conn, request, clock=clock, role=Role.COORDINATOR, airport="AUH")
    assert wrong_airport.reason_code == ReasonCode.CASE_NOT_PENDING
    assert trace(conn) == [("assistance_requested", "pending_approval")]
    rejected = assist.decide(conn, request, clock=clock, role=Role.COORDINATOR)
    assert rejected.reason_code == ReasonCode.STALE_REVISION
    collision = assist.decide(conn, decision(case), clock=clock, role=Role.COORDINATOR)
    assert collision.reason_code == ReasonCode.DUPLICATE_EVENT
    await drain(open_db, clock)
    assert trace(conn) == [("assistance_requested", "pending_approval"), ("decision_approve", "rejected"),
                           ("coordinator_notification", "available")]


@pytest.mark.parametrize("change", ["gate", "revision", "pickup_eligibility", "pickup_identity"])
async def test_claim_rechecks_approved_scope(setup, change):
    conn, clock, open_db = setup
    case = new_case(conn, clock)
    assist.decide(conn, decision(case), clock=clock, role=Role.COORDINATOR)
    if change == "gate":
        change_gate(conn, clock)
    elif change == "revision":
        conn.execute("UPDATE journeys SET revision=revision+1 WHERE id='journey'")
    elif change == "pickup_eligibility":
        conn.execute("UPDATE nodes SET pickup_eligible=0 WHERE id='c22'")
    else:
        conn.execute("UPDATE cases SET pickup_node_id='c23'")
    await drain(open_db, clock)
    assert conn.execute("SELECT state FROM cases").fetchone()[0] == "cancelled"
    assert trace(conn) == [
        ("assistance_requested", "pending_approval"), ("decision_approve", "applied"),
        ("coordinator_notification", "available"), ("dispatch_scope_rejected", "cancelled"),
        ("scope_amendment", "staff_review"), ("coordinator_notification", "available"),
    ]


async def test_stale_scope_approval_is_recorded_and_needs_new_case(setup):
    conn, clock, open_db = setup
    case = new_case(conn, clock)
    change_gate(conn, clock)
    result = assist.decide(conn, decision(case), clock=clock, role=Role.COORDINATOR)
    assert (result.state, result.reason_code) == (CaseState.CANCELLED, ReasonCode.CASE_SCOPE_CHANGED)
    assert assist.decide(conn, decision(case), clock=clock, role=Role.COORDINATOR).outcome == "replayed"
    replacement = new_case(conn, clock, client_input_id="retry",
                           journey_revision=assist.journey_row(conn, "journey")["revision"])
    assert replacement.state == CaseState.PENDING_APPROVAL
    assert replacement.case_ref != case.case_ref
    await drain(open_db, clock)
    assert trace(conn) == [
        ("assistance_requested", "pending_approval"), ("approval_scope_rejected", "cancelled"),
        ("decision_approve", "rejected"), ("assistance_requested", "pending_approval"),
        ("coordinator_notification", "available"), ("coordinator_notification", "available"),
    ]


async def test_competing_approval_and_cancel(setup):
    conn, clock, open_db = setup
    case = new_case(conn, clock)
    ready = Barrier(2)

    def action(fn):
        with closing(open_db()) as client_conn:
            ready.wait(timeout=5)
            return fn(client_conn)

    with ThreadPoolExecutor(max_workers=2) as pool:
        approve = pool.submit(action, lambda db: assist.decide(db, decision(case), clock=clock,
                                                              role=Role.COORDINATOR))
        cancel = pool.submit(action, lambda db: assist.cancel(db, case.case_ref, clock=clock,
                                                             journey_id="journey"))
        applied = approve.result()
        assert cancel.result().state == CaseState.CANCELLED
    await drain(open_db, clock)
    approve_event = ("decision_approve", applied.outcome)
    cancel_event = ("case_cancelled", "cancelled")
    order = [approve_event, cancel_event] if applied.outcome == CallbackOutcome.APPLIED else [cancel_event, approve_event]
    assert trace(conn) == [("assistance_requested", "pending_approval"), *order,
                           ("coordinator_notification", "available")]


async def test_worker_restart_after_outbox_commit(setup):
    conn, clock, open_db = setup
    started = asyncio.Event()
    start_claims = asyncio.Event()

    async def old_worker():
        started.set()
        await start_claims.wait()
        await drain(open_db, clock)

    worker = asyncio.create_task(old_worker())
    await started.wait()
    case = new_case(conn, clock)
    assert assist.decide(conn, decision(case), clock=clock, role=Role.COORDINATOR).outcome == "applied"
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert conn.execute("SELECT status FROM outbox WHERE effect='mobility_dispatch'").fetchone()[0] == "pending"
    await drain(open_db, clock)
    assert trace(conn) == [
        ("assistance_requested", "pending_approval"), ("decision_approve", "applied"),
        ("coordinator_notification", "available"), ("dispatch_claimed", "in_flight"),
        ("mobility_dispatch", "accepted"), ("dispatch_outcome", "accepted"),
    ]


async def test_ambiguous_post_send_timeout_and_amendment(setup):
    conn, clock, open_db = setup
    case = new_case(conn, clock)
    assist.decide(conn, decision(case), clock=clock, role=Role.COORDINATOR)
    sent, release = Event(), Event()
    keys = []

    def hook(location):
        keys.append(assist.dispatch_context().idempotency_key)
        assert location.node_id == "c22"
        with closing(open_db()) as db:
            change_gate(db, clock)
        sent.set()
        release.wait(timeout=5)
        return CallAssistanceOutput(accepted=True)

    try:
        assert await assist.claim_and_dispatch(open_db, clock=clock, call_assistance=hook)
        assert await assist.claim_and_dispatch(open_db, clock=clock, call_assistance=hook, deadline=0.2)
        assert sent.is_set()
        assert conn.execute("SELECT state FROM cases").fetchone()[0] == "dispatch_pending_verification"
        assert conn.execute("SELECT status FROM outbox WHERE effect='mobility_dispatch'").fetchone()[0] == "unknown"
        clock.advance(60)
        await drain(open_db, clock, call_assistance=hook)
        assert assist.cancel(conn, case.case_ref, clock=clock, journey_id="journey").state == "dispatch_pending_verification"
        assert assist.report_dispatch(conn, evidence(case, clock), clock=clock,
                                      role=Role.INTEGRATION).state == CaseState.DISPATCHED
        assert len(keys) == 1
        assert trace(conn) == [
            ("assistance_requested", "pending_approval"), ("decision_approve", "applied"),
            ("coordinator_notification", "available"), ("dispatch_claimed", "in_flight"),
            ("scope_amendment", "staff_review"), ("dispatch_outcome", "unknown"),
            ("dispatch_reconciliation", "staff_review"), ("coordinator_notification", "available"),
            ("coordinator_notification", "available"), ("cancellation_requested", "staff_review"),
            ("dispatch_dispatched", "applied"),
        ]
    finally:
        release.set()


@pytest.mark.parametrize("provider_accepted", [True, False])
async def test_expired_in_flight_claim_is_never_resent(setup, monkeypatch, provider_accepted):
    conn, clock, open_db = setup
    case = new_case(conn, clock)
    assist.decide(conn, decision(case), clock=clock, role=Role.COORDINATOR)
    assert await assist.claim_and_dispatch(open_db, clock=clock)

    def crash(*args):
        raise RuntimeError("worker exited")

    with monkeypatch.context() as patch:
        patch.setattr(assist, "_finish", crash)
        if not provider_accepted:
            patch.setattr(assist, "_mock_call_assistance", crash)
        with pytest.raises(RuntimeError, match="worker exited"):
            await assist.claim_and_dispatch(open_db, clock=clock)
    clock.advance(10)
    await drain(open_db, clock)
    history = trace(conn)
    assert history[:4] == [
        ("assistance_requested", "pending_approval"), ("decision_approve", "applied"),
        ("coordinator_notification", "available"), ("dispatch_claimed", "in_flight"),
    ]
    if provider_accepted:
        assert history[4:] == [("mobility_dispatch", "accepted"), ("dispatch_outcome", "recovered")]
        assert conn.execute("SELECT state FROM cases").fetchone()[0] == "approved"
    else:
        assert history[4:] == [("dispatch_outcome", "unknown"), ("dispatch_reconciliation", "staff_review"),
                               ("coordinator_notification", "available")]
        assert conn.execute("SELECT state FROM cases").fetchone()[0] == "dispatch_pending_verification"


async def test_gate_hold_only_submits_review(setup):
    conn, clock, open_db = setup
    case = new_case(conn, clock, service=AssistanceService.GATE_HOLD,
                    target_leg_id="outbound", target_revision=0)
    assert case.state == CaseState.PENDING_APPROVAL
    assert assist.decide(conn, decision(case), clock=clock, role=Role.COORDINATOR).state == CaseState.APPROVED
    await drain(open_db, clock)
    assert "assistance.gate_hold_submitted" in assist.latest_revision(conn, "journey")["view_json"]
    assert assist.report_dispatch(conn, evidence(case, clock), clock=clock,
                                  role=Role.INTEGRATION).reason_code == ReasonCode.CASE_SCOPE_CHANGED
    assert trace(conn) == [
        ("assistance_requested", "pending_approval"), ("decision_approve", "applied"),
        ("coordinator_notification", "available"), ("gate_hold_review", "submitted_for_review"),
        ("dispatch_dispatched", "rejected"),
    ]


async def test_dispatch_evidence_matches_provider_and_order(setup):
    conn, clock, open_db = setup
    case = new_case(conn, clock)
    assist.decide(conn, decision(case), clock=clock, role=Role.COORDINATOR)
    await drain(open_db, clock, call_assistance=lambda _: CallAssistanceOutput(accepted=True, provider_ref="handoff"))
    old = evidence(case, clock, status=DispatchState.COMPLETED, event_id="old")
    clock.advance(1)
    dispatch = evidence(case, clock)
    mismatched = dispatch.model_copy(update={"event_id": "wrong", "provider_ref": "another"})
    assert assist.report_dispatch(conn, mismatched, clock=clock,
                                  role=Role.INTEGRATION).reason_code == ReasonCode.DISPATCH_UNCONFIRMED
    dispatch = dispatch.model_copy(update={"provider_ref": "handoff"})
    assert assist.report_dispatch(conn, dispatch, clock=clock, role=Role.INTEGRATION).state == CaseState.DISPATCHED
    assert assist.report_dispatch(conn, old, clock=clock,
                                  role=Role.INTEGRATION).reason_code == ReasonCode.DISPATCH_UNCONFIRMED
    assert assist.report_dispatch(conn, evidence(case, clock, status=DispatchState.COMPLETED),
                                  clock=clock, role=Role.INTEGRATION).state == CaseState.COMPLETED
    assert trace(conn) == [
        ("assistance_requested", "pending_approval"), ("decision_approve", "applied"),
        ("coordinator_notification", "available"), ("dispatch_claimed", "in_flight"),
        ("mobility_dispatch", "accepted"), ("dispatch_outcome", "accepted"),
        ("dispatch_dispatched", "rejected"), ("dispatch_dispatched", "applied"),
        ("dispatch_completed", "rejected"), ("dispatch_completed", "applied"),
    ]
