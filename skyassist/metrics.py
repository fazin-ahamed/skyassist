"""Run SkyAssist release gates against offline fixtures."""

import asyncio
import json
import platform
import re
import subprocess
import sys
import tomllib
from collections import Counter, defaultdict
from contextlib import closing
from datetime import timedelta
from functools import partial
from hashlib import sha256
from itertools import cycle, islice, product
from math import ceil
from os import environ
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, sleep
from unittest.mock import patch
from xml.etree import ElementTree

import httpx
from fastapi.testclient import TestClient

from skyassist.agent import JourneyAgent
from skyassist.assist import enqueue_event
from skyassist.clock import SimClock
from skyassist.config import DATA_DIR, Config, Mode
from skyassist.db import connect, timestamp
from skyassist.evidence import get_leg
from skyassist.model_boundary import PROMPT, IntentSlots, OllamaExtractor, TemplateExtractor
from skyassist.models import AircraftReport, CalculateTerminalTransferTimeInput, ExpiringSourceStamp, SecondsRange
from skyassist.providers import MockAircraftProvider, MockArrivalEstimateProvider
from skyassist.routing import load_graph
from skyassist.seed import DATASET_VERSION, SCENARIO_IDS, build_demo_db, load
from skyassist.service import DEMO_COORDINATOR_TOKEN, decide
from skyassist.service_read import create_integrated_app
from skyassist.timing import TransferFacts, calculate_terminal_transfer_time
from skyassist.tools import REPAIR_RULE_VERSION, SCHEMA_VERSION, ToolTurn


ROOT = DATA_DIR.parent
INVARIANTS = (
    'unauthorized effects', 'fabricated critical facts', 'duplicate dispatch', 'stale gate directions',
    'false dispatch confirmation', 'restricted paths shown usable',
)


def _row(name, target, value, samples, passed, **details):
    return {'gate': name, 'target': target, 'measured': value, 'samples': samples,
            'result': 'PASS' if passed else 'FAIL', **details}


def _p95(samples):
    return sorted(samples)[ceil(0.95 * len(samples)) - 1] if samples else None


def _test_gates(workdir):
    xml = workdir / 'pytest.xml'
    paths = ('tests/scenarios', 'tests/safety', 'tests/degradation', 'tests/test_assist.py', 'tests/test_routing.py',
             'tests/test_timing.py', 'tests/test_repair_regression.py', 'tests/test_second_process.py',
             'tests/test_service_read.py', 'tests/test_tools.py', 'tests/test_adapters.py')
    run = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-o', 'junit_family=xunit1',
                          f'--junitxml={xml}', *paths], cwd=ROOT, text=True, capture_output=True, check=False)
    if run.returncode:
        print(run.stdout + run.stderr, file=sys.stderr)
    tests = ElementTree.parse(xml).findall('.//testcase') if xml.exists() else []
    passing = {test for test in tests if all(test.find(tag) is None for tag in ('error', 'failure', 'skipped'))}
    props = {test: {item.get('name'): item.get('value') for item in test.findall('./properties/property')}
             for test in tests}
    core = [test for test in tests if test.get('name').startswith('test_core_scenario[')]
    total = len(SCENARIO_IDS)
    correct = sum(test in passing for test in core)
    rows = [_row('Core correctness', f'{total}/{total}', f'{correct}/{total}', len(core),
                 correct == len(core) == total)]
    rows.append(_row('Integration regression suite', 'All selected tests pass', f'{len(passing)}/{len(tests)}',
                     len(tests), run.returncode == 0 and bool(tests) and len(passing) == len(tests)))
    for invariant in INVARIANTS:
        key = f'safety:{invariant}'
        measured = [tuple(map(int, item[key].split('/'))) for item in props.values() if key in item]
        violations = sum(count for count, _ in measured)
        opportunities = sum(count for _, count in measured)
        rows.append(_row(f'Safety: {invariant}', '0 violations', f'{violations}/{opportunities}', opportunities,
                         opportunities > 0 and violations == 0))
    recovery = [test for test in tests if test.get('name').startswith('test_restart_recovers_durable_work[')]
    case_count = len(recovery)
    event_count = sum(int(props[test].get('durable_events', 0)) for test in recovery)
    for name, key, count in (('cases', 'recovered_case', case_count), ('events', 'recovered_events', event_count)):
        recovered = sum(int(props[test].get(key, 0)) for test in recovery if test in passing)
        rows.append(_row(f'Recovery: durable {name}', 'All recover', f'{recovered}/{count}', count,
                         recovered == count and count > 0))

    labeled = re.search(r'Repair regression v\S+, (\d+) labelled cases', run.stdout)
    rule_results = re.findall(r'^((?:safe repair|unsafe-must-refuse|ambiguous-must-ask) / .+?)\s+(\d+)/(\d+)\s+[\d.]+%$',
                              run.stdout, re.MULTILINE)
    repair_pass = any(test.get('name') == 'test_labelled_repair_regression' for test in passing)
    rows.append(_row('Repair regression', 'All labeled cases correct',
                     'all correct' if repair_pass else 'failed or unavailable', int(labeled[1]) if labeled else 0,
                     repair_pass and bool(rule_results)))
    for label, accurate, count in rule_results:
        rows.append(_row(f'Repair: {label}', '100% correct', f'{accurate}/{count}', int(count), accurate == count))
    return rows, next((line for line in reversed(run.stdout.splitlines()) if 'passed' in line or 'failed' in line), '')


async def _reads(path, policy, samples):
    clock = SimClock()
    now = clock.now()
    start = timestamp(now)
    expires = now + timedelta(minutes=5)
    texts = {'en': 'Gate ZX310?', 'ar': 'بوابة ZX310؟'}
    template = TemplateExtractor()

    def mock_model(request):
        text = json.loads(request.content)['messages'][-1]['content']
        slots = IntentSlots(intent='find_gate', flight_aliases=('ZX310',), language='ar' if text == texts['ar'] else 'en')
        return httpx.Response(200, json={'message': {'content': slots.model_dump_json()}})

    models = {'template': template, 'ollama-mock': OllamaExtractor(transport=httpx.MockTransport(mock_model))}
    counts = Counter(dict.fromkeys(('success', 'repaired', 'error', 'refused'), 0))
    dimensions = defaultdict(Counter)
    tool_times, model_times = defaultdict(list), defaultdict(list)
    with closing(connect(path)) as conn:
        inbound = get_leg(conn, 'dx402-1119-in')
        stamp = ExpiringSourceStamp(source='healthy-mock', observed_at=now, valid_until=expires)
        arrivals, aircraft = MockArrivalEstimateProvider(conn, policy), MockAircraftProvider(conn, policy)
        arrivals.ingest(inbound.id, 'exiting', 'baggage', stamp=stamp, received_at=now,
                        applicability='applicable', wait=SecondsRange(lower=120, upper=240))
        aircraft.ingest(AircraftReport(source=stamp.source, observed_at=now, matched_leg_id=inbound.id,
                        provider_leg_id=inbound.provider_leg_id, position={'latitude': 25.25, 'longitude': 55.36}),
                        received_at=now, valid_until=expires)
        graph = load_graph(conn, 'DXB')
        route_request = CalculateTerminalTransferTimeInput(origin_id='b_hall', gate_id='b14', profile='standard',
                                                            as_of=now)
        transfer = partial(calculate_terminal_transfer_time, graph, policy=policy, facts_provider=lambda request:
            TransferFacts(origin_id=request.origin_id, gate_id=request.gate_id, gate_state='fresh',
                          cutoff=now + timedelta(minutes=40), cutoff_verified=True,
                          ready=SecondsRange(lower=0, upper=0), as_of=request.as_of))
        leg = inbound.model_dump(mode='json')
        arguments = {
            'get_flight_and_gate_status': {'refs': ['zx310-1119-out'], 'airport': 'DXB', 'as_of': start},
            'calculate_terminal_transfer_time': route_request.model_dump(mode='json'),
            'get_arrival_estimates': {'arrival': leg, 'airport': 'DXB', 'journey_type': 'exiting'},
            'get_aircraft_position': {'leg': leg},
        }
        mix = product(arguments, models, texts, ('compact', 'pretty', 'alternate'))
        for index, (tool, model, locale, shape) in enumerate(islice(cycle(mix), samples)):
            args = arguments[tool]
            begin = perf_counter()
            slots = await models[model].extract(texts[locale])
            model_times[model].append(perf_counter() - begin)
            if slots.language != locale or slots.flight_aliases != ('ZX310',):
                raise RuntimeError(f'Offline model fixture changed intent: {model}/{locale}')
            if shape == 'alternate':
                if tool == 'get_flight_and_gate_status':
                    args = args | {'airport': ' dxb ', 'refs': json.dumps(slots.flight_aliases)}
                elif tool == 'get_arrival_estimates':
                    args = args | {'airport': ' dxb '}
                else:
                    args = dict(reversed(tuple(args.items())))
            raw = json.dumps(args, indent=2 if shape == 'pretty' else None)
            event = enqueue_event(conn, None, 'release_read', start, event_id=f'release-read-{index}', processed=True)
            turn = ToolTurn(conn, clock, policy, event_id=event, airport='DXB', confirmed_transfer=route_request,
                            transfer=transfer, arrivals=arrivals, aircraft=aircraft)
            begin = perf_counter()
            turn.call(tool, raw)
            tool_times[tool].append(perf_counter() - begin)
            outcome = conn.execute('SELECT outcome FROM audit WHERE event_id=?', (event,)).fetchone()[0]
            counts[outcome] += 1
            dimensions[f'{tool}/{model}/{locale}/{shape}'][outcome] += 1
    return counts, dict(dimensions), {'tool_path': tool_times, 'model_only': model_times}


def _latencies(path, policy, rounds, typed_samples):
    config, clock = Config(Mode.DEMO, path), SimClock()
    agent = JourneyAgent(clock, config, policy=policy)
    app = create_integrated_app(clock, config, agent=agent)
    auth = {'Authorization': f'Bearer {environ.get("SKYASSIST_COORDINATOR_TOKEN") or DEMO_COORDINATOR_TOKEN}'}
    gate_event = next(event for event in load()['events'] if event.id == 'ev-c02-gate-b18')
    measured = defaultdict(list)
    decision_commit = None

    def committed_decision(*args, **kwargs):
        nonlocal decision_commit
        result = decide(*args, **kwargs)
        decision_commit = perf_counter()
        return result

    with TestClient(app) as client, patch('skyassist.service.decide', side_effect=committed_decision):
        def new_journey(leg=None):
            response = client.post('/journeys', json={'airport': 'DXB', 'outbound_leg_id': leg}).raise_for_status()
            return response.json()['journey_id'], {'Authorization': response.headers['Authorization']}

        def send(session, input_id):
            journey, headers = session
            response = client.post(f'/journeys/{journey}/inputs', headers=headers, json={
                'journey_id': journey, 'client_input_id': input_id, 'intent': 'check_connection',
                'confirmed_origin_id': 'b_hall',
            }).raise_for_status()
            return response.json()['event_id']

        def await_view(session, predicate):
            journey, headers = session
            deadline = perf_counter() + 4
            while perf_counter() < deadline:
                view = client.get(f'/journeys/{journey}', headers=headers).raise_for_status().json()['view']
                if predicate(view):
                    return view
                sleep(0.005)
            raise RuntimeError(f'No visible state within 4 s (last revision: {view["revision"]})')

        def routed(leg):
            session = new_journey(leg)
            send(session, 'initial')
            await_view(session, lambda view: view['route'] is not None)
            return session

        gate_leg = gate_event.payload.leg_id
        routed_session = routed(gate_leg)
        typed_session = routed('zx310-1119-out')
        for load_name in ('normal', 'loaded'):
            if load_name == 'loaded':
                for _ in range(12):
                    routed(gate_leg)
                routed_session = routed(gate_leg)
            for index in range(rounds):
                clock.advance(1)
                gate = 'B18' if (clock.now().second % 2) else 'B14'
                now = clock.now()
                event = gate_event.model_copy(update={'id': f'latency-gate-{load_name}-{index}', 'timestamp': now,
                    'payload': gate_event.payload.model_copy(update={'value': gate, 'observed_at': now, 'received_at': now})})
                agent.enqueue_update(event)
                committed = perf_counter()
                await_view(routed_session, lambda view: view['route'] is not None and view['gate']['value'] == gate)
                measured[f'gate/{load_name}'].append(perf_counter() - committed)

                journey, headers = new_journey()
                created = client.post(f'/journeys/{journey}/assistance', headers=headers, json={
                    'journey_id': journey, 'client_input_id': 'confirm', 'journey_revision': 0, 'service': 'wheelchair',
                    'pickup_node_id': 'b_hall', 'pickup_confirmed': True, 'consent': True,
                }).raise_for_status().json()
                case = created['case_ref']
                response = client.post(f'/staff/cases/{case}/decisions', headers=auth, json={
                    'case_id': case, 'event_id': f'latency-approval-{load_name}-{index}',
                    'request_revision': created['request_revision'], 'decision': 'approve',
                }).raise_for_status().json()
                if response['state'] != 'approved' or decision_commit is None:
                    raise RuntimeError(f'Approval did not commit: {response}')
                await_view((journey, headers), lambda view: view['assistance']['state'] == 'approved')
                measured[f'approval/{load_name}'].append(perf_counter() - decision_commit)

            for index in range(typed_samples // 2 + (typed_samples % 2 if load_name == 'loaded' else 0)):
                started = perf_counter()
                event_id = send(typed_session, f'typed-{load_name}-{index}')

                def result_visible(view):
                    if view['status']['status'] != 'Time available' or view['route'] is None:
                        return False
                    with closing(connect(path)) as conn:
                        revision = conn.execute('SELECT revision FROM journey_revisions WHERE event_id=?',
                                                (event_id,)).fetchone()
                    return revision is not None and view['revision'] >= revision[0]

                await_view(typed_session, result_visible)
                measured[f'typed/{load_name}'].append(perf_counter() - started)
    return dict(measured)


def _summaries(groups):
    return {name: {'samples': len(samples), 'p95_seconds': _p95(samples)} for name, samples in groups.items()}


def run_gates(report_dir, *, reads=512, rounds=25, typed=100):
    if min(reads, rounds, typed) < 1:
        raise ValueError('sample sizes must be positive')
    bundle = load()
    policy = bundle['policy']
    smoke = reads < 500 or rounds * 4 < 100 or typed < 100
    with TemporaryDirectory(prefix='skyassist-gates-') as scratch:
        workdir = Path(scratch)
        rows, test_summary = _test_gates(workdir)
        read_counts, distribution, component_times = asyncio.run(
            _reads(build_demo_db(workdir / 'reads.sqlite3'), policy, reads))
        latency = _latencies(build_demo_db(workdir / 'latency.sqlite3'), policy, rounds, typed)

    accepted = read_counts['success'] + read_counts['repaired']
    rows.append(_row('Read acceptance', f'>=99% of {reads} eligible reads', f'{accepted / reads:.2%}', reads,
                     accepted * 100 >= reads * 99, accepted=accepted,
                     initial_validation_success=read_counts['success'], repaired_success=read_counts['repaired'],
                     backend_failure=read_counts['error'], safe_refusal=read_counts['refused']))
    for label, maximum, minimum, select in (
        ('Committed gate or approval to visible state', 2, rounds * 4, lambda name: not name.startswith('typed/')),
        ('Local typed connection result', 3, typed, lambda name: name.startswith('typed/')),
    ):
        samples = [sample for name, group in latency.items() if select(name) for sample in group]
        percentile = _p95(samples)
        rows.append(_row(label, f'p95 <= {maximum} s', f'{percentile:.6f} s' if percentile is not None else 'unmeasured',
                         len(samples), len(samples) >= minimum and percentile is not None and percentile <= maximum))

    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    report = {'scope': 'reduced smoke only' if smoke else 'offline release gates', 'gates': rows,
              'versions': {'skyassist': project['version'], 'python': platform.python_version(),
                           'dataset': DATASET_VERSION, 'policy': policy.policy_version,
                           'graph': bundle['graph'].graph_version, 'tool_schema': SCHEMA_VERSION,
                           'fixture_sha256': sha256(bundle['raw'].encode()).hexdigest(),
                           'repair_rules': REPAIR_RULE_VERSION, 'prompt_sha256': sha256(PROMPT.encode()).hexdigest(),
                           'model': 'template and Ollama mock transport'},
              'test_summary': test_summary, 'read_distribution': distribution, 'latency': _summaries(latency),
              'components': {name: _summaries(group) for name, group in component_times.items()}}
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / 'release_gates.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    lines = ['# SkyAssist release gates', '', f'Scope: {report["scope"]}. {test_summary}', '',
             '| Gate | Target | Measured | Samples | Result |', '| --- | --- | --- | ---: | --- |']
    lines.extend(f'| {row["gate"]} | {row["target"]} | {row["measured"]} | {row["samples"]} | {row["result"]} |'
                 for row in rows)
    lines.extend(['', f'Eligible reads: {read_counts["success"]} initial validation successes, '
                  f'{read_counts["repaired"]} repaired successes, {read_counts["error"]} backend failures, '
                  f'{read_counts["refused"]} safe refusals.', '',
                  'Versions: ' + ', '.join(f'{name} {value}' for name, value in report['versions'].items()) + '.', '',
                  'Latency: monotonic wall time via in-process HTTP transport and real workers. Loaded runs keep at '
                  'least 12 additional monitoring journeys active. No human, browser, physical scanner, live model, '
                  'or live-provider timing is included. JSON breaks out tool, model, and workload samples.'])
    (report_dir / 'release_gates.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return report


def main():
    report = run_gates(ROOT / 'reports')
    print(report['test_summary'])
    for row in report['gates']:
        print(f'{row["result"]} {row["gate"]}: {row["measured"]} (n={row["samples"]}; target {row["target"]})')
    failures = sum(row['result'] == 'FAIL' for row in report['gates'])
    print(f'Release gates: {len(report["gates"]) - failures}/{len(report["gates"])} PASS')
    return int(failures > 0)


if __name__ == '__main__':
    raise SystemExit(main())
