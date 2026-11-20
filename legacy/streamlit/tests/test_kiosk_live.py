from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from kiosk.api import Service, ServiceDown, ServiceRefused, board_rows
from skyassist.agent import JourneyAgent
from skyassist.models import (
    CreateJourneyRequest, FlightDirection, Intent, JourneyInputRequest, ObservationState, PartyNeeds,
)
from skyassist.service_read import create_integrated_app
from test_agent import fixture


def wait_for(svc, jid, check, rev=None):
    deadline = monotonic() + 3
    while True:
        res = svc.poll(jid, rev)
        if not res.unchanged and check(res.view):
            return res.view
        if not res.unchanged:
            rev = res.revision
        assert monotonic() < deadline, res
        sleep(0.01)


def test_board_journey_and_landmark_flow(tmp_path):
    clock, config = fixture(tmp_path)
    with TestClient(create_integrated_app(clock, config, agent=JourneyAgent(clock, config))) as tc:
        svc = Service(client=tc)
        assert svc.now() == clock.now()
        rows = board_rows(svc.flights("DXB", FlightDirection.DEPARTURES), FlightDirection.DEPARTURES)
        assert [(r.code, r.city, r.gate, r.gate_state) for r in rows] == [("EK 202", "SIN", "B14", ObservationState.FRESH)]
        assert rows[0].time == "10:50"

        jid = svc.create_journey(CreateJourneyRequest(airport="DXB", outbound_leg_id=rows[0].leg_id,
                                                      party_needs=PartyNeeds(wheelchair=True, step_free=True)))
        svc.send(JourneyInputRequest(journey_id=jid, client_input_id="a", intent=Intent.CHECK_CONNECTION,
                                     outbound_leg_id=rows[0].leg_id))
        first = wait_for(svc, jid, lambda v: v.gate.value == "B14")
        assert first.route is None and first.qr.token is None

        svc.send(JourneyInputRequest(journey_id=jid, client_input_id="b", confirmed_origin_id="c22"))
        routed = wait_for(svc, jid, lambda v: v.route is not None)
        assert routed.transfer is not None and routed.cutoff.value is not None
        assert svc.poll(jid, routed.revision).unchanged


def test_errors_are_classified(tmp_path):
    clock, config = fixture(tmp_path)
    with TestClient(create_integrated_app(clock, config, agent=JourneyAgent(clock, config))) as tc:
        svc = Service(client=tc)
        with pytest.raises(ServiceRefused):
            svc.create_journey(CreateJourneyRequest(airport="DXB", outbound_leg_id="nope"))
    with pytest.raises(ServiceDown):
        Service("http://127.0.0.1:9").now()
