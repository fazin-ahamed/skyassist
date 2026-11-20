"""The autonomous reviewer: a deterministic policy that decides staff requests with no person in the loop.

A human coordinator can still override from the dashboard. Passenger messages never reach this module's decisions.
"""

import asyncio
import logging
import sqlite3
import time
from contextlib import closing
from uuid import uuid4

from skyassist import assist, buggy
from skyassist.clock import Clock
from skyassist.db import timestamp
from skyassist.models import (
    AssistanceService, Decision, DecisionKind, DispatchState, DispatchStatusRequest,
    ReasonCode, Role, SourceStamp, ViewModel,
)

log = logging.getLogger(__name__)

BUSY_STATES = ("approved", "dispatch_pending_verification", "dispatched")
GATE_HOLD_MIN_MARGIN = -15
WAIT_MIN = {
    AssistanceService.WHEELCHAIR: 20,
    AssistanceService.MOBILITY_ESCORT: 10,
    AssistanceService.BUGGY: 15,
}
KIND_FOR = {
    AssistanceService.WHEELCHAIR: "wheelchair",
    AssistanceService.MOBILITY_ESCORT: "escort",
}


GROUP_WINDOW = 6.0


def _free_resources(conn: sqlite3.Connection, kind: str, service: AssistanceService) -> int:
    row = conn.execute("SELECT total FROM resources WHERE kind=?", (kind,)).fetchone()
    if row is None:
        return 0
    busy = conn.execute(
        f"SELECT COUNT(*) FROM cases WHERE service=? AND state IN ({','.join('?' for _ in BUSY_STATES)})",
        (service.value, *BUSY_STATES),
    ).fetchone()[0]
    return max(0, row[0] - busy)


def review_case(conn: sqlite3.Connection, case, view: ViewModel | None, clock: Clock):
    """Return (DecisionKind, ReasonCode | None, wait_min | None). Pure policy over stored state."""
    service = case.service
    if service in KIND_FOR:
        if _free_resources(conn, KIND_FOR[service], service) < 1:
            return DecisionKind.DECLINE, ReasonCode.RESOURCE_UNAVAILABLE, WAIT_MIN[service]
        return DecisionKind.APPROVE, None, None
    if service == AssistanceService.BUGGY:
        free, _seats = buggy.pool(conn)
        if free < 1:
            return DecisionKind.DECLINE, ReasonCode.RESOURCE_UNAVAILABLE, WAIT_MIN[service]
        return DecisionKind.APPROVE, None, None
    if service == AssistanceService.GATE_HOLD:
        cutoff = view.cutoff.value if view is not None else None
        margin = view.verdict.margin_min if view is not None and view.verdict is not None else None
        if cutoff is None or cutoff <= clock.now() or margin is None or margin < GATE_HOLD_MIN_MARGIN:
            return DecisionKind.DECLINE, ReasonCode.APPROVAL_DECLINED, None
        return DecisionKind.APPROVE, None, None
    return DecisionKind.APPROVE, None, None


def _latest_view(conn, journey_id) -> ViewModel | None:
    rev = assist.latest_revision(conn, journey_id)
    return ViewModel.model_validate_json(rev["view_json"]) if rev else None


def _review_pending(conn, clock, seen, delay) -> None:
    rows = conn.execute("SELECT * FROM cases WHERE state='pending_approval'").fetchall()
    for row in rows:
        try:
            case = assist.case_from_row(conn, row)
            now = time.monotonic()
            first = seen.setdefault(case.id, now) if seen is not None else now
            # a short grouping window: families heading to the same gate are planned onto one buggy before anyone is decided
            window = GROUP_WINDOW if delay and case.service in (AssistanceService.WHEELCHAIR, AssistanceService.MOBILITY_ESCORT,
                                                               AssistanceService.BUGGY) and case.id not in buggy._anchors(conn) else 0
            if now - first < delay + window:
                continue
            kind, reason, wait = review_case(conn, case, _latest_view(conn, case.journey_id), clock)
            decision = Decision(event_id=uuid4().hex, case_id=case.id, request_revision=case.request_revision,
                                decision=kind, reason_code=reason, wait_min=wait)
            assist.decide(conn, decision, clock=clock, role=Role.REVIEWER)
        except Exception:
            log.exception("reviewer failed on a case")


def _confirm_dispatch(conn, clock) -> None:
    rows = conn.execute("SELECT * FROM cases WHERE state='dispatch_pending_verification'").fetchall()
    for row in rows:
        try:
            case = assist.case_from_row(conn, row)
            request = DispatchStatusRequest(
                event_id=uuid4().hex, case_id=case.id, request_revision=case.request_revision,
                status=DispatchState.DISPATCHED,
                evidence=SourceStamp(source="dispatch-sim", observed_at=clock.now()),
            )
            assist.report_dispatch(conn, request, clock=clock, role=Role.INTEGRATION)
        except Exception:
            log.exception("reviewer dispatch confirmation failed")


def review_once(target, clock: Clock, *, delay: float = 0, seen: dict | None = None) -> None:
    """Decide every pending case and confirm dispatches. `target` is a connection or a callable that opens one.

    A case is decided only after `delay` seconds of wall-clock time (time.monotonic) since it was first seen.
    """
    if callable(target):
        with closing(target()) as conn:
            _review_pending(conn, clock, seen, delay)
            _confirm_dispatch(conn, clock)
    else:
        _review_pending(target, clock, seen, delay)
        _confirm_dispatch(target, clock)


async def run_reviewer(open_db, clock: Clock, delay_s: float = 3.0, poll_s: float = 0.5) -> None:
    seen: dict = {}
    while True:
        try:
            await asyncio.to_thread(review_once, open_db, clock, delay=delay_s, seen=seen)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("reviewer sweep failed")
        await asyncio.sleep(poll_s)
