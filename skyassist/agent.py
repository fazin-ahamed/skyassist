import asyncio
import json
import re
import secrets
from collections.abc import Iterable
from contextlib import closing, suppress
from datetime import datetime, timedelta
from functools import partial
from hashlib import sha256

from pydantic import ValidationError

from skyassist import assist, booking, replan
from skyassist.clock import Clock
from skyassist.config import Config
from skyassist.db import connect, timestamp, transaction
from skyassist.evidence import current_field, get_leg, ingest, resolve_flight
from skyassist.i18n import render
from skyassist.model_boundary import INSTRUCTION, URGENT, IntentExtractor, IntentSlots, TemplateExtractor
from skyassist.models import (
    AircraftFreshness, AircraftReport, AmenityObservation, ArrivalEstimates, AssistanceService, BannerKind,
    CaseState, Contract, CutoffView, EventStreamItem, EventType, GateView, GetAircraftPositionInput,
    GetArrivalEstimatesInput, GetFlightAndGateStatusInput, I18nText, Intent, JourneyInputRequest,
    LandmarkAliasFixture, Observation, ObservationField, ObservationState, Overlay, PartyNeeds,
    Notice, PermittedChoice, PolicyPack, Profile, QRView, QueueObservation, ReadinessEvent,
    ReasonCode, SecondsRange, SourceStamp, Success, ToolError, ViewModel,
)
from skyassist.providers import AircraftProvider, ArrivalEstimateProvider, FlightProvider, matched_leg
from skyassist.routing import _public, load_graph
from skyassist.service import SESSION_HORIZON
from skyassist.timing import TransferFacts, evaluate_transfer, profile_for
from skyassist.tools import ReadRecovery, ToolTurn
from skyassist.view import HANDOFF_PENDING, build_view, reviewed_text

R = ReasonCode
E = EventType
F = ObservationField
FRESH = ObservationState.FRESH
UPDATE_KINDS = (E.FLIGHT_UPDATED, E.GATE_CHANGED, E.FACILITY_CHANGED)
FLIGHT_TOOL = "get_flight_and_gate_status"
ARRIVALS_TOOL = "get_arrival_estimates"
AIRCRAFT_TOOL = "get_aircraft_position"
GATE_FIELDS = {F.GATE: "value", F.TERMINAL: "terminal", F.CONCOURSE: "concourse", F.LEVEL: "level"}
SAFE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .-]{0,23}")
MOBILITY = (AssistanceService.WHEELCHAIR, AssistanceService.MOBILITY_ESCORT, AssistanceService.BUGGY)
KIOSK_NODE = "c22"
GATE_CODE = re.compile(r"(?:[A-Za-z]{1,4}[- ]?)?[0-9]{1,4}[A-Za-z]?")


class Selection(Contract):
    aliases: tuple[str, ...] = ()
    reason: ReasonCode | None = None
    choices: tuple[PermittedChoice, ...] = ()
    assistance: bool = False


def valid_observation(observation: Observation) -> bool:
    if observation.field not in GATE_FIELDS or observation.withdrawn or observation.value is None:
        return True
    pattern = GATE_CODE if observation.field == F.GATE else SAFE_LABEL
    return isinstance(observation.value, str) and bool(pattern.fullmatch(observation.value))


def _revoke(conn, journey_id, event_id, now):
    revoked = conn.execute(
        "UPDATE journey_revisions SET route_revoked_at=? WHERE journey_id=? "
        "AND route_token_hash IS NOT NULL AND route_revoked_at IS NULL AND route_expires_at>?",
        (now, journey_id, now),
    )
    if revoked.rowcount:
        assist.audit(conn, event_id, now, "route_revoked", "applied", journey_id=journey_id)


def _finish(conn, event_id, now, **outcome):
    conn.execute("UPDATE events SET processed_at=?, outcome_json=coalesce(outcome_json, ?) WHERE id=?",
                 (now, json.dumps(outcome), event_id))


def _as_listed(conn, text: str) -> str:
    """Text typed by a passenger ("dxb 402", "DXB402") named as the schedule lists it ("DXB-402").

    Only used on the passenger path. The evidence tools stay strict: a model-written reference with its
    punctuation removed is still refused there.
    """
    compact = re.sub(r"[\s-]", "", text).casefold()
    if not compact:
        return text
    for (alias,) in conn.execute("SELECT DISTINCT alias FROM flight_aliases"):
        if re.sub(r"[\s-]", "", alias).casefold() == compact:
            return alias
    for (code,) in conn.execute("SELECT DISTINCT operating_carrier || operating_number FROM flight_legs"):
        if code.casefold() == compact:
            return code
    return text


class JourneyAgent:
    def __init__(
        self, clock: Clock, config: Config, *, policy: PolicyPack | None = None,
        extractor: IntentExtractor | None = None, flights: FlightProvider | None = None,
        arrivals: ArrivalEstimateProvider | None = None, aircraft: AircraftProvider | None = None,
        landmark_aliases: Iterable[LandmarkAliasFixture] = (),
    ):
        self.clock = clock
        self.open_db = partial(connect, config.db_path)
        self.policy = policy if policy is not None else PolicyPack()
        self.extractor = extractor if extractor is not None else TemplateExtractor()
        self.flights, self.arrivals, self.aircraft = flights, arrivals, aircraft
        self.landmark_aliases = tuple(landmark_aliases)
        self.recovery = ReadRecovery(clock)
        self._lock = asyncio.Lock()
        self._reads = {}

    def enqueue_update(self, event: EventStreamItem) -> None:
        if event.event_type not in UPDATE_KINDS:
            raise ValueError("expected a flight or facility update")
        with closing(self.open_db()) as conn, transaction(conn):
            conn.execute(
                "INSERT INTO events(id, event_type, occurred_at, due_at, payload_json) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                (event.id, event.event_type, timestamp(event.timestamp), timestamp(event.timestamp),
                 event.payload.model_dump_json()),
            )
            self._apply_updates(conn, timestamp(self.clock.now()))

    def stop_monitoring(self, journey_id: str) -> None:
        with closing(self.open_db()) as conn, transaction(conn):
            if assist.journey_row(conn, journey_id) is None:
                return
            now = timestamp(self.clock.now())
            conn.execute("INSERT OR IGNORE INTO meta VALUES (?, '1')", (f"monitoring_stopped:{journey_id}",))
            conn.execute("UPDATE journeys SET next_refresh_at=NULL WHERE id=?", (journey_id,))
            event_id = assist.enqueue_event(conn, journey_id, "monitoring_stopped", now, processed=True)
            _revoke(conn, journey_id, event_id, now)

    def _apply_updates(self, conn, now):
        updates = conn.execute(
            "SELECT * FROM events WHERE processed_at IS NULL AND due_at<=? AND payload_json IS NOT NULL "
            "AND event_type IN (?, ?, ?) ORDER BY rowid", (now, *UPDATE_KINDS),
        ).fetchall()
        for row in updates:
            try:
                event = EventStreamItem(id=row["id"], timestamp=row["occurred_at"], event_type=row["event_type"],
                                        payload=json.loads(row["payload_json"]))
                payload = event.payload
                if isinstance(payload, Observation):
                    if not valid_observation(payload) or get_leg(conn, payload.leg_id) is None:
                        raise ValueError("invalid observation")
                    if payload.received_at > self.clock.now() or payload.observed_at > self.clock.now():
                        raise ValueError("future observation")
                    self._ingest(conn, payload, row["id"], now)
                elif isinstance(payload, ReadinessEvent):
                    if get_leg(conn, payload.leg_id) is None or timestamp(payload.observed_at) > now:
                        raise ValueError("invalid readiness")
                    key = f"readiness:{payload.leg_id}"
                    prev = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
                    if not prev or ReadinessEvent.model_validate_json(prev[0]).observed_at <= payload.observed_at:
                        conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, payload.model_dump_json()))
                        self._fanout(conn, row["id"], now, E.FLIGHT_UPDATED, leg_id=payload.leg_id)
                else:
                    if timestamp(payload.observed_at) > now:
                        raise ValueError("future facility observation")
                    self._facility(conn, payload)
                    self._fanout(conn, row["id"], now, E.FACILITY_CHANGED, airport=payload.airport,
                                 invalidate=isinstance(payload, Overlay) and
                                 (payload.closed is True or payload.accessible is False))
            except (ValueError, TypeError):
                _finish(conn, row["id"], now, reason_code=R.INVALID_ARGUMENTS)
            else:
                _finish(conn, row["id"], now, outcome="ingested")
        return bool(updates)

    @staticmethod
    def _facility(conn, payload):
        common = (payload.airport, payload.source, timestamp(payload.observed_at), timestamp(payload.valid_until))
        if isinstance(payload, Overlay):
            if not conn.execute("SELECT 1 FROM edges JOIN nodes ON nodes.id=edges.from_node_id "
                                "WHERE edges.id=? AND nodes.airport=?", (payload.edge_id, payload.airport)).fetchone():
                raise ValueError("unknown facility")
            conn.execute("INSERT OR IGNORE INTO overlays VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (payload.id, payload.edge_id, *common, payload.closed, payload.accessible))
        elif isinstance(payload, QueueObservation):
            wait = payload.wait
            conn.execute("INSERT INTO queue_observations(airport, source, observed_at, valid_until, queue_id, flow, "
                         "lower_seconds, upper_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (*common, payload.queue_id, payload.flow, wait.lower if wait else None,
                          wait.upper if wait else None))
        elif isinstance(payload, AmenityObservation):
            if not conn.execute("SELECT 1 FROM nodes WHERE id=? AND airport=?",
                                (payload.node_id, payload.airport)).fetchone():
                raise ValueError("unknown amenity")
            conn.execute("INSERT INTO amenity_observations(airport, source, observed_at, valid_until, node_id, open) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (*common, payload.node_id, payload.open))

    def _ingest(self, conn, observation, event_id, now, *, skip=None):
        as_of = self.clock.now()
        before = current_field(conn, observation.leg_id, observation.field, as_of, self.policy)
        if observation == before.observation or observation in before.alternatives:
            return
        ingest(conn, observation)
        after = current_field(conn, observation.leg_id, observation.field, as_of, self.policy)
        if after != before:
            def identity(fact):
                return fact.state, fact.observation.value if fact.observation else None

            invalidate = observation.field in (*GATE_FIELDS, F.STATUS) and identity(before) != identity(after)
            self._fanout(conn, event_id, now, E.GATE_CHANGED if observation.field == F.GATE else E.FLIGHT_UPDATED,
                         leg_id=observation.leg_id, invalidate=invalidate, skip=skip)

    def _fanout(self, conn, event_id, now, kind, *, leg_id=None, airport=None, invalidate=False, skip=None):
        scope, params = (("(inbound_leg_id=? OR outbound_leg_id=?)", (leg_id, leg_id)) if leg_id
                         else ("airport=?", (airport,)))
        for journey in conn.execute(
            f"SELECT * FROM journeys WHERE closed_at IS NULL AND {scope} AND created_at>? "
            "AND NOT EXISTS (SELECT 1 FROM meta WHERE key='monitoring_stopped:' || journeys.id)",
            (*params, timestamp(self.clock.now() - SESSION_HORIZON)),
        ):
            journey_id = journey["id"]
            if invalidate and (leg_id is None or journey["outbound_leg_id"] == leg_id):
                _revoke(conn, journey_id, event_id, now)
                latest = assist.latest_revision(conn, journey_id)
                if latest and ViewModel.model_validate_json(latest["view_json"]).route:
                    assist.audit(conn, event_id, now, "route_invalidated", "applied", journey_id=journey_id)
            if journey_id != skip:
                assist.enqueue_event(conn, journey_id, kind, now)

    def _prepare(self):
        with closing(self.open_db()) as conn, transaction(conn):
            instant = self.clock.now()
            now = timestamp(instant)
            applied = self._apply_updates(conn, now)
            conn.execute("UPDATE journeys SET next_refresh_at=NULL WHERE next_refresh_at IS NOT NULL "
                         "AND (closed_at IS NOT NULL OR created_at<=?)", (timestamp(instant - SESSION_HORIZON),))
            for (journey_id,) in conn.execute(
                "SELECT id FROM journeys WHERE next_refresh_at<=? AND NOT EXISTS "
                "(SELECT 1 FROM events WHERE journey_id=journeys.id AND processed_at IS NULL AND event_type=?) "
                "ORDER BY next_refresh_at, id", (now, E.REFRESH_DUE),
            ).fetchall():
                assist.enqueue_event(conn, journey_id, E.REFRESH_DUE, now)
            event = conn.execute(
                "SELECT rowid AS seq, * FROM events WHERE journey_id IS NOT NULL AND processed_at IS NULL "
                "AND due_at<=? AND event_type IN (?, ?, ?, ?, ?, ?, ?) ORDER BY rowid LIMIT 1", (now, *E),
            ).fetchone()
            if event is None:
                return applied, None
            journey = self._journey(conn, event["journey_id"])
            watermark = conn.execute("SELECT max(rowid) FROM events").fetchone()[0]
            observed = conn.execute("SELECT coalesce(max(id), 0) FROM observations").fetchone()[0]
            return applied, (event, journey, watermark, observed)

    @staticmethod
    def _journey(conn, journey_id):
        journey = dict(assist.journey_row(conn, journey_id))
        selected = conn.execute("SELECT value FROM meta WHERE key=?", (f"selection:{journey_id}",)).fetchone()
        journey["selection"] = Selection.model_validate_json(selected[0]) if selected else Selection()
        journey["stopped"] = conn.execute("SELECT 1 FROM meta WHERE key=?",
                                          (f"monitoring_stopped:{journey_id}",)).fetchone() is not None
        return journey

    def _aliases(self, graph):
        labels = {}
        public_nodes = set()
        for node in graph.nodes:
            if not _public(node):
                continue
            public_nodes.add(node.id)
            labels.setdefault(node.id.casefold(), set()).add(node.id)
            for language in ("en", "ar"):
                with suppress(KeyError, ValueError, TypeError):
                    labels.setdefault(render(node.landmark, language).casefold(), set()).add(node.id)
        for alias in self.landmark_aliases:
            if alias.airport == graph.airport and alias.node_id in public_nodes:
                labels.setdefault(alias.alias.casefold(), set()).add(alias.node_id)
        return labels

    def normalize_landmark(self, conn, airport, text):
        folded = text.strip().casefold()
        aliases = self._aliases(load_graph(conn, airport))
        # Keep only public wayfinding terms in the durable event. An empty match
        # still has to produce a location question rather than clear the selection.
        return folded if folded in aliases else "\ufffd"

    def _resolve(self, conn, journey, body, slots, graph, as_of):
        if body is None or (body.text and INSTRUCTION.search(body.text)):
            return
        airport = journey["airport"]
        selection = journey["selection"]
        reason, choices = selection.reason, selection.choices
        urgent = body.text and URGENT.search(body.text)
        intent = body.intent if body.intent != Intent.UNKNOWN else slots.intent
        aliases = body.flight_aliases or slots.flight_aliases or (selection.aliases if body.operation_date else ())
        references = [("inbound_leg_id", body.inbound_leg_id), ("outbound_leg_id", body.outbound_leg_id)]
        if aliases or any(ref for _, ref in references):
            reason, choices = None, ()
            resolved = {}
            # Codes pulled out of a sentence are guesses. One that matches no flight is dropped, not an error,
            # unless nothing else resolved; codes the passenger typed into the flight form stay strict.
            soft = not body.flight_aliases and not any(ref for _, ref in references) and bool(slots.flight_aliases)
            unmatched = None
            for field, ref in (*references, *((None, alias) for alias in aliases)):
                if not ref:
                    continue
                if field is None:
                    ref = _as_listed(conn, ref)
                leg = resolve_flight(conn, ref, airport, as_of, body.operation_date if field is None else None)
                if isinstance(leg, ToolError):
                    if soft and leg.diagnostic in (R.FLIGHT_UNKNOWN, R.WRONG_AIRPORT):
                        unmatched = unmatched or leg
                        continue
                    reason, choices = leg.diagnostic, leg.permitted_choices
                    break
                direction = "outbound_leg_id" if leg.origin == airport else "inbound_leg_id"
                if field not in (None, direction) or direction in resolved and resolved[direction] != leg.id:
                    reason = R.AMBIGUOUS_FLIGHT if field is None else R.WRONG_AIRPORT
                    break
                resolved[direction] = leg.id
            if reason is None and soft and not resolved and unmatched is not None:
                reason, choices = unmatched.diagnostic, ()
            if reason is None:
                journey.update(resolved)
        origin = body.confirmed_origin_id
        landmark = body.landmark_text or slots.landmark_text
        if origin or landmark:
            nodes = {node.id: node for node in graph.nodes if _public(node)}
            if origin in nodes:
                journey["origin_node_id"] = origin
                if reason in (R.LOCATION_UNKNOWN, R.LOCATION_UNCONFIRMED, R.AMBIGUOUS_LANDMARK):
                    reason, choices = None, ()
            elif reason not in (R.AMBIGUOUS_DATE, R.AMBIGUOUS_FLIGHT, R.FLIGHT_UNKNOWN, R.WRONG_AIRPORT):
                candidates = self._aliases(graph).get(landmark.strip().casefold(), ()) if landmark else ()
                choices = tuple(PermittedChoice(id=node_id, label=reviewed_text(
                    nodes[node_id].landmark, I18nText(key="kiosk.floor", params={"floor": nodes[node_id].floor})),
                ) for node_id in sorted(candidates))
                reason = (R.AMBIGUOUS_LANDMARK if len(choices) > 1 else
                          R.LOCATION_UNCONFIRMED if choices else R.LOCATION_UNKNOWN)
        if urgent:
            reason, choices = R.URGENT_SAFETY, ()
        if body.language in ("en", "ar") or body.text:
            journey["language"] = body.language if body.language in ("en", "ar") else slots.language
        needs = body.party_needs or self._needs(journey)
        if body.party_needs is None and (slots.wheelchair or slots.step_free or slots.children):
            needs = needs.model_copy(update={
                "wheelchair": needs.wheelchair or slots.wheelchair, "step_free": needs.step_free or slots.step_free,
                "children": max(needs.children, int(slots.children)),
                "party_size": max(needs.party_size, 2 if slots.children else 1),
            })
        journey["party_needs_json"] = needs.model_dump_json()
        journey["profile"] = profile_for(needs)
        journey["selection"] = Selection(
            aliases=aliases if reason == R.AMBIGUOUS_DATE else (), reason=reason, choices=choices,
            assistance=intent == Intent.REQUEST_ASSISTANCE or (intent == Intent.UNKNOWN and selection.assistance),
        )

    @staticmethod
    def _needs(journey):
        needs = PartyNeeds.model_validate_json(journey["party_needs_json"])
        if needs == PartyNeeds() and journey["profile"] != Profile.STANDARD:
            return PartyNeeds(children=int(journey["profile"] in (Profile.FAMILY, Profile.FAMILY_MOBILITY)),
                              step_free=journey["profile"] in (Profile.REDUCED_MOBILITY, Profile.FAMILY_MOBILITY))
        return needs

    def _tool(self, event_id, airport, as_of, deadline, name, request, background):
        adapter = {FLIGHT_TOOL: self.flights, ARRIVALS_TOOL: self.arrivals, AIRCRAFT_TOOL: self.aircraft}[name]
        with closing(self.open_db()) as conn:
            turn = ToolTurn(
                conn, self.clock, self.policy, event_id=event_id, airport=airport, as_of=as_of,
                recovery=self.recovery if adapter else None, flights=self.flights,
                arrivals=self.arrivals, aircraft=self.aircraft,
            )
            turn.deadline = min(turn.deadline, deadline)
            return turn.call(name, request.model_dump_json(), background=background)

    async def _read(self, event, journey, deadline):
        if (journey["stopped"] or journey["closed_at"] or
                event["event_type"] not in (E.PASSENGER_INPUT, E.REFRESH_DUE)):
            return {}, False
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return {}, True
        background = event["event_type"] == E.REFRESH_DUE
        as_of = self.clock.now()
        provider_deadline = as_of + timedelta(seconds=remaining)
        airport, outbound, inbound = (journey[field] for field in ("airport", "outbound_leg_id", "inbound_leg_id"))
        with closing(self.open_db()) as conn:
            arrival = get_leg(conn, inbound) if inbound else None
            tracked = arrival or (get_leg(conn, outbound) if outbound else None)
            refs = tuple(ref for ref in (inbound, outbound) if ref)
            if background and self.flights:
                refs = tuple(sorted(set(refs) | {ref for row in conn.execute(
                    "SELECT inbound_leg_id, outbound_leg_id FROM journeys WHERE airport=? AND closed_at IS NULL "
                    "AND (next_refresh_at IS NOT NULL OR id=?) AND created_at>?",
                    (airport, journey["id"], timestamp(as_of - SESSION_HORIZON)),
                ) for ref in row if ref}))
        requests = []
        window = int(as_of.timestamp()) // max(1, self.policy.refresh_seconds)
        if self.flights and refs:
            requests.append((FLIGHT_TOOL, GetFlightAndGateStatusInput(refs=refs, airport=airport, as_of=as_of)))
        if arrival:
            requests.append((ARRIVALS_TOOL, GetArrivalEstimatesInput(arrival=arrival, airport=airport,
                                                                    journey_type=journey["flow"])))
        if tracked:
            requests.append((AIRCRAFT_TOOL, GetAircraftPositionInput(leg=tracked)))
        limit = max(0, self.policy.turn_reads - int(bool(outbound and journey["origin_node_id"])))
        spent = len(requests) > limit
        tasks = {}
        for name, request in requests[:limit]:
            adapter = {FLIGHT_TOOL: self.flights, ARRIVALS_TOOL: self.arrivals, AIRCRAFT_TOOL: self.aircraft}[name]
            key = name, id(adapter)
            if adapter and key in self._reads:
                spent = True
                continue
            if background and name == FLIGHT_TOOL:
                with closing(self.open_db()) as conn, transaction(conn):
                    claimed = conn.execute(
                        "INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value "
                        "WHERE meta.value<>excluded.value", (f"flight_refresh_window:{airport}", str(window)),
                    )
                    if not claimed.rowcount:
                        continue
            task = asyncio.create_task(asyncio.to_thread(self._tool, event["id"], airport, as_of, provider_deadline,
                                                         name, request, background))
            tasks[task] = name, request
            if adapter:
                self._reads[key] = task
                task.add_done_callback(lambda done, key=key: self._reads.pop(key, None))
        if not tasks:
            return {}, spent
        done, pending = await asyncio.wait(tasks, timeout=max(0, deadline - asyncio.get_running_loop().time()))
        results = {tasks[task][0]: (tasks[task][1], task.result()) for task in done}
        timed_out = any(isinstance(result, ToolError) and result.diagnostic == R.TIMEOUT
                        for _, result in results.values())
        return results, spent or bool(pending) or timed_out

    def _accept_flights(self, conn, pair, event, journey, now, observed):
        if pair is None or not isinstance(pair[1], Success):
            return
        request, result = pair
        legs = result.value.legs
        if (set(request.refs) != {leg.id for leg in legs}
                or any(matched_leg(conn, leg) is None for leg in legs)):
            return
        changed = {row[0] for row in conn.execute(
            "SELECT DISTINCT leg_id FROM observations WHERE id>?", (observed,),
        )}
        for fact in result.value.facts:
            if fact.leg_id not in request.refs or fact.leg_id in changed:
                continue
            for observation in ((fact.observation,) if fact.observation else fact.alternatives):
                if (valid_observation(observation) and observation.observed_at <= request.as_of
                        and observation.received_at <= self.clock.now()):
                    self._ingest(conn, observation, event["id"], now, skip=journey["id"])

    def _facts(self, conn, journey, as_of):
        leg = journey["outbound_leg_id"]
        facts = {field: current_field(conn, leg, field, as_of, self.policy) for field in
                 (*GATE_FIELDS, F.BOARDING_CUTOFF, F.STATUS)} if leg else {}
        gate = facts.get(F.GATE)
        values, sources = {}, {}
        for field, name in GATE_FIELDS.items():
            fact = facts.get(field)
            if (fact and fact.observation and fact.observation.value is not None and valid_observation(fact.observation)
                    and (fact.state == FRESH or field == F.GATE and fact.state == ObservationState.STALE)):
                values[name] = fact.observation.value
                sources[name] = SourceStamp(source=fact.observation.source, observed_at=fact.observation.observed_at)
        gate_state = gate.state if gate and ("value" in values or gate.state == ObservationState.CONFLICT) else (
            ObservationState.UNKNOWN)
        cutoff = facts.get(F.BOARDING_CUTOFF)
        item = cutoff.observation if cutoff and cutoff.state == FRESH else None
        cutoff_view = CutoffView(value=item.value, source=item.source, observed_at=item.observed_at,
                                 unavailable=False, estimated=item.cutoff_estimated) if item and item.value else CutoffView()
        return GateView(state=gate_state, provenance=sources, **values), cutoff_view, facts

    def passenger_view(self, conn, journey, view):
        if view.route is None:
            return view
        now = self.clock.now()
        gate, cutoff, _ = self._facts(conn, journey, now)
        latest = conn.execute(
            "SELECT action FROM audit WHERE journey_id=? AND action IN ('route_invalidated', 'journey_published') "
            "ORDER BY id DESC LIMIT 1", (journey['id'],),
        ).fetchone()
        fields = ('value', 'terminal', 'concourse', 'level')
        if (gate.state == FRESH and all(getattr(gate, field) == getattr(view.gate, field) for field in fields)
                and (not latest or latest[0] != 'route_invalidated')):
            return view
        safe = build_view(revision=view.revision, as_of=now, policy=self.policy, language=view.language,
                          gate=GateView() if gate.state == FRESH else gate, cutoff=cutoff,
                          reasons=(R.ROUTE_REVOKED,) if gate.state == FRESH else (),
                          banners=(BannerKind.ROUTE_CHANGED,))
        return safe.model_copy(update={'assistance': view.assistance, 'arrivals': view.arrivals,
                                       'aircraft': view.aircraft, 'requests': view.requests, 'notice': view.notice})

    def _commit(self, event, initial, watermark, observed, body, slots, reads, spent):
        with closing(self.open_db()) as conn, transaction(conn):
            as_of = self.clock.now()
            now = timestamp(as_of)
            self._apply_updates(conn, now)
            journey_id = initial["id"]
            if conn.execute("SELECT processed_at FROM events WHERE id=?", (event["id"],)).fetchone()[0]:
                return
            journey = self._journey(conn, journey_id)
            if journey["closed_at"] or journey["created_at"] <= timestamp(as_of - SESSION_HORIZON):
                _finish(conn, event["id"], now, reason_code=R.SESSION_EXPIRED)
                return
            if body and body.known_revision not in (None, initial["revision"]):
                _finish(conn, event["id"], now, reason_code=R.STALE_REVISION)
                return
            newer = conn.execute("SELECT 1 FROM events WHERE rowid>? AND journey_id=? LIMIT 1",
                                 (watermark, journey_id)).fetchone()
            if (body and body.known_revision is not None and body.known_revision != journey["revision"]):
                _finish(conn, event["id"], now, reason_code=R.STALE_REVISION)
                return
            superseded = newer or journey["revision"] != initial["revision"]
            if superseded:
                spent = spent or bool(reads)
                reads = {}
            if journey["stopped"]:
                reads = {}
            self._accept_flights(conn, reads.get(FLIGHT_TOOL), event, journey, now, observed)
            graph = load_graph(conn, journey["airport"])
            previous_legs = journey["inbound_leg_id"], journey["outbound_leg_id"]
            self._resolve(conn, journey, body, slots, graph, as_of)
            conn.execute("UPDATE journeys SET inbound_leg_id=:inbound_leg_id, outbound_leg_id=:outbound_leg_id, "
                         "origin_node_id=:origin_node_id, profile=:profile, party_needs_json=:party_needs_json, "
                         "language=:language WHERE id=:id", journey)
            selection = journey["selection"]
            conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"selection:{journey_id}", selection.model_dump_json()))
            guard = None
            if body is not None and (body.text or body.cancel_case_ref):
                guard = booking.screen(conn, journey, body, self.clock, now, event["id"])
            self._publish(conn, event, journey, graph, reads, spent, previous_legs, as_of, guard)

    def _publish(self, conn, event, journey, graph, reads, spent, previous_legs, as_of, guard=None):
        now, journey_id = timestamp(as_of), journey["id"]
        previous = assist.latest_revision(conn, journey_id)
        old = ViewModel.model_validate_json(previous["view_json"]) if previous else None
        selection = journey["selection"]
        gate, cutoff, facts = self._facts(conn, journey, as_of)
        gate_changed = old and any(getattr(old.gate, attr) != getattr(gate, attr) for attr in (*GATE_FIELDS.values(), "state"))
        case_rows = conn.execute("SELECT * FROM cases WHERE journey_id=? ORDER BY created_at DESC, rowid DESC",
                                 (journey_id,)).fetchall()
        all_cases = [assist.case_from_row(conn, row) for row in case_rows]
        case = next((c for c in all_cases if c.service in MOBILITY and c.state in assist.OPEN),
                    all_cases[0] if all_cases else None)
        moved = bool(old and gate.state == FRESH and gate.value and old.gate.value and old.gate.value != gate.value)
        retargets = [c for c in all_cases if moved and c.service in MOBILITY and c.state in assist.OPEN]
        for row in case_rows:
            if row["state"] != CaseState.PENDING_APPROVAL:
                continue
            pending = assist.case_from_row(conn, row)
            if moved and pending.service in MOBILITY:
                continue  # HS-4.1: the same request is pointed at the new gate below, not cancelled and asked for again
            reason = assist._scope_reason(conn, pending, now)
            if gate_changed and pending.target_leg_id:
                reason = reason or R.CASE_SCOPE_CHANGED
            if reason:
                _revoke(conn, journey_id, event["id"], now)
                updated = assist._transition(conn, pending, CaseState.CANCELLED, event["id"], self.clock, reason)
                assist.audit(conn, event["id"], now, "case_reassessed", updated.state, case=updated, reason=reason)
                if case.id == pending.id:
                    case = updated
        revision = assist.journey_row(conn, journey_id)["revision"] + 1
        pending = case is not None and case.service != AssistanceService.GATE_HOLD and case.state in HANDOFF_PENDING
        reasons = [selection.reason] if selection.reason else []
        choices = selection.choices
        if not journey["outbound_leg_id"] and not journey["inbound_leg_id"] and not reasons:
            reasons.append(R.FLIGHT_UNKNOWN)
        elif journey["inbound_leg_id"] and not journey["outbound_leg_id"] and not reasons:
            reasons.append(R.OUTBOUND_MISSING)  # we know where they landed; ask which flight they are catching
        if selection.assistance and not pending and (case is None or event["event_type"] == E.PASSENGER_INPUT):
            pickup = assist.pickup_location(conn, journey["airport"], journey["origin_node_id"])
            if pickup is not None:
                choices = (PermittedChoice(id=pickup.node_id, label=reviewed_text(
                    pickup.landmark, I18nText(key="action.confirm_pickup"))),)
            reasons.append(R.PICKUP_INELIGIBLE if journey["origin_node_id"] and pickup is None else R.PICKUP_UNCONFIRMED)
        cutoff_fact = facts.get(F.BOARDING_CUTOFF)
        if cutoff_fact and cutoff_fact.state in (ObservationState.STALE, ObservationState.CONFLICT):
            reasons.append(R.CUTOFF_STALE if cutoff_fact.state == ObservationState.STALE else R.CUTOFF_CONFLICT)
        status = facts.get(F.STATUS)
        cancelled = bool(status and status.state == FRESH and isinstance(status.observation.value, str)
                         and status.observation.value.casefold() in ("cancelled", "canceled"))
        if cancelled:
            reasons.append(R.FLIGHT_CANCELLED)
        if spent:
            reasons.append(R.BUDGET_EXHAUSTED)
        origin = case.pickup.node_id if pending and case.pickup else (
            journey["origin_node_id"])
        if not origin and journey["outbound_leg_id"] and gate.state == FRESH and not selection.reason:
            reasons.append(R.LOCATION_UNCONFIRMED)
        nodes = self._aliases(graph)
        targets = [node for node in graph.nodes if gate.value and node.id in nodes.get(gate.value.casefold(), ())
                   and _public(node) and all(value is None or node_value == value for node_value, value in (
                       (node.terminal, gate.terminal), (node.concourse, gate.concourse), (node.floor, gate.level),
                   ))]
        readiness = conn.execute("SELECT value FROM meta WHERE key=?", (f"readiness:{journey['inbound_leg_id']}",)).fetchone()
        readiness = ReadinessEvent.model_validate_json(readiness[0]) if readiness else None
        ready = (readiness.ready if readiness and readiness.observed_at <= as_of else
                 SecondsRange(lower=0, upper=0) if origin and not journey["inbound_leg_id"] else None)
        result = None
        if journey["outbound_leg_id"] and not cancelled:
            target = targets[0].id if len(targets) == 1 and origin and not selection.reason and self.policy.turn_reads else None
            result = evaluate_transfer(graph, TransferFacts(
                origin_id=origin or "unconfirmed", gate_id=target, gate_state=gate.state,
                cutoff=cutoff.value, cutoff_verified=not cutoff.unavailable and not cutoff.estimated,
                cutoff_estimated=cutoff.estimated, ready=ready, party_needs=self._needs(journey), flow=journey["flow"],
                handoff_pending=pending, as_of=as_of, airport=journey["airport"], gate_label=gate.value,
            ), self.policy)
            if gate.state == FRESH and target is None:
                result = result.model_copy(update={"reason_codes": tuple(
                    reason for reason in result.reason_codes if reason != R.GATE_UNKNOWN
                )})
        basis = booking.minor_basis(conn, journey_id)
        if basis and result is not None:
            # HS-4.3: no self-navigation for an unaccompanied minor, whatever the map could compute
            result = result.model_copy(update={"route": None})
            reasons.append(R.UNACCOMPANIED_MINOR)
        if journey["outbound_leg_id"] and not cancelled and result is not None:
            pass
            if target is None and gate.state == FRESH and not selection.reason and origin and self.policy.turn_reads:
                reasons.append(R.MAP_UNAVAILABLE)
        changed = old and old.route and (gate_changed or not result or not result.route or
                                        old.route.route_id != result.route.route_id)
        if changed and result and result.route and not pending:
            reasons.append(R.ROUTE_REVOKED)
        token_row = conn.execute("SELECT * FROM journey_revisions WHERE route_token_hash=?",
                                 (sha256(old.qr.token.encode()).hexdigest(),)).fetchone() if old and old.qr.token else None
        expired = token_row is not None and token_row["route_expires_at"] <= now
        banners = ((BannerKind.ROUTE_CHANGED,) if changed else (BannerKind.ROUTE_EXPIRED,) if expired else ())
        completed = case and case.service != AssistanceService.GATE_HOLD and case.state == CaseState.COMPLETED
        if completed:
            conn.execute("INSERT OR IGNORE INTO meta VALUES (?, '1')", (f"monitoring_stopped:{journey_id}",))
        stopped = journey["stopped"] or bool(completed)
        keep_token = token_row and not (changed or expired or token_row["route_revoked_at"] or stopped)
        token = old.qr.token if keep_token else secrets.token_urlsafe(24)
        same_legs = previous_legs == (journey["inbound_leg_id"], journey["outbound_leg_id"])
        arrivals, aircraft = self._telemetry(reads, journey, old if same_legs else None, as_of)
        view = build_view(
            revision=revision, as_of=as_of, policy=self.policy, language=journey["language"], gate=gate, cutoff=cutoff,
            result=result, case=case, reasons=tuple(reasons), choices=choices, banners=banners,
            route_token=None if stopped else token, arrivals=arrivals, aircraft=aircraft,
        )
        if old and same_legs and aircraft is None and old.aircraft.freshness == AircraftFreshness.HIDDEN:
            view = view.model_copy(update={"aircraft": old.aircraft})
        view = view.model_copy(update={
            "requests": assist.request_views(conn, journey_id, gate.value),
            "notice": (Notice(kind=guard[0], text=guard[1], seq=revision) if guard else (old.notice if old else None)),
        })
        token_hash = route_expires = None
        if view.qr.token and keep_token:
            view = view.model_copy(update={"route": view.route.model_copy(update={"revision": old.route.revision})})
        else:
            _revoke(conn, journey_id, event["id"], now)
            if view.qr.token:
                token_hash = sha256(token.encode()).hexdigest()
                observation = facts[F.GATE].observation
                freshness = timedelta(seconds=self.policy.stale_seconds)
                expiries = [observation.valid_until, observation.received_at + freshness,
                            as_of + timedelta(minutes=2)]
                route_nodes = set(result.route.node_ids)
                edges = {edge.id for edge in graph.edges if {edge.from_node_id, edge.to_node_id} <= route_nodes}
                expiries.extend(min(item.valid_until, item.observed_at + freshness) for item in graph.overlays
                                if item.edge_id in edges and item.observed_at <= as_of <
                                min(item.valid_until, item.observed_at + freshness))
                route_expires = timestamp(min(expiries))
                if route_expires <= now:
                    token_hash = route_expires = None
                    view = view.model_copy(update={"qr": QRView(suppressed_reason=R.ROUTE_EXPIRED)})
        refresh = None
        if not stopped and (spent or journey["outbound_leg_id"] or journey["inbound_leg_id"]) and self.policy.refresh_seconds:
            period = self.policy.refresh_seconds
            delay = period if event["event_type"] != E.REFRESH_DUE else period - int(as_of.timestamp()) % period
            refresh = timestamp(min(as_of + timedelta(seconds=delay),
                                    datetime.fromisoformat(journey["created_at"]) + SESSION_HORIZON))
        conn.execute("UPDATE journeys SET revision=?, next_refresh_at=? WHERE id=?", (revision, refresh, journey_id))
        conn.execute(
            "INSERT INTO journey_revisions(journey_id, revision, created_at, event_id, policy_version, graph_version, "
            "view_json, route_token_hash, route_expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (journey_id, revision, now, event["id"], self.policy.policy_version, graph.graph_version,
             view.model_dump_json(), token_hash, route_expires),
        )
        _finish(conn, event["id"], now, revision=revision, budget_exhausted=spent)
        if event["event_type"] == E.REFRESH_DUE:
            conn.execute("UPDATE events SET processed_at=?, outcome_json=? WHERE journey_id=? AND event_type=? "
                         "AND processed_at IS NULL AND due_at<=?",
                         (now, json.dumps({"revision": revision, "coalesced": True}), journey_id, E.REFRESH_DUE, now))
        assist.audit(conn, event["id"], now, "journey_published", str(revision), journey_id=journey_id)
        if retargets:
            for moved_case in retargets:
                assist.retarget(conn, moved_case, old_gate=old.gate.value, new_gate=gate.value, now=now)
            outcome = replan.after_gate_change(conn, self.clock, journey, old.gate.value, gate.value, view, retargets)
            if outcome:
                assist.republish_notice(conn, journey_id, self.clock, *outcome)
        if basis:
            replan.ensure_escort(conn, self.clock, journey, KIOSK_NODE, basis)

    def _telemetry(self, reads, journey, old, as_of):
        arrivals, report = (old.arrivals if old else ArrivalEstimates()), None
        tracked = journey["inbound_leg_id"] or journey["outbound_leg_id"]
        if old and old.aircraft.position and tracked:
            report = AircraftReport(matched_leg_id=tracked, position=old.aircraft.position,
                                    source=old.aircraft.source, observed_at=old.aircraft.observed_at)
        if pair := reads.get(ARRIVALS_TOOL):
            request, result = pair
            if isinstance(result, Success) and request.arrival.id == journey["inbound_leg_id"]:
                arrivals = ArrivalEstimates(**{name: value for name, value in result.value
                                               if value is None or value.provenance is None or
                                               value.provenance.observed_at <= as_of})
        if pair := reads.get(AIRCRAFT_TOOL):
            request, result = pair
            if isinstance(result, Success) and request.leg.id == tracked:
                observation = result.value.report
                if observation is None:
                    report = None
                if observation and observation.matched_leg_id == request.leg.id and (
                    observation.provider_leg_id == request.leg.provider_leg_id
                ) and observation.observed_at <= as_of and (not report or report.observed_at <= observation.observed_at):
                    report = observation
        arrivals = ArrivalEstimates(**{
            name: value.model_copy(update={"state": ObservationState.STALE, "wait": None})
            if value and value.state == FRESH and value.provenance and
            (as_of - value.provenance.observed_at).total_seconds() >= self.policy.stale_seconds else value
            for name, value in arrivals
        })
        return arrivals, report

    async def run_once(self) -> bool:
        async with self._lock:
            deadline = asyncio.get_running_loop().time() + self.policy.turn_wall_seconds
            applied, prepared = self._prepare()
            if prepared is None:
                return applied
            event, journey, watermark, observed = prepared
            body = (JourneyInputRequest.model_validate_json(event["payload_json"])
                    if event["event_type"] == E.PASSENGER_INPUT else None)
            slots, spent = IntentSlots(intent=Intent.UNKNOWN), False
            unsafe = body and body.text and (INSTRUCTION.search(body.text) or URGENT.search(body.text)
                                             or booking.classify(body.text) in ("approval", "cancel"))
            if body and body.text and body.intent == Intent.UNKNOWN and not unsafe:
                with closing(self.open_db()) as conn:
                    aliases = self._aliases(load_graph(conn, journey["airport"]))
                remaining = max(0, deadline - asyncio.get_running_loop().time())
                try:
                    async with asyncio.timeout(remaining):
                        slots = await self.extractor.extract(body.text, aliases=aliases, timeout=remaining,
                                                             retries=self.policy.model_retries)
                except (TimeoutError, ValidationError):
                    spent = True
                    slots = await TemplateExtractor().extract(body.text, aliases=aliases)
            projected = journey.copy()
            with closing(self.open_db()) as conn:
                if body:
                    self._resolve(conn, projected, body, slots, load_graph(conn, journey["airport"]), self.clock.now())
            reads, budget = ({}, False) if unsafe else await self._read(event, projected, deadline)
            self._commit(event, journey, watermark, observed, body, slots, reads, spent or budget)
            return True
