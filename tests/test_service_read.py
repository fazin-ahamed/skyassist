from contextlib import closing
from datetime import timedelta
from hashlib import sha256

from fastapi.testclient import TestClient

from skyassist.agent import JourneyAgent
from skyassist.db import connect
from skyassist.evidence import ingest
from skyassist.models import ViewModel
from skyassist.service_read import create_integrated_app
from test_agent import await_view, create, fixture, observation, submit


def test_board_pagination_provenance_and_bounded_query(tmp_path):
    clock, config = fixture(tmp_path)
    with closing(connect(config.db_path)) as conn:
        conn.execute("INSERT INTO flight_legs VALUES ('next', 'EK', '303', '2026-11-19', 'DXB', 'AUH', NULL)")
        ingest(conn, observation(clock, "departure", leg_id="next", scheduled=clock.now() + timedelta(hours=1)))
    with TestClient(create_integrated_app(clock, config, agent=JourneyAgent(clock, config))) as client:
        params = {"airport": "DXB", "direction": "departures", "start": clock.now().isoformat(),
                  "end": (clock.now() + timedelta(hours=2)).isoformat(), "limit": 1}
        response = client.get("/flights", params=params)
        assert response.status_code == 200, response.text
        assert response.headers["Cache-Control"] == "no-store"
        board = response.json()
        assert board["next_cursor"] == "out" and board["flights"][0]["leg"]["id"] == "out"
        gate = next(fact for fact in board["flights"][0]["facts"] if fact["field"] == "gate")
        assert gate["state"] == "fresh" and gate["observation"]["source"] == "inline"
        assert client.get("/flights", params=params | {"cursor": "out"}).json()["flights"][0]["leg"]["id"] == "next"
        assert client.get("/flights", params=params | {"query": "QF202"}).json()["next_cursor"] is None
        assert client.get("/flights", params=params | {"end": params["start"]}).status_code == 422
        assert client.get("/flights", params=params | {"end": "2026-12-19T07:00:00Z"}).status_code == 422


def test_phone_rechecks_current_revision_and_expiry_on_every_load(tmp_path):
    clock, config = fixture(tmp_path)
    with TestClient(create_integrated_app(clock, config, agent=JourneyAgent(clock, config))) as client:
        session = create(client)
        submit(client, session)
        issued = await_view(client, session, lambda current: current["qr"]["token"] is not None)
        token = issued["qr"]["token"]
        url = f"/route/{token}"
        route = client.get(url).json()["route"]
        assert not route["floor_shapes"] and not route["floor_outlines"]
        assert route["revision"] == issued["route"]["revision"]
        assert all(step["map_position"] for step in issued["route"]["steps"])
        assert all(step["map_position"] is None for step in route["steps"])
        assert all(step["accessible_mode"] != "staff_handoff" for step in route["steps"])
        html = client.get(url, headers={"Accept": "text/html"})
        assert "Go to gate B14." in html.text and "pickup" not in html.text
        assert html.headers["Cache-Control"] == "no-store" and html.headers["Referrer-Policy"] == "no-referrer"
        assert client.get("/route/not-a-token").json()["reason_code"] == "ROUTE_UNAVAILABLE"
        with closing(connect(config.db_path)) as conn:
            view = ViewModel.model_validate(issued)
            mismatched = view.model_copy(update={"route": view.route.model_copy(update={"revision": view.revision + 1})})
            conn.execute("UPDATE journey_revisions SET view_json=? WHERE journey_id=? AND revision=?",
                         (mismatched.model_dump_json(), session[0], view.revision))
        assert client.get(url).json()["reason_code"] == "ROUTE_REVOKED"
        with closing(connect(config.db_path)) as conn:
            conn.execute("UPDATE journey_revisions SET route_expires_at=? WHERE route_token_hash=?",
                         (clock.now().isoformat(timespec="microseconds"), sha256(token.encode()).hexdigest()))
        assert client.get(url).json()["reason_code"] == "ROUTE_EXPIRED"


def test_kiosk_end_session_revokes_journey_and_phone_link(tmp_path):
    clock, config = fixture(tmp_path)
    with TestClient(create_integrated_app(clock, config, agent=JourneyAgent(clock, config))) as client:
        session = create(client)
        submit(client, session)
        issued = await_view(client, session, lambda current: current["qr"]["token"] is not None)
        journey_url = f"/journeys/{session[0]}"
        phone_url = f"/route/{issued['qr']['token']}"
        assert client.delete(journey_url).status_code == 403
        closed = client.delete(journey_url, headers=session[1])
        assert closed.status_code == 204 and closed.headers["Cache-Control"] == "no-store"
        assert client.delete(journey_url, headers=session[1]).status_code == 204
        assert client.get(journey_url, headers=session[1]).json()["detail"]["reason_code"] == "SESSION_EXPIRED"
        assert client.get(phone_url).json()["reason_code"] == "ROUTE_EXPIRED"


def test_unrecognized_landmark_is_never_written_to_the_event_store(tmp_path):
    clock, config = fixture(tmp_path)
    with TestClient(create_integrated_app(clock, config, agent=JourneyAgent(clock, config))) as client:
        session = create(client)
        event = submit(client, session, confirmed_origin_id=None, landmark_text="unrecognized passenger text")
        await_view(client, session, lambda current: current["revision"] > 0)
        with closing(connect(config.db_path)) as conn:
            payload, = conn.execute("SELECT payload_json FROM events WHERE id=?", (event,)).fetchone()
        assert "unrecognized passenger text" not in payload
