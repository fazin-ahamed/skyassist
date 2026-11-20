"""HTML builders for the coordinator dashboard. Shares the kiosk component set so status labels keep one meaning."""

from __future__ import annotations

from html import escape

from kiosk import components as C
from kiosk.components import Lang, hhmm
from kiosk.icons import icon
from skyassist import i18n
from skyassist.models import Case, CaseState, StaffCaseItem


def _landmark(case: Case, L: Lang) -> str:
    if case.pickup is None:
        return ""
    try:
        return escape(i18n.render(case.pickup.landmark, L.code))
    except KeyError:
        return C._code(case.pickup.node_id)


def short_id(case: Case) -> str:
    return case.id[:8]


def time_left(item: StaffCaseItem, L: Lang) -> str:
    t = item.time_remaining
    if t is None:
        return L.t("flag.cutoff_unavailable")
    return L.t("status.boarding_cutoff_passed") if t < 0 else f"{t // 60} min"


def case_row(item: StaffCaseItem, L: Lang, selected: bool) -> str:
    c = item.case
    tone = C._ASSIST_TONE.get(c.state, "info")
    gate = C._code(item.gate.value) if item.gate.value else L.t("kiosk.gate.tbc")
    return (
        f'<div class="sa-board__cells{" is-selected" if selected else ""}">'
        f'<span class="sa-board__city">{L.t(f"assistance.service.{c.service.value}")}</span>'
        f'<span class="sa-board__time">{time_left(item, L)}</span>'
        f'<span class="sa-board__gate">{gate}</span>'
        f'<span class="sa-pill sa-pill--{tone}">{icon(C.PILL_ICON[tone], 18)}{L.t(f"assistance.stage.{c.state.value}")}</span></div>'
    )


def _fact(label: str, value: str) -> str:
    return f'<div class="sa-est"><dt>{label}</dt><dd>{value}</dd></div>'


def case_detail(item: StaffCaseItem, L: Lang) -> str:
    c = item.case
    tone = C._ASSIST_TONE.get(c.state, "info")
    warn = ""
    if not c.pickup_confirmed or c.pickup is None:
        warn = (
            f'<div class="sa-banner sa-banner--tight" role="alert">{icon("alert", 28)}'
            f'<p>{L.t("reason.PICKUP_UNCONFIRMED")}</p></div>'
        )
    reasons = "".join(f"<li>{L.t(f'reason.{r.value}')}</li>" for r in c.reason_codes)
    expires = hhmm(c.approval_expires_at) if c.approval_expires_at else L.t("arrivals.unknown")
    facts = (
        _fact(L.t("ui.staff.service"), L.t(f"assistance.service.{c.service.value}"))
        + _fact(L.t("ui.staff.pickup"), _landmark(c, L) or L.t("arrivals.unknown"))
        + _fact(L.t("ui.staff.time_remaining"), time_left(item, L))
        + _fact("Approval window ends", expires)
        + _fact("Request revision", C._code(str(c.request_revision)))
    )
    return (
        f'<h2 class="sa-title">{L.t("ui.staff.case")} {C._code(short_id(c))}</h2>'
        f'<p class="sa-pill sa-pill--{tone}">{icon(C.PILL_ICON[tone], 18)}{L.t(f"assistance.stage.{c.state.value}")}</p>{warn}'
        f'<div class="sa-headrow">{C.gate_plate(item.gate, L)}{C.cutoff_block(item.cutoff, L)}</div>'
        f'<dl class="sa-staff-facts">{facts}</dl>'
        f'{"<section class=sa-status><h2>" + L.t("ui.staff.priority_reason") + "</h2><ul>" + reasons + "</ul></section>" if reasons else ""}'
    )


OPEN_STATES = (
    CaseState.PENDING_APPROVAL, CaseState.APPROVED, CaseState.DISPATCH_PENDING_VERIFICATION, CaseState.DISPATCHED,
)
