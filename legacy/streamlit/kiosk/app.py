"""SkyAssist passenger kiosk (Streamlit).

Renders server-owned view models. With SKYASSIST_SERVICE_URL set it talks to the live service; without it, typed
demo states stand in so every screen can be inspected.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from kiosk import components as C
from kiosk import demo_views as D
from kiosk.api import Service, ServiceDown, ServiceRefused, board_rows
from kiosk.components import BoardRow, Lang
from kiosk.icons import css as icon_css
from kiosk.icons import icon
from skyassist.models import (
    AssistanceRequest, AssistanceService, CreateJourneyRequest, FlightDirection, Intent, JourneyInputRequest,
    ObservationState, PartyNeeds, PassengerAction, Profile,
)

SERVICE_URL = os.environ.get("SKYASSIST_SERVICE_URL")
LIVE = bool(SERVICE_URL)
AIRPORT = os.environ.get("SKYASSIST_AIRPORT", "DXB")
IDLE_WARN = int(os.environ.get("SKYASSIST_IDLE_WARN", "90"))
IDLE_END = int(os.environ.get("SKYASSIST_IDLE_END", "120"))
BASE_URL = os.environ.get("SKYASSIST_PUBLIC_URL", SERVICE_URL or "http://kiosk.local:8000")

DEMO_FLIGHTS = {
    "departures": [
        ("ZA 402", "Frankfurt", "10:35", "c01_pending"),
        ("ZB 118", "London", "10:50", "c02_rerouted"),
        ("ZC 664", "Doha", "11:20", "c03_available"),
        ("ZD 331", "Amman", "10:08", "c04_unlikely"),
        ("ZE 907", "Muscat", "11:45", "c05_no_cutoff"),
        ("ZF 220", "Cairo", "12:10", "c06_stale"),
        ("ZG 515", "Mumbai", "12:30", "c10_queue_stale"),
    ],
    "arrivals": [("ZH 205", "Mumbai", "09:55", "arrivals"), ("ZJ 842", "Colombo", "10:20", "arrivals")],
}
PARTY = {
    "standard": (Profile.STANDARD, PartyNeeds()),
    "family": (Profile.FAMILY, PartyNeeds(party_size=3, children=1)),
    "wheelchair": (Profile.REDUCED_MOBILITY, PartyNeeds(party_size=2, step_free=True, wheelchair=True)),
    "both": (Profile.FAMILY_MOBILITY, PartyNeeds(party_size=3, children=1, step_free=True, wheelchair=True)),
}

st.set_page_config(page_title="SkyAssist", layout="wide", initial_sidebar_state="collapsed")
st.html(f"<style>{(Path(__file__).parent / 'static' / 'kiosk.css').read_text()}{icon_css()}</style>")

S = st.session_state
qp = st.query_params
S.setdefault("lang", qp.get("lang", "en"))
S.setdefault("tab", qp.get("tab", "departures"))
S.setdefault("sel", 0)
S.setdefault("state_override", qp.get("state"))
S.setdefault("following", qp.get("follow") == "1")
S.setdefault("draft", False)
S.setdefault("help", False)
S.setdefault("ask", False)
S.setdefault("where", False)
S.setdefault("assist_state", None)
S.setdefault("journeys", {})
S.setdefault("views", {})
S.setdefault("last_active", time.time())
S["last_active"] = time.time()
if LIVE and "svc" not in S:
    S.svc = Service(SERVICE_URL)
L = Lang(S.lang)
if L.dir == "rtl":
    st.html("<style>.stApp{direction:rtl}</style>")


def reset_session() -> None:
    for k in list(S.keys()):
        del S[k]
    st.rerun()


def pick(**kw) -> None:
    S.update(kw)


def notice(kind: str, text: str, ico: str) -> None:
    st.html(f'<div class="sa-banner sa-banner--{kind}" role="alert">{icon(ico, 28)}<p>{text}</p></div>')


def service_call(fn, *args, **kw):
    """Run a service call; show a plain recovery message on failure and return None."""
    try:
        return fn(*args, **kw)
    except ServiceDown:
        notice("tight", L.t("kiosk.service_down"), "alert")
    except ServiceRefused as exc:
        notice("tight", L.t(f"reason.{exc.reason_code}") if exc.reason_code else L.t("ui.error_generic"), "alert")
    return None


def send_input(jid: str, view_rev: int | None = None, **fields) -> None:
    body = JourneyInputRequest(journey_id=jid, client_input_id=uuid4().hex, known_revision=view_rev, **fields)
    service_call(S.svc.send, body)


def open_journey(party: str = "standard", **legs) -> str | None:
    profile, needs = PARTY[party]
    jid = service_call(S.svc.create_journey, CreateJourneyRequest(
        airport=AIRPORT, profile=profile, party_needs=needs, language=S.lang, **legs))
    if jid:
        send_input(jid, intent=Intent.CHECK_CONNECTION, party_needs=needs, **legs)
    return jid


# Top bar
top = st.container(key="topbar").columns([5, 1.2, 1, 1.6], vertical_alignment="center")
top[0].html(C.topbar(L, L.t("ui.terminal_guide")))
top[1].button(L.t("ui.lang_toggle"), key="lang_btn", use_container_width=True, on_click=pick,
              kwargs={"lang": "ar" if S.lang == "en" else "en"})
top[2].button(L.t("ui.help"), key="help_btn", use_container_width=True, on_click=pick, kwargs={"help": not S.help})
top[3].button(L.t("ui.end_session"), key="end_btn", use_container_width=True, on_click=reset_session)

if S.help:
    st.html(
        f'<div class="sa-banner sa-banner--info" role="region">{icon("help", 28)}<p>{L.t("kiosk.help.body")}</p></div>'
        f'<div class="sa-banner sa-banner--tight" role="alert">{icon("alert", 28)}<p>{L.t("ui.urgent.help")}</p></div>'
    )


@st.fragment(run_every=1)
def idle_guard() -> None:
    idle = time.time() - S.last_active
    if idle >= IDLE_END:
        reset_session()
    if idle >= IDLE_WARN:
        notice("tight", L.t("ui.idle_countdown", seconds=int(IDLE_END - idle)), "clock")
        if st.button(L.t("ui.extend_session"), key="extend_btn", type="primary"):
            S.last_active = time.time()


idle_guard()

TABS = (("departures", "ui.tab.departures"), ("arrivals", "ui.tab.arrivals"), ("connection", "ui.tab.connection"))
tabs = st.container(key="tabs").columns([1, 1, 1.5, 2.6])
for col, (name, key) in zip(tabs, TABS):
    col.button(L.t(key), key=f"tab_{name}_{'on' if S.tab == name else 'off'}", use_container_width=True, on_click=pick,
               kwargs={"tab": name, "sel": 0, "following": False, "draft": False, "assist_state": None, "where": False})
query = tabs[3].text_input(L.attr("ui.search_flight"), placeholder=L.attr("kiosk.search_placeholder"),
                           label_visibility="collapsed", key="search")


# Detail panel
def choice_buttons(jid: str, view) -> None:
    action = view.next_action.action
    for c in view.next_action.choices:
        def chosen(c=c):
            if action is PassengerAction.CONFIRM_PICKUP:
                S.draft, S.pickup_node, S.pickup_label = True, c.id, c.label
            elif action is PassengerAction.CHOOSE_LANDMARK:
                send_input(jid, view.revision, confirmed_origin_id=c.id)
            elif action is PassengerAction.CONFIRM_DATE:
                send_input(jid, view.revision, intent=Intent.CHECK_CONNECTION, operation_date=date.fromisoformat(c.id))
            else:
                send_input(jid, view.revision, intent=Intent.CHECK_CONNECTION, flight_aliases=(c.id,))
        st.button(L.r(c.label), key=f"choice_{jid}_{c.id}", use_container_width=True, on_click=chosen)


def type_panel(jid: str, view) -> None:
    st.text_input(L.attr("ui.type"), key=f"ask_{jid}", placeholder=L.attr("kiosk.search_placeholder"))

    def go() -> None:
        if S.get(f"ask_{jid}"):
            send_input(jid, view.revision, text=S[f"ask_{jid}"], language=S.lang)
    st.button(L.t("ui.submit_request"), key=f"ask_go_{jid}", on_click=go)


def where_panel(jid: str, view) -> None:
    st.text_input(L.attr("kiosk.assist.pickup_label"), key=f"where_{jid}")

    def go() -> None:
        if S.get(f"where_{jid}"):
            send_input(jid, view.revision, landmark_text=S[f"where_{jid}"])
    st.button(L.t("ui.take_me_there"), key=f"where_go_{jid}", on_click=go)


def request_assistance(jid: str | None, view) -> None:
    if not LIVE:
        S.draft = True
    elif jid:
        S.want_assist = jid
        send_input(jid, view.revision, intent=Intent.REQUEST_ASSISTANCE)


def render_draft(jid: str | None, view) -> None:
    st.html(f'<h2>{icon("wheelchair", 30)}<span>{L.t("kiosk.assist.title")}</span></h2>')
    if LIVE:
        st.html(f'<p class="sa-meta"><span class="sa-k">{L.t("ui.staff.pickup")}</span> {L.r(S.get("pickup_label"))}</p>')
    else:
        steps = view.route.steps if view.route else ()
        st.radio(L.attr("kiosk.assist.pickup_label"), [L.r(steps[0].landmark)] if steps else [], key="pickup")
    aid = st.radio(L.attr("kiosk.assist.aid_label"), ["wheelchair", "mobility_escort"], key="aid", horizontal=True,
                   format_func=lambda a: L.attr(f"kiosk.assist.aid.{a}"))
    stay = st.checkbox(L.attr("kiosk.assist.stay"), key="stay")
    consent = st.checkbox(L.attr("kiosk.assist.consent"), key="consent")
    ok, back = st.columns([1.4, 1])
    if ok.button(L.t("ui.submit_request"), key="submit_btn", type="primary", use_container_width=True,
                 disabled=not (stay and consent) or (LIVE and not S.get("pickup_node"))):
        if LIVE:
            if service_call(S.svc.assist, AssistanceRequest(
                journey_id=jid, client_input_id=uuid4().hex, journey_revision=view.revision,
                service=AssistanceService(aid), pickup_node_id=S.get("pickup_node"), pickup_confirmed=True, consent=True,
            )):
                S.draft = False
        else:
            S.draft, S.assist_state = False, "c01_pending"
        st.rerun()
    back.button(L.t("ui.cancel"), key="cancel_btn", use_container_width=True, on_click=pick, kwargs={"draft": False})


def render_view(title: str, view, jid: str | None = None) -> None:
    with st.container(key="detail"):
        st.html(
            f'<a class="sa-backlink" href="#sa-board-top">{icon("back", 22)}{L.t("ui.back_to_flights")}</a>'
            f'<h2 class="sa-title">{title}</h2>'
        )
        if view.banners:
            st.html(C.banners_block(view, L))
        st.html(f'<div class="sa-headrow">{C.gate_plate(view.gate, L)}{C.cutoff_block(view.cutoff, L)}</div>')
        st.html(C.status_block(view, L) + C.next_action_block(view, L))
        if LIVE and jid and S.get("want_assist") == jid and view.next_action.action is PassengerAction.CONFIRM_PICKUP \
                and view.next_action.choices:
            first = view.next_action.choices[0]
            S.want_assist, S.draft, S.pickup_node, S.pickup_label = None, True, first.id, first.label
        if LIVE and jid:
            choice_buttons(jid, view)
        if S.draft:
            render_draft(jid, view)
            return
        with st.container(key="actions"):
            a, b, c = st.columns([1, 1.7, 1])
            needs_where = LIVE and view.route is None and view.gate.state is ObservationState.FRESH
            a.button(L.t("ui.take_me_there"), key="take_btn", type="primary", use_container_width=True,
                     disabled=view.route is None and not needs_where, on_click=pick,
                     kwargs={"where": True} if needs_where else {"following": True})
            can_assist = view.assistance is None and view.gate.value is not None
            b.button(L.t("ui.request_assistance"), key="assist_btn", use_container_width=True, disabled=not can_assist,
                     on_click=request_assistance, args=(jid, view))
            c.button(L.t("ui.ask_skyassist"), key="ask_btn", use_container_width=True, disabled=not LIVE,
                     on_click=pick, kwargs={"ask": not S.ask})
        hints = [L.t("kiosk.ask_unavailable")] if not LIVE else []
        if view.assistance is not None:
            hints.append(L.t("kiosk.assist.already"))
        if hints:
            st.html("".join(f'<p class="sa-hint">{h}</p>' for h in hints))
        if view.status.reason_codes:
            st.html(C.reasons_block(view, L))
        if LIVE and jid and S.ask:
            type_panel(jid, view)
        if LIVE and jid and S.where and view.route is None:
            where_panel(jid, view)
        if view.assistance:
            st.html(C.assistance_block(view.assistance, L))
        if view.route:
            st.html('<div class="sa-routegrid">' + C.route_map(view.route, L) + C.route_steps(view.route, L) + "</div>")
            if S.following:
                st.html(C.qr_block(view, L, BASE_URL))
        else:
            st.html(C.qr_block(view, L, BASE_URL))
        st.html(C.aircraft_block(view, L))


@st.fragment(run_every=2)
def live_detail(jid: str, title: str) -> None:
    held = S.views.get(jid)
    res = service_call(S.svc.poll, jid, held[0] if held else None)
    if res is not None and not res.unchanged:
        held = S.views[jid] = (res.revision, res.view)
    if held is None:
        st.html('<div class="sa-state"><div class="sa-skel sa-skel--lg" style="flex:1"></div></div>')
        return
    render_view(title, held[1], jid)


def board_data(direction: str):
    """Rows for the board as (BoardRow, key); key is a demo state or a live leg id. None means the service failed."""
    if not LIVE:
        rows = [r for r in DEMO_FLIGHTS[direction] if not query or query.lower() in (r[0] + r[1]).lower()]
        out = []
        for code, city, when, key in rows:
            v = D.STATES[key]
            tone, _ = C.STATUS_TONE[v.status.status]
            out.append((BoardRow(code, city, when, v.gate.value if v.gate.state is ObservationState.FRESH else None,
                                 L.t(v.status.status.i18n_key), tone), key))
        return out
    resp = service_call(S.svc.flights, AIRPORT, FlightDirection(direction), query or None)
    if resp is None:
        return None
    out = []
    for r in board_rows(resp, FlightDirection(direction)):
        bad = r.gate_state in (ObservationState.STALE, ObservationState.CONFLICT)
        pill = L.t(f"ui.freshness.{r.gate_state.value}") if bad else ""
        out.append((BoardRow(r.code, r.city, r.time, r.gate, pill, "warn"), r.leg_id))
    return out


def render_board(direction: str):
    data = board_data(direction)
    if data is None:
        st.button(L.t("ui.retry"), key="retry_btn", type="primary")
        return None
    if not data:
        st.html(f'<div class="sa-state">{icon("info", 32)}<p>{L.t("ui.empty_flights")}</p></div>')
        return None
    S.sel = min(S.sel, len(data) - 1)
    st.html('<div id="sa-board-top"></div>' + C.board_header(L))
    with st.container(key="board", border=False):
        for i, (row, _) in enumerate(data):
            shown = BoardRow(row.flight, row.city, row.time, row.gate, row.status, row.tone, i == S.sel)
            with st.container(key=f"flight_{i}"):
                bcol, ccol = st.columns([1.1, 2.9], vertical_alignment="center")
                bcol.button(row.flight, key=f"pick_{i}", type="primary" if i == S.sel else "secondary",
                            use_container_width=True, on_click=pick,
                            kwargs={"sel": i, "following": False, "draft": False, "assist_state": None, "where": False})
                ccol.html(C.board_cells(shown, L))
    return data[S.sel]


def demo_view(key: str):
    return D.STATES[S.assist_state or S.state_override or key]


if S.tab == "connection":
    st.html(f'<h2 class="sa-title">{L.t("ui.tab.connection")}</h2><p class="sa-meta">{L.t("kiosk.conn.hint")}</p>')
    c1, c2 = st.columns(2)
    inbound = c1.text_input(L.attr("kiosk.conn.inbound"), value="DXB-402", key="conn_in")
    outbound = c2.text_input(L.attr("kiosk.conn.outbound"), key="conn_out")
    party = st.radio(L.attr("kiosk.party.label"), list(PARTY), key="party",
                     format_func=lambda p: L.attr(f"kiosk.party.{p}"))
    if st.button(L.t("kiosk.conn.check"), key="check_btn", type="primary"):
        if LIVE:
            aliases = tuple(a.strip() for a in (inbound, outbound) if a.strip())
            profile, needs = PARTY[party]
            jid = service_call(S.svc.create_journey, CreateJourneyRequest(
                airport=AIRPORT, profile=profile, party_needs=needs, language=S.lang))
            if jid:
                send_input(jid, intent=Intent.CHECK_CONNECTION, flight_aliases=aliases, party_needs=needs)
                S.conn_jid = jid
        else:
            S.assist_state = {"standard": "c03_available", "family": "c04_unlikely"}.get(party, "c01_pending")
    if LIVE and S.get("conn_jid"):
        live_detail(S.conn_jid, L.t("ui.tab.connection"))
    elif S.assist_state:
        render_view(f'{C._code("ZA 402")}<span class="sa-sep">·</span>Frankfurt', D.STATES[S.assist_state])
else:
    left, right = st.container(key="layout").columns([2, 3], gap="large")
    with left:
        chosen = render_board(S.tab)
    with right:
        if chosen is not None:
            row, key = chosen
            title = f'{C._code(row.flight)}<span class="sa-sep">·</span>{row.city}'
            if LIVE:
                kind = "outbound_leg_id" if S.tab == "departures" else "inbound_leg_id"
                if key not in S.journeys:
                    S.journeys[key] = open_journey(**{kind: key})
                if S.journeys[key]:
                    live_detail(S.journeys[key], title)
            elif S.tab == "arrivals":
                with st.container(key="detail"):
                    st.html(f'<h2 class="sa-title">{title}</h2>' + C.arrivals_block(D.STATES[key], L))
            else:
                render_view(title, demo_view(key))

if qp.get("dev"):
    st.divider()
    st.selectbox("state", list(D.STATES), key="state_override")
