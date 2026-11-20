import asyncio
import json
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta
from functools import partial
from os import environ
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from skyassist.agent import JourneyAgent, evaluate_transfer
from skyassist.assist import claim_and_dispatch, enqueue_event
from skyassist.clock import SimClock
from skyassist.config import Config, Mode
from skyassist.db import connect, timestamp, transaction
from skyassist.models import EventStreamItem, GetStaffCasesRequest, OutboundEffect, ViewModel
from skyassist.seed import SCENARIO_IDS, build_demo_db, load
from skyassist.service import DEMO_COORDINATOR_TOKEN
from skyassist.service_read import create_integrated_app
from skyassist.staff_client import StaffClient


START = {
    'C01': ('zx515-1119-out', 'c22'),
    'C02': ('zx730-1119-out', 'b_hall'),
    'C03': ('zx310-1119-out', 'b_hall'),
    'C04': ('zx118-1119-out', 'public_hall'),
    'C05': ('zx901-1119-out', 'b_hall'),
    'C06': ('zx420-1119-out', 'b_hall'),
    'C07': ('zx322-1119-out', None),
    'C08': ('zx656-1119-out', 'c22'),
    'C09': ('zx515-1120-out', 'b_hall'),
    'C10': ('zx777-1119-out', 'c_hall'),
}


async def drain(agent):
    for _ in range(50):
        if not await agent.run_once():
            return
    pytest.fail('journey event queue did not drain')


def passenger(client, **body):
    response = client.post('/journeys', json={'airport': 'DXB', **body})
    assert response.status_code == 201, response.text
    return response.json()['journey_id'], {'Authorization': response.headers['Authorization']}


def send(client, session, input_id, **body):
    journey, headers = session
    response = client.post(f'/journeys/{journey}/inputs', headers=headers, json={
        'intent': 'check_connection', **body, 'journey_id': journey, 'client_input_id': input_id,
    })
    assert response.status_code == 202, response.text
    return response.json()['event_id']


def visible(client, session):
    journey, headers = session
    return ViewModel.model_validate(client.get(f'/journeys/{journey}', headers=headers).raise_for_status().json()['view'])


def pickup(client, session, node, revision, **scope):
    journey, headers = session
    response = client.post(f'/journeys/{journey}/assistance', headers=headers, json={
        'journey_id': journey, 'client_input_id': 'pickup', 'journey_revision': revision,
        'service': 'wheelchair', 'pickup_node_id': node, 'pickup_confirmed': True, 'consent': True, **scope,
    })
    assert response.status_code == 200, response.text
    assert response.json()['state'] == 'pending_approval'
    return response.json()['case_ref']


def seeded(tmp_path, **options):
    config = Config(Mode.DEMO, build_demo_db(tmp_path / 'demo.sqlite3'))
    clock = SimClock()
    return config, clock, JourneyAgent(clock, config, policy=load()['policy'], **options)


def assert_fixture(scenario, view, result):
    assert view.gate.value == scenario.expected_gate and view.gate.state == 'fresh'
    assert view.cutoff.value == scenario.expected_cutoff
    assert view.cutoff.unavailable == (scenario.expected_cutoff is None) and not view.cutoff.estimated
    assert view.status.status == result.status == scenario.expected_status
    assert view.next_action.action == scenario.required_passenger_action
    expected = scenario.expected_route_node_ids
    assert (result.route.node_ids if result.route else ()) == expected
    assert (result.lower, result.upper, result.margin) == (
        scenario.expected_transfer_lower_seconds, scenario.expected_transfer_upper_seconds,
        scenario.expected_margin_seconds,
    )
    if expected:
        assert view.route.route_id == result.route.route_id
        assert view.route.steps[-1].landmark.key == f'landmark.{expected[-1]}'
        assert (view.transfer.lower_min, view.transfer.upper_min, view.transfer.margin_min) == (
            result.lower // 60, (result.upper + 59) // 60 if result.upper is not None else None,
            result.margin // 60 if result.margin is not None else None,
        )
    else:
        assert view.route is None and view.transfer is None and not view.qr.token


@pytest.mark.parametrize('scenario_id', SCENARIO_IDS)
def test_core_scenario(tmp_path, scenario_id):
    bundle = load()
    scenario = next(item for item in bundle['scenarios'] if item.id == scenario_id)
    events = {event.id: event for event in bundle['events']}
    sequence = [events[event_id] for event_id in scenario.event_sequence]
    outbound, origin = START[scenario_id]
    inbound = next(event.payload.leg_id for event in sequence if event.event_type == 'flight_updated')
    config = Config(Mode.DEMO, build_demo_db(tmp_path / 'demo.sqlite3'))
    clock = SimClock(scenario.clock_start, starts={
        name: datetime.fromisoformat(instant) for name, instant in bundle['clocks'].items()
    })
    agent = JourneyAgent(clock, config, policy=bundle['policy'], landmark_aliases=bundle['landmarks'])
    for event in sequence:
        if event.timestamp < clock.now():
            agent.enqueue_update(event)
    if scenario_id == 'C08':
        agent.enqueue_update(events['ev-c01-lift-open'])

    calculations = []

    def record(*args, **kwargs):
        result = evaluate_transfer(*args, **kwargs)
        calculations.append(result)
        return result

    def worker():
        async def run():
            while await claim_and_dispatch(partial(connect, config.db_path), clock=clock):
                pass

        asyncio.run(run())

    app = create_integrated_app(clock, config, agent=agent)
    token = environ.get('SKYASSIST_COORDINATOR_TOKEN') or DEMO_COORDINATOR_TOKEN
    with closing(TestClient(app)) as client, StaffClient('http://testserver', token) as staff, \
            patch('skyassist.agent.evaluate_transfer', side_effect=record):
        staff.http.close()
        staff.http = TestClient(app, headers={'Authorization': f'Bearer {token}'})
        session = passenger(client, flow='departing' if scenario_id == 'C04' else 'connecting',
                            party_needs=scenario.party_needs.model_dump(mode='json'))
        send(client, session, 'start', inbound_leg_id=inbound,
             outbound_leg_id=outbound if scenario_id != 'C09' else None, confirmed_origin_id=origin)
        asyncio.run(drain(agent))
        current = visible(client, session)
        case_ref = scoped_session = None
        if scenario_id == 'C02':
            assert current.gate.value == 'B14' and current.qr.token
            old_token = current.qr.token
            assert client.get(f'/route/{old_token}').json()['route']['route_id'] == current.route.route_id
            scoped_session = passenger(client, party_needs=scenario.party_needs.model_dump(mode='json'))
            send(client, scoped_session, 'start', inbound_leg_id=inbound, outbound_leg_id=outbound,
                 confirmed_origin_id=origin)
            asyncio.run(drain(agent))
            case_ref = pickup(client, scoped_session, origin, visible(client, scoped_session).revision,
                              target_leg_id=None)
            asyncio.run(drain(agent))
            assert visible(client, scoped_session).assistance.state == 'pending_approval'
        if scenario_id == 'C06':
            assert current.gate.state == 'stale' and not current.route and not current.qr.token
        if scenario_id == 'C08':
            assert current.route and any(step.accessible_mode == 'lift' for step in current.route.steps)

        checked = False
        for event in sequence:
            if event.timestamp < clock.now():
                continue
            clock.set(event.timestamp)
            payload = event.payload
            if event.event_type == 'passenger_input':
                body = payload.model_dump(mode='json', exclude={'journey_id', 'client_input_id'}, exclude_none=True)
                send(client, session, payload.client_input_id, **body)
            elif event.event_type == 'coordinator_decided':
                decision = payload.model_copy(update={'case_id': case_ref})
                assert staff.decide(decision).state == 'approved'
                assert visible(client, session).assistance.state == 'approved'
                worker()
                assert visible(client, session).assistance.state == 'approved'
            elif event.event_type == 'dispatch_status':
                with closing(connect(config.db_path)) as conn:
                    accepted, = conn.execute("SELECT payload_json FROM events WHERE event_type='handoff_accepted'").fetchall()
                acknowledgment = payload.model_copy(update={
                    'case_id': case_ref, 'provider_ref': json.loads(accepted[0])['provider_ref'],
                })
                assert staff.dispatch(acknowledgment).state == 'dispatched'
                assert staff.dispatch(acknowledgment).outcome == 'replayed'
                assert staff.decide(decision).outcome == 'replayed'
            elif event.event_type == 'refresh_due':
                with closing(connect(config.db_path)) as conn, transaction(conn):
                    enqueue_event(conn, session[0], event.event_type, timestamp(clock.now()), event_id=event.id)
            else:
                agent.enqueue_update(event)
                if event.id == 'ev-c02-gate-b18':
                    assert client.get(f'/route/{old_token}').json()['reason_code'] == 'ROUTE_REVOKED'
                    assert visible(client, session).route is None
            calculations.clear()
            asyncio.run(drain(agent))
            current = visible(client, session)
            if event.id == 'ev-c01-assist':
                assert current.next_action.action == 'confirm_pickup'
                assert not staff.list_cases(GetStaffCasesRequest(airport='DXB')).cases
                case_ref = pickup(client, session, origin, current.revision)
                asyncio.run(drain(agent))
                current = visible(client, session)
                assert current.assistance.state == 'pending_approval'
                assert current.transfer.conditional and not current.qr.token
                assert current.route.steps[0].accessible_mode == 'staff_handoff'
                assert_fixture(scenario, current, calculations[-1])
                checked = True
            elif event.id == 'ev-c02-gate-b18':
                result = next(item for item in calculations if item.route and item.route.route_id == current.route.route_id)
                assert_fixture(scenario, current, result)
                assert current.qr.token and current.qr.token != old_token
                retained = visible(client, scoped_session)
                assert retained.assistance.case_ref == case_ref and retained.assistance.state == 'pending_approval'
                checked = True
            elif event.id == 'ev-c06-conflict-b':
                assert current.gate.state == 'conflict' and not current.route and not current.qr.token
            elif event.id == 'ev-c07-costa':
                assert current.next_action.action == 'choose_landmark' and not current.route
                assert {choice.id for choice in current.next_action.choices} == {'b_costa', 'c_costa'}
            elif event.id == 'ev-c09-input':
                assert current.next_action.action == 'confirm_date' and not current.route

        if not checked:
            result = calculations[-1] if calculations else None
            assert result is not None
            assert_fixture(scenario, current, result)
        if scenario_id == 'C01':
            assert visible(client, session).assistance.state == 'dispatched'
        elif scenario_id == 'C02':
            assert current.gate.value == 'B18' and current.route.steps[-1].landmark.key == 'landmark.b18'
        elif scenario_id == 'C04':
            clock.advance(481)
            cutoff_evidence = next(obs for leg in bundle['legs'] if leg.id == outbound
                                   for obs in leg.initial_observations if obs.field == 'boarding_cutoff')
            agent.enqueue_update(EventStreamItem(id='c04-cutoff-recheck', timestamp=clock.now(), event_type='flight_updated',
                payload=cutoff_evidence.model_copy(update={'observed_at': clock.now(), 'received_at': clock.now()})))
            asyncio.run(drain(agent))
            assert visible(client, session).status.status == 'Boarding cutoff passed'
        elif scenario_id == 'C05':
            board = client.get('/flights', params={'airport': 'DXB', 'direction': 'departures', 'query': 'ZX901',
                'start': '2026-11-19T06:00:00Z', 'end': '2026-11-19T12:00:00Z'}).raise_for_status().json()
            facts = {fact['field']: fact for fact in board['flights'][0]['facts']}
            assert facts['departure']['observation']['estimated'] == '2026-11-19T10:00:00Z'
            assert facts['boarding_cutoff']['observation'] is None
        elif scenario_id in ('C08', 'C10'):
            assert ('LIFT_CLOSED' if scenario_id == 'C08' else 'QUEUE_BOUND_STALE') in current.status.reason_codes
            event = events['ev-c08-lift-reopen' if scenario_id == 'C08' else 'ev-c10-queue-fresh']
            if scenario_id == 'C08':
                invalid = event.payload.model_copy(update={'airport': 'AUH', 'observed_at': clock.now()})
                agent.enqueue_update(EventStreamItem(id='wrong-airport-reopen', timestamp=clock.now(),
                                                     event_type='facility_changed', payload=invalid))
                assert visible(client, session).route is None
            else:
                assert any(step.accessible_mode == 'checkpoint' for step in current.route.steps)
            agent.enqueue_update(event)
            asyncio.run(drain(agent))
            assert visible(client, session).status.status == 'Timing unverified'
            clock.set(event.timestamp)
            asyncio.run(drain(agent))
            refreshed = visible(client, session)
            assert refreshed.status.status == 'Time available' and refreshed.route
            assert refreshed.transfer.margin_min is not None
            if scenario_id == 'C10':
                assert any(step.accessible_mode == 'checkpoint' for step in refreshed.route.steps)
                assert (calculations[-1].lower, calculations[-1].upper, calculations[-1].margin) == (660, 1020, 750)
            else:
                assert any(step.accessible_mode == 'lift' for step in refreshed.route.steps)

        worker()
        with closing(connect(config.db_path)) as conn:
            assert tuple(conn.execute('SELECT outbound_leg_id, profile FROM journeys WHERE id=?',
                                      (session[0],)).fetchone()) == (outbound, scenario.profile)
            audit = conn.execute('SELECT event_id, journey_id, action, outcome FROM audit ORDER BY id').fetchall()
        actions = [row['action'] for row in audit]
        effects = Counter(action for action in actions if action in OutboundEffect)
        assert set(effects) <= set(scenario.allowed_outbound_effects)
        assert effects == ({'coordinator_notification': 1, 'mobility_dispatch': 1} if scenario_id == 'C01' else
                           {'coordinator_notification': 1} if scenario_id == 'C02' else {})
        if scenario_id == 'C01':
            ordered = ['assistance_requested', 'decision_approve', 'dispatch_claimed',
                       'mobility_dispatch', 'dispatch_dispatched']
            assert [action for action in actions if action in ordered] == ordered
        if scenario_id == 'C02':
            history = [row['action'] for row in audit if row['journey_id'] == session[0]
                       and row['action'] in ('route_revoked', 'route_invalidated', 'journey_published')]
            assert history[:4] == ['journey_published', 'route_revoked', 'route_invalidated', 'journey_published']
