# SkyAssist foundation contracts

## Modules and persistence

| Module | Imports and use |
| --- | --- |
| `skyassist.models` | Frozen, extra-forbidden Pydantic v2 models. Supply aware datetimes or ISO 8601 strings with offsets. Values normalize to UTC. Durations are non-negative integer seconds. `time_remaining` and `margin` are signed seconds. |
| `skyassist.clock` | Inject `Clock.now()`. Use `SystemClock()` for real time, `SimClock(start, starts=...)` for replay, `in_dubai(instant)` for DXB/AUH display and `as_utc(instant)` for normalization. `advance(seconds)` moves forward, `set(name_or_instant)` repositions. Built-ins `demo` and `C01` start at 10:00 Dubai on 2026-11-19. `C09` starts at 23:50 that day. Supply additional replay instants in `starts`. |
| `skyassist.config` | `Mode`, `Config`, `PROFILES`, `CONFIG`, `DATA_DIR`. `SKYASSIST_MODE` selects once at import, default `demo`. Stores are `data/skyassist-demo.sqlite3` and `data/skyassist-live.sqlite3`. |
| `skyassist.db` | Call `init_db(path=None, dataset_version="unseeded")` before serving. `connect(path=None)` returns a caller-owned connection with row objects, FK checks, WAL and a 5000 ms busy timeout. Use `with transaction(conn):` for short writes. Use `timestamp(instant)` for indexed times so SQLite sorts UTC instants chronologically, including fractions. |

- `meta` stores `mode` and `dataset_version`. Re-init preserves both. `connect` and `init_db` refuse an opposite mode stamp.
- `flight_legs` have a unique operating identity and optional unique provider ID. `flight_aliases` allow one alias on multiple dated legs. `observations` hold per-field provenance, validity, withdrawal, cutoff estimate flag and separate schedule fields.
- `nodes`, directed `edges`, `overlays`, `queue_observations` and `amenity_observations` hold map and access facts. JSON columns (`*_json`) carry the corresponding model data, with edge endpoints and queryable facts in columns.
- `journeys` index active inbound/outbound legs and the next refresh. `journey_revisions` store `ViewModel` JSON, policy/map versions, and optional token hash, expiry and revocation for route handoff.
- `events.id` is global. `(journey_id, client_input_id)` deduplicates passenger input. `staff_decisions.event_id` references that same event identity and stores the committed outcome for replay.
- `cases` deduplicate an open journey, service, pickup and target-leg tuple, including null pickup/target values. Open states run from draft through dispatched, and include dispatch pending verification. Cases and outbox rows each have unique idempotency keys. Namespace outbox keys by effect. `outbox.claim_expires_at`, `attempts`, and `in_flight_at` support claims and reconciliation.
- `audit` is append-only and can have multiple sanitized records per event. Persist decisions, events, new revisions and outbox work together where required by the plan.

## Enumerations

Values are shown in wire form. Status enum names follow the upper snake case form of the label.

| Enum | Values |
| --- | --- |
| `ConnectionStatus` | Time available, Tight connection, Transfer unlikely before cutoff, May miss boarding, Boarding cutoff passed, Timing unverified |
| `ConnectionFlag` | Boarding cutoff unavailable |
| `Profile` | `standard, family, reduced_mobility, family_mobility` |
| `CaseState` | `draft, pending_approval, approved, dispatch_pending_verification, dispatched, completed, declined, cancelled, expired` |
| `ObservationState` | `fresh, stale, conflict, unknown` |
| `ToolErrorClass` | `ambiguous, missing, stale, conflict, rate_limited, timeout, unavailable` |
| `Intent` | `check_connection, find_gate, find_amenity, request_assistance, arrival_estimates, unknown` |
| `EventType` | `passenger_input, flight_updated, gate_changed, facility_changed, refresh_due, coordinator_decided, dispatch_status` |
| `Role` | `passenger, coordinator, integration` |
| `ObservationField` | `gate, terminal, concourse, level, status, departure, arrival, boarding_cutoff, carousel` |
| `PassengerFlow` | `connecting, exiting, departing` |
| `AccessibleMode` | `walk, step_free, stairs, lift, transit, checkpoint, staff_handoff` |
| `AssistanceService` | `wheelchair, mobility_escort, gate_hold` |
| `DecisionKind` | `approve, decline` |
| `DispatchState` | `unknown, dispatched, completed` |
| `OutboxStatus` | `pending, claimed, sent, unknown, done` |
| `OutboundEffect` | `coordinator_notification, mobility_dispatch, gate_hold_review` |
| `FlightDirection` | `arrivals, departures` |
| `CallbackOutcome` | `applied, replayed, rejected` |
| `ReadinessState` | `parked, doors_open, ready_to_move, staff_handoff_pending, mobility_dispatched` |
| `PassengerAction` | `choose_flight, confirm_date, choose_landmark, confirm_pickup, wait_for_handoff, follow_route, seek_staff, refresh_route, check_gate, seek_emergency_help, wait_for_update` |
| `BannerKind` | `route_changed, route_expired, stale_data` |
| `Applicability` | `applicable, not_applicable, unknown` |
| `AircraftFreshness` | `current, last_reported, hidden, unavailable` |
| `Mode` (in config) | `demo, live` |

`ReasonCode` is closed. Its uppercase wire values map directly to `reason.<code>`.

- **Flight and gate** `FLIGHT_UNKNOWN, FLIGHT_CANCELLED, AMBIGUOUS_FLIGHT, AMBIGUOUS_DATE, GATE_STALE, GATE_CONFLICT, GATE_UNKNOWN, GATE_CHANGED`.
- **Timing** `CUTOFF_MISSING, CUTOFF_STALE, CUTOFF_CONFLICT, CUTOFF_ESTIMATED, CUTOFF_PASSED, TIGHT_MARGIN, NEGATIVE_MARGIN, LOWER_BOUND_EXCEEDS_CUTOFF, QUEUE_BOUND_STALE, QUEUE_BOUND_MISSING, QUEUE_BOUND_CONFLICT, READINESS_UNKNOWN, STAFF_HANDOFF_PENDING`.
- **Wayfinding** `AMBIGUOUS_LANDMARK, LOCATION_UNKNOWN, LOCATION_UNCONFIRMED, MAP_UNAVAILABLE, FACILITY_CLOSED, FACILITY_STALE, LIFT_CLOSED, LIFT_UNVERIFIED, EDGE_RESTRICTED, CHECKPOINT_INELIGIBLE, ACCESS_UNVERIFIED, NO_PATH, NO_ACCESSIBLE_PATH, ROUTE_REVOKED, ROUTE_EXPIRED, ROUTE_UNAVAILABLE, WRONG_AIRPORT, AMENITY_CLOSED`.
- **Assistance and state** `AMBIGUOUS_PICKUP, PICKUP_UNCONFIRMED, PICKUP_INELIGIBLE, CONSENT_MISSING, APPROVAL_REQUIRED, APPROVAL_EXPIRED, APPROVAL_DECLINED, CASE_CANCELLED, CASE_NOT_PENDING, CASE_SCOPE_CHANGED, DISPATCH_UNCONFIRMED, DISPATCH_OUTCOME_UNKNOWN, WRONG_ROLE, STALE_REVISION, DUPLICATE_EVENT, ALREADY_DECIDED, SESSION_EXPIRED, URGENT_SAFETY`.
- **Telemetry and input** `AIRCRAFT_UNMATCHED, POSITION_UNAVAILABLE, POSITION_STALE, ESTIMATE_UNAVAILABLE, ESTIMATE_STALE, NOT_APPLICABLE, DATA_STALE, UNSUPPORTED_INPUT, INVALID_ARGUMENTS, UNAUTHORIZED_TOOL, RATE_LIMITED, TIMEOUT, SOURCE_UNAVAILABLE, BUDGET_EXHAUSTED`.

## Model imports and fixture conventions

- **Domain** `FlightLeg, Observation, FieldEvidence, SourceStamp, ExpiringSourceStamp, Node, Edge, Overlay, QueueObservation, AmenityObservation, MapPoint, FloorShape, TimeWindow, SecondsRange, PolicyPack, SpeedBand, RouteStep, RouteResult, TransferResult, PartyNeeds, Location, Journey, JourneyRevision, Case, Decision, OutboxItem, AuditRecord`.
- **View** `ViewModel, GateView, CutoffView, StatusView, TransferView, RouteView, AssistanceView, NextActionView, BannerView, QRView, ArrivalEstimate, ArrivalEstimates, AircraftPosition, AircraftReport, AircraftView, I18nText, PermittedChoice`.
- **Fixture schemas** `FixtureGraph, FlightLegFixture, EventStreamItem, LandmarkAliasFixture, ScenarioFixture, AirportMeta, PolicyPack`. Run `uv run python -m skyassist.models export-schema`. The seven `snake_case.schema.json` files describe individual records. `export_schemas(directory=None)` supports a custom output directory.
- `FlightLegFixture.codeshare_aliases` adds marketing aliases to the leg's ordinary aliases. `scheduled_operation_date` is the scheduled departure's local date at origin. Keep that date even when `scheduled_departure` normalizes to the previous UTC date. Keep the cutoff separate from departure times.
- `ScenarioFixture.event_sequence` lists IDs of `EventStreamItem` records in replay order. `flight_updated` takes an `Observation` or `ReadinessEvent`. `gate_changed` takes a gate observation. `facility_changed` takes an overlay, queue observation, or amenity observation. Passenger inputs, decisions and dispatch status carry their API payloads, and refresh takes null. Preserve original source clocks.
- `Edge.fixed_movement` (SecondsRange, optional) replaces distance/speed movement for that edge (see docs/ROUTING-SPEC.md).
- `SecondsRange.upper=null` represents a missing validated upper bound. `RouteResult.node_ids` records internal traversal order. `RouteView.steps` and `floor_shapes` carry public directions. Use explicit UTC intervals in `Edge.open_windows`. An empty list leaves the edge unrestricted by hours.
- `PolicyPack` default buffer floors are 180, 300, 420 and 420 seconds by profile. Refresh/stale defaults are 20/90 seconds. Aircraft thresholds are 60/180 seconds. Turn defaults are 4 reads, 1 model retry and 8 seconds. Default speed bands are synthetic demo calibration.

## HTTP and tools

API models are in `skyassist.models`. Bind path IDs and the authenticated scope at the service boundary. An unchanged journey response sets `unchanged=true` and omits the view. Check route token expiry and revocation against the current route revision on each public read. Build that response from approved public wayfinding steps and omit private pickup facts and staff handoff instructions.

| Interface | Request | Response |
| --- | --- | --- |
| GET /flights | `GetFlightsRequest` | `GetFlightsResponse` with `FlightBoardItem` records |
| POST /journeys | `CreateJourneyRequest` | `CreateJourneyResponse` |
| POST /journeys/{id}/inputs | `JourneyInputRequest` | `JourneyInputResponse` |
| GET /journeys/{id} | `GetJourneyRequest` | `GetJourneyResponse` |
| POST /journeys/{id}/assistance | `AssistanceRequest` | `AssistanceResponse` |
| GET /staff/cases | `GetStaffCasesRequest` | `GetStaffCasesResponse` with `StaffCaseItem` records |
| POST /staff/cases/{id}/decisions | `DecisionRequest` | `DecisionResponse` |
| Dispatch status callback | `DispatchStatusRequest` | `DispatchStatusResponse` |
| GET /route/{token} | `GetRouteRequest` | `GetRouteResponse` with public route or reason |

Tools return `ToolResult[Output]`, the `kind`-discriminated `Success[T]` or `ToolError` union. Success carries `value`. Errors carry `class` (`error_class` in Python), a `diagnostic` reason, permitted choices and optional retry-after seconds. Bind the approved case context internally for `call_assistance(Location)`. An accepted hook result records handoff acceptance. Dispatch confirmation comes from the separate status callback.

| Tool | Input | Output |
| --- | --- | --- |
| `get_flight_and_gate_status` | `GetFlightAndGateStatusInput` | `GetFlightAndGateStatusOutput` |
| `calculate_terminal_transfer_time` | `CalculateTerminalTransferTimeInput` | `CalculateTerminalTransferTimeOutput` (`RouteResult`) |
| `request_mobility_dispatch_async` | `RequestMobilityDispatchAsyncInput` | `RequestMobilityDispatchAsyncOutput` (`AssistanceResponse`) |
| `call_assistance` | `CallAssistanceInput` | `CallAssistanceOutput` |
| `receive_assistance_decision` | `ReceiveAssistanceDecisionInput` | `ReceiveAssistanceDecisionOutput` (`DecisionResponse`) |
| `get_arrival_estimates` | `GetArrivalEstimatesInput` | `GetArrivalEstimatesOutput` (`ArrivalEstimates`) |
| `get_aircraft_position` | `GetAircraftPositionInput` | `GetAircraftPositionOutput` |

## Kiosk ViewModel

- `revision, as_of, language, i18n` identify the journey snapshot and localization data. Render `I18nText` as a reviewed catalog key with `params`. Use `ConnectionStatus.i18n_key` (`status.<lower_snake_enum_name>`), `ConnectionFlag.i18n_key` (`flag.cutoff_unavailable`) and `ReasonCode.i18n_key`. Gate codes, floor IDs and references are display facts.
- `gate` has value, terminal, concourse, level, state and per-field provenance. `cutoff` has UTC value or null, source, observed_at, unavailable and estimated. `status` has status, reason_codes and provenance.
- `transfer` has lower_min, upper_min, includes_readiness, margin_min, conditional and provenance, or is null. `TransferView.from_result` rounds lower down and upper up, floors the signed margin, and marks pending handoff conditional.
- `route` has route_id, revision, numbered steps, floor_shapes and provenance, or is null. Steps carry floor, landmark, accessible_mode and text. `assistance` has state, pickup_landmark, case_ref, stage, staffed_alternative, approval_window and provenance, or is null.
- `next_action` has action, text and choices. `banners` carry kind and text. `qr` has token or suppressed_reason. Pass derived fact provenance using `{source, observed_at}`.
- `arrivals` has independent nullable baggage, immigration and security estimates, each with applicability, state, wait and provenance. `aircraft` has nullable position, source and observed_at plus freshness. `AircraftView.from_report` marks reports older than 60 seconds last reported and removes the position at 180 seconds under the demo policy.

C01 at 10:00 DXB time, 21-29 min total including readiness, 6 min conservative margin, Tight connection and a pending request.

```json
{
  "revision": 2, "as_of": "2026-11-19T06:00:00Z", "language": "en",
  "gate": {"value": "B14", "terminal": "1", "concourse": "B", "level": "2", "state": "fresh", "provenance": {
    "value": {"source": "demo", "observed_at": "2026-11-19T06:00:00Z"},
    "terminal": {"source": "demo", "observed_at": "2026-11-19T06:00:00Z"},
    "concourse": {"source": "demo", "observed_at": "2026-11-19T06:00:00Z"},
    "level": {"source": "demo", "observed_at": "2026-11-19T06:00:00Z"}
  }},
  "cutoff": {"value": "2026-11-19T06:35:00Z", "source": "demo", "observed_at": "2026-11-19T06:00:00Z", "unavailable": false, "estimated": false},
  "status": {"status": "Tight connection", "reason_codes": ["TIGHT_MARGIN", "STAFF_HANDOFF_PENDING"], "provenance": {"source": "demo-policy", "observed_at": "2026-11-19T06:00:00Z"}},
  "transfer": {"lower_min": 21, "upper_min": 29, "includes_readiness": true, "margin_min": 6, "conditional": true, "provenance": {"source": "demo-policy", "observed_at": "2026-11-19T06:00:00Z"}},
  "route": {"route_id": "c01-b14", "revision": 2, "steps": [
    {"number": 1, "floor": "2", "landmark": {"key": "landmark.c22"}, "accessible_mode": "staff_handoff", "text": {"key": "route.wait_at_pickup"}},
    {"number": 2, "floor": "2", "landmark": {"key": "landmark.lift_c"}, "accessible_mode": "lift", "text": {"key": "route.take_lift"}},
    {"number": 3, "floor": "1", "landmark": {"key": "landmark.train_c"}, "accessible_mode": "transit", "text": {"key": "route.board_train"}},
    {"number": 4, "floor": "1", "landmark": {"key": "landmark.train_b"}, "accessible_mode": "transit", "text": {"key": "route.exit_train"}},
    {"number": 5, "floor": "2", "landmark": {"key": "landmark.b14"}, "accessible_mode": "step_free", "text": {"key": "route.approach_gate", "params": {"gate": "B14"}}}
  ], "floor_shapes": [{"terminal": "1", "concourse": "C", "floor": "2", "points": [{"x": 0, "y": 0}, {"x": 10, "y": 5}]}],
  "provenance": {"source": "demo-graph", "observed_at": "2026-11-19T06:00:00Z"}},
  "assistance": {"state": "pending_approval", "pickup_landmark": {"key": "landmark.c22"}, "case_ref": "demo-c01",
    "stage": {"key": "assistance.pending_approval"}, "staffed_alternative": {"key": "action.seek_staff"},
    "approval_window": {"start": "2026-11-19T06:00:00Z", "end": "2026-11-19T06:05:00Z"},
    "provenance": {"source": "demo-case", "observed_at": "2026-11-19T06:00:00Z"}},
  "next_action": {"action": "wait_for_handoff", "text": {"key": "action.wait_for_handoff"}},
  "banners": [{"kind": "route_changed", "text": {"key": "banner.route_changed"}}],
  "qr": {"token": null, "suppressed_reason": "STAFF_HANDOFF_PENDING"},
  "arrivals": {"baggage": null, "immigration": null, "security": null},
  "aircraft": {"position": null, "observed_at": null, "freshness": "unavailable"},
  "i18n": {"transfer": {"key": "transfer.total_with_readiness", "params": {"lower": 21, "upper": 29}}}
}
```
