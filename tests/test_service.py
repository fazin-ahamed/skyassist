from contextlib import closing

from fastapi.testclient import TestClient

from skyassist.clock import SimClock
from skyassist.config import Config, Mode
from skyassist.db import connect
from skyassist.service import DEMO_COORDINATOR_TOKEN, DEMO_INTEGRATION_TOKEN, create_app
from test_assist import seed, trace


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def create_journey(client):
    response = client.post("/journeys", json={"airport": "DXB", "outbound_leg_id": "outbound"})
    assert response.status_code == 201, response.text
    assert response.headers["Cache-Control"] == "no-store"
    return response.json()["journey_id"], {"Authorization": response.headers["Authorization"]}


def request_body(journey, **overrides):
    return dict(journey_id=journey, client_input_id="tap", journey_revision=0,
                service="wheelchair", pickup_node_id="c22", pickup_confirmed=True, consent=True) | overrides


def test_journey_input_dedupe_scope_revision_and_pending_restart(tmp_path):
    path = tmp_path / "service.sqlite3"
    config, clock = Config(Mode.DEMO, path), SimClock()
    with TestClient(create_app(clock, config)) as client:
        with closing(connect(path)) as conn:
            seed(conn)
        journey, headers = create_journey(client)
        other, other_headers = create_journey(client)
        url = f"/journeys/{journey}"
        assert client.get(url, headers=other_headers).json()["detail"]["reason_code"] == "WRONG_ROLE"
        assert client.get(f"/journeys/{other}", headers=headers).status_code == 403
        assert client.get(url, headers=headers, params={"known_revision": 0}).json() == {
            "revision": 0, "unchanged": True,
        }
        body = {"journey_id": journey, "client_input_id": "message-1", "text": "Find my gate"}
        first = client.post(f"{url}/inputs", json=body, headers=headers)
        assert first.status_code == 202 and first.json()["work_pending"]
        assert client.post(f"{url}/inputs", json=body, headers=headers).json() == first.json()
        stale = client.post(f"{url}/assistance", headers=headers, json=request_body(journey, journey_revision=7))
        assert stale.json()["detail"]["reason_code"] == "STALE_REVISION"
        missing_consent = client.post(f"{url}/assistance", headers=headers, json=request_body(journey, consent=False))
        assert missing_consent.json()["detail"]["reason_code"] == "CONSENT_MISSING"
        unconfirmed = client.post(f"{url}/assistance", headers=headers,
                                  json=request_body(journey, pickup_confirmed=False))
        assert unconfirmed.json()["detail"]["next_action"] == "seek_staff"
        pending = client.post(f"{url}/assistance", headers=headers, json=request_body(journey))
        assert pending.status_code == 200
        assert pending.json()["state"] == "pending_approval"
        assert client.get(url, headers=headers).json()["view"]["assistance"]["case_ref"] == pending.json()["case_ref"]
    with TestClient(create_app(clock, config)) as client:
        stored = client.get(url, headers=headers).json()
        assert stored["view"]["assistance"]["state"] == "pending_approval"
        assert stored["revision"] == pending.json()["journey_revision"]
        assert client.get(url, headers=headers, params={"known_revision": stored["revision"]}).json()["unchanged"]
        staff = client.get("/staff/cases", headers=auth(DEMO_COORDINATOR_TOKEN), params={"airport": "DXB"})
        assert [item["case"]["id"] for item in staff.json()["cases"]] == [pending.json()["case_ref"]]
        clock.advance(8 * 3600)
        assert client.get(url, headers=headers).json()["detail"]["reason_code"] == "SESSION_EXPIRED"
    with closing(connect(path)) as conn:
        history = trace(conn)
        assert history[:2] == [("journey_input", "queued"), ("assistance_requested", "pending_approval")]
        assert history.count(("coordinator_notification", "available")) <= 1
        assert all(action != "mobility_dispatch" for action, _ in history)
        queued = conn.execute("SELECT id, processed_at FROM events WHERE event_type='passenger_input'").fetchall()
        assert [tuple(row) for row in queued] == [(first.json()["event_id"], None)]
        assert headers["Authorization"].split()[1] not in "\n".join(conn.iterdump())
        audit = str([tuple(row) for row in conn.execute("SELECT * FROM audit")])
        assert "12.75" not in audit and "45.25" not in audit


def test_wrong_role_callbacks_have_no_effect(tmp_path):
    path = tmp_path / "roles.sqlite3"
    clock = SimClock()
    with TestClient(create_app(clock, Config(Mode.DEMO, path))) as client:
        with closing(connect(path)) as conn:
            seed(conn)
        journey, headers = create_journey(client)
        case = client.post(f"/journeys/{journey}/assistance", headers=headers, json=request_body(journey)).json()
        case_id = case["case_ref"]
        payload = {"case_id": case_id, "event_id": "decision", "request_revision": case["request_revision"]}
        decisions = f"/staff/cases/{case_id}/decisions"
        for unauthorized in (headers, auth(DEMO_INTEGRATION_TOKEN)):
            response = client.post(decisions, headers=unauthorized, json=payload | {"decision": "approve"})
            assert response.status_code == 403 and response.json()["detail"]["reason_code"] == "WRONG_ROLE"
        callback = f"/staff/cases/{case_id}/dispatch-status"
        for unauthorized in (headers, auth(DEMO_COORDINATOR_TOKEN)):
            response = client.post(callback, headers=unauthorized, json=payload | {
                "status": "dispatched", "evidence": {"source": "demo", "observed_at": clock.now().isoformat()},
            })
            assert response.status_code == 403 and response.json()["detail"]["reason_code"] == "WRONG_ROLE"
        assert client.get(f"/journeys/{journey}", headers=headers).json()["view"]["assistance"]["state"] == "pending_approval"
    with closing(connect(path)) as conn:
        history = trace(conn)
        assert history[0] == ("assistance_requested", "pending_approval")
        assert set(history) <= {("assistance_requested", "pending_approval"), ("coordinator_notification", "available")}
        assert conn.execute("SELECT count(*) FROM staff_decisions").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM events WHERE id='decision'").fetchone()[0] == 0
