"""One buggy, several families: who rides and who waits.

Requests that are heading to the same gate are grouped. Whole parties ride or wait, never split. Seats are filled in
priority order: wheelchair users first, then elderly travellers, then families with young children, then anyone else
who needs a step-free route. A higher priority party that fits is never displaced by a lower one. Code stages ONE
combined dispatch (a request a coordinator approves once) and leaves each family that did not fit with its own
separate request. Nothing is dispatched without that approval.
"""

import json
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4

from skyassist import assist
from skyassist.db import timestamp
from skyassist.models import AssistanceService, CaseState, I18nText, ReasonCode, ViewModel

S, R, SVC = CaseState, ReasonCode, AssistanceService
ELIGIBLE = (SVC.WHEELCHAIR, SVC.MOBILITY_ESCORT, SVC.BUGGY)
OPEN_PLAN = (S.PENDING_APPROVAL,)
BUSY = ("approved", "dispatch_pending_verification", "dispatched")
_staging: ContextVar[bool] = ContextVar("buggy_staging", default=False)


@dataclass(frozen=True)
class Party:
    case_id: str
    journey_id: str
    people: int
    needs: dict
    created_at: str

    @property
    def tier(self) -> tuple[int, str]:
        if self.needs.get("wheelchair"):
            return 1, "wheelchair user"
        if self.needs.get("elderly", 0) > 0:
            return 2, "elderly traveller"
        if self.needs.get("children", 0) > 0:
            return 3, "young children"
        return 4, "step-free"


def plan_seats(parties: list[Party], *, seats: int, buggies: int) -> dict:
    """Pure seat plan. No database, no clock, no model: the same requests always give the same plan."""
    ordered = sorted(parties, key=lambda p: (p.tier[0], p.created_at, p.case_id))
    waiting = list(ordered)
    runs: list[list[Party]] = []
    for _ in range(max(0, buggies)):
        left, run = seats, []
        for party in list(waiting):
            if party.people <= left:
                run.append(party)
                left -= party.people
                waiting.remove(party)
        if run:
            runs.append(run)
    riders = [p for run in runs for p in run]
    return {
        "seats": seats, "buggies": buggies,
        "riders": [{"case_id": p.case_id, "journey_id": p.journey_id, "people": p.people, "seats": p.people,
                    "priority": p.tier[0], "basis": p.tier[1], "run": index + 1}
                   for index, run in enumerate(runs) for p in run],
        "leftover": [{"case_id": p.case_id, "journey_id": p.journey_id, "people": p.people, "seats": p.people,
                      "priority": p.tier[0], "basis": p.tier[1]} for p in waiting],
        "seats_used": sum(p.people for p in riders),
    }


def pool(conn) -> tuple[int, int]:
    """(buggies free to plan with, seats per buggy). A buggy already approved or out on a run is not free."""
    row = conn.execute("SELECT total, seats FROM resources WHERE kind='buggy'").fetchone()
    if row is None:
        return 0, 0
    busy = conn.execute(
        f"""SELECT COUNT(*) FROM buggy_groups g JOIN cases c ON c.id = g.anchor_case_id
            WHERE c.state IN ({",".join("?" for _ in BUSY)})""", BUSY).fetchone()[0]
    return max(0, row["total"] - busy), row["seats"]


def free_seats(conn) -> int:
    free, seats = pool(conn)
    return free * seats


def destination_of(conn, case) -> str | None:
    note = assist.retarget_note(conn, case.id)
    if note and note.get("to"):
        return note["to"]
    latest = assist.latest_revision(conn, case.journey_id)
    return ViewModel.model_validate_json(latest["view_json"]).gate.value if latest else None


def _anchors(conn) -> set[str]:
    return {row[0] for row in conn.execute("SELECT anchor_case_id FROM buggy_groups")}


def eligible(conn, destination: str) -> list[Party]:
    anchors = _anchors(conn)
    parties = []
    for row in conn.execute(
        f"SELECT * FROM cases WHERE state='pending_approval' AND service IN ({','.join('?' for _ in ELIGIBLE)}) ORDER BY created_at, rowid",
        tuple(ELIGIBLE),
    ):
        if row["id"] in anchors:
            continue
        case = assist.case_from_row(conn, row)
        if destination_of(conn, case) != destination:
            continue
        journey = assist.journey_row(conn, case.journey_id)
        needs = json.loads(journey["party_needs_json"] or "{}")
        parties.append(Party(case.id, case.journey_id, int(needs.get("party_size", 1)), needs, timestamp(case.created_at)))
    return parties


def regroup(conn, clock, destination: str) -> dict | None:
    """Plan the buggy for everyone waiting for approval to the same gate, and stage the one combined dispatch."""
    if not destination:
        return None
    parties = eligible(conn, destination)
    now = timestamp(clock.now())
    # a plan that is still only a proposal is replaced, never stacked
    for row in conn.execute(
        """SELECT c.* FROM buggy_groups g JOIN cases c ON c.id = g.anchor_case_id
           WHERE g.destination=? AND c.state='pending_approval'""", (destination,)).fetchall():
        stale = assist.case_from_row(conn, row)
        event_id = assist.enqueue_event(conn, stale.journey_id, "group_replaced", now, processed=True)
        assist._transition(conn, stale, S.CANCELLED, event_id, clock, R.GROUPED_DISPATCH)
        assist.audit(conn, event_id, now, "group_replaced", "cancelled", case=stale, reason=R.GROUPED_DISPATCH)
    free, seats = pool(conn)
    if len(parties) < 2 or not free:
        return None
    plan = plan_seats(parties, seats=seats, buggies=free)
    if len(plan["riders"]) < 2:
        return None
    plan = {**plan, "destination": destination}
    first = next(p for p in parties if p.case_id == plan["riders"][0]["case_id"])
    anchor_row = assist._case(conn, first.case_id)
    journey = assist.journey_row(conn, first.journey_id)
    waits = len(plan["leftover"])
    with assist.announcing("group_rides", I18nText(key="notice.group_rides", params={"gate": destination})):
        response = assist.create_case(
            conn, clock=clock, journey_id=first.journey_id, client_input_id=f"group-{uuid4().hex}",
            journey_revision=journey["revision"], service=SVC.BUGGY,
            pickup_node_id=anchor_row.pickup.node_id if anchor_row.pickup else None,
            pickup_confirmed=anchor_row.pickup is not None, consent=True, target_leg_id=anchor_row.target_leg_id,
        )
    plan["anchor_case_id"] = response.case_ref
    conn.execute("INSERT INTO buggy_groups (id, anchor_case_id, destination, created_at, seats, plan_json) VALUES (?, ?, ?, ?, ?, ?)",
                 (uuid4().hex, response.case_ref, destination, now, plan["seats_used"], json.dumps(plan)))
    for member, key, text in (
        *((r, "group_rides", I18nText(key="notice.group_rides", params={"gate": destination})) for r in plan["riders"][1:]),
        *((w, "group_waits", I18nText(key="notice.group_waits", params={"gate": destination})) for w in plan["leftover"]),
    ):
        assist.republish_notice(conn, member["journey_id"], clock, key, text)
    # the anchor's own view was published before the plan row existed: refresh it so it shows its seat
    assist.republish_notice(conn, first.journey_id, clock, "group_rides", I18nText(key="notice.group_rides", params={"gate": destination}))
    assist.audit(conn, assist.enqueue_event(conn, first.journey_id, "group_planned", now, processed=True), now,
                 "group_planned", f"{len(plan['riders'])} riding, {waits} waiting",
                 case=assist._case(conn, response.case_ref))
    return plan


def sync_riders(conn, anchor, clock) -> None:
    """The coordinator approved (or the run was dispatched) once: bring every family on it to the same state.

    Riders are never dispatched separately: they ride the combined dispatch, which has its own outbox entry.
    """
    row = conn.execute("SELECT plan_json FROM buggy_groups WHERE anchor_case_id=?", (anchor.id,)).fetchone()
    if row is None:
        return
    now = timestamp(clock.now())
    target = {S.APPROVED: S.APPROVED, S.DISPATCH_PENDING_VERIFICATION: S.APPROVED, S.DISPATCHED: S.DISPATCHED}.get(anchor.state)
    if target is None:
        return
    for rider in json.loads(row[0])["riders"]:
        case = assist._case(conn, rider["case_id"])
        if case is None:
            continue
        steps = {S.PENDING_APPROVAL: [S.APPROVED, S.DISPATCHED], S.APPROVED: [S.DISPATCHED]}.get(case.state, [])
        if target == S.APPROVED:
            steps = steps[:1] if case.state == S.PENDING_APPROVAL else []
        for step in steps:
            event_id = assist.enqueue_event(conn, case.journey_id, "approved_via_group", now, processed=True)
            case = assist._transition(conn, case, step, event_id, clock, None)
            assist.audit(conn, event_id, now, "approved_via_group", case.state, case=case, reason=R.GROUPED_DISPATCH)


def plans(conn) -> list[dict]:
    """What the coordinator console shows: every plan, newest first, with the state of its combined dispatch."""
    out = []
    for row in conn.execute(
        """SELECT g.id, g.destination, g.created_at, g.plan_json, c.state, c.id AS case_id
           FROM buggy_groups g JOIN cases c ON c.id = g.anchor_case_id
           WHERE c.state != 'cancelled' ORDER BY g.created_at DESC, g.rowid DESC LIMIT 10"""):
        out.append({"id": row["id"], "case_id": row["case_id"], "state": row["state"], "destination": row["destination"],
                    "created_at": row["created_at"], **json.loads(row["plan_json"])})
    return out
