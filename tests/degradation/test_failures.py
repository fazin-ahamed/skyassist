import asyncio
from contextlib import closing
from datetime import timedelta
from hashlib import sha256

import httpx
import pytest

from skyassist.agent import JourneyAgent
from skyassist.db import connect, timestamp
from skyassist.model_boundary import OllamaExtractor
from skyassist.models import GetStaffCasesRequest
from skyassist.providers import MockFlightProvider, ProviderFailure
from skyassist.tools import ReadRecovery
from tests.safety.test_adversarial import OUTBOUND, acknowledgment, client_for, decision, staff_for, start, work
from tests.scenarios.test_core import drain, passenger, pickup, seeded, send, visible


@pytest.mark.parametrize('fault', ['429', 'quota', 'network_loss'])
def test_provider_degradation_preserves_cache_age(tmp_path, fault):
    calls = []
    delays = []

    class Flights:
        def flight_status(self, request, *, deadline):
            calls.append(clock.now())
            if fault == '429' and len(calls) == 1:
                raise ProviderFailure('rate_limit', retry_after_seconds=2)
            if fault == 'quota':
                raise ProviderFailure('quota', retry_after_seconds=180)
            if fault == 'network_loss':
                raise httpx.ConnectError('offline')
            with closing(connect(config.db_path)) as conn:
                return MockFlightProvider(conn, agent.policy).flight_status(request, deadline=deadline)

    config, clock, agent = seeded(tmp_path, flights=Flights())

    def wait(delay):
        delays.append(delay)
        clock.advance(int(delay))

    agent.recovery = ReadRecovery(clock, wait=wait, jitter=lambda: 0)
    with closing(client_for(config, clock, agent)) as client:
        session = start(client, agent)
        first = visible(client, session)
        assert first.route and first.gate.state == 'fresh' and first.qr.token
        if fault == '429':
            assert delays == [2] and calls[1] - calls[0] == timedelta(seconds=2)
        else:
            assert len(calls) == 1 and not delays
        with closing(connect(config.db_path)) as conn:
            audit = conn.execute("SELECT outcome, attempts, reason_codes_json FROM audit WHERE tool_id=?",
                                 ('get_flight_and_gate_status',)).fetchone()
            assert tuple(audit) == {
                '429': ('success', 2, '[]'), 'quota': ('error', 1, '["BUDGET_EXHAUSTED"]'),
                'network_loss': ('error', 1, '["SOURCE_UNAVAILABLE"]'),
            }[fault]
        clock.advance(90)
        asyncio.run(drain(agent))
        stale = visible(client, session)
        assert stale.gate.state == 'stale' and stale.gate.provenance == first.gate.provenance
        assert not stale.route and not stale.qr.token and stale.next_action.action == 'check_gate'
        assert stale.status.status == 'Timing unverified'
        if fault == 'quota':
            assert len(calls) == 1


@pytest.mark.parametrize(('text', 'language'), [('Gate ZX310?', 'en'), ('بوابة ZX310؟', 'ar')])
def test_model_outage_falls_back_to_typed_wayfinding(tmp_path, text, language):
    calls = []

    def offline(request):
        calls.append(request.url)
        raise httpx.ConnectError('local model unavailable', request=request)

    config, clock, agent = seeded(tmp_path, extractor=OllamaExtractor(transport=httpx.MockTransport(offline)))
    with closing(client_for(config, clock, agent)) as client:
        session = passenger(client)
        send(client, session, 'fallback', text=text, intent='unknown', confirmed_origin_id='b_hall')
        asyncio.run(drain(agent))
        result = visible(client, session)
        assert calls and result.language == language
        assert result.gate.value == 'B14' and result.cutoff.value == clock.now() + timedelta(minutes=40)
        assert result.route and result.next_action.action == 'follow_route'


def test_qr_scan_revision_mismatch_and_exact_expiry(tmp_path):
    config, clock, agent = seeded(tmp_path)
    with closing(client_for(config, clock, agent)) as client:
        session = start(client, agent)
        result = visible(client, session)
        url = f'/route/{result.qr.token}'
        html = client.get(url, headers={'Accept': 'text/html'})
        assert html.status_code == 200 and '<ol>' in html.text and 'B14' in html.text
        assert not client.get(url).json()['route']['floor_shapes']
        updated = result.model_copy(update={'route': result.route.model_copy(update={'revision': result.revision + 1})})
        with closing(connect(config.db_path)) as conn:
            conn.execute('UPDATE journey_revisions SET view_json=? WHERE journey_id=? AND revision=?',
                         (updated.model_dump_json(), session[0], result.revision))
        assert client.get(url).json()['reason_code'] == 'ROUTE_REVOKED'
        with closing(connect(config.db_path)) as conn:
            conn.execute('UPDATE journey_revisions SET view_json=? WHERE journey_id=? AND revision=?',
                         (result.model_dump_json(), session[0], result.revision))
            expiry = conn.execute('SELECT route_expires_at FROM journey_revisions WHERE route_token_hash=?',
                                  (sha256(result.qr.token.encode()).hexdigest(),)).fetchone()[0]
        clock.advance(89)
        assert client.get(url).json()['route']
        clock.advance(1)
        assert timestamp(clock.now()) == expiry
        assert client.get(url).json()['reason_code'] == 'ROUTE_EXPIRED'


@pytest.mark.parametrize('restart_state', ['pending', 'approved', 'unknown'])
def test_restart_recovers_durable_work(tmp_path, record_property, restart_state):
    config, clock, agent = seeded(tmp_path)
    sends = []

    def callback_timeout(location):
        sends.append(location.node_id)
        raise TimeoutError('acknowledgment lost after send')

    with closing(client_for(config, clock, agent)) as client, staff_for(client) as staff:
        session = start(client, agent)
        before = visible(client, session)
        case_ref = pickup(client, session, 'b_hall', before.revision)
        if restart_state != 'pending':
            assert staff.decide(decision(case_ref)).state == 'approved'
        if restart_state == 'unknown':
            work(config, clock, call_assistance=callback_timeout)
            assert sends == ['b_hall']
        queued = send(client, session, 'queued-before-restart', outbound_leg_id=OUTBOUND)
        expected = {'pending': 'pending_approval', 'approved': 'approved',
                    'unknown': 'dispatch_pending_verification'}[restart_state]
        assert visible(client, session).assistance.state == expected
        with closing(connect(config.db_path)) as conn:
            pending = {row[0] for row in conn.execute(
                'SELECT id FROM events WHERE journey_id=? AND processed_at IS NULL', (session[0],),
            )}
            assert queued in pending
            record_property('durable_events', len(pending))

    restarted = JourneyAgent(clock, config, policy=agent.policy)
    with closing(client_for(config, clock, restarted)) as client, staff_for(client) as staff:
        new_session = passenger(client)
        url = f'/journeys/{session[0]}'
        assert client.get(url, headers=new_session[1]).status_code == 403
        assert visible(client, new_session).assistance is None
        cases = staff.list_cases(GetStaffCasesRequest(airport='DXB', states=(expected,))).cases
        assert [item.case.id for item in cases] == [case_ref]
        asyncio.run(drain(restarted))
        work(config, clock)
        current = visible(client, session)
        assert current.assistance.case_ref == case_ref and current.assistance.state == expected
        record_property('recovered_case', 1)
        if restart_state == 'unknown':
            assert current.assistance.staffed_alternative.key == 'action.seek_staff'
            assert staff.dispatch(acknowledgment(case_ref, clock)).state == 'dispatched'
        if restart_state != 'pending':
            assert staff.decide(decision(case_ref)).outcome == 'replayed'
        with closing(connect(config.db_path)) as conn:
            events = conn.execute("SELECT id, processed_at FROM events WHERE journey_id=? AND due_at<=?",
                                  (session[0], timestamp(clock.now()))).fetchall()
            recovered = sum(dict(events).get(event_id) is not None for event_id in pending)
            record_property('recovered_events', recovered)
            assert recovered == len(pending)
            trace = [(row[0], row[1]) for row in conn.execute('SELECT action, outcome FROM audit ORDER BY id')]
            assert trace.count(('coordinator_notification', 'available')) == (2 if restart_state == 'unknown' else 1)
            assert trace.count(('mobility_dispatch', 'accepted')) == (1 if restart_state == 'approved' else 0)
            assert trace.count(('dispatch_claimed', 'in_flight')) == int(restart_state != 'pending')
            assert trace.count(('decision_approve', 'applied')) == int(restart_state != 'pending')
