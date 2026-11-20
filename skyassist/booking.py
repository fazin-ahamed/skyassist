"""Who a journey belongs to, and who may change what.

A journey can carry a booking reference (typed in, or read from a boarding pass). The manifest says who is on that
booking. Three rules sit on top of it, and all three are plain code that no message can talk around:

  * Approvals come only from the coordinator dashboard. A passenger message that tries to approve, however it is
    worded and whoever it claims to be, is logged and refused.
  * A request can be changed or cancelled only from a journey on the same booking. Everything else is logged and refused.
  * A child travelling alone never gets walking directions. The manifest decides; a message can raise the question
    but never lower the guard.
"""

import json
import re

from skyassist import assist
from skyassist.models import AssistanceService, I18nText, ReasonCode, Role

R = ReasonCode
MINOR_AGE = 18

_APPROVE = re.compile(r"\b(approv\w*|authori[sz]\w*|sign(?:ed)?[ -]?off|clear(?:ed)? it|release it)\b", re.I)
_CANCEL = re.compile(r"\b(cancel\w*|withdraw\w*|revok\w*|remov\w*|delete\w*|call off|stop the)\b", re.I)
_REQUEST = re.compile(r"\b(wheel\s?chair|buggy|escort|assistance|help request|request|gate hold|hold|dispatch|case)\b", re.I)
_SERVICES = {"wheelchair": AssistanceService.WHEELCHAIR, "wheel chair": AssistanceService.WHEELCHAIR,
             "buggy": AssistanceService.BUGGY, "escort": AssistanceService.MOBILITY_ESCORT,
             "gate hold": AssistanceService.GATE_HOLD}
_AGE = re.compile(r"\b(?:i'?m|i am|am)\s+(\d{1,2})\b|\b(\d{1,2})\s*(?:years?|yrs?)[ -]*old\b", re.I)
_ALONE = re.compile(r"\b(?:travel(?:l)?ing|flying|am)\s+(?:alone|by myself|on my own)\b|\bunaccompanied\b", re.I)


def classify(text: str | None) -> str | None:
    """What a passenger message is trying to do to a request, before any model reads it. None means nothing special."""
    if not text:
        return None
    if _APPROVE.search(text) and _REQUEST.search(text):
        return "approval"
    if _CANCEL.search(text) and _REQUEST.search(text):
        return "cancel"
    if claims_minor(text):
        return "minor_claim"
    return None


def claims_minor(text: str) -> bool:
    for match in _AGE.finditer(text):
        age = int(next(group for group in match.groups() if group))
        if age < MINOR_AGE:
            return True
    return bool(_ALONE.search(text))


def booking_ref(conn, journey_id: str) -> str | None:
    row = conn.execute("SELECT booking_ref FROM journeys WHERE id=?", (journey_id,)).fetchone()
    return row[0] if row else None


def manifest(conn, ref: str | None) -> list:
    return conn.execute("SELECT * FROM manifest WHERE booking_ref=?", (ref,)).fetchall() if ref else []


def minor_basis(conn, journey_id: str) -> str | None:
    """"manifest" when the booking says a child travels alone, "claimed" when only the message says so, else None.

    A booking that is on the manifest with no flagged child is an adult, whatever a message claims. A message that
    claims a child with no booking on file fails closed: the escort is staged and staff confirm.
    """
    rows = manifest(conn, booking_ref(conn, journey_id))
    if any(row["unaccompanied_minor"] for row in rows):
        return "manifest"
    claimed = conn.execute("SELECT 1 FROM meta WHERE key=?", (f"minor_claim:{journey_id}",)).fetchone()
    return "claimed" if claimed and not rows else None


def same_booking_cases(conn, journey_id: str) -> list:
    """Open requests on any journey that shares this journey's booking. Empty when the booking is unknown."""
    ref = booking_ref(conn, journey_id)
    if not ref:
        return []
    return conn.execute(
        """SELECT c.* FROM cases c JOIN journeys j ON j.id = c.journey_id
           WHERE j.booking_ref=? AND c.state IN ('draft', 'pending_approval', 'approved',
                                                 'dispatch_pending_verification', 'dispatched')
           ORDER BY c.created_at, c.rowid""", (ref,)).fetchall()


def screen(conn, journey, body, clock, now, event_id):
    """Apply the rules to one passenger message. Returns (kind, text) for the kiosk to say, or None.

    Runs inside the journey's commit, so a refusal, a cancellation and the revision that reports them land together.
    """
    text = getattr(body, "text", None)
    cancel_ref = getattr(body, "cancel_case_ref", None)
    kind = "cancel" if cancel_ref else classify(text)
    journey_id = journey["id"]
    if kind == "approval":
        assist.audit(conn, event_id, now, "approval_attempt_refused", "refused", journey_id=journey_id,
                     role=Role.PASSENGER, reason=R.APPROVAL_NOT_ALLOWED)
        return "approval_refused", I18nText(key="notice.approval_refused")
    if kind == "minor_claim":
        conn.execute("INSERT OR IGNORE INTO meta VALUES (?, '1')", (f"minor_claim:{journey_id}",))
        assist.audit(conn, event_id, now, "minor_claimed_in_message", "recorded", journey_id=journey_id, role=Role.PASSENGER,
                     reason=R.UNACCOMPANIED_MINOR)
        return None
    if kind != "cancel":
        return None
    own = same_booking_cases(conn, journey_id)
    if cancel_ref:
        own = [row for row in own if row["id"] == cancel_ref]
    elif text:
        named = {service for word, service in _SERVICES.items() if word in text.casefold()}
        if named:
            own = [row for row in own if row["service"] in named]
    if not own:
        assist.audit(conn, event_id, now, "cancel_refused", "refused", journey_id=journey_id, role=Role.PASSENGER,
                     reason=R.NOT_ON_BOOKING)
        return "cancel_refused", I18nText(key="notice.cancel_refused")
    for row in own:
        assist.cancel(conn, row["id"], clock=clock, journey_id=row["journey_id"])
        assist.audit(conn, event_id, now, "cancelled_by_booking", "applied", journey_id=journey_id,
                     case=assist.case_from_row(conn, row), role=Role.PASSENGER)
    return "cancelled", I18nText(key="notice.cancelled")


def load_manifest(conn, people: list[dict], resources: list[dict]) -> None:
    """Seed the demo manifest and the equipment pool from data/passengers.json."""
    conn.execute("DELETE FROM manifest")
    for person in people:
        conn.execute(
            "INSERT INTO manifest (booking_ref, pax_id, name, age, unaccompanied_minor, elderly, wheelchair) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (person["booking_ref"], person["pax_id"], person["name"], person["age"], int(person.get("unaccompanied_minor", False)),
             int(person.get("elderly", False)), int(person.get("wheelchair", False))),
        )
    conn.execute("DELETE FROM resources")
    for item in resources:
        conn.execute("INSERT INTO resources (kind, total, seats) VALUES (?, ?, ?)", (item["kind"], item["total"], item["seats"]))


def dumps(value) -> str:
    return json.dumps(value, separators=(",", ":"))
