"""Drive the real kiosk script against a real service over HTTP with Streamlit's AppTest."""

import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from streamlit.testing.v1 import AppTest

from skyassist import seed
from skyassist.clock import SimClock
from skyassist.config import Config, Mode
from skyassist.service_read import create_integrated_app

KIOSK = Path(__file__).resolve().parents[1] / "kiosk" / "app.py"
STAFF = Path(__file__).resolve().parents[1] / "staff" / "app.py"
COORDINATOR = {"Authorization": "Bearer demo-coordinator"}
C01_EVENTS = ("ev-c01-lift-closed", "ev-c01-lift-open", "ev-c01-ready", "ev-c01-gate-b14")


@pytest.fixture
def service(tmp_path, monkeypatch):
    db = seed.build_demo_db(tmp_path / "demo.sqlite3")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        create_integrated_app(SimClock("C01"), Config(Mode.DEMO, Path(db))), host="127.0.0.1", port=port, log_level="error",
    ))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    for event in C01_EVENTS:
        httpx.post(f"{url}/demo/events/{event}/apply", headers=COORDINATOR).raise_for_status()
    monkeypatch.setenv("SKYASSIST_SERVICE_URL", url)
    yield url
    server.should_exit = True
    thread.join(5)


def settle(at, until, tries=40):
    for _ in range(tries):
        at.run()
        jid = at.session_state["conn_jid"] if "conn_jid" in at.session_state else None
        held = at.session_state["views"].get(jid) if jid else None
        if held and until(held[1]):
            return held[1]
        time.sleep(0.25)
    raise AssertionError("view never reached the expected state")


def choice(at, jid, suffix=None):
    found = [b for b in at.button if b.key and b.key.startswith(f"choice_{jid}_") and (suffix is None or b.key.endswith(suffix))]
    assert found, [b.key for b in at.button]
    return found[0]


def drive_kiosk_to_pending(service):
    at = AppTest.from_file(str(KIOSK), default_timeout=30).run()
    assert not at.exception
    at.button(key="tab_connection_off").click().run()
    at.text_input(key="conn_in").set_value("DXB-402")
    at.text_input(key="conn_out").set_value("ZX515")
    at.radio(key="party").set_value("both")
    at.button(key="check_btn").click().run()

    view = settle(at, lambda v: v.next_action.choices)
    jid = at.session_state["conn_jid"]
    assert view.next_action.action.value == "confirm_date"
    choice(at, jid, "2026-11-19").click().run()

    view = settle(at, lambda v: v.gate.value == "B14")
    assert view.gate.state.value == "fresh" and view.cutoff.value is not None and view.route is None

    at.button(key="take_btn").click().run()
    at.text_input(key=f"where_{jid}").set_value("C22")
    at.button(key=f"where_go_{jid}").click().run()
    settle(at, lambda v: v.next_action.choices)
    choice(at, jid).click().run()
    view = settle(at, lambda v: v.route is not None)
    assert (view.transfer.lower_min, view.transfer.upper_min, view.transfer.margin_min) == (21, 29, 6)
    assert view.status.status.value == "Tight connection"

    at.button(key="assist_btn").click().run()
    settle(at, lambda v: v.next_action.action.value == "confirm_pickup")
    at.run()
    assert at.session_state["draft"] is True and at.session_state["pickup_node"] == "c22"
    at.checkbox(key="stay").check()
    at.checkbox(key="consent").check().run()
    at.button(key="submit_btn").click().run()
    view = settle(at, lambda v: v.assistance is not None)
    assert view.assistance.state.value == "pending_approval"
    assert not at.exception
    return at


def test_connection_to_pending_assistance(service):
    drive_kiosk_to_pending(service)


def test_staff_dashboard_approves_then_dispatches(service):
    kiosk = drive_kiosk_to_pending(service)
    jid = kiosk.session_state["conn_jid"]
    staff = AppTest.from_file(str(STAFF), default_timeout=30).run()
    assert not staff.exception
    row = [b for b in staff.button if b.key and b.key.startswith("pick_")]
    assert len(row) == 1
    staff.button(key="approve_btn").click().run()
    assert staff.session_state["result"].outcome.value == "applied"
    assert staff.session_state["result"].state.value == "approved"
    staff.button(key="dispatch_btn").click().run()
    assert staff.session_state["result"].state.value == "dispatched"
    view = settle(kiosk, lambda v: v.assistance and v.assistance.state.value == "dispatched")
    assert view.assistance.state.value == "dispatched"
    assert jid
