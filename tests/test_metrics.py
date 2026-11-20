import json

from skyassist.metrics import run_gates


def test_release_metrics_smoke(tmp_path):
    report = run_gates(tmp_path, reads=48, rounds=1, typed=2)
    assert report['scope'] == 'reduced smoke only'
    gates = {row['gate']: row for row in report['gates']}
    assert [row for row in gates.values() if row['result'] != 'PASS'] == []
    assert gates['Core correctness']['measured'] == '10/10'
    assert gates['Read acceptance']['samples'] == 48
    assert gates['Read acceptance']['repaired_success'] > 0
    assert gates['Committed gate or approval to visible state']['samples'] == 4
    assert gates['Local typed connection result']['samples'] == 2
    assert json.loads((tmp_path / 'release_gates.json').read_text()) == report
    assert '| Core correctness |' in (tmp_path / 'release_gates.md').read_text()
