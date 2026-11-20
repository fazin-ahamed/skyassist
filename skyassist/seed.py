"""Load, validate and seed the SkyAssist demo store. All data is synthetic."""

import json
import re
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from skyassist import models as m
from skyassist.config import CONFIG, DATA_DIR, Mode
from skyassist.db import connect, init_db, timestamp, transaction

DATASET_VERSION = "skyassist-demo-2026-11-19.1"
GENERATOR_SEED = 20261119
SCENARIO_IDS = tuple(f"C{n:02d}" for n in range(1, 11))
# ev-c01-gate-a3 is the HS-4.1 curveball: the gate moves to Concourse A after a wheelchair request is approved
SUB_CHECK_EVENTS = ("ev-c01-gate-a3", "ev-c08-lift-reopen", "ev-c10-queue-fresh")
SOURCES = frozenset({
    "airline-ops-feed", "gate-feed-a", "gate-feed-b", "ops-desk", "crew-report",
    "facility-ops", "checkpoint-feed-c", "dispatch-sim", "demo-landmarks-v1",
})
MAP_WIDTH, MAP_HEIGHT = 200, 120
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"\+\d[\d -]{6,}\d")
PASSPORT = re.compile(r"\b[A-Z]{1,2}\d{6,9}\b")
ROUTE_LEGEND = {
    "c22": "Concourse C pickup point C22 (parked aircraft)",
    "lift_c": "Lift C lobby, level 2",
    "train_c": "Train C platform, level 1",
    "train_b": "Train B platform, level 2",
    "b14": "Gate B14",
    "b_hall": "Concourse B hall (pickup point)",
    "b18": "Gate B18",
    "b_costa": "Costa landmark, level 2, concourse B",
    "public_hall": "Public arrivals hall (landside)",
    "c_hall": "Concourse C central hall",
    "c12": "Gate C12",
}
KNOWN_GAPS = [
    "No live provider data (AirLabs, Flightradar24, OpenSky, queue or baggage feeds).",
    "Overlay, QueueObservation and AmenityObservation carry no received_at; ReadinessEvent carries no valid_until or received_at (models.py). Lint checks the fields that exist.",
    "Amenity observations (family stretch) are not seeded.",
    "db.py has no landmark-alias table; data/landmarks.json is read from fixtures only.",
    "build_demo_db persists nodes, edges, flight legs, aliases and initial observations. Events and policy are replayed from fixtures, not stored.",
    "Speed bands are synthetic calibration; 0.5, 0.75, 1.0, 1.25 and 1.5 m/s keep ceil() exact.",
    "Boarding cutoff 10:40 Dubai is shared by ZX310 (C03) and ZX420 (C06); cutoffs are distinct per leg, not globally.",
    "Arabic landmark labels are unreviewed.",
]

Bundle = dict


def _fail(message: str) -> None:
    raise ValueError(message)


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load(data: Path = DATA_DIR) -> Bundle:
    files = [p for p in data.rglob("*.json") if "schema" not in p.parts and p.name != "manifest.json"]
    return {
        "graph": m.FixtureGraph.model_validate(_read(data / "graph.json")),
        "policy": m.PolicyPack.model_validate(_read(data / "policy.json")),
        "legs": tuple(m.FlightLegFixture.model_validate(x) for x in _read(data / "flights.json")),
        "events": tuple(m.EventStreamItem.model_validate(x) for x in _read(data / "events.json")),
        "landmarks": tuple(m.LandmarkAliasFixture.model_validate(x) for x in _read(data / "landmarks.json")),
        "clocks": _read(data / "clock_starts.json"),
        "scenarios": tuple(
            m.ScenarioFixture.model_validate(_read(data / "scenarios" / f"{sid}.json")) for sid in SCENARIO_IDS
        ),
        "i18n": {lang: _read(data / "i18n" / lang / "landmarks.json") for lang in ("en", "ar")},
        "airports": {row["iata_code"]: row["timezone"] for row in _read(data / "airports.json")},
        "raw": "\n".join(p.read_text(encoding="utf-8") for p in sorted(files)),
    }


def derive_profile(needs: m.PartyNeeds) -> m.Profile:
    reduced = needs.wheelchair or needs.step_free
    family = needs.children > 0
    if reduced and family:
        return m.Profile.FAMILY_MOBILITY
    if reduced:
        return m.Profile.REDUCED_MOBILITY
    return m.Profile.FAMILY if family else m.Profile.STANDARD


def check_foreign_keys(b: Bundle) -> None:
    nodes = {n.id for n in b["graph"].nodes}
    edges = {e.id for e in b["graph"].edges}
    legs = {leg.id for leg in b["legs"]}
    events = {item.id for item in b["events"]}
    for lm in b["landmarks"]:
        if lm.node_id not in nodes:
            _fail(f"landmark alias {lm.alias!r} references missing node {lm.node_id}")
    for item in b["events"]:
        p = item.payload
        if isinstance(p, (m.Observation, m.ReadinessEvent)) and p.leg_id not in legs:
            _fail(f"event {item.id} references missing leg {p.leg_id}")
        if isinstance(p, m.Overlay) and p.edge_id not in edges:
            _fail(f"event {item.id} references missing edge {p.edge_id}")
    for sc in b["scenarios"]:
        for node in sc.expected_route_node_ids:
            if node not in nodes:
                _fail(f"{sc.id} route references missing node {node}")
        for eid in sc.event_sequence:
            if eid not in events:
                _fail(f"{sc.id} references missing event {eid}")


def check_directed_edges(b: Bundle) -> None:
    g = b["graph"]
    zone = {n.id: n.security_zone for n in g.nodes}
    public = {n.id: n.public for n in g.nodes}
    pairs = {(e.from_node_id, e.to_node_id) for e in g.edges}
    incoming: dict[str, list[m.Edge]] = {}
    for e in g.edges:
        incoming.setdefault(e.to_node_id, []).append(e)
        if zone[e.from_node_id] != zone[e.to_node_id] and e.mode is not m.AccessibleMode.CHECKPOINT:
            _fail(f"{e.id} changes security zone outside a checkpoint")
        if e.mode is m.AccessibleMode.CHECKPOINT and (e.to_node_id, e.from_node_id) in pairs:
            _fail(f"checkpoint {e.id} must not have a reverse edge")
        if e.lift and (e.mode is not m.AccessibleMode.LIFT or not e.requires_access_evidence):
            _fail(f"{e.id} lift must be mode lift and require access evidence")
        if e.mode is m.AccessibleMode.STAIRS and e.accessible:
            _fail(f"{e.id} stairs cannot be accessible")
    for n in g.nodes:
        if not public[n.id]:
            for e in incoming.get(n.id, []):
                if not e.restricted:
                    _fail(f"non-public node {n.id} reachable through unrestricted edge {e.id}")


def check_access_coverage(b: Bundle) -> None:
    for e in b["graph"].edges:
        if e.accessible and e.steps > 0:
            _fail(f"{e.id} is accessible but records {e.steps} steps")
        if not e.accessible and e.steps == 0 and e.mode is not m.AccessibleMode.STAIRS:
            _fail(f"{e.id} is inaccessible but records no steps or stairs mode")
        if e.mode is m.AccessibleMode.CHECKPOINT and e.queue_id is None and e.fixed_movement is None:
            _fail(f"{e.id} checkpoint needs a queue or screening time")


def check_geometry(b: Bundle) -> None:
    g = b["graph"]
    shapes = {(s.terminal, s.concourse, s.floor): s for s in g.floor_shapes}
    for s in g.floor_shapes:
        for p in s.points:
            if not (0 <= p.x <= MAP_WIDTH and 0 <= p.y <= MAP_HEIGHT):
                _fail(f"floor shape point {p.x},{p.y} is outside the map")
    for n in g.nodes:
        if not (0 <= n.x <= MAP_WIDTH and 0 <= n.y <= MAP_HEIGHT):
            _fail(f"node {n.id} is outside the map")
        shape = shapes.get((n.terminal, n.concourse, n.floor))
        if shape is None:
            _fail(f"node {n.id} has no floor shape")
        xs = [p.x for p in shape.points]
        ys = [p.y for p in shape.points]
        if not (min(xs) <= n.x <= max(xs) and min(ys) <= n.y <= max(ys)):
            _fail(f"node {n.id} lies outside its floor shape")


def check_timezones(b: Bundle) -> None:
    for leg in b["legs"]:
        name = b["airports"].get(leg.origin)
        if name is None:
            _fail(f"{leg.id} origin {leg.origin} has no timezone")
        try:
            zone = ZoneInfo(name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"{leg.id} timezone {name} is not IANA") from exc
        local = leg.scheduled_departure.astimezone(zone).date()
        if local != leg.scheduled_operation_date:
            _fail(f"{leg.id} operation date {leg.scheduled_operation_date} is not local departure date {local}")


def check_unique_legs(b: Bundle) -> None:
    legs = b["legs"]
    if len({leg.id for leg in legs}) != len(legs):
        _fail("duplicate flight leg id")
    keys = [(x.operating_carrier, x.operating_number, x.scheduled_operation_date, x.origin, x.destination)
            for x in legs]
    if len(set(keys)) != len(keys):
        _fail("duplicate operating leg identity")
    provider = [x.provider_leg_id for x in legs if x.provider_leg_id]
    if len(set(provider)) != len(provider):
        _fail("duplicate provider leg id")


def check_reachability(b: Bundle) -> None:
    events = {item.id: item for item in b["events"]}
    pairs = {(e.from_node_id, e.to_node_id) for e in b["graph"].edges}
    referenced: set[str] = set()
    for sc in b["scenarios"]:
        if sc.clock_start not in b["clocks"]:
            _fail(f"{sc.id} clock start {sc.clock_start} is not in clock_starts.json")
        previous: datetime | None = None
        for eid in sc.event_sequence:
            if eid not in events:
                _fail(f"{sc.id} replays unknown event {eid}")
            stamp = events[eid].timestamp
            if previous is not None and stamp < previous:
                _fail(f"{sc.id} replays {eid} before the previous event")
            previous = stamp
            referenced.add(eid)
        route = sc.expected_route_node_ids
        for start, end in zip(route, route[1:]):
            if (start, end) not in pairs:
                _fail(f"{sc.id} route step {start} -> {end} has no directed edge")
    if set(events) - referenced != set(SUB_CHECK_EVENTS):
        _fail(f"unreferenced events {sorted(set(events) - referenced)} are not declared sub-checks")


def check_profiles(b: Bundle) -> None:
    for sc in b["scenarios"]:
        if derive_profile(sc.party_needs) is not sc.profile:
            _fail(f"{sc.id} profile {sc.profile} does not follow its party needs")


def _stamped(record) -> None:
    if not record.source:
        _fail(f"{type(record).__name__} has no source")
    if record.observed_at > getattr(record, "valid_until", record.observed_at):
        _fail(f"{type(record).__name__} expires before it was observed")
    received = getattr(record, "received_at", None)
    if received is not None and received < record.observed_at:
        _fail(f"{type(record).__name__} received before observed")


def check_provenance(b: Bundle) -> None:
    for leg in b["legs"]:
        for o in leg.initial_observations:
            _stamped(o)
    for item in b["events"]:
        p = item.payload
        if isinstance(p, (m.Observation, m.Overlay, m.QueueObservation, m.AmenityObservation, m.ReadinessEvent)):
            _stamped(p)
        elif isinstance(p, m.DispatchStatusRequest) and not p.evidence.source:
            _fail(f"dispatch status {item.id} has no evidence source")


def check_sources(b: Bundle) -> None:
    seen = {lm.source for lm in b["landmarks"]}
    for leg in b["legs"]:
        seen.update(o.source for o in leg.initial_observations)
    for item in b["events"]:
        p = item.payload
        if isinstance(p, (m.Observation, m.Overlay, m.QueueObservation, m.ReadinessEvent)):
            seen.add(p.source)
        elif isinstance(p, m.DispatchStatusRequest):
            seen.add(p.evidence.source)
    if unknown := seen - SOURCES:
        _fail(f"sources outside the demo allowlist: {sorted(unknown)}")
    if live := [s for s in seen if s.startswith("live")]:
        _fail(f"live-looking source mixed into demo data: {sorted(live)}")


def check_identifiers(b: Bundle) -> None:
    for pattern in (EMAIL, PHONE, PASSPORT):
        if found := pattern.search(b["raw"]):
            _fail(f"possible personal identifier {found.group(0)!r}")


def check_i18n(b: Bundle) -> None:
    public = {f"landmark.{n.id}" for n in b["graph"].nodes if n.public}
    for lang, catalog in b["i18n"].items():
        if missing := public - {k for k in catalog if not k.startswith("_")}:
            _fail(f"{lang} catalog lacks {sorted(missing)}")
    if b["i18n"]["ar"].get("_review") != "unreviewed":
        _fail("Arabic catalog must be marked unreviewed")


VALIDATORS = (
    check_foreign_keys, check_directed_edges, check_access_coverage, check_geometry,
    check_timezones, check_unique_legs, check_reachability, check_profiles,
    check_provenance, check_sources, check_identifiers, check_i18n,
)


def validate(b: Bundle) -> None:
    for check in VALIDATORS:
        check(b)


def _instant(value):
    return None if value is None else timestamp(value)


def _obs_row(o: m.Observation):
    value = timestamp(o.value) if isinstance(o.value, datetime) else o.value
    return (o.leg_id, o.field.value, json.dumps(value), o.source, o.authority_rank,
            timestamp(o.observed_at), timestamp(o.received_at), timestamp(o.valid_until),
            _instant(o.scheduled), _instant(o.estimated), _instant(o.actual),
            int(o.withdrawn), int(o.cutoff_estimated))


def _store(conn, b: Bundle) -> None:
    g = b["graph"]
    conn.executemany(
        "INSERT INTO nodes (id, airport, terminal, concourse, floor, security_zone, landmark_json, x, y, "
        "public, pickup_eligible) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(n.id, n.airport, n.terminal, n.concourse, n.floor, n.security_zone, n.landmark.model_dump_json(),
          n.x, n.y, int(n.public), int(n.pickup_eligible)) for n in g.nodes],
    )
    conn.executemany(
        "INSERT INTO edges (id, from_node_id, to_node_id, edge_json) VALUES (?,?,?,?)",
        [(e.id, e.from_node_id, e.to_node_id, e.model_dump_json()) for e in g.edges],
    )
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)",
                 (f"floor_outlines:{g.airport}", json.dumps([shape.model_dump() for shape in g.floor_shapes])))
    conn.executemany(
        "INSERT INTO flight_legs (id, operating_carrier, operating_number, scheduled_operation_date, origin, "
        "destination, provider_leg_id) VALUES (?,?,?,?,?,?,?)",
        [(x.id, x.operating_carrier, x.operating_number, x.scheduled_operation_date.isoformat(),
          x.origin, x.destination, x.provider_leg_id) for x in b["legs"]],
    )
    conn.executemany(
        "INSERT INTO flight_aliases (alias, leg_id) VALUES (?,?)",
        [(alias, x.id) for x in b["legs"] for alias in (*x.aliases, *x.codeshare_aliases)],
    )
    conn.executemany(
        "INSERT INTO observations (leg_id, field, value_json, source, authority_rank, observed_at, received_at, "
        "valid_until, scheduled, estimated, actual, withdrawn, cutoff_estimated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [_obs_row(o) for x in b["legs"] for o in x.initial_observations],
    )


def build_demo_db(path: Path | str | None = None, data: Path = DATA_DIR) -> Path:
    if CONFIG.mode is not Mode.DEMO:
        raise ValueError("build_demo_db only seeds the demo store (SKYASSIST_MODE=demo)")
    bundle = load(data)
    validate(bundle)
    target = Path(CONFIG.db_path if path is None else path)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{target}{suffix}").unlink(missing_ok=True)
    init_db(target, dataset_version=DATASET_VERSION)
    with closing(connect(target)) as conn, transaction(conn):
        _store(conn, bundle)
        from skyassist import booking  # late import: booking builds on the assistance module
        extra = data / "passengers.json"
        passengers = _read(extra) if extra.exists() else {"people": [], "resources": []}
        booking.load_manifest(conn, passengers["people"], passengers["resources"])
    return target


def write_manifest(data: Path = DATA_DIR) -> Path:
    b = load(data)
    validate(b)
    airports = _read(data / "airports.manifest.json")
    manifest = {
        "label": "Demo dataset: terminal map, flights and fixtures",
        "mode": "demo",
        "dataset_version": DATASET_VERSION,
        "generator_seed": GENERATOR_SEED,
        "graph_version": b["graph"].graph_version,
        "policy_version": b["policy"].policy_version,
        "airport": "DXB",
        "timezone": "Asia/Dubai",
        "demo_date": "2026-11-19",
        "counts": {
            "nodes": len(b["graph"].nodes), "edges": len(b["graph"].edges), "flight_legs": len(b["legs"]),
            "events": len(b["events"]), "scenarios": len(b["scenarios"]), "landmark_aliases": len(b["landmarks"]),
        },
        "scenario_routes": {sc.id: list(sc.expected_route_node_ids) for sc in b["scenarios"]},
        "route_node_legend": ROUTE_LEGEND,
        "sub_check_events": list(SUB_CHECK_EVENTS),
        "licenses": [
            {"component": "Airport metadata (data/airports.json)",
             "source": f"{airports['dataset']} ({airports['source_repo']})",
             "license": airports["license"], "revision": airports["commit_sha"]},
            {"component": "Terminal map, flights, events, scenarios and i18n",
             "source": "Hand-authored synthetic fixtures in this repository",
             "license": "Project demo data; no real persons, passengers or airline schedules"},
        ],
        "known_gaps": KNOWN_GAPS,
    }
    path = data / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    if sys.argv[1:2] == ["build"]:
        print(build_demo_db(sys.argv[2] if len(sys.argv) > 2 else None))
    else:
        print(write_manifest())
