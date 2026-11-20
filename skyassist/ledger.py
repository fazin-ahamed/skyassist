"""The Promise Ledger.

Every sentence the kiosk says about the passenger's connection is one of three kinds:

  verified  a fact a source gave us, still fresh, or a number our own code computed from such facts
  pending   something that is only true if a person acts (a coordinator approves, a staff member arrives)
  unknown   a fact we do not have, or no longer trust

The ledger is built by code from the same evidence as the rest of the view. A model may phrase an answer, but it
can only point at entries in this list, and `grounding.py` rejects any answer that says more than the ledger holds.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from skyassist.models import (
    CaseState, ConnectionStatus, CutoffView, GateView, I18nText, ObservationState, Promise, PromiseState,
    ReasonCode, TransferView, Verdict, VerdictLevel,
)

DUBAI = ZoneInfo("Asia/Dubai")
V, P, U = PromiseState.VERIFIED, PromiseState.PENDING, PromiseState.UNKNOWN

_VERDICT = {
    ConnectionStatus.TIME_AVAILABLE: VerdictLevel.SAFE,
    ConnectionStatus.TIGHT_CONNECTION: VerdictLevel.TIGHT,
    ConnectionStatus.TRANSFER_UNLIKELY_BEFORE_CUTOFF: VerdictLevel.AT_RISK,
    ConnectionStatus.MAY_MISS_BOARDING: VerdictLevel.AT_RISK,
    ConnectionStatus.BOARDING_CUTOFF_PASSED: VerdictLevel.IMPOSSIBLE,
    ConnectionStatus.TIMING_UNVERIFIED: VerdictLevel.UNVERIFIED,
}


def hhmm(moment: datetime) -> str:
    return moment.astimezone(DUBAI).strftime("%H:%M")


def verdict_for(status: ConnectionStatus, transfer: TransferView | None, *, verified: bool) -> Verdict:
    """SAFE, TIGHT, AT_RISK, IMPOSSIBLE, or UNVERIFIED, with the margin against boarding close.

    Fails closed: if any fact the number rests on is not verified, the answer is UNVERIFIED, never a guess.
    A passed cutoff is the one answer that needs no other fact.
    """
    level = _VERDICT[status] if verified or status == ConnectionStatus.BOARDING_CUTOFF_PASSED else VerdictLevel.UNVERIFIED
    margin = transfer.margin_min if transfer and level is not VerdictLevel.UNVERIFIED else None
    return Verdict(level=level, margin_min=margin, text=I18nText(key=f"verdict.{level.value}"))


def build_ledger(
    *, gate: GateView, cutoff: CutoffView, transfer: TransferView | None, has_route: bool,
    case_state: CaseState | None,
) -> tuple[Promise, ...]:
    items: list[Promise] = []

    gate_stamp = gate.provenance.get("value")
    gate_at = {"source": gate_stamp.source, "observed_at": gate_stamp.observed_at} if gate_stamp else {}
    if gate.state is ObservationState.FRESH and gate.value:
        items.append(Promise(key="gate", state=V, text=I18nText(key="ledger.gate.verified", params={"gate": gate.value}), **gate_at))
    elif gate.state is ObservationState.CONFLICT:
        items.append(Promise(key="gate", state=U, text=I18nText(key="ledger.gate.conflict"), **gate_at))
    elif gate.state is ObservationState.STALE and gate.value:
        items.append(Promise(key="gate", state=U, text=I18nText(key="ledger.gate.stale"), **gate_at))
    else:
        items.append(Promise(key="gate", state=U, text=I18nText(key="ledger.gate.unknown")))

    if cutoff.value and not cutoff.unavailable:
        at = {"source": cutoff.source, "observed_at": cutoff.observed_at} if cutoff.source and cutoff.observed_at else {}
        key, state = ("ledger.cutoff.estimated", P) if cutoff.estimated else ("ledger.cutoff.verified", V)
        items.append(Promise(key="cutoff", state=state, text=I18nText(key=key, params={"time": hhmm(cutoff.value)}), **at))
    else:
        items.append(Promise(key="cutoff", state=U, text=I18nText(key="ledger.cutoff.unknown")))

    if transfer and transfer.lower_min is not None and transfer.upper_min is not None:
        at = {"source": transfer.provenance.source, "observed_at": transfer.provenance.observed_at}
        items.append(Promise(key="transfer", state=V, text=I18nText(
            key="ledger.transfer.verified", params={"lower": transfer.lower_min, "upper": transfer.upper_min}), **at))
        if transfer.margin_min is not None:
            if transfer.conditional:
                items.append(Promise(key="margin", state=P, text=I18nText(
                    key="ledger.margin.conditional", params={"margin": transfer.margin_min}), **at))
            elif transfer.margin_min >= 0:
                items.append(Promise(key="margin", state=V, text=I18nText(
                    key="ledger.margin.verified", params={"margin": transfer.margin_min}), **at))
            else:
                items.append(Promise(key="margin", state=V, text=I18nText(
                    key="ledger.margin.negative", params={"margin": -transfer.margin_min}), **at))
    else:
        items.append(Promise(key="transfer", state=U, text=I18nText(key="ledger.transfer.unknown")))

    items.append(Promise(key="route", state=V if has_route else U,
                         text=I18nText(key="ledger.route.verified" if has_route else "ledger.route.unknown")))

    if case_state is not None:
        state, key = {
            CaseState.PENDING_APPROVAL: (P, "pending"), CaseState.APPROVED: (P, "approved"),
            CaseState.DISPATCH_PENDING_VERIFICATION: (P, "approved"), CaseState.DISPATCHED: (V, "dispatched"),
            CaseState.COMPLETED: (V, "completed"), CaseState.DECLINED: (U, "declined"),
            CaseState.EXPIRED: (U, "expired"), CaseState.CANCELLED: (U, "cancelled"),
        }.get(case_state, (P, "pending"))
        items.append(Promise(key="assistance", state=state, text=I18nText(key=f"ledger.assistance.{key}")))
    return tuple(items)
