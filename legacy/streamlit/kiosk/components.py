"""Pure HTML builders for the kiosk and staff views. They render a ViewModel and never compute facts."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from base64 import b64encode
from math import hypot
from urllib.parse import quote

from kiosk.icons import MODE_ICON, icon
from skyassist import i18n
from skyassist.clock import in_dubai
from skyassist.models import (
    AircraftFreshness,
    Applicability,
    ArrivalEstimate,
    AssistanceView,
    BannerKind,
    CaseState,
    ConnectionStatus,
    CutoffView,
    GateView,
    I18nText,
    ObservationState,
    RouteView,
    ViewModel,
)
from skyassist.util.qr import qr_svg


class Lang:
    def __init__(self, code: str = "en") -> None:
        self.code = code
        self.dir = "rtl" if code == "ar" else "ltr"

    def t(self, key: str, **params) -> str:
        return escape(i18n.t(key, self.code, **params), quote=False)

    def attr(self, key: str, **params) -> str:
        return escape(i18n.t(key, self.code, **params))

    def source(self, slug: str) -> str:
        key = "kiosk.source." + slug.replace("-", "_")
        return escape(i18n.t(key, self.code) if i18n.has(key, self.code) else slug.replace("-", " "), quote=False)

    def r(self, text: I18nText | None) -> str:
        return "" if text is None else escape(i18n.render(text, self.code), quote=False)


def hhmm(instant) -> str:
    return in_dubai(instant).strftime("%H:%M")


def _code(value: str) -> str:
    return f'<bdi dir="ltr">{escape(value)}</bdi>'


STATUS_TONE = {
    ConnectionStatus.TIME_AVAILABLE: ("ok", "check"),
    ConnectionStatus.TIGHT_CONNECTION: ("tight", "clock"),
    ConnectionStatus.TRANSFER_UNLIKELY_BEFORE_CUTOFF: ("warn", "alert"),
    ConnectionStatus.MAY_MISS_BOARDING: ("warn", "alert"),
    ConnectionStatus.BOARDING_CUTOFF_PASSED: ("warn", "alert"),
    ConnectionStatus.TIMING_UNVERIFIED: ("info", "info"),
}


def gate_plate(g: GateView, L: Lang) -> str:
    if g.state is ObservationState.FRESH and g.value:
        parts = [L.t(key, **{name: value}) for key, name, value in
                 (("kiosk.gate.concourse", "concourse", g.concourse), ("kiosk.gate.level", "level", g.level)) if value]
        where = " · ".join(parts)
        return (
            '<div class="sa-gate">'
            f'<span class="sa-gate__tag">{L.t("ui.gate")}</span>'
            f'<bdi dir="ltr" class="sa-gate__num">{escape(g.value)}</bdi>'
            f'<span class="sa-gate__where">{where}</span></div>'
        )
    return (
        '<div class="sa-gate sa-gate--off">'
        f'<span class="sa-gate__tag">{L.t("ui.gate")}</span>'
        f'<span class="sa-gate__none">{L.t("kiosk.gate.unconfirmed")}</span></div>'
    )


def cutoff_block(c: CutoffView, L: Lang) -> str:
    if c.unavailable:
        return (
            '<div class="sa-cutoff sa-cutoff--none">'
            f'<p class="sa-cutoff__main">{L.t("flag.cutoff_unavailable")}</p>'
            f'<p class="sa-meta">{L.t("kiosk.cutoff.not_inferred")}</p></div>'
        )
    est = f' <span class="sa-tag">{L.t("kiosk.cutoff.estimated")}</span>' if c.estimated else ""
    return (
        '<div class="sa-cutoff">'
        f'<p class="sa-cutoff__main">{L.t("ui.boarding_closes", time=hhmm(c.value))}{est}</p>'
        f'<p class="sa-meta">{L.t("ui.last_verified", time=hhmm(c.observed_at), source=L.source(c.source))}</p></div>'
    )


def status_block(v: ViewModel, L: Lang) -> str:
    tone, ico = STATUS_TONE[v.status.status]
    return (
        f'<section class="sa-status sa-status--{tone}" aria-labelledby="sa-status-h">'
        f'<h2 id="sa-status-h">{icon(ico, 32)}<span>{L.t(v.status.status.i18n_key)}</span></h2>'
        f"{transfer_block(v, L)}</section>"
    )


def reasons_block(v: ViewModel, L: Lang) -> str:
    items = "".join(f"<li>{L.t(f'reason.{code.value}')}</li>" for code in v.status.reason_codes)
    return f'<section class="sa-why"><h2>{L.t("kiosk.why")}</h2><ul>{items}</ul></section>' if items else ""


def transfer_block(v: ViewModel, L: Lang) -> str:
    t = v.transfer
    if t is None or t.lower_min is None or t.upper_min is None:
        return ""
    if t.includes_readiness:
        main = L.t("transfer.total_with_readiness", lower=t.lower_min, upper=t.upper_min)
    else:
        main = L.t("kiosk.transfer.range", lower=t.lower_min, upper=t.upper_min)
    extra = ""
    if t.margin_min is not None:
        key = "transfer.margin" if t.margin_min >= 0 else "kiosk.transfer.short_by"
        extra += f'<p class="sa-transfer__margin">{L.t(key, margin=abs(t.margin_min))}</p>'
    if t.conditional:
        extra += f'<p class="sa-meta">{L.t("transfer.conditional")}</p>'
    return f'<section class="sa-transfer"><p class="sa-transfer__range">{main}</p>{extra}</section>'


def next_action_block(v: ViewModel, L: Lang) -> str:
    return (
        '<section class="sa-next" aria-live="polite">'
        f'<h2>{L.t("kiosk.next_step")}</h2><p>{L.r(v.next_action.text)}</p></section>'
    )


def banners_block(v: ViewModel, L: Lang) -> str:
    out = []
    for b in v.banners:
        tone, ico = ("tight", "alert") if b.kind is BannerKind.STALE_DATA else ("info", "info")
        out.append(
            f'<div class="sa-banner sa-banner--{tone}" role="status">{icon(ico, 28)}<p>{L.r(b.text)}</p></div>'
        )
    return "".join(out)


_STAGES = (
    (CaseState.PENDING_APPROVAL, "kiosk.stage.requested"),
    (CaseState.APPROVED, "kiosk.stage.approved"),
    (CaseState.DISPATCHED, "kiosk.stage.dispatched"),
    (CaseState.COMPLETED, "kiosk.stage.complete"),
)
_STAGE_INDEX = {
    CaseState.DRAFT: -1,
    CaseState.PENDING_APPROVAL: 0,
    CaseState.APPROVED: 1,
    CaseState.DISPATCH_PENDING_VERIFICATION: 1,
    CaseState.DISPATCHED: 2,
    CaseState.COMPLETED: 3,
}
_ASSIST_TONE = {
    CaseState.DISPATCH_PENDING_VERIFICATION: "tight",
    CaseState.DISPATCHED: "ok",
    CaseState.COMPLETED: "ok",
    CaseState.DECLINED: "warn",
    CaseState.CANCELLED: "warn",
    CaseState.EXPIRED: "warn",
}


def assistance_block(a: AssistanceView | None, L: Lang) -> str:
    if a is None:
        return ""
    tone = _ASSIST_TONE.get(a.state, "info")
    idx = _STAGE_INDEX.get(a.state)
    steps = ""
    if idx is not None:
        items = []
        for i, (_, key) in enumerate(_STAGES):
            cur = ' aria-current="step"' if i == idx else ""
            cls = "done" if i < idx else "now" if i == idx else "next"
            items.append(f'<li class="sa-stage sa-stage--{cls}"{cur}><span class="sa-stage__dot"></span>{L.t(key)}</li>')
        steps = f'<ol class="sa-stages">{"".join(items)}</ol>'
    pickup = (
        f'<p><span class="sa-k">{L.t("ui.staff.pickup")}</span> {L.r(a.pickup_landmark)}</p>' if a.pickup_landmark else ""
    )
    ref = f'<p><span class="sa-k">{L.t("kiosk.case_ref")}</span> {_code(a.case_ref)}</p>' if a.case_ref else ""
    window = ""
    if a.approval_window is not None and a.state is CaseState.PENDING_APPROVAL:
        window = f'<p class="sa-meta">{L.t("kiosk.approval_window", time=hhmm(a.approval_window.end))}</p>'
    alt = f'<p class="sa-alt">{icon("person", 24)}<span>{L.r(a.staffed_alternative)}</span></p>' if a.staffed_alternative else ""
    return (
        f'<section class="sa-assist sa-assist--{tone}" aria-labelledby="sa-assist-h" aria-live="polite">'
        f'<h2 id="sa-assist-h">{icon("wheelchair", 30)}<span>{L.r(a.stage)}</span></h2>'
        f"{steps}{pickup}{ref}{window}{alt}</section>"
    )


def _polyline(route: RouteView, floor: str):
    pts = [(p.x, p.y) for s in route.floor_shapes if s.floor == floor for p in s.points]
    return pts


def _fit(points, n_stops: int, w: float, h: float, pad: float):
    if len(points) < 2:
        points = [(0.0, 0.0), (1.0, 0.0)]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    sx, sy = max(xs) - min(xs), max(ys) - min(ys)
    kx = (w - 2 * pad) / sx if sx else 0.0
    ky = (h - 2 * pad * 0.9) / sy if sy else 0.0
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    pts = [((x - cx) * kx + w / 2, (y - cy) * ky + h / 2) for x, y in points]
    seg = [hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1)]
    total = sum(seg) or 1.0
    stops = []
    for j in range(n_stops):
        target = total * (j / (n_stops - 1) if n_stops > 1 else 0.5)
        run = 0.0
        for i, s in enumerate(seg):
            if run + s >= target or i == len(seg) - 1:
                f = 0 if s == 0 else min(1.0, (target - run) / s)
                stops.append((pts[i][0] + (pts[i + 1][0] - pts[i][0]) * f, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * f))
                break
            run += s
    return pts, stops


def route_map(route: RouteView, L: Lang) -> str:
    runs: list[tuple[str, list]] = []
    for s in route.steps:
        if runs and runs[-1][0] == s.floor:
            runs[-1][1].append(s)
        else:
            runs.append((s.floor, [s]))
    W, LANE = 640, 184
    last = route.steps[-1].number if route.steps else 0
    lanes = []
    for floor, steps in runs:
        pts, stops = _fit(_polyline(route, floor), len(steps), W, LANE, 70)
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        svg = (
            f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {W} {LANE}' preserveAspectRatio='none'>"
            f"<polyline points='{poly}' fill='none' stroke='#005F61' stroke-width='9' stroke-linecap='round' "
            "stroke-linejoin='round'/></svg>"
        )
        nodes = ""
        for s, (x, y) in zip(steps, stops):
            kind = " sa-stop--goal" if s.number == last else " sa-stop--start" if s.number == 1 else ""
            pos = f"left:{x / W * 100:.2f}%;top:{y / LANE * 100:.2f}%"
            nodes += (
                f'<span class="sa-stop{kind}" style="{pos}">{s.number}</span>'
            )
        lanes.append(
            f'<div class="sa-lanewrap"><span class="sa-lane__label">{L.t("kiosk.floor", floor=floor)}</span>'
            f'<div class="sa-lane" style="background-image:url(\'data:image/svg+xml,{quote(svg)}\')">{nodes}</div></div>'
        )
    sim = f'<p class="sa-sim">{L.r(route.simulation_label)}</p>' if route.simulation_label else ""
    return f'<figure class="sa-map" aria-label="{L.attr("kiosk.route_map")}">{"".join(lanes)}{sim}</figure>'


def route_steps(route: RouteView, L: Lang) -> str:
    items = []
    for s in route.steps:
        ico = MODE_ICON.get(s.accessible_mode.value, "walk")
        items.append(
            "<li>"
            f'<span class="sa-step__n">{s.number}</span>'
            f'<span class="sa-step__body"><strong>{L.r(s.landmark)}</strong>'
            f'<span>{L.r(s.text)}</span>'
            f'<span class="sa-meta">{icon(ico, 20)}{L.t(f"mode.{s.accessible_mode.value}")} · {L.t("kiosk.floor", floor=s.floor)}</span></span></li>'
        )
    return f'<ol class="sa-steps" aria-label="{L.attr("ui.route_text_alternative")}">{"".join(items)}</ol>'


def qr_block(v: ViewModel, L: Lang, base_url: str) -> str:
    if v.qr.token:
        svg = qr_svg(f"{base_url.rstrip('/')}/route/{v.qr.token}", label=L.attr("ui.scan_to_continue"))
        uri = "data:image/svg+xml;base64," + b64encode(svg.encode()).decode()
        return (
            f'<section class="sa-qr"><img class="sa-qr__code" alt="{L.attr("ui.scan_to_continue")}" src="{uri}"><div>'
            f'<h2>{L.t("ui.scan_to_continue")}</h2><p class="sa-meta">{L.t("kiosk.qr.text_alt", code=v.qr.token[:8])}</p></div></section>'
        )
    return (
        f'<section class="sa-qr sa-qr--off">{icon("qr", 28)}<p>'
        f'{L.t(f"reason.{v.qr.suppressed_reason.value}")}</p></section>'
    )


def aircraft_block(v: ViewModel, L: Lang) -> str:
    a = v.aircraft
    head = f'<h2>{icon("plane", 28)}<span>{L.t("kiosk.aircraft")}</span></h2>'
    if a.freshness in (AircraftFreshness.CURRENT, AircraftFreshness.LAST_REPORTED) and a.position:
        key = "ui.aircraft_last_reported" if a.freshness is AircraftFreshness.LAST_REPORTED else "kiosk.aircraft.current"
        body = (
            f'<p class="sa-aircraft__pos">{a.position.latitude:.3f}, {a.position.longitude:.3f}</p>'
            f'<p class="sa-meta">{L.t(key, time=hhmm(a.observed_at))} · {escape(a.source or "")}</p>'
        )
    else:
        body = f'<p>{L.t("ui.position_unavailable")}</p>'
    return f'<section class="sa-aircraft">{head}{body}</section>'


def _estimate(e: ArrivalEstimate | None, L: Lang) -> str:
    if e is None or e.applicability is Applicability.UNKNOWN:
        return L.t("arrivals.unknown")
    if e.applicability is Applicability.NOT_APPLICABLE:
        return L.t("arrivals.not_applicable")
    if e.state is not ObservationState.FRESH or e.wait is None or e.wait.upper is None:
        return L.t("ui.freshness.stale")
    return L.t("kiosk.minutes_range", lower=e.wait.lower // 60, upper=(e.wait.upper + 59) // 60)


def arrivals_block(v: ViewModel, L: Lang) -> str:
    rows = "".join(
        f'<div class="sa-est"><dt>{L.t(f"arrivals.{k}")}</dt><dd>{_estimate(getattr(v.arrivals, k), L)}</dd></div>'
        for k in ("baggage", "immigration", "security")
    )
    return f'<section class="sa-arrivals"><h2>{L.t("kiosk.arrivals_estimates")}</h2><dl>{rows}</dl></section>'


PILL_ICON = {"ok": "check", "tight": "clock", "warn": "alert", "info": "info"}


@dataclass(frozen=True)
class BoardRow:
    flight: str
    city: str
    time: str
    gate: str | None
    status: str
    tone: str
    selected: bool = False


def board_header(L: Lang) -> str:
    return (
        '<div class="sa-board__head" aria-hidden="true">'
        f'<span>{L.t("ui.col.flight")}</span><span>{L.t("ui.col.city")}</span>'
        f'<span>{L.t("ui.col.time")}</span><span>{L.t("ui.col.gate")}</span></div>'
    )


def board_cells(row: BoardRow, L: Lang) -> str:
    gate = _code(row.gate) if row.gate else L.t("kiosk.gate.tbc")
    return (
        f'<div class="sa-board__cells{" is-selected" if row.selected else ""}">'
        f'<span class="sa-board__city">{escape(row.city)}</span>'
        f'<span class="sa-board__time">{escape(row.time)}</span>'
        f'<span class="sa-board__gate">{gate}</span>'
        f'{f"<span class=\"sa-pill sa-pill--{row.tone}\">{icon(PILL_ICON[row.tone], 18)}{escape(row.status)}</span>" if row.status else ""}</div>'
    )


def topbar(L: Lang, title: str) -> str:
    return (
        '<header class="sa-top"><div class="sa-brand">'
        f'<span class="sa-brand__name">{L.t("ui.brand")}</span>'
        f'<span class="sa-brand__sub">{escape(title)}</span></div>'
        f'<p class="sa-simbar">{icon("info", 20)}<span>{L.t("kiosk.simulation_badge")}</span></p></header>'
    )
