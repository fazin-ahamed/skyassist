"""SkyAssist coordinator dashboard (Streamlit). Runs as its own process and talks to the service over HTTP."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
import streamlit as st

from kiosk.api import Service
from kiosk.components import Lang, hhmm
from kiosk.icons import css as icon_css
from kiosk.icons import icon
from skyassist.config import CONFIG, Mode
from skyassist.models import (
    CaseState, DecisionKind, DecisionRequest, DispatchState, DispatchStatusRequest, FlightDirection,
    GetStaffCasesRequest, SourceStamp,
)
from skyassist.service import DEMO_COORDINATOR_TOKEN
from skyassist.staff_client import StaffClient
from staff import views as V

URL = os.environ.get("SKYASSIST_SERVICE_URL", "http://127.0.0.1:8000")
TOKEN = os.environ.get("SKYASSIST_COORDINATOR_TOKEN") or (DEMO_COORDINATOR_TOKEN if CONFIG.mode is Mode.DEMO else "")
AIRPORT = os.environ.get("SKYASSIST_AIRPORT", "DXB")
L = Lang("en")

st.set_page_config(page_title="SkyAssist coordinator", layout="wide", initial_sidebar_state="collapsed")
EXTRA = """
.block-container { max-width: 1600px; }
.st-key-queue, .st-key-case { background: var(--surface); border: 1px solid var(--rule); border-radius: var(--r-panel); padding: 24px; }
.sa-staff-facts { margin: 0; padding: 0 !important; display: grid; gap: 10px; }
.st-key-case .sa-pill { grid-column: auto; display: inline-block; width: fit-content; }
.st-key-queue [data-testid="stHorizontalBlock"] { align-items: stretch; }
"""
st.html(f"<style>{(ROOT / 'kiosk' / 'static' / 'kiosk.css').read_text()}{icon_css()}{EXTRA}</style>")
S = st.session_state
S.setdefault("sel", None)
S.setdefault("result", None)
S.setdefault("filter", "open")

st.html(
    f'<header class="sa-top"><div class="sa-brand"><span class="sa-brand__name">SkyAssist</span>'
    f'<span class="sa-brand__sub">Coordinator, {AIRPORT}</span></div></header>'
    f'<p class="sa-simbar">{icon("info", 20)}<span>{L.t("kiosk.simulation_badge")}</span></p>'
)


def fetch(states):
    with StaffClient(URL, TOKEN) as client:
        return client.list_cases(GetStaffCasesRequest(airport=AIRPORT, states=states))


def act(item, kind: DecisionKind | None) -> None:
    c = item.case
    key = f"evt_{c.id}_{c.request_revision}_{kind or 'dispatch'}"
    event_id = S.setdefault(key, uuid4().hex)
    try:
        with StaffClient(URL, TOKEN) as client:
            if kind is None:
                S.result = client.dispatch(DispatchStatusRequest(
                    event_id=event_id, case_id=c.id, request_revision=c.request_revision,
                    status=DispatchState.DISPATCHED, evidence=SourceStamp(source="demo-staff", observed_at=fetch_time()),
                ))
            else:
                S.result = client.decide(DecisionRequest(
                    event_id=event_id, case_id=c.id, request_revision=c.request_revision, decision=kind,
                ))
    except httpx.HTTPError:
        S.result = "error"


def fetch_time() -> datetime:
    return datetime.fromisoformat(demo_call("GET", "/clock")["now"])


@st.fragment(run_every=3)
def board() -> None:
    states = {"open": V.OPEN_STATES, "pending": (CaseState.PENDING_APPROVAL,), "all": tuple(CaseState)}[S.filter]
    left, right = st.columns([2, 3], gap="large")
    try:
        page = fetch(states)
    except httpx.HTTPError:
        left.html(f'<div class="sa-state">{icon("alert", 32)}<p>{L.t("ui.offline_notice")}</p></div>')
        left.button(L.t("ui.retry"), key="retry_btn", type="primary")
        return
    items = page.cases
    ids = [i.case.id for i in items]
    if S.sel not in ids:
        S.sel = ids[0] if ids else None
    with left.container(key="queue"):
        st.html(f'<h2 class="sa-title">{L.t("ui.staff.queue")}</h2>')
        st.radio("filter", ["open", "pending", "all"], key="filter", horizontal=True, label_visibility="collapsed",
                 format_func=lambda f: {"open": "Open", "pending": "Needs decision", "all": "All"}[f])
        if not items:
            st.html(f'<div class="sa-state">{icon("info", 32)}<p>{L.t("ui.staff.empty_queue")}</p></div>')
        for item in items:
            with st.container(key=f"row_{item.case.id}"):
                b, c = st.columns([1.6, 2.4], vertical_alignment="center")
                b.button(V.short_id(item.case), key=f"pick_{item.case.id}", use_container_width=True,
                         type="primary" if item.case.id == S.sel else "secondary", on_click=S.update, kwargs={"sel": item.case.id})
                c.html(V.case_row(item, L, item.case.id == S.sel))
    item = next((i for i in items if i.case.id == S.sel), None)
    with right.container(key="case"):
        if item is None:
            st.html(f'<p class="sa-meta">{L.t("kiosk.select_flight")}</p>')
            return
        st.html(V.case_detail(item, L))
        pending = item.case.state is CaseState.PENDING_APPROVAL
        a, b = st.columns(2)
        a.button(L.t("ui.staff.approve"), key="approve_btn", type="primary", use_container_width=True, disabled=not pending,
                 on_click=act, args=(item, DecisionKind.APPROVE))
        b.button(L.t("ui.staff.decline"), key="decline_btn", use_container_width=True, disabled=not pending,
                 on_click=act, args=(item, DecisionKind.DECLINE))
        if CONFIG.mode is Mode.DEMO and item.case.state is CaseState.APPROVED:
            st.button("Mark dispatched (simulated staff)", key="dispatch_btn", use_container_width=True,
                      on_click=act, args=(item, None))
        r = S.result
        if r == "error":
            st.html(f'<div class="sa-banner sa-banner--tight" role="alert">{icon("alert", 28)}<p>{L.t("ui.error_generic")}</p></div>')
        elif r is not None:
            tone = "info" if r.reason_code is None else "tight"
            why = f" {L.t(f'reason.{r.reason_code.value}')}" if r.reason_code else ""
            st.html(
                f'<div class="sa-banner sa-banner--{tone}" role="status">{icon("check" if tone == "info" else "alert", 28)}'
                f'<p>{L.t("ui.staff.decision_sent")}: {r.outcome.value}.{why}</p></div>'
            )

def demo_call(method: str, path: str, **kw):
    with StaffClient(URL, TOKEN) as client:
        r = client.http.request(method, path, **kw)
        r.raise_for_status()
        return r.json()


def demo_act(method: str, path: str, **kw) -> None:
    try:
        demo_call(method, path, **kw)
    except httpx.HTTPError:
        S.demo_error = True
    else:
        S.demo_error = False


def feed_act(action: str) -> None:
    if S.get("feed_leg"):
        demo_act("POST", f"/demo/feed/{action}", json={"leg_id": S.feed_leg, "field": "gate"})


@st.fragment(run_every=5)
def demo_controls() -> None:
    try:
        state = demo_call("GET", "/demo/state")
    except httpx.HTTPError:
        return
    with st.container(key="demo"):
        now = datetime.fromisoformat(state["now"])
        st.html(
            f'<h2 class="sa-title">Scenario controls</h2>'
            f'<p class="sa-meta">{icon("clock", 20)}<span>Simulated time {hhmm(now)} (Dubai). '
            "Approval and dispatch stay in the case queue above; these controls only change the world.</span></p>"
        )
        c1, c2, c3 = st.columns(3)
        c1.button("Back to scenario start", key="clock_reset", use_container_width=True,
                  on_click=demo_act, args=("POST", "/demo/clock"), kwargs={"json": {"name": "C01"}})
        c2.button("Advance 1 minute", key="clock_1", use_container_width=True,
                  on_click=demo_act, args=("POST", "/demo/clock"), kwargs={"json": {"advance_seconds": 60}})
        c3.button("Advance 5 minutes", key="clock_5", use_container_width=True,
                  on_click=demo_act, args=("POST", "/demo/clock"), kwargs={"json": {"advance_seconds": 300}})
        if S.get("demo_error"):
            st.html(f'<div class="sa-banner sa-banner--tight" role="alert">{icon("alert", 28)}<p>{L.t("ui.error_generic")}</p></div>')
        try:
            board = Service(URL).flights(AIRPORT, FlightDirection.DEPARTURES)
        except Exception:
            board = None
        if board is not None and board.flights:
            legs = {i.leg.id: f"{i.leg.operating_carrier} {i.leg.operating_number} ({i.leg.scheduled_operation_date})"
                    for i in board.flights}
            held = ", ".join(f"{h['leg_id']} {h['field']}" for h in state.get("held", [])) or "none"
            st.html(f'<p class="sa-meta"><strong>Gate feed</strong><span>Held: {held}</span></p>')
            f1, f2, f3 = st.columns([2, 1, 1], vertical_alignment="bottom")
            f1.selectbox("Flight", list(legs), format_func=legs.get, key="feed_leg")
            for col, action, label in ((f2, "hold", "Hold gate updates"), (f3, "resume", "Resume")):
                col.button(label, key=f"feed_{action}", use_container_width=True, on_click=feed_act, args=(action,))
        for event in state["events"]:
            label, meta, act = st.columns([3, 1.6, 1.2], vertical_alignment="center")
            label.html(f'<p class="sa-meta"><strong>{event["label"]}</strong></p>')
            meta.html(f'<p class="sa-meta">{", ".join(event["scenario_ids"]) or event["kind"]}</p>')
            act.button("Applied" if event["applied"] else "Apply", key=f"evt_{event['id']}", disabled=event["applied"],
                       use_container_width=True, on_click=demo_act, args=("POST", f"/demo/events/{event['id']}/apply"))


board()
if CONFIG.mode is Mode.DEMO:
    demo_controls()
