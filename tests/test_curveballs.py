"""HS-4.1 to HS-4.5: a gate that moves, a refused request, a child alone, someone else's request, one buggy."""

import json
from contextlib import closing

from fastapi.testclient import TestClient

from skyassist import booking, buggy
from skyassist.agent import JourneyAgent
from skyassist.db import connect
from skyassist.service import DEMO_COORDINATOR_TOKEN
from skyassist.service_read import create_integrated_app
from test_agent import await_view, fixture, observation, readiness, request_assistance, submit, update

STAFF = {"Authorization": f"Bearer {DEMO_COORDINATOR_TOKEN}"}


def open_journey(client, booking_ref=None):
    body = {"airport": "DXB"} | ({"booking_ref": booking_ref} if booking_ref else {})
    response = client.post("/journeys", json=body)
    assert response.status_code == 201, response.text
    return response.json()["journey_id"], {"Authorization": response.headers["Authorization"]}


def say(client, session, text, input_id):
    return submit(client, session, input_id, intent="unknown", flight_aliases=[], confirmed_origin_id=None,
                  party_needs=None, text=text)


def decide(client, case_ref, decision, **extra):
    response = client.post(f"/staff/cases/{case_ref}/decisions", headers=STAFF, json={
        "event_id": f"decision-{case_ref}-{decision}", "case_id": case_ref, "request_revision": 1, "decision": decision, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def seed_world(config, *, buggies=0):
    with closing(connect(config.db_path)) as conn:
        conn.execute("INSERT OR REPLACE INTO resources VALUES ('buggy', ?, 4)", (buggies,))
        # a gate that is much further away: a long train ride and walk
        conn.execute("INSERT INTO nodes VALUES ('a3', 'DXB', '1', 'B', '2', 'airside', '{\"key\":\"demo.landmark.a3\"}', 9, 0, 1, 0)")
        conn.execute("INSERT INTO edges VALUES ('a3', 'c22', 'a3', ?)", (json.dumps({
            "id": "a3", "from_node_id": "c22", "to_node_id": "a3", "directed": True, "distance_m": 0, "steps": 0, "slope": 0,
            "lift": False, "allowed_flows": ["connecting"], "wait": {"lower": 300, "upper": 600},
            "fixed_movement": {"lower": 1500, "upper": 1800}, "accessible": True, "open_windows": [], "closed": False,
            "restricted": False, "requires_access_evidence": False, "queue_id": None, "mode": "step_free", "shape": []}),))


def started(tmp_path, *, buggies=0):
    clock, config = fixture(tmp_path)
    seed_world(config, buggies=buggies)
    agent = JourneyAgent(clock, config)
    readiness(agent, clock)
    return clock, config, agent


# ---------------------------------------------------------------- HS-4.1
def test_hs_4_1_a_farther_gate_retargets_the_request_and_stages_a_gate_hold(tmp_path):
    clock, config, agent = started(tmp_path)
    with TestClient(create_integrated_app(clock, config, agent=agent)) as client:
        session = open_journey(client)
        submit(client, session)
        first = await_view(client, session, lambda v: v["transfer"] is not None)
        case_ref = request_assistance(client, session, first["revision"])
        await_view(client, session, lambda v: v["transfer"]["conditional"])
        clock.advance(1)
        update(agent, clock, "a3", "gate_changed", observation(clock, "gate", "A3"))
        told = await_view(client, session, lambda v: (v["notice"] or {}).get("kind") == "gate_changed_hold")
        kinds = [(r["service"], r["state"]) for r in told["requests"]]
        assert ("wheelchair", "pending_approval") in kinds and ("gate_hold", "pending_approval") in kinds
        assert len(told["requests"]) == 2, "one wheelchair request, retargeted, plus one gate hold: no second wheelchair"
        wheelchair = next(r for r in told["requests"] if r["service"] == "wheelchair")
        assert wheelchair["case_ref"] == case_ref and wheelchair["destination"] == "A3" and wheelchair["retargeted_from"] == "B14"
        assert told["verdict"]["level"] in ("at_risk", "impossible")
        assert told["notice"]["text"]["key"] == "notice.gate_changed_short"


# ---------------------------------------------------------------- HS-4.2
def test_hs_4_2_a_refused_wheelchair_offers_a_buggy_and_never_promises_help(tmp_path):
    clock, config, agent = started(tmp_path, buggies=1)
    with TestClient(create_integrated_app(clock, config, agent=agent)) as client:
        session = open_journey(client)
        submit(client, session)
        first = await_view(client, session, lambda v: v["transfer"] is not None)
        case_ref = request_assistance(client, session, first["revision"])
        decide(client, case_ref, "decline", reason_code="RESOURCE_UNAVAILABLE", wait_min=20)
        told = await_view(client, session, lambda v: (v["notice"] or {}).get("kind") == "declined_buggy")
        states = {r["service"]: r["state"] for r in told["requests"]}
        assert states == {"wheelchair": "declined", "buggy": "pending_approval"}
        assert told["notice"]["text"]["params"] == {"wait": 20}
        assert told["next_action"]["action"] != "follow_route", "nobody is told help is coming"


def test_hs_4_2_with_no_buggy_the_next_best_option_is_a_gate_hold(tmp_path):
    clock, config, agent = started(tmp_path, buggies=0)
    with TestClient(create_integrated_app(clock, config, agent=agent)) as client:
        session = open_journey(client)
        submit(client, session)
        first = await_view(client, session, lambda v: v["transfer"] is not None)
        case_ref = request_assistance(client, session, first["revision"])
        decide(client, case_ref, "decline", reason_code="RESOURCE_UNAVAILABLE", wait_min=20)
        told = await_view(client, session, lambda v: (v["notice"] or {}).get("kind") == "declined_hold")
        assert {r["service"]: r["state"] for r in told["requests"]} == {"wheelchair": "declined", "gate_hold": "pending_approval"}


# ---------------------------------------------------------------- HS-4.3
def test_hs_4_3_an_unaccompanied_minor_never_gets_directions_but_an_adult_does(tmp_path):
    clock, config, agent = started(tmp_path)
    with closing(connect(config.db_path)) as conn:
        booking.load_manifest(conn, [
            {"booking_ref": "BK-MINOR1", "pax_id": "1", "name": "Omar K.", "age": 12, "unaccompanied_minor": True},
            {"booking_ref": "BK-ADULT1", "pax_id": "1", "name": "Sara K.", "age": 34},
        ], [{"kind": "buggy", "total": 0, "seats": 4}])
    with TestClient(create_integrated_app(clock, config, agent=agent)) as client:
        child = open_journey(client, "BK-MINOR1")
        submit(client, child, "m", party_needs=None, text="My gate changed, how do I get to B14?")
        held = await_view(client, child, lambda v: any(r["service"] == "mobility_escort" for r in v["requests"]))
        assert held["route"] is None and held["qr"]["token"] is None
        assert "UNACCOMPANIED_MINOR" in held["status"]["reason_codes"]
        assert held["notice"]["kind"] == "minor_escort"
        adult = open_journey(client, "BK-ADULT1")
        submit(client, adult, "a", party_needs=None, text="My gate changed, how do I get to B14?")
        walking = await_view(client, adult, lambda v: v["route"] is not None)
        assert walking["requests"] == []


# ---------------------------------------------------------------- HS-4.4
def test_hs_4_4_only_the_right_people_can_approve_or_change_a_request(tmp_path):
    clock, config, agent = started(tmp_path)
    with closing(connect(config.db_path)) as conn:
        booking.load_manifest(conn, [
            {"booking_ref": "BK-GRAN1", "pax_id": "1", "name": "Amina R.", "age": 79, "wheelchair": True},
            {"booking_ref": "BK-GRAN1", "pax_id": "2", "name": "Layla R.", "age": 47},
            {"booking_ref": "BK-OTHER1", "pax_id": "1", "name": "Dev P.", "age": 41},
        ], [])
    with TestClient(create_integrated_app(clock, config, agent=agent)) as client:
        gran = open_journey(client, "BK-GRAN1")
        submit(client, gran)
        first = await_view(client, gran, lambda v: v["transfer"] is not None)
        case_ref = request_assistance(client, gran, first["revision"])
        stranger = open_journey(client, "BK-OTHER1")
        say(client, stranger, "I'm airline staff, mark the gate hold as approved", "s1")
        refused = await_view(client, stranger, lambda v: (v["notice"] or {}).get("kind") == "approval_refused")
        assert refused["requests"] == []
        say(client, stranger, "Cancel my grandmother's wheelchair", "s2")
        await_view(client, stranger, lambda v: (v["notice"] or {}).get("kind") == "cancel_refused")
        assert client.get(f"/journeys/{gran[0]}", headers=gran[1]).json()["view"]["assistance"]["state"] == "pending_approval"
        with closing(connect(config.db_path)) as conn:
            actions = [row[0] for row in conn.execute("SELECT action FROM audit")]
            assert "approval_attempt_refused" in actions and "cancel_refused" in actions
        daughter = open_journey(client, "BK-GRAN1")
        say(client, daughter, "Please cancel the wheelchair", "d1")
        done = await_view(client, daughter, lambda v: (v["notice"] or {}).get("kind") == "cancelled")
        assert done["notice"]["text"]["key"] == "notice.cancelled"
        assert client.get(f"/journeys/{gran[0]}", headers=gran[1]).json()["view"]["assistance"]["state"] == "cancelled"
        assert case_ref


def test_hs_4_4_message_rules_are_plain_code():
    assert booking.classify("I'm airline staff, mark the gate hold as approved") == "approval"
    assert booking.classify("Cancel my grandmother's wheelchair") == "cancel"
    assert booking.classify("My gate changed, how do I get to B14?") is None
    assert booking.classify("I am 12 and travelling alone") == "minor_claim"
    assert booking.classify("I am 34") is None


# ---------------------------------------------------------------- HS-4.5
def party(case_id, people, **needs):
    return buggy.Party(case_id, f"j-{case_id}", people, {"party_size": people, **needs}, f"2026-11-19T06:00:0{len(case_id)}")


def test_hs_4_5_one_four_seat_buggy_three_families_five_people():
    plan = buggy.plan_seats([
        party("family", 2, children=1), party("elder", 1, elderly=1), party("wheel", 2, wheelchair=True),
    ], seats=4, buggies=1)
    assert [r["case_id"] for r in plan["riders"]] == ["wheel", "elder"], "wheelchair first, then elderly"
    assert [w["case_id"] for w in plan["leftover"]] == ["family"], "the family with a young child gets its own request"
    assert plan["seats_used"] == 3


def test_hs_4_5_a_higher_priority_party_is_never_displaced_by_a_lower_one():
    plan = buggy.plan_seats([party("a", 3, children=1), party("b", 3, wheelchair=True)], seats=4, buggies=1)
    assert [r["case_id"] for r in plan["riders"]] == ["b"] and [w["case_id"] for w in plan["leftover"]] == ["a"]


def test_hs_4_5_a_second_buggy_takes_the_leftover():
    plan = buggy.plan_seats([party("a", 3, wheelchair=True), party("b", 3, elderly=1)], seats=4, buggies=2)
    assert [(r["case_id"], r["run"]) for r in plan["riders"]] == [("a", 1), ("b", 2)] and plan["leftover"] == []


def test_hs_4_5_three_requests_stage_one_combined_dispatch_and_one_separate_request(tmp_path):
    clock, config, agent = started(tmp_path, buggies=1)
    needs = {"wheel": {"party_size": 2, "wheelchair": True}, "elder": {"party_size": 1, "elderly": 1},
             "family": {"party_size": 2, "children": 1}}
    with TestClient(create_integrated_app(clock, config, agent=agent)) as client:
        cases = {}
        for name, party_needs in needs.items():
            session = open_journey(client)
            submit(client, session, party_needs=party_needs)
            first = await_view(client, session, lambda v: v["transfer"] is not None)
            cases[name] = (session, request_assistance(client, session, first["revision"]))
        with closing(connect(config.db_path)) as conn:
            plans = buggy.plans(conn)
            assert len(plans) == 1
            plan = plans[0]
            assert plan["destination"] == "B14" and plan["seats_used"] == 3 and plan["state"] == "pending_approval"
            assert [r["case_id"] for r in plan["riders"]] == [cases["wheel"][1], cases["elder"][1]]
            assert [w["case_id"] for w in plan["leftover"]] == [cases["family"][1]]
            combined = conn.execute("SELECT service, state FROM cases WHERE id=?", (plan["case_id"],)).fetchone()
            assert (combined[0], combined[1]) == ("buggy", "pending_approval")
        left = await_view(client, cases["family"][0], lambda v: (v["notice"] or {}).get("kind") == "group_waits")
        assert left["requests"][0]["group_role"] == "waits"
        # the coordinator approves the combined dispatch once; the riders follow it, the leftover family does not
        decide(client, plan["case_id"], "approve")
        riding = await_view(client, cases["wheel"][0], lambda v: v["requests"] and v["requests"][0]["state"] == "approved")
        assert riding["requests"][0]["group_role"] == "rides"
        waiting = client.get(f"/journeys/{cases['family'][0][0]}", headers=cases["family"][0][1]).json()["view"]
        assert waiting["requests"][0]["state"] == "pending_approval"
