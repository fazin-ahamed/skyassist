import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from skyassist.clock import as_utc
from skyassist.config import CONFIG

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS flight_legs (
    id TEXT PRIMARY KEY,
    operating_carrier TEXT NOT NULL,
    operating_number TEXT NOT NULL,
    scheduled_operation_date TEXT NOT NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    provider_leg_id TEXT UNIQUE,
    UNIQUE (operating_carrier, operating_number, scheduled_operation_date, origin, destination)
) STRICT;

CREATE TABLE IF NOT EXISTS flight_aliases (
    alias TEXT NOT NULL,
    leg_id TEXT NOT NULL REFERENCES flight_legs(id),
    PRIMARY KEY (alias, leg_id)
) STRICT;

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    leg_id TEXT NOT NULL REFERENCES flight_legs(id),
    field TEXT NOT NULL,
    value_json TEXT CHECK (json_valid(value_json)),
    source TEXT NOT NULL,
    authority_rank INTEGER NOT NULL CHECK (authority_rank >= 0),
    observed_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    scheduled TEXT,
    estimated TEXT,
    actual TEXT,
    withdrawn INTEGER NOT NULL DEFAULT 0 CHECK (withdrawn IN (0, 1)),
    cutoff_estimated INTEGER NOT NULL DEFAULT 0 CHECK (cutoff_estimated IN (0, 1)),
    CHECK (valid_until >= observed_at)
) STRICT;

CREATE INDEX IF NOT EXISTS observations_by_fact_source
    ON observations (leg_id, field, source, observed_at DESC, received_at DESC);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    airport TEXT NOT NULL,
    terminal TEXT NOT NULL,
    concourse TEXT NOT NULL,
    floor TEXT NOT NULL,
    security_zone TEXT NOT NULL,
    landmark_json TEXT NOT NULL CHECK (json_valid(landmark_json)),
    x REAL NOT NULL,
    y REAL NOT NULL,
    public INTEGER NOT NULL DEFAULT 1 CHECK (public IN (0, 1)),
    pickup_eligible INTEGER NOT NULL DEFAULT 0 CHECK (pickup_eligible IN (0, 1))
) STRICT;

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    from_node_id TEXT NOT NULL REFERENCES nodes(id),
    to_node_id TEXT NOT NULL REFERENCES nodes(id),
    edge_json TEXT NOT NULL CHECK (json_valid(edge_json))
) STRICT;

CREATE INDEX IF NOT EXISTS edges_by_origin ON edges (from_node_id);

CREATE TABLE IF NOT EXISTS overlays (
    id TEXT PRIMARY KEY,
    edge_id TEXT NOT NULL REFERENCES edges(id),
    airport TEXT NOT NULL,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    closed INTEGER CHECK (closed IN (0, 1)),
    accessible INTEGER CHECK (accessible IN (0, 1)),
    CHECK (valid_until >= observed_at)
) STRICT;

CREATE INDEX IF NOT EXISTS overlays_by_fact_source
    ON overlays (edge_id, source, observed_at DESC);

CREATE TABLE IF NOT EXISTS queue_observations (
    id INTEGER PRIMARY KEY,
    airport TEXT NOT NULL,
    queue_id TEXT NOT NULL,
    flow TEXT NOT NULL,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    lower_seconds INTEGER CHECK (lower_seconds >= 0),
    upper_seconds INTEGER CHECK (upper_seconds >= lower_seconds),
    CHECK (upper_seconds IS NULL OR lower_seconds IS NOT NULL),
    CHECK (valid_until >= observed_at)
) STRICT;

CREATE INDEX IF NOT EXISTS queues_by_fact_source
    ON queue_observations (airport, queue_id, flow, source, observed_at DESC);

CREATE TABLE IF NOT EXISTS amenity_observations (
    id INTEGER PRIMARY KEY,
    airport TEXT NOT NULL,
    node_id TEXT NOT NULL REFERENCES nodes(id),
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    open INTEGER CHECK (open IN (0, 1)),
    CHECK (valid_until >= observed_at)
) STRICT;

CREATE INDEX IF NOT EXISTS amenities_by_fact_source
    ON amenity_observations (node_id, source, observed_at DESC);

CREATE TABLE IF NOT EXISTS journeys (
    id TEXT PRIMARY KEY,
    airport TEXT NOT NULL,
    profile TEXT NOT NULL,
    party_needs_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(party_needs_json)),
    flow TEXT NOT NULL DEFAULT 'connecting',
    language TEXT NOT NULL DEFAULT 'en',
    created_at TEXT NOT NULL,
    inbound_leg_id TEXT REFERENCES flight_legs(id),
    outbound_leg_id TEXT REFERENCES flight_legs(id),
    origin_node_id TEXT REFERENCES nodes(id),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    next_refresh_at TEXT,
    closed_at TEXT,
    booking_ref TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS journeys_by_active_inbound
    ON journeys (inbound_leg_id) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS journeys_by_active_outbound
    ON journeys (outbound_leg_id) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS journeys_due
    ON journeys (next_refresh_at) WHERE closed_at IS NULL;

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    journey_id TEXT REFERENCES journeys(id),
    client_input_id TEXT,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    due_at TEXT,
    processed_at TEXT,
    outcome_json TEXT CHECK (json_valid(outcome_json)),
    payload_json TEXT CHECK (json_valid(payload_json)),
    CHECK (client_input_id IS NULL OR journey_id IS NOT NULL),
    UNIQUE (journey_id, client_input_id)
) STRICT;

CREATE INDEX IF NOT EXISTS events_due ON events (due_at) WHERE processed_at IS NULL;

CREATE TABLE IF NOT EXISTS journey_revisions (
    journey_id TEXT NOT NULL REFERENCES journeys(id),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    created_at TEXT NOT NULL,
    event_id TEXT REFERENCES events(id),
    policy_version TEXT NOT NULL,
    graph_version TEXT,
    overlay_version TEXT,
    view_json TEXT NOT NULL CHECK (json_valid(view_json)),
    route_token_hash TEXT UNIQUE,
    route_expires_at TEXT,
    route_revoked_at TEXT,
    CHECK (route_token_hash IS NULL OR route_expires_at IS NOT NULL),
    PRIMARY KEY (journey_id, revision)
) STRICT;

CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    journey_id TEXT NOT NULL REFERENCES journeys(id),
    airport TEXT NOT NULL,
    service TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'draft', 'pending_approval', 'approved', 'dispatch_pending_verification',
        'dispatched', 'completed', 'declined', 'cancelled', 'expired'
    )),
    request_revision INTEGER NOT NULL CHECK (request_revision >= 0),
    journey_revision INTEGER NOT NULL CHECK (journey_revision >= 0),
    created_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    pickup_node_id TEXT REFERENCES nodes(id),
    pickup_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (pickup_confirmed IN (0, 1)),
    consent INTEGER NOT NULL DEFAULT 0 CHECK (consent IN (0, 1)),
    approval_expires_at TEXT,
    target_leg_id TEXT REFERENCES flight_legs(id),
    target_revision INTEGER CHECK (target_revision >= 0),
    reason_codes_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(reason_codes_json))
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS equivalent_open_case ON cases (
    journey_id, service, coalesce(pickup_node_id, ''), coalesce(target_leg_id, '')
) WHERE state IN ('draft', 'pending_approval', 'approved', 'dispatch_pending_verification', 'dispatched');

CREATE INDEX IF NOT EXISTS staff_cases_by_airport ON cases (airport, state, created_at);
CREATE INDEX IF NOT EXISTS case_approvals_due
    ON cases (approval_expires_at) WHERE state = 'pending_approval';

CREATE TABLE IF NOT EXISTS staff_decisions (
    event_id TEXT PRIMARY KEY REFERENCES events(id),
    case_id TEXT NOT NULL REFERENCES cases(id),
    request_revision INTEGER NOT NULL CHECK (request_revision >= 0),
    decision TEXT NOT NULL CHECK (decision IN ('approve', 'decline')),
    reason_code TEXT,
    outcome_json TEXT NOT NULL CHECK (json_valid(outcome_json))
) STRICT;

CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id),
    request_revision INTEGER NOT NULL CHECK (request_revision >= 0),
    effect TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','claimed','sent','unknown','done')),
    due_at TEXT NOT NULL,
    claim_expires_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    idempotency_key TEXT NOT NULL UNIQUE,
    in_flight_at TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS outbox_due ON outbox (due_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS outbox_claims_due ON outbox (claim_expires_at) WHERE status = 'claimed';

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id),
    occurred_at TEXT NOT NULL,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL,
    role TEXT,
    journey_id TEXT REFERENCES journeys(id),
    case_id TEXT REFERENCES cases(id),
    tool_id TEXT,
    schema_version TEXT,
    repair_rule_version TEXT,
    rule_id TEXT,
    error_class TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    duration_seconds INTEGER CHECK (duration_seconds >= 0),
    reason_codes_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(reason_codes_json))
) STRICT;

CREATE INDEX IF NOT EXISTS audit_by_event ON audit (event_id, id);

CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN
    SELECT RAISE(ABORT, 'audit is append-only');
END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN
    SELECT RAISE(ABORT, 'audit is append-only');
END;
"""


def timestamp(instant: datetime) -> str:
    return as_utc(instant).isoformat(timespec="microseconds")


def _check_mode(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'meta'").fetchone():
        mode = conn.execute("SELECT value FROM meta WHERE key = 'mode'").fetchone()
        if mode is not None and mode[0] != CONFIG.mode:
            raise ValueError(f"{CONFIG.mode} process refuses database stamped {mode[0]!r}")


# Tables and columns added after the first release. They are created on first connect so an existing
# demo database keeps working without a reset.
_ADDED = """
CREATE TABLE IF NOT EXISTS manifest (
    booking_ref TEXT NOT NULL,
    pax_id TEXT NOT NULL,
    name TEXT NOT NULL,
    age INTEGER NOT NULL CHECK (age >= 0),
    unaccompanied_minor INTEGER NOT NULL DEFAULT 0 CHECK (unaccompanied_minor IN (0, 1)),
    elderly INTEGER NOT NULL DEFAULT 0 CHECK (elderly IN (0, 1)),
    wheelchair INTEGER NOT NULL DEFAULT 0 CHECK (wheelchair IN (0, 1)),
    PRIMARY KEY (booking_ref, pax_id)
) STRICT;

CREATE TABLE IF NOT EXISTS resources (
    kind TEXT PRIMARY KEY,
    total INTEGER NOT NULL CHECK (total >= 0),
    seats INTEGER NOT NULL CHECK (seats >= 0)
) STRICT;

CREATE TABLE IF NOT EXISTS buggy_groups (
    id TEXT PRIMARY KEY,
    anchor_case_id TEXT NOT NULL REFERENCES cases(id),
    destination TEXT NOT NULL,
    created_at TEXT NOT NULL,
    seats INTEGER NOT NULL,
    plan_json TEXT NOT NULL CHECK (json_valid(plan_json))
) STRICT;
"""
_migrated: set[str] = set()


def _migrate(conn: sqlite3.Connection, path: object) -> None:
    key = str(path)
    if key in _migrated:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(journeys)")}
    if columns:
        if "booking_ref" not in columns:
            conn.execute("ALTER TABLE journeys ADD COLUMN booking_ref TEXT")
        conn.executescript(_ADDED)
        _migrated.add(key)


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(CONFIG.db_path if path is None else path, autocommit=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        # REPLACE fires the audit delete trigger only with recursive triggers enabled.
        conn.execute("PRAGMA recursive_triggers = ON")
        _check_mode(conn)
        if conn.execute("PRAGMA journal_mode = WAL").fetchone()[0] != "wal":
            raise ValueError("SkyAssist requires a WAL-capable SQLite database")
        _migrate(conn, CONFIG.db_path if path is None else path)
        return conn
    except BaseException:
        conn.close()
        raise


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def init_db(path: Path | str | None = None, *, dataset_version: str = "unseeded") -> None:
    db_path = Path(CONFIG.db_path if path is None else path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(db_path)) as conn, transaction(conn):
        _check_mode(conn)
        conn.executescript(_SCHEMA)
        conn.executescript(_ADDED)
        conn.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
            (("mode", CONFIG.mode), ("dataset_version", dataset_version)),
        )
