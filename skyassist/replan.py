"""Replanning when the plan breaks: a gate that moves, a request that is refused, a child on their own.

Everything here is decided by code from facts the system already holds. The kiosk is told the truth in plain words,
and nothing is described as coming until a coordinator has approved it.
"""

import json

from skyassist import assist, booking, buggy
from skyassist.assist import AssistanceError
from skyassist.db import timestamp
from skyassist.models import (
    AssistanceService, CaseState, ConnectionStatus, I18nText, ReasonCode, ViewModel,
)

S, R, SVC = CaseState, ReasonCode, AssistanceService
MOBILITY = (SVC.WHEELCHAIR, SVC.MOBILITY_ESCORT, SVC.BUGGY)
OPEN = assist.OPEN
NOT_WORKING = (ConnectionStatus.TRANSFER_UNLIKELY_BEFORE_CUTOFF, ConnectionStatus.MAY_MISS_BOARDING)


def _view(conn, journey_id):
    latest = assist.latest_revision(conn, journey_id)
    return ViewModel.model_validate_json(latest["view_json"]) if latest else None


def stage(conn, clock, case, service, notice_kind, notice_text, *, why):
    """Stage a new request from an existing one's consent and meeting point. Returns the new case ref or None."""
    journey = assist.journey_row(conn, case.journey_id)
    revision = journey["revision"]
    try:
        with assist.announcing(notice_kind, notice_text):
            response = assist.create_case(
                conn, clock=clock, journey_id=case.journey_id, client_input_id=f"{why}-{case.id}-{service}",
                journey_revision=revision, service=service,
                pickup_node_id=case.pickup.node_id if case.pickup else None, pickup_confirmed=case.pickup is not None,
                consent=True, target_leg_id=case.target_leg_id,
                target_revision=revision if service == SVC.GATE_HOLD else None,
            )
    except AssistanceError:
        return None
    return response.case_ref


def after_decline(conn, case, decision, clock):
    """HS-4.2. The coordinator refused. Tell the family the truth and stage the next best option as a NEW request.

    The order of options is fixed: a seat in a buggy if one is free and the party fits, otherwise a gate hold if the
    boarding cutoff has not passed. A refused wheelchair is never retried silently, and "help is coming" is never said.
    """
    wait = decision.wait_min
    if decision.reason_code != R.RESOURCE_UNAVAILABLE or case.service == SVC.GATE_HOLD or wait is None:
        assist.republish_notice(conn, case.journey_id, clock, "declined", I18nText(key="notice.declined"))
        return
    view = _view(conn, case.journey_id)
    journey = assist.journey_row(conn, case.journey_id)
    party = int(json.loads(journey["party_needs_json"] or "{}").get("party_size", 1))
    params = {"wait": wait}
    if case.service != SVC.BUGGY and buggy.free_seats(conn) >= party:
        if stage(conn, clock, case, SVC.BUGGY, "declined_buggy", I18nText(key="notice.declined_buggy", params=params), why="alt"):
            return
    cutoff = view.cutoff.value if view else None
    if cutoff and cutoff > clock.now():
        if stage(conn, clock, case, SVC.GATE_HOLD, "declined_hold", I18nText(key="notice.declined_hold", params=params), why="alt"):
            return
    assist.republish_notice(conn, case.journey_id, clock, "declined_none", I18nText(key="notice.declined_none", params=params))


def after_group_approved(conn, case, clock):
    buggy.sync_riders(conn, assist._case(conn, case.id), clock)


def after_request(conn, case, clock):
    """A new request exists: if others are heading to the same gate, plan the shared buggy."""
    if case.service not in MOBILITY or buggy._staging.get():
        return
    token = buggy._staging.set(True)
    try:
        destination = buggy.destination_of(conn, case)
        if destination:
            buggy.regroup(conn, clock, destination)
    finally:
        buggy._staging.reset(token)


def after_gate_change(conn, clock, journey, old_gate, new_gate, view, retargeted):
    """HS-4.1. The gate moved after help was asked for. Called with the new view already published.

    The existing requests were retargeted (no second request). If the recalculated connection no longer works, stage a
    gate hold as a new request. Returns the notice for the revision that is already published, or None when staging
    published a richer one itself.
    """
    transfer = view.transfer
    numbers = ({"lower": transfer.lower_min, "upper": transfer.upper_min, "margin": abs(transfer.margin_min)}
               if transfer and transfer.margin_min is not None and transfer.upper_min is not None else None)
    params = {"old": old_gate, "gate": new_gate}
    if numbers is None or not retargeted:
        return ("gate_changed", I18nText(key="notice.gate_changed_plain", params=params))
    if view.verdict and view.verdict.level.value in ("safe", "tight"):
        return ("gate_changed", I18nText(key="notice.gate_changed_ok", params={**params, **numbers}))
    # the connection no longer works: ask staff to hold the gate, on the same consent and meeting point
    anchor = next((c for c in retargeted if c.service in (SVC.WHEELCHAIR, SVC.MOBILITY_ESCORT, SVC.BUGGY)), retargeted[0])
    already = conn.execute("SELECT 1 FROM cases WHERE journey_id=? AND service='gate_hold' AND state IN ('pending_approval','approved')",
                           (journey["id"],)).fetchone()
    if not already and stage(conn, clock, anchor, SVC.GATE_HOLD, "gate_changed_hold",
                             I18nText(key="notice.gate_changed_short", params={**params, **numbers}), why="gatechange"):
        return None
    return ("gate_changed", I18nText(key="notice.gate_changed_plain", params=params))


def ensure_escort(conn, clock, journey, kiosk_node, basis):
    """HS-4.3. A child on their own gets no walking directions. Stage a staff escort request if there is none."""
    if conn.execute("SELECT 1 FROM cases WHERE journey_id=? AND service IN ('mobility_escort','wheelchair') AND state IN "
                    "('draft','pending_approval','approved','dispatch_pending_verification','dispatched')", (journey["id"],)).fetchone():
        return
    node = journey["origin_node_id"] or kiosk_node
    if node is None or assist.pickup_location(conn, journey["airport"], node) is None:
        return
    revision = assist.journey_row(conn, journey["id"])["revision"]
    text = I18nText(key="notice.minor_escort" if basis == "manifest" else "notice.minor_unverified")
    try:
        with assist.announcing("minor_escort", text):
            assist.create_case(conn, clock=clock, journey_id=journey["id"], client_input_id=f"minor-{journey['id']}",
                               journey_revision=revision, service=SVC.MOBILITY_ESCORT, pickup_node_id=node,
                               pickup_confirmed=True, consent=True)
    except AssistanceError:
        return
    now = timestamp(clock.now())
    assist.audit(conn, assist.enqueue_event(conn, journey["id"], "minor_escort_staged", now, processed=True), now,
                 "minor_escort_staged", basis, journey_id=journey["id"], reason=R.UNACCOMPANIED_MINOR)


__all__ = ["after_decline", "after_group_approved", "after_request", "after_gate_change", "ensure_escort", "booking"]
