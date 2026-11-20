import asyncio
from contextlib import closing
from datetime import datetime, timedelta
from threading import Lock

from skyassist.agent import JourneyAgent
from skyassist.clock import Clock
from skyassist.config import Config, Mode
from skyassist.db import timestamp, transaction
from skyassist.evidence import _latest, _observation, _select, ingest
from skyassist.models import ObservationField, PolicyPack


class DemoFeed:
    def __init__(self, clock: Clock, config: Config, agent: JourneyAgent, policy: PolicyPack):
        if config.mode != Mode.DEMO:
            raise ValueError("demo feed requires demo mode")
        self.clock, self.open_db, self.policy = clock, agent.open_db, policy
        self.held: set[tuple[str, ObservationField]] = set()
        self.lock = Lock()

    def tick(self) -> None:
        with self.lock, closing(self.open_db()) as conn, transaction(conn):
            self._tick(conn, self.clock.now())

    def _tick(self, conn, now: datetime, pair: tuple[str, ObservationField] | None = None) -> None:
        pairs = (pair,) if pair else (
            (leg_id, field) for (leg_id,) in conn.execute("SELECT id FROM flight_legs") for field in ObservationField
        )
        for leg_id, field in pairs:
            if pair is None and (leg_id, field) in self.held:
                continue
            keys = ("scheduled", "estimated", "actual") if field in (
                ObservationField.ARRIVAL, ObservationField.DEPARTURE,
            ) else ("value_json", "cutoff_estimated")
            rows = [row for row in _latest(conn, leg_id, field, now)
                    if any(row[key] is not None for key in keys if key != "cutoff_estimated")]
            # Letting one report age out must not make a contested fact fresh.
            if not rows or len({tuple(row[key] for key in keys) for row in rows
                                if row["authority_rank"] == rows[0]["authority_rank"]}) != 1:
                continue
            _, selected = _select(rows, now, self.policy.stale_seconds, *keys)
            observation = _observation(selected[0])
            changes = conn.total_changes
            ingest(conn, observation.model_copy(update={
                "observed_at": now, "received_at": now,
                "valid_until": now + (observation.valid_until - observation.observed_at),
            }))
            if conn.total_changes != changes:
                new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                key = f"demo_feed:{leg_id}:{field}"
                conn.execute("DELETE FROM observations WHERE id=(SELECT value FROM meta WHERE key=?) "
                             "AND leg_id=? AND field=?", (key, leg_id, field))
                conn.execute("INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (key, str(new_id)))

        stamp = timestamp(now)
        scope = ("AND (inbound_leg_id=? OR outbound_leg_id=?)" if pair else
                 "AND (inbound_leg_id IS NOT NULL OR outbound_leg_id IS NOT NULL)")
        params = (stamp, stamp, pair[0], pair[0]) if pair else (stamp, stamp)
        conn.execute("UPDATE journeys SET next_refresh_at=? WHERE next_refresh_at>? "
                     "AND closed_at IS NULL " + scope, params)

    async def run(self) -> None:
        period = timedelta(seconds=self.policy.refresh_seconds)
        if not period:
            return
        previous = self.clock.now()
        while True:
            await asyncio.sleep(0.1)
            now = self.clock.now()
            if now < previous or now - previous >= period:
                self.tick()
                previous = now
