import json
import math
import shutil
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from skyassist import models as m
from skyassist import seed
from skyassist.config import DATA_DIR
from skyassist.db import connect

SCENARIOS = [f"C{n:02d}" for n in range(1, 11)]


def _read(rel: str):
    return json.loads((DATA_DIR / rel).read_text(encoding="utf-8"))


def _dump(path: Path) -> str:
    with closing(connect(path)) as conn:
        return "\n".join(conn.iterdump())


def _count(path: Path, table: str) -> int:
    with closing(connect(path)) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_every_fixture_file_validates_against_models():
    m.FixtureGraph.model_validate(_read("graph.json"))
    m.PolicyPack.model_validate(_read("policy.json"))
    for doc in _read("flights.json"):
        m.FlightLegFixture.model_validate(doc)
    for doc in _read("events.json"):
        m.EventStreamItem.model_validate(doc)
    for doc in _read("landmarks.json"):
        m.LandmarkAliasFixture.model_validate(doc)
    for sid in SCENARIOS:
        m.ScenarioFixture.model_validate(_read(f"scenarios/{sid}.json"))
    starts = TypeAdapter(dict[str, m.UTCInstant]).validate_python(_read("clock_starts.json"))
    assert set(starts) == set(SCENARIOS)
    key = TypeAdapter(m.TranslationKey)
    for lang in ("en", "ar"):
        for name in _read(f"i18n/{lang}/landmarks.json"):
            if not name.startswith("_"):
                key.validate_python(name)
    assert _read("i18n/ar/landmarks.json")["_review"] == "unreviewed"


def test_manifest_is_deterministic_and_labelled_demo(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    first = seed.write_manifest(data).read_bytes()
    assert first == seed.write_manifest(data).read_bytes()
    manifest = json.loads(first)
    assert manifest["mode"] == "demo" and manifest["label"].startswith("Demo dataset")
    assert manifest["route_node_legend"]["lift_c"] and "Asia/Dubai" == manifest["timezone"]
    assert any("OurAirports" in lic["source"] for lic in manifest["licenses"])


def test_build_twice_gives_identical_content(tmp_path):
    first = seed.build_demo_db(tmp_path / "a.sqlite3")
    second = seed.build_demo_db(tmp_path / "b.sqlite3")
    assert _dump(first) == _dump(second)
    graph, legs = _read("graph.json"), _read("flights.json")
    assert _count(first, "nodes") == len(graph["nodes"]) == 24
    assert _count(first, "edges") == len(graph["edges"]) == 34
    assert _count(first, "flight_legs") == len(legs) == 21
    assert _count(first, "observations") == sum(len(leg["initial_observations"]) for leg in legs)
    aliases = {(alias, leg["id"]) for leg in legs for alias in leg["aliases"] + leg["codeshare_aliases"]}
    assert _count(first, "flight_aliases") == len(aliases)


def test_fixture_plan_requirements_hold():
    legs = [m.FlightLegFixture.model_validate(x) for x in _read("flights.json")]
    assert len(legs) >= 12
    assert sum(1 for leg in legs if leg.scheduled_operation_date.isoformat() == "2026-11-20") >= 1
    assert sum(1 for leg in legs if leg.codeshare_aliases) >= 2
    assert sum(1 for leg in legs if leg.scheduled_operation_date.isoformat() == "2026-11-19") >= 2
    assert [leg.id for leg in legs if "DXB-402" in leg.aliases] == ["dx402-1119-in"]
    dated = {leg.operating_carrier + leg.operating_number: set() for leg in legs}
    for leg in legs:
        dated[leg.operating_carrier + leg.operating_number].add(leg.scheduled_operation_date)
    assert any(len(days) == 2 for days in dated.values())
    landmarks = [m.LandmarkAliasFixture.model_validate(x) for x in _read("landmarks.json")]
    costa = {lm.node_id: lm for lm in landmarks if lm.alias == "Costa"}
    floors = {node["id"]: node["floor"] for node in _read("graph.json")["nodes"]}
    assert len(costa) == 2 and len({floors[node] for node in costa}) == 2


def test_graph_plan_requirements_hold():
    graph = m.FixtureGraph.model_validate(_read("graph.json"))
    assert 22 <= len(graph.nodes) <= 30
    assert {n.concourse for n in graph.nodes} >= {"B", "C"}
    assert any(e.fixed_movement and e.wait.upper for e in graph.edges if e.mode.value == "transit")
    modes = {e.mode.value for e in graph.edges}
    assert {"lift", "transit", "checkpoint", "stairs"} <= modes
    doors = [n for n in graph.nodes if not n.public]
    assert doors and all(e.restricted for n in doors for e in graph.edges if e.to_node_id == n.id)


# Each validator in seed.VALIDATORS must have at least one failing case below.
def _by_id(rows, key, value):
    return next(row for row in rows if row[key] == value)


def _edge(doc, eid):
    return _by_id(doc["edges"], "id", eid)


def _event_payload(doc):
    return _by_id(doc, "id", "ev-c01-ready")["payload"]


def _first_obs(doc):
    return doc[0]["initial_observations"][0]


NEGATIVE = [
    ("check_foreign_keys", "events.json",
     lambda doc: _event_payload(doc).__setitem__("leg_id", "no-such-leg"), "references missing leg"),
    ("check_directed_edges", "graph.json",
     lambda doc: _edge(doc, "e_b14_b14_door").__setitem__("restricted", False), "unrestricted edge"),
    ("check_access_coverage", "graph.json",
     lambda doc: (_edge(doc, "e_c_hall_b_hall").__setitem__("queue_id", None),
                  _edge(doc, "e_c_hall_b_hall").__setitem__("fixed_movement", None)), "queue or screening"),
    ("check_geometry", "graph.json",
     lambda doc: _by_id(doc["nodes"], "id", "b14").__setitem__("x", 10.0), "outside its floor shape"),
    ("check_timezones", "airports.json",
     lambda doc: _by_id(doc, "iata_code", "DXB").__setitem__("timezone", "Mars/Olympus"), "is not IANA"),
    ("check_timezones", "flights.json",
     lambda doc: _by_id(doc, "id", "zx515-1120-out").__setitem__("scheduled_operation_date", "2026-11-19"),
     "not local departure date"),
    ("check_unique_legs", "flights.json",
     lambda doc: doc.append(dict(doc[0])), "duplicate flight leg id"),
    ("check_reachability", "scenarios/C06.json",
     lambda doc: doc.__setitem__("event_sequence", ["ev-c06-resolve", "ev-c06-ready"]), "before the previous event"),
    ("check_reachability", "scenarios/C01.json",
     lambda doc: doc.__setitem__("expected_route_node_ids", ["c22", "train_c", "lift_c", "train_b", "b14"]),
     "has no directed edge"),
    ("check_profiles", "scenarios/C04.json",
     lambda doc: doc.__setitem__("profile", "standard"), "does not follow its party needs"),
    ("check_provenance", "flights.json",
     lambda doc: _first_obs(doc).__setitem__("received_at", "2026-11-19T03:00:00Z"), "received before observed"),
    ("check_sources", "flights.json",
     lambda doc: _first_obs(doc).__setitem__("source", "live-airlabs-feed"), "demo allowlist"),
    ("check_identifiers", "landmarks.json",
     lambda doc: doc[0].__setitem__("alias", "jane.doe@example.com"), "personal identifier"),
    ("check_i18n", "i18n/ar/landmarks.json",
     lambda doc: doc.pop("landmark.c22"), "lacks"),
    ("check_i18n", "i18n/ar/landmarks.json",
     lambda doc: doc.pop("_review"), "must be marked unreviewed"),
]


def test_negative_cases_cover_every_validator():
    assert {case[0] for case in NEGATIVE} == {check.__name__ for check in seed.VALIDATORS}


@pytest.mark.parametrize("check, rel, mutate, message", NEGATIVE, ids=lambda v: str(v)[:40])
def test_each_validator_rejects_bad_fixture(tmp_path, check, rel, mutate, message):
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    path = data / rel
    doc = json.loads(path.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    bundle = seed.load(data)
    with pytest.raises(ValueError, match=message):
        getattr(seed, check)(bundle)


def test_clean_data_passes_every_validator():
    seed.validate(seed.load(DATA_DIR))


# Independent reference: DFS over every simple path of admissible directed edges, scored with the
# ROUTING-SPEC formulas read from graph.json and policy.json. No Dijkstra, no skyassist.routing import.
REFERENCE = {
    # scenario: (ready event, outbound leg, passenger flow)
    "C01": ("ev-c01-ready", "zx515-1119-out", "connecting"),
    "C02": ("ev-c02-ready", "zx730-1119-out", "connecting"),
    "C03": ("ev-c03-ready", "zx310-1119-out", "connecting"),
    "C04": ("ev-c04-ready", "zx118-1119-out", "departing"),
    "C09": ("ev-c09-ready", "zx515-1120-out", "connecting"),
}
# Hand-derived golden values: ROUTING-SPEC table (minutes x 60) and plan section 8.
GOLDEN = {
    "C01": dict(ready=(240, 360), remaining=2100, buffer=420, move=(420, 540), wait=(180, 420),
                route=["c22", "lift_c", "train_c", "train_b", "b14"], lower=1260, upper=1740, margin=360,
                status="Tight connection"),
    "C02": dict(ready=(60, 120), remaining=1920, buffer=420, move=(210, 420), wait=(0, 0),
                route=["b_hall", "b18"], lower=690, upper=960, margin=960, status="Time available"),
    "C03": dict(ready=(0, 0), remaining=2400, buffer=180, move=(300, 480), wait=(0, 0),
                route=["b_hall", "b14"], lower=480, upper=660, margin=1740, status="Time available"),
    "C04": dict(ready=(0, 0), remaining=480, buffer=300, move=(240, 600), wait=(180, 420),
                route=["public_hall", "c_hall", "c12"], lower=720, upper=1320, margin=-840,
                status="Transfer unlikely before cutoff"),
    "C09": dict(ready=(300, 510), remaining=1680, buffer=180, move=(140, 210), wait=(0, 0),
                route=["b_hall", "b18"], lower=620, upper=900, margin=780, status="Time available"),
}
STEP_FREE = {"reduced_mobility", "family_mobility"}


def _admissible(graph, profile, flow, evidence):
    nodes = {n["id"]: n for n in graph["nodes"]}
    out: dict[str, list[dict]] = {}
    for e in sorted(graph["edges"], key=lambda item: item["id"]):
        src, dst = nodes[e["from_node_id"]], nodes[e["to_node_id"]]
        if src["airport"] != "DXB" or dst["airport"] != "DXB" or not dst["public"]:
            continue
        if e["closed"] or e["restricted"] or flow not in e["allowed_flows"]:
            continue
        if e["requires_access_evidence"] and e["id"] not in evidence:
            continue
        if src["security_zone"] != dst["security_zone"] and e["mode"] != "checkpoint":
            continue
        if profile in STEP_FREE and not (e["accessible"] and e["steps"] == 0):
            continue
        out.setdefault(e["from_node_id"], []).append(e)
    return out


def _simple_paths(out, origin, target):
    found = []

    def walk(node, seen, taken):
        if node == target:
            found.append(list(taken))
            return
        for e in out.get(node, []):
            nxt = e["to_node_id"]
            if nxt not in seen:
                seen.add(nxt)
                taken.append(e)
                walk(nxt, seen, taken)
                taken.pop()
                seen.remove(nxt)

    walk(origin, {origin}, [])
    return found


def _move(e, policy, profile):
    if e["fixed_movement"]:
        return e["fixed_movement"]["lower"], e["fixed_movement"]["upper"]
    band = policy["speed_bands"][profile]
    return math.ceil(e["distance_m"] / band["fast_mps"]), math.ceil(e["distance_m"] / band["slow_mps"])


def _wait(e, queues):
    if e["queue_id"] is None:
        return e["wait"]["lower"], e["wait"]["upper"]
    if e["queue_id"] not in queues:
        return e["wait"]["lower"], None  # missing or stale queue: upper unbounded
    return queues[e["queue_id"]]


def _covers(e, start, span):
    return not e["open_windows"] or any(
        datetime.fromisoformat(w["start"]) <= start
        and datetime.fromisoformat(w["end"]) >= start + timedelta(seconds=span)
        for w in e["open_windows"]
    )


def _status(remaining, upper, margin, best_lower, tight):
    if remaining < 0:
        return "Boarding cutoff passed"
    if upper is None:
        return "Timing unverified"
    if 0 <= margin <= tight:
        return "Tight connection"
    if margin > tight:
        return "Time available"
    return "Transfer unlikely before cutoff" if best_lower > remaining else "May miss boarding"


def _reference(scenario, graph, policy, ready, remaining, flow, as_of, evidence, queues=None):
    profile = scenario["profile"]
    origin, target = scenario["expected_route_node_ids"][0], scenario["expected_route_node_ids"][-1]
    buffer = policy["buffer_floors"][profile]
    options = []
    for path in _simple_paths(_admissible(graph, profile, flow, evidence), origin, target):
        move = [_move(e, policy, profile) for e in path]
        wait = [_wait(e, queues or {}) for e in path]
        flagged = any(up is None for _, up in wait)
        weight = sum(up for _, up in move) + sum(lo if up is None else up for lo, up in wait)
        if all(_covers(e, as_of, weight) for e in path):
            options.append(((flagged, weight, [e["id"] for e in path]), path, move, wait))
    _, path, move, wait = min(options, key=lambda option: option[0])
    move_lo, move_up = sum(lo for lo, _ in move), sum(up for _, up in move)
    wait_lo = sum(lo for lo, _ in wait)
    wait_up = None if any(up is None for _, up in wait) else sum(up for _, up in wait)
    lower = ready[0] + move_lo + wait_lo + buffer
    upper = None if wait_up is None else ready[1] + move_up + wait_up + buffer
    margin = None if upper is None else remaining - upper
    best_lower = min(
        ready[0] + sum(lo for lo, _ in mv) + sum(lo for lo, _ in wt) + buffer for _, _, mv, wt in options
    )
    return {
        "route": [origin] + [e["to_node_id"] for e in path], "buffer": buffer,
        "move": (move_lo, move_up), "wait": (wait_lo, wait_up), "lower": lower, "upper": upper,
        "margin": margin,
        "status": _status(remaining, upper, margin, best_lower, policy["tight_margin_seconds"]),
    }


def _fixture_inputs(sid):
    ready_id, leg_id, flow = REFERENCE[sid]
    ready = _by_id(_read("events.json"), "id", ready_id)["payload"]["ready"]
    leg = _by_id(_read("flights.json"), "id", leg_id)
    cutoff = next(o["value"] for o in leg["initial_observations"] if o["field"] == "boarding_cutoff")
    as_of = datetime.fromisoformat(_read("clock_starts.json")[sid])
    remaining = int((datetime.fromisoformat(cutoff) - as_of).total_seconds())
    return (ready["lower"], ready["upper"]), remaining, flow, as_of


def _access_evidence(scenario):
    events = _read("events.json")
    latest = {}
    for eid in scenario["event_sequence"]:
        item = _by_id(events, "id", eid)
        if item["event_type"] == "facility_changed" and "edge_id" in item["payload"]:
            latest[item["payload"]["edge_id"]] = item["payload"]
    return {edge for edge, p in latest.items() if p["closed"] is False and p["accessible"] is True}


@pytest.mark.parametrize("sid", sorted(REFERENCE))
def test_reference_enumerator_reproduces_scenario_and_golden(sid):
    graph, policy = _read("graph.json"), _read("policy.json")
    scenario = _read(f"scenarios/{sid}.json")
    ready, remaining, flow, as_of = _fixture_inputs(sid)
    got = _reference(scenario, graph, policy, ready, remaining, flow, as_of, _access_evidence(scenario))
    want = GOLDEN[sid]
    assert (ready, remaining) == (want["ready"], want["remaining"])
    assert got["buffer"] == want["buffer"] and got["move"] == want["move"] and got["wait"] == want["wait"]
    assert got["route"] == want["route"] == scenario["expected_route_node_ids"]
    assert got["lower"] == want["lower"] == scenario["expected_transfer_lower_seconds"]
    assert got["upper"] == want["upper"] == scenario["expected_transfer_upper_seconds"]
    assert got["margin"] == want["margin"] == scenario["expected_margin_seconds"]
    assert got["status"] == want["status"] == scenario["expected_status"]


def test_c01_needs_fresh_lift_evidence():
    graph, policy = _read("graph.json"), _read("policy.json")
    scenario = _read("scenarios/C01.json")
    ready, remaining, flow, as_of = _fixture_inputs("C01")
    got = _reference(scenario, graph, policy, ready, remaining, flow, as_of, set())
    assert got["upper"] is None and got["route"] != GOLDEN["C01"]["route"]


def test_c10_stale_queue_withholds_upper_bound():
    graph, policy = _read("graph.json"), _read("policy.json")
    scenario = _read("scenarios/C10.json")
    got = _reference(scenario, graph, policy, (0, 0), 1800, "connecting",
                     datetime.fromisoformat("2026-11-19T06:00:00+00:00"), set())
    assert got["route"] == scenario["expected_route_node_ids"]
    assert got["upper"] is None and got["margin"] is None and got["lower"] == 660
    assert got["status"] == "Timing unverified"
