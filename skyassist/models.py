from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field,
    JsonValue, StrictBool, StrictFloat, StrictInt, TypeAdapter, ValidationInfo,
    field_validator, model_validator,
)

from skyassist.clock import as_utc


def _datetime_input(value: object) -> datetime:
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise ValueError("expected an ISO 8601 datetime or datetime object")
    return value


def _require_true(value: object) -> Literal[True]:
    if value is not True:
        raise ValueError("expected true")
    return value


type UTCInstant = Annotated[
    AwareDatetime, BeforeValidator(_datetime_input), AfterValidator(as_utc)
]
type Seconds = Annotated[int, Field(strict=True, ge=0)]
type NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
type Id = Annotated[str, Field(min_length=1, max_length=128)]
type AirportCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
type PageSize = Annotated[int, Field(strict=True, ge=1, le=100)]
type TranslationKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*(\.[A-Za-z0-9_]+)+$")]
type Confirmed = Annotated[Literal[True], BeforeValidator(_require_true)]


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True, allow_inf_nan=False)


class ConnectionStatus(StrEnum):
    TIME_AVAILABLE = "Time available"
    TIGHT_CONNECTION = "Tight connection"
    TRANSFER_UNLIKELY_BEFORE_CUTOFF = "Transfer unlikely before cutoff"
    MAY_MISS_BOARDING = "May miss boarding"
    BOARDING_CUTOFF_PASSED = "Boarding cutoff passed"
    TIMING_UNVERIFIED = "Timing unverified"

    @property
    def i18n_key(self) -> str:
        return f"status.{self.name.lower()}"


class PromiseState(StrEnum):
    VERIFIED = "verified"
    PENDING = "pending"
    UNKNOWN = "unknown"


class VerdictLevel(StrEnum):
    SAFE = "safe"
    TIGHT = "tight"
    AT_RISK = "at_risk"
    IMPOSSIBLE = "impossible"
    UNVERIFIED = "unverified"


class ConnectionFlag(StrEnum):
    CUTOFF_UNAVAILABLE = "Boarding cutoff unavailable"

    @property
    def i18n_key(self) -> str:
        return "flag.cutoff_unavailable"


class Profile(StrEnum):
    STANDARD = "standard"
    FAMILY = "family"
    REDUCED_MOBILITY = "reduced_mobility"
    FAMILY_MOBILITY = "family_mobility"


class CaseState(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    DISPATCH_PENDING_VERIFICATION = "dispatch_pending_verification"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ObservationState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


class ToolErrorClass(StrEnum):
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"
    STALE = "stale"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


class Intent(StrEnum):
    CHECK_CONNECTION = "check_connection"
    FIND_GATE = "find_gate"
    FIND_AMENITY = "find_amenity"
    REQUEST_ASSISTANCE = "request_assistance"
    ARRIVAL_ESTIMATES = "arrival_estimates"
    UNKNOWN = "unknown"


class EventType(StrEnum):
    PASSENGER_INPUT = "passenger_input"
    FLIGHT_UPDATED = "flight_updated"
    GATE_CHANGED = "gate_changed"
    FACILITY_CHANGED = "facility_changed"
    REFRESH_DUE = "refresh_due"
    COORDINATOR_DECIDED = "coordinator_decided"
    DISPATCH_STATUS = "dispatch_status"


class Role(StrEnum):
    PASSENGER = "passenger"
    COORDINATOR = "coordinator"
    INTEGRATION = "integration"
    REVIEWER = "reviewer"


class ReasonCode(StrEnum):
    FLIGHT_UNKNOWN = "FLIGHT_UNKNOWN"
    OUTBOUND_MISSING = "OUTBOUND_MISSING"
    FLIGHT_CANCELLED = "FLIGHT_CANCELLED"
    AMBIGUOUS_FLIGHT = "AMBIGUOUS_FLIGHT"
    AMBIGUOUS_LANDMARK = "AMBIGUOUS_LANDMARK"
    AMBIGUOUS_DATE = "AMBIGUOUS_DATE"
    AMBIGUOUS_PICKUP = "AMBIGUOUS_PICKUP"
    GATE_STALE = "GATE_STALE"
    GATE_CONFLICT = "GATE_CONFLICT"
    GATE_UNKNOWN = "GATE_UNKNOWN"
    GATE_CHANGED = "GATE_CHANGED"
    CUTOFF_MISSING = "CUTOFF_MISSING"
    CUTOFF_STALE = "CUTOFF_STALE"
    CUTOFF_CONFLICT = "CUTOFF_CONFLICT"
    CUTOFF_ESTIMATED = "CUTOFF_ESTIMATED"
    CUTOFF_PASSED = "CUTOFF_PASSED"
    TIGHT_MARGIN = "TIGHT_MARGIN"
    NEGATIVE_MARGIN = "NEGATIVE_MARGIN"
    LOWER_BOUND_EXCEEDS_CUTOFF = "LOWER_BOUND_EXCEEDS_CUTOFF"
    QUEUE_BOUND_STALE = "QUEUE_BOUND_STALE"
    QUEUE_BOUND_MISSING = "QUEUE_BOUND_MISSING"
    QUEUE_BOUND_CONFLICT = "QUEUE_BOUND_CONFLICT"
    LOCATION_UNKNOWN = "LOCATION_UNKNOWN"
    LOCATION_UNCONFIRMED = "LOCATION_UNCONFIRMED"
    MAP_UNAVAILABLE = "MAP_UNAVAILABLE"
    FACILITY_CLOSED = "FACILITY_CLOSED"
    FACILITY_STALE = "FACILITY_STALE"
    LIFT_CLOSED = "LIFT_CLOSED"
    LIFT_UNVERIFIED = "LIFT_UNVERIFIED"
    EDGE_RESTRICTED = "EDGE_RESTRICTED"
    CHECKPOINT_INELIGIBLE = "CHECKPOINT_INELIGIBLE"
    ACCESS_UNVERIFIED = "ACCESS_UNVERIFIED"
    NO_PATH = "NO_PATH"
    NO_ACCESSIBLE_PATH = "NO_ACCESSIBLE_PATH"
    READINESS_UNKNOWN = "READINESS_UNKNOWN"
    STAFF_HANDOFF_PENDING = "STAFF_HANDOFF_PENDING"
    PICKUP_UNCONFIRMED = "PICKUP_UNCONFIRMED"
    PICKUP_INELIGIBLE = "PICKUP_INELIGIBLE"
    CONSENT_MISSING = "CONSENT_MISSING"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_DECLINED = "APPROVAL_DECLINED"
    CASE_CANCELLED = "CASE_CANCELLED"
    CASE_NOT_PENDING = "CASE_NOT_PENDING"
    CASE_SCOPE_CHANGED = "CASE_SCOPE_CHANGED"
    DISPATCH_UNCONFIRMED = "DISPATCH_UNCONFIRMED"
    DISPATCH_OUTCOME_UNKNOWN = "DISPATCH_OUTCOME_UNKNOWN"
    ROUTE_REVOKED = "ROUTE_REVOKED"
    ROUTE_EXPIRED = "ROUTE_EXPIRED"
    ROUTE_UNAVAILABLE = "ROUTE_UNAVAILABLE"
    WRONG_AIRPORT = "WRONG_AIRPORT"
    WRONG_ROLE = "WRONG_ROLE"
    STALE_REVISION = "STALE_REVISION"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    ALREADY_DECIDED = "ALREADY_DECIDED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    AIRCRAFT_UNMATCHED = "AIRCRAFT_UNMATCHED"
    POSITION_UNAVAILABLE = "POSITION_UNAVAILABLE"
    POSITION_STALE = "POSITION_STALE"
    ESTIMATE_UNAVAILABLE = "ESTIMATE_UNAVAILABLE"
    ESTIMATE_STALE = "ESTIMATE_STALE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    AMENITY_CLOSED = "AMENITY_CLOSED"
    DATA_STALE = "DATA_STALE"
    UNSUPPORTED_INPUT = "UNSUPPORTED_INPUT"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    UNAUTHORIZED_TOOL = "UNAUTHORIZED_TOOL"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    URGENT_SAFETY = "URGENT_SAFETY"
    RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE"
    UNACCOMPANIED_MINOR = "UNACCOMPANIED_MINOR"
    APPROVAL_NOT_ALLOWED = "APPROVAL_NOT_ALLOWED"
    NOT_ON_BOOKING = "NOT_ON_BOOKING"
    GROUPED_DISPATCH = "GROUPED_DISPATCH"

    @property
    def i18n_key(self) -> str:
        return f"reason.{self.value}"


class PassengerFlow(StrEnum):
    CONNECTING = "connecting"
    EXITING = "exiting"
    DEPARTING = "departing"


class AccessibleMode(StrEnum):
    WALK = "walk"
    STEP_FREE = "step_free"
    STAIRS = "stairs"
    LIFT = "lift"
    TRANSIT = "transit"
    CHECKPOINT = "checkpoint"
    STAFF_HANDOFF = "staff_handoff"


class ObservationField(StrEnum):
    GATE = "gate"
    TERMINAL = "terminal"
    CONCOURSE = "concourse"
    LEVEL = "level"
    STATUS = "status"
    DEPARTURE = "departure"
    ARRIVAL = "arrival"
    BOARDING_CUTOFF = "boarding_cutoff"
    CAROUSEL = "carousel"


class I18nText(Contract):
    key: TranslationKey
    params: dict[str, JsonValue] = Field(default_factory=dict)


class SourceStamp(Contract):
    source: Id
    observed_at: UTCInstant


class ExpiringSourceStamp(SourceStamp):
    valid_until: UTCInstant

    @model_validator(mode="after")
    def _expiry(self) -> Self:
        if self.valid_until < self.observed_at:
            raise ValueError("valid_until must be at or after observed_at")
        return self


class SecondsRange(Contract):
    lower: Seconds
    upper: Seconds | None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.upper is not None and self.upper < self.lower:
            raise ValueError("upper must be at least lower")
        return self


class TimeWindow(Contract):
    start: UTCInstant
    end: UTCInstant

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError("end must follow start")
        return self


class FlightLeg(Contract):
    id: Id
    operating_carrier: Id
    operating_number: Id
    scheduled_operation_date: date
    origin: AirportCode
    destination: AirportCode
    aliases: tuple[Id, ...] = ()
    provider_leg_id: Id | None = None


class Observation(ExpiringSourceStamp):
    leg_id: Id
    field: ObservationField
    value: UTCInstant | JsonValue = None
    authority_rank: NonNegativeInt
    received_at: UTCInstant
    scheduled: UTCInstant | None = None
    estimated: UTCInstant | None = None
    actual: UTCInstant | None = None
    withdrawn: StrictBool = False
    cutoff_estimated: StrictBool = False

    @field_validator("value", mode="before")
    @classmethod
    def _cutoff_instant(cls, value: object, info: ValidationInfo) -> object:
        if info.data.get("field") is ObservationField.BOARDING_CUTOFF and value is not None:
            return _INSTANT.validate_python(value)
        return value

    @model_validator(mode="after")
    def _evidence_shape(self) -> Self:
        if self.field in (ObservationField.ARRIVAL, ObservationField.DEPARTURE):
            if self.value is not None:
                raise ValueError("arrival and departure times use scheduled, estimated, and actual")
        elif any(value is not None for value in (self.scheduled, self.estimated, self.actual)):
            raise ValueError("scheduled, estimated, and actual are for arrival or departure")
        if self.cutoff_estimated and self.field is not ObservationField.BOARDING_CUTOFF:
            raise ValueError("cutoff_estimated requires a boarding cutoff observation")
        return self


_INSTANT = TypeAdapter(UTCInstant)


class FieldEvidence(Contract):
    leg_id: Id
    field: ObservationField
    state: ObservationState
    observation: Observation | None = None
    alternatives: tuple[Observation, ...] = ()

    @model_validator(mode="after")
    def _fact_identity(self) -> Self:
        observations = self.alternatives
        if self.observation is not None:
            observations += (self.observation,)
        if any(item.leg_id != self.leg_id or item.field != self.field for item in observations):
            raise ValueError("observations must match the leg and field")
        if self.state in (ObservationState.FRESH, ObservationState.STALE) and self.observation is None:
            raise ValueError("fresh or stale facts require an observation")
        if self.state is ObservationState.CONFLICT and self.observation is not None:
            raise ValueError("conflicted facts cannot have a selected observation")
        return self


class MapPoint(Contract):
    x: StrictFloat
    y: StrictFloat


class Node(Contract):
    id: Id
    airport: AirportCode
    terminal: Id
    concourse: Id
    floor: Id
    security_zone: Id
    landmark: I18nText
    x: StrictFloat
    y: StrictFloat
    public: StrictBool = True
    pickup_eligible: StrictBool = False


class Edge(Contract):
    id: Id
    from_node_id: Id
    to_node_id: Id
    directed: Confirmed = True
    distance_m: Annotated[StrictFloat, Field(ge=0)]
    steps: NonNegativeInt = 0
    slope: StrictFloat = 0
    lift: StrictBool = False
    allowed_flows: tuple[PassengerFlow, ...] = Field(min_length=1)
    wait: SecondsRange = SecondsRange(lower=0, upper=0)
    fixed_movement: SecondsRange | None = None
    accessible: StrictBool
    open_windows: tuple[TimeWindow, ...] = ()
    closed: StrictBool = False
    restricted: StrictBool = False
    requires_access_evidence: StrictBool = False
    queue_id: Id | None = None
    mode: AccessibleMode = AccessibleMode.WALK
    shape: tuple[MapPoint, ...] = ()


class Overlay(ExpiringSourceStamp):
    id: Id
    airport: AirportCode
    edge_id: Id
    closed: StrictBool | None = None
    accessible: StrictBool | None = None


class QueueObservation(ExpiringSourceStamp):
    airport: AirportCode
    queue_id: Id
    flow: PassengerFlow
    wait: SecondsRange | None


class AmenityObservation(ExpiringSourceStamp):
    airport: AirportCode
    node_id: Id
    open: StrictBool | None


class SpeedBand(Contract):
    slow_mps: Annotated[StrictFloat, Field(gt=0)]
    fast_mps: Annotated[StrictFloat, Field(gt=0)]

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.fast_mps < self.slow_mps:
            raise ValueError("fast_mps must be at least slow_mps")
        return self


class PolicyPack(Contract):
    policy_version: Id = "demo-v1"
    buffer_floors: dict[Profile, Seconds] = Field(default_factory=lambda: {
        Profile.STANDARD: 180,
        Profile.FAMILY: 300,
        Profile.REDUCED_MOBILITY: 420,
        Profile.FAMILY_MOBILITY: 420,
    })
    speed_bands: dict[Profile, SpeedBand] = Field(default_factory=lambda: {
        Profile.STANDARD: SpeedBand(slow_mps=1.0, fast_mps=1.5),
        Profile.FAMILY: SpeedBand(slow_mps=0.8, fast_mps=1.2),
        Profile.REDUCED_MOBILITY: SpeedBand(slow_mps=0.6, fast_mps=1.0),
        Profile.FAMILY_MOBILITY: SpeedBand(slow_mps=0.6, fast_mps=1.0),
    })
    refresh_seconds: Seconds = 20
    stale_seconds: Seconds = 90
    aircraft_last_reported_seconds: Seconds = 60
    aircraft_hide_seconds: Seconds = 180
    turn_reads: NonNegativeInt = 4
    model_retries: NonNegativeInt = 1
    turn_wall_seconds: Seconds = 8
    tight_margin_seconds: Seconds = 600

    @model_validator(mode="after")
    def _policy_bounds(self) -> Self:
        if self.buffer_floors.keys() != set(Profile) or self.speed_bands.keys() != set(Profile):
            raise ValueError("buffer floors and speed bands must cover every profile")
        if self.buffer_floors[Profile.FAMILY_MOBILITY] < max(
            self.buffer_floors[Profile.FAMILY], self.buffer_floors[Profile.REDUCED_MOBILITY]
        ):
            raise ValueError("combined buffer must cover both profiles")
        if self.refresh_seconds > self.stale_seconds:
            raise ValueError("refresh must occur at or before staleness")
        if self.aircraft_last_reported_seconds > self.aircraft_hide_seconds:
            raise ValueError("last-reported threshold must precede hiding")
        return self


class RouteMapPoint(MapPoint):
    terminal: Id
    concourse: Id


class RouteStep(Contract):
    number: Annotated[int, Field(strict=True, ge=1)]
    floor: Id
    landmark: I18nText
    accessible_mode: AccessibleMode
    text: I18nText
    map_position: RouteMapPoint | None = None


class FloorShape(Contract):
    terminal: Id
    concourse: Id
    floor: Id
    points: tuple[MapPoint, ...] = Field(min_length=2)


class RouteView(Contract):
    route_id: Id
    revision: NonNegativeInt
    steps: tuple[RouteStep, ...]
    provenance: SourceStamp
    floor_shapes: tuple[FloorShape, ...] = ()
    floor_outlines: tuple[FloorShape, ...] = ()

    @model_validator(mode="after")
    def _step_numbers(self) -> Self:
        if any(step.number != index for index, step in enumerate(self.steps, start=1)):
            raise ValueError("route steps must be numbered in order starting at 1")
        return self


class RouteResult(RouteView):
    node_ids: tuple[Id, ...] = Field(min_length=1)
    movement: SecondsRange
    wait: SecondsRange
    buffer: Seconds
    reason_codes: tuple[ReasonCode, ...] = ()


class TransferResult(Contract):
    route: RouteResult | None = None
    ready: SecondsRange | None = None
    lower: Seconds | None = None
    upper: Seconds | None = None
    time_remaining: StrictInt | None = None
    margin: StrictInt | None = None
    status: ConnectionStatus
    reason_codes: tuple[ReasonCode, ...] = ()
    cutoff_unavailable: StrictBool = False

    @model_validator(mode="after")
    def _bounds(self) -> Self:
        if self.upper is not None and (self.lower is None or self.upper < self.lower):
            raise ValueError("upper requires a lower bound no greater than itself")
        if self.margin is not None and (self.upper is None or self.time_remaining is None):
            raise ValueError("margin requires an upper bound and time remaining")
        return self


class PartyNeeds(Contract):
    party_size: Annotated[int, Field(strict=True, ge=1)] = 1
    children: NonNegativeInt = 0
    step_free: StrictBool = False
    wheelchair: StrictBool = False
    elderly: NonNegativeInt = 0

    @model_validator(mode="after")
    def _party_size(self) -> Self:
        if self.children > self.party_size or self.elderly > self.party_size:
            raise ValueError("children and elderly travellers cannot exceed party size")
        return self


class Location(Contract):
    airport: AirportCode
    node_id: Id
    landmark: I18nText


class Journey(Contract):
    id: Id
    airport: AirportCode
    profile: Profile = Profile.STANDARD
    party_needs: PartyNeeds = Field(default_factory=PartyNeeds)
    flow: PassengerFlow = PassengerFlow.CONNECTING
    language: Id = "en"
    created_at: UTCInstant
    inbound_leg_id: Id | None = None
    outbound_leg_id: Id | None = None
    origin_node_id: Id | None = None
    revision: NonNegativeInt = 0
    next_refresh_at: UTCInstant | None = None
    closed_at: UTCInstant | None = None


class AssistanceService(StrEnum):
    WHEELCHAIR = "wheelchair"
    MOBILITY_ESCORT = "mobility_escort"
    GATE_HOLD = "gate_hold"
    BUGGY = "buggy"


class DecisionKind(StrEnum):
    APPROVE = "approve"
    DECLINE = "decline"


class DispatchState(StrEnum):
    UNKNOWN = "unknown"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    SENT = "sent"
    UNKNOWN = "unknown"
    DONE = "done"


class OutboundEffect(StrEnum):
    COORDINATOR_NOTIFICATION = "coordinator_notification"
    MOBILITY_DISPATCH = "mobility_dispatch"
    GATE_HOLD_REVIEW = "gate_hold_review"


class Case(Contract):
    id: Id
    journey_id: Id
    airport: AirportCode
    service: AssistanceService
    state: CaseState = CaseState.DRAFT
    request_revision: NonNegativeInt
    journey_revision: NonNegativeInt
    created_at: UTCInstant
    idempotency_key: Id
    pickup: Location | None = None
    pickup_confirmed: StrictBool = False
    consent: StrictBool = False
    approval_expires_at: UTCInstant | None = None
    target_leg_id: Id | None = None
    target_revision: NonNegativeInt | None = None
    reason_codes: tuple[ReasonCode, ...] = ()


class Decision(Contract):
    event_id: Id
    case_id: Id
    request_revision: NonNegativeInt
    decision: DecisionKind
    reason_code: ReasonCode | None = None
    wait_min: NonNegativeInt | None = None


class OutboxItem(Contract):
    id: Id
    case_id: Id
    request_revision: NonNegativeInt
    effect: OutboundEffect
    status: OutboxStatus = OutboxStatus.PENDING
    idempotency_key: Id
    due_at: UTCInstant
    claim_expires_at: UTCInstant | None = None
    attempts: NonNegativeInt = 0
    in_flight_at: UTCInstant | None = None


class AuditRecord(Contract):
    event_id: Id
    occurred_at: UTCInstant
    action: Id
    outcome: Id
    role: Role | None = None
    journey_id: Id | None = None
    case_id: Id | None = None
    tool_id: Id | None = None
    schema_version: Id | None = None
    repair_rule_version: Id | None = None
    rule_id: Id | None = None
    error_class: ToolErrorClass | None = None
    attempts: NonNegativeInt = 0
    duration_seconds: Seconds | None = None
    reason_codes: tuple[ReasonCode, ...] = ()


class PermittedChoice(Contract):
    id: Id
    label: I18nText


class Success[T](Contract):
    kind: Literal["success"] = "success"
    value: T


class ToolError(Contract):
    kind: Literal["error"] = "error"
    error_class: ToolErrorClass = Field(alias="class")
    diagnostic: ReasonCode
    permitted_choices: tuple[PermittedChoice, ...] = ()
    retry_after_seconds: Seconds | None = None

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


type ToolResult[T] = Annotated[Success[T] | ToolError, Field(discriminator="kind")]


class PassengerAction(StrEnum):
    CHOOSE_FLIGHT = "choose_flight"
    CONFIRM_DATE = "confirm_date"
    CHOOSE_LANDMARK = "choose_landmark"
    CONFIRM_PICKUP = "confirm_pickup"
    WAIT_FOR_HANDOFF = "wait_for_handoff"
    FOLLOW_ROUTE = "follow_route"
    SEEK_STAFF = "seek_staff"
    REFRESH_ROUTE = "refresh_route"
    CHECK_GATE = "check_gate"
    SEEK_EMERGENCY_HELP = "seek_emergency_help"
    WAIT_FOR_UPDATE = "wait_for_update"


class BannerKind(StrEnum):
    ROUTE_CHANGED = "route_changed"
    ROUTE_EXPIRED = "route_expired"
    STALE_DATA = "stale_data"


class Applicability(StrEnum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class AircraftFreshness(StrEnum):
    CURRENT = "current"
    LAST_REPORTED = "last_reported"
    HIDDEN = "hidden"
    UNAVAILABLE = "unavailable"


class GateView(Contract):
    value: Id | None = None
    terminal: Id | None = None
    concourse: Id | None = None
    level: Id | None = None
    state: ObservationState = ObservationState.UNKNOWN
    provenance: dict[Literal["value", "terminal", "concourse", "level"], SourceStamp] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def _gate_facts(self) -> Self:
        if self.state is ObservationState.FRESH and self.value is None:
            raise ValueError("a fresh gate requires a value")
        if any(getattr(self, fact) is not None and fact not in self.provenance
               for fact in ("value", "terminal", "concourse", "level")):
            raise ValueError("each displayed gate fact requires provenance")
        return self


class CutoffView(Contract):
    value: UTCInstant | None = None
    source: Id | None = None
    observed_at: UTCInstant | None = None
    unavailable: StrictBool = True
    estimated: StrictBool = False

    @model_validator(mode="after")
    def _availability(self) -> Self:
        if self.unavailable != (self.value is None):
            raise ValueError("unavailable must reflect a missing cutoff")
        if (self.source is None) != (self.observed_at is None):
            raise ValueError("source and observed_at must be provided together")
        if self.value is not None and self.source is None:
            raise ValueError("a cutoff requires provenance")
        if self.estimated and self.value is None:
            raise ValueError("an estimated cutoff requires a value")
        return self


class StatusView(Contract):
    status: ConnectionStatus
    provenance: SourceStamp
    reason_codes: tuple[ReasonCode, ...] = ()


class TransferView(Contract):
    provenance: SourceStamp
    lower_min: NonNegativeInt | None = None
    upper_min: NonNegativeInt | None = None
    includes_readiness: StrictBool = False
    margin_min: StrictInt | None = None
    conditional: StrictBool = False

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.upper_min is not None and (self.lower_min is None or self.upper_min < self.lower_min):
            raise ValueError("upper_min requires a lower bound no greater than itself")
        if self.margin_min is not None and self.upper_min is None:
            raise ValueError("a margin requires an upper bound")
        return self

    @classmethod
    def from_result(cls, result: TransferResult, *, provenance: SourceStamp) -> Self:
        return cls(
            lower_min=None if result.lower is None else result.lower // 60,
            upper_min=None if result.upper is None else (result.upper + 59) // 60,
            includes_readiness=result.ready is not None,
            margin_min=None if result.margin is None else result.margin // 60,
            conditional=ReasonCode.STAFF_HANDOFF_PENDING in result.reason_codes,
            provenance=provenance,
        )


class AssistanceView(Contract):
    state: CaseState
    stage: I18nText
    provenance: SourceStamp
    pickup_landmark: I18nText | None = None
    case_ref: Id | None = None
    staffed_alternative: I18nText | None = None
    approval_window: TimeWindow | None = None
    service: AssistanceService | None = None
    destination: str | None = None
    retargeted_from: str | None = None


class RequestView(Contract):
    """One staff request on this journey. A family can have several: a wheelchair, a buggy seat, a gate hold."""
    case_ref: Id
    service: AssistanceService
    state: CaseState
    stage: I18nText
    destination: str | None = None
    retargeted_from: str | None = None
    group_role: Literal["rides", "waits"] | None = None
    seats: NonNegativeInt | None = None


class Notice(Contract):
    """The one thing the kiosk says unprompted about a change, with the revision it belongs to."""
    kind: Id
    text: I18nText
    seq: NonNegativeInt


class Promise(Contract):
    """One thing the kiosk is willing to say, and how far it can stand behind it."""
    key: Id
    state: PromiseState
    text: I18nText
    source: Id | None = None
    observed_at: UTCInstant | None = None


class Verdict(Contract):
    """The connection in one word, measured against boarding close, with the margin in minutes."""
    level: VerdictLevel
    margin_min: StrictInt | None = None
    text: I18nText


class NextActionView(Contract):
    action: PassengerAction
    text: I18nText
    choices: tuple[PermittedChoice, ...] = ()


class BannerView(Contract):
    kind: BannerKind
    text: I18nText


class QRView(Contract):
    token: Id | None = None
    suppressed_reason: ReasonCode | None = None

    @model_validator(mode="after")
    def _one_outcome(self) -> Self:
        if (self.token is None) == (self.suppressed_reason is None):
            raise ValueError("provide a token or a suppression reason")
        return self


class ArrivalEstimate(Contract):
    applicability: Applicability
    state: ObservationState
    wait: SecondsRange | None = None
    provenance: SourceStamp | None = None

    @model_validator(mode="after")
    def _estimate_provenance(self) -> Self:
        if self.wait is not None and self.provenance is None:
            raise ValueError("displayed arrival estimates require provenance")
        return self


class ArrivalEstimates(Contract):
    baggage: ArrivalEstimate | None = None
    immigration: ArrivalEstimate | None = None
    security: ArrivalEstimate | None = None


class AircraftPosition(Contract):
    latitude: Annotated[StrictFloat, Field(ge=-90, le=90)]
    longitude: Annotated[StrictFloat, Field(ge=-180, le=180)]
    altitude_m: StrictFloat | None = None


class AircraftReport(SourceStamp):
    position: AircraftPosition
    matched_leg_id: Id
    provider_leg_id: Id | None = None
    aircraft_id: Id | None = None


class AircraftView(Contract):
    position: AircraftPosition | None = None
    source: Id | None = None
    observed_at: UTCInstant | None = None
    freshness: AircraftFreshness = AircraftFreshness.UNAVAILABLE

    @model_validator(mode="after")
    def _visibility(self) -> Self:
        visible = self.freshness in (AircraftFreshness.CURRENT, AircraftFreshness.LAST_REPORTED)
        if visible != (self.position is not None):
            raise ValueError("position is required only for visible reports")
        if (self.source is None) != (self.observed_at is None) or (visible and self.source is None):
            raise ValueError("visible reports require source and observed_at")
        return self

    @classmethod
    def from_report(
        cls, report: AircraftReport | None, *, as_of: datetime, policy: PolicyPack
    ) -> Self:
        now = as_utc(as_of)
        if report is None or report.observed_at > now:
            return cls()
        age = (now - report.observed_at).total_seconds()
        if age >= policy.aircraft_hide_seconds:
            return cls(source=report.source, observed_at=report.observed_at,
                       freshness=AircraftFreshness.HIDDEN)
        freshness = AircraftFreshness.CURRENT
        if age > policy.aircraft_last_reported_seconds:
            freshness = AircraftFreshness.LAST_REPORTED
        return cls(position=report.position, source=report.source, observed_at=report.observed_at,
                   freshness=freshness)


class ViewModel(Contract):
    revision: NonNegativeInt
    as_of: UTCInstant
    language: Id = "en"
    gate: GateView = Field(default_factory=GateView)
    cutoff: CutoffView = Field(default_factory=CutoffView)
    status: StatusView
    transfer: TransferView | None = None
    route: RouteView | None = None
    assistance: AssistanceView | None = None
    next_action: NextActionView
    banners: tuple[BannerView, ...] = ()
    qr: QRView = QRView(suppressed_reason=ReasonCode.ROUTE_UNAVAILABLE)
    arrivals: ArrivalEstimates = Field(default_factory=ArrivalEstimates)
    aircraft: AircraftView = Field(default_factory=AircraftView)
    verdict: Verdict | None = None
    ledger: tuple[Promise, ...] = ()
    requests: tuple[RequestView, ...] = ()
    notice: Notice | None = None
    i18n: dict[Id, I18nText] = Field(default_factory=dict)


class JourneyRevision(Contract):
    journey_id: Id
    revision: NonNegativeInt
    created_at: UTCInstant
    policy_version: Id
    view: ViewModel
    event_id: Id | None = None
    graph_version: Id | None = None
    overlay_version: Id | None = None

    @model_validator(mode="after")
    def _same_revision(self) -> Self:
        if self.view.revision != self.revision:
            raise ValueError("view revision must match its persisted revision")
        return self


class FlightDirection(StrEnum):
    ARRIVALS = "arrivals"
    DEPARTURES = "departures"


class GetFlightsRequest(Contract):
    airport: AirportCode
    direction: FlightDirection
    window: TimeWindow
    cursor: Id | None = None
    limit: PageSize = 25
    query: Id | None = None


class FlightBoardItem(Contract):
    leg: FlightLeg
    facts: tuple[FieldEvidence, ...] = ()


class GetFlightsResponse(Contract):
    as_of: UTCInstant
    flights: tuple[FlightBoardItem, ...]
    next_cursor: Id | None = None


class CreateJourneyRequest(Contract):
    airport: AirportCode
    profile: Profile = Profile.STANDARD
    party_needs: PartyNeeds = Field(default_factory=PartyNeeds)
    flow: PassengerFlow = PassengerFlow.CONNECTING
    language: Id = "en"
    inbound_leg_id: Id | None = None
    outbound_leg_id: Id | None = None
    booking_ref: Annotated[str, Field(min_length=3, max_length=32, pattern="^[A-Za-z0-9-]+$")] | None = None


class CreateJourneyResponse(Contract):
    journey_id: Id
    revision: NonNegativeInt


class JourneyInputRequest(Contract):
    journey_id: Id
    client_input_id: Id
    known_revision: NonNegativeInt | None = None
    intent: Intent = Intent.UNKNOWN
    text: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    flight_aliases: tuple[Id, ...] = ()
    operation_date: date | None = None
    inbound_leg_id: Id | None = None
    outbound_leg_id: Id | None = None
    landmark_text: Id | None = None
    confirmed_origin_id: Id | None = None
    language: Id | None = None
    party_needs: PartyNeeds | None = None
    cancel_case_ref: Id | None = None


class JourneyInputResponse(Contract):
    journey_id: Id
    event_id: Id
    revision: NonNegativeInt
    work_pending: StrictBool = False


class GetJourneyRequest(Contract):
    journey_id: Id
    known_revision: NonNegativeInt | None = None


class GetJourneyResponse(Contract):
    revision: NonNegativeInt
    unchanged: StrictBool
    view: ViewModel | None = None

    @model_validator(mode="after")
    def _view_version(self) -> Self:
        if self.unchanged != (self.view is None):
            raise ValueError("unchanged responses omit the view")
        if self.view is not None and self.view.revision != self.revision:
            raise ValueError("view revision must match response revision")
        return self


class AssistanceRequest(Contract):
    journey_id: Id
    client_input_id: Id
    journey_revision: NonNegativeInt
    service: AssistanceService
    pickup_node_id: Id
    pickup_confirmed: Confirmed
    consent: Confirmed
    target_leg_id: Id | None = None
    target_revision: NonNegativeInt | None = None

    @model_validator(mode="after")
    def _hold_scope(self) -> Self:
        if self.service is AssistanceService.GATE_HOLD and (
            self.target_leg_id is None or self.target_revision is None
        ):
            raise ValueError("a gate hold requires a target leg and revision")
        return self


class AssistanceResponse(Contract):
    case_ref: Id
    state: CaseState
    request_revision: NonNegativeInt
    journey_revision: NonNegativeInt


class GetStaffCasesRequest(Contract):
    airport: AirportCode
    states: tuple[CaseState, ...] = (CaseState.PENDING_APPROVAL,)
    cursor: Id | None = None
    limit: PageSize = 25


class StaffCaseItem(Contract):
    case: Case
    gate: GateView
    cutoff: CutoffView
    time_remaining: StrictInt | None = None


class GetStaffCasesResponse(Contract):
    as_of: UTCInstant
    cases: tuple[StaffCaseItem, ...]
    next_cursor: Id | None = None


class DecisionRequest(Decision):
    pass


class CallbackOutcome(StrEnum):
    APPLIED = "applied"
    REPLAYED = "replayed"
    REJECTED = "rejected"


class DecisionResponse(Contract):
    event_id: Id
    case_id: Id
    request_revision: NonNegativeInt
    state: CaseState
    outcome: CallbackOutcome
    reason_code: ReasonCode | None = None


class DispatchStatusRequest(Contract):
    event_id: Id
    case_id: Id
    request_revision: NonNegativeInt
    status: DispatchState
    evidence: SourceStamp
    provider_ref: Id | None = None
    eta: SecondsRange | None = None


class DispatchStatusResponse(DecisionResponse):
    pass


class GetRouteRequest(Contract):
    token: Id


class GetRouteResponse(Contract):
    route: RouteView | None = None
    reason_code: ReasonCode | None = None

    @model_validator(mode="after")
    def _one_outcome(self) -> Self:
        if (self.route is None) == (self.reason_code is None):
            raise ValueError("provide a public route or a reason")
        return self


class GetFlightAndGateStatusInput(Contract):
    refs: tuple[Id, ...] = Field(min_length=1)
    airport: AirportCode
    as_of: UTCInstant


class GetFlightAndGateStatusOutput(Contract):
    legs: tuple[FlightLeg, ...]
    facts: tuple[FieldEvidence, ...]


class CalculateTerminalTransferTimeInput(Contract):
    origin_id: Id
    gate_id: Id
    profile: Profile
    as_of: UTCInstant
    flow: PassengerFlow = PassengerFlow.CONNECTING


CalculateTerminalTransferTimeOutput = RouteResult


class RequestMobilityDispatchAsyncInput(Contract):
    case: Case

    @model_validator(mode="after")
    def _confirmed_pickup(self) -> Self:
        if self.case.service is AssistanceService.GATE_HOLD:
            raise ValueError("gate holds use a separate approval scope")
        if not self.case.consent or not self.case.pickup_confirmed or self.case.pickup is None:
            raise ValueError("mobility proposals require consent and confirmed pickup")
        if self.case.pickup.airport != self.case.airport:
            raise ValueError("pickup must belong to the case airport")
        return self


RequestMobilityDispatchAsyncOutput = AssistanceResponse


class CallAssistanceInput(Contract):
    location: Location
    case_id: Id
    request_revision: NonNegativeInt
    idempotency_key: Id


class CallAssistanceOutput(Contract):
    accepted: StrictBool
    provider_ref: Id | None = None


class ReceiveAssistanceDecisionInput(Contract):
    decision: Decision


ReceiveAssistanceDecisionOutput = DecisionResponse


class GetArrivalEstimatesInput(Contract):
    arrival: FlightLeg
    airport: AirportCode
    journey_type: PassengerFlow


GetArrivalEstimatesOutput = ArrivalEstimates


class GetAircraftPositionInput(Contract):
    leg: FlightLeg


class GetAircraftPositionOutput(Contract):
    report: AircraftReport | None = None
    freshness: AircraftFreshness
    reason_code: ReasonCode | None = None


class ReadinessState(StrEnum):
    PARKED = "parked"
    DOORS_OPEN = "doors_open"
    READY_TO_MOVE = "ready_to_move"
    STAFF_HANDOFF_PENDING = "staff_handoff_pending"
    MOBILITY_DISPATCHED = "mobility_dispatched"


class ReadinessEvent(SourceStamp):
    leg_id: Id
    state: ReadinessState
    ready: SecondsRange | None = None


class FixtureGraph(Contract):
    airport: AirportCode
    graph_version: Id
    nodes: tuple[Node, ...] = Field(min_length=1)
    edges: tuple[Edge, ...]
    floor_shapes: tuple[FloorShape, ...] = ()

    @model_validator(mode="after")
    def _graph_scope(self) -> Self:
        nodes = {node.id for node in self.nodes}
        if len(nodes) != len(self.nodes) or len({edge.id for edge in self.edges}) != len(self.edges):
            raise ValueError("duplicate node or edge id")
        if any(node.airport != self.airport for node in self.nodes):
            raise ValueError("all nodes must belong to the graph airport")
        if any(edge.from_node_id not in nodes or edge.to_node_id not in nodes for edge in self.edges):
            raise ValueError("all edge endpoints must reference graph nodes")
        return self


class FlightLegFixture(FlightLeg):
    codeshare_aliases: tuple[Id, ...] = ()
    scheduled_departure: UTCInstant
    scheduled_arrival: UTCInstant
    initial_observations: tuple[Observation, ...] = ()

    @model_validator(mode="after")
    def _flight_scope(self) -> Self:
        if self.scheduled_arrival < self.scheduled_departure:
            raise ValueError("arrival cannot precede departure")
        if any(observation.leg_id != self.id for observation in self.initial_observations):
            raise ValueError("initial observations must belong to the fixture leg")
        return self


class EventStreamItem(Contract):
    id: Id
    timestamp: UTCInstant
    event_type: EventType
    payload: (
        Observation | Overlay | QueueObservation | AmenityObservation | ReadinessEvent
        | JourneyInputRequest | Decision | DispatchStatusRequest | None
    ) = None

    @model_validator(mode="after")
    def _event_payload(self) -> Self:
        payload_types = {
            EventType.PASSENGER_INPUT: (JourneyInputRequest,),
            EventType.FLIGHT_UPDATED: (Observation, ReadinessEvent),
            EventType.GATE_CHANGED: (Observation,),
            EventType.FACILITY_CHANGED: (Overlay, QueueObservation, AmenityObservation),
            EventType.REFRESH_DUE: (type(None),),
            EventType.COORDINATOR_DECIDED: (Decision,),
            EventType.DISPATCH_STATUS: (DispatchStatusRequest,),
        }
        if not isinstance(self.payload, payload_types[self.event_type]):
            raise ValueError("payload does not match event_type")
        if self.event_type is EventType.GATE_CHANGED and self.payload.field is not ObservationField.GATE:
            raise ValueError("gate_changed requires a gate observation")
        if isinstance(self.payload, (Decision, DispatchStatusRequest)) and self.payload.event_id != self.id:
            raise ValueError("callback event id must match the replay event id")
        return self


class LandmarkAliasFixture(Contract):
    airport: AirportCode
    node_id: Id
    alias: Id
    language: Id = "en"
    source: Id


class ScenarioFixture(Contract):
    id: Id
    clock_start: Id
    profile: Profile
    party_needs: PartyNeeds
    event_sequence: tuple[Id, ...]
    expected_route_node_ids: tuple[Id, ...]
    expected_transfer_lower_seconds: Seconds | None
    expected_transfer_upper_seconds: Seconds | None
    expected_status: ConnectionStatus
    expected_margin_seconds: StrictInt | None
    allowed_outbound_effects: tuple[OutboundEffect, ...]
    required_passenger_action: PassengerAction
    expected_gate: Id | None = None
    expected_cutoff: UTCInstant | None = None


class AirportMeta(Contract):
    airport: AirportCode
    name: I18nText
    timezone: Id = "Asia/Dubai"
    centroid: AircraftPosition
    source: Id
    source_revision: Id

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("expected an IANA timezone identifier") from exc
        return value


def export_schemas(directory: Path | None = None) -> tuple[Path, ...]:
    from json import dumps

    if directory is None:
        directory = Path(__file__).resolve().parents[1] / "data" / "schema"
    directory.mkdir(parents=True, exist_ok=True)
    models = (
        ("fixture_graph", FixtureGraph),
        ("flight_leg_fixture", FlightLegFixture),
        ("event_stream_item", EventStreamItem),
        ("landmark_alias_fixture", LandmarkAliasFixture),
        ("scenario_fixture", ScenarioFixture),
        ("airport_meta", AirportMeta),
        ("policy_pack", PolicyPack),
    )
    paths = []
    for name, model in models:
        path = directory / f"{name}.schema.json"
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", **model.model_json_schema()}
        path.write_text(dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        paths.append(path)
    return tuple(paths)


if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser()
    parser.add_argument("command", choices=("export-schema",))
    parser.parse_args()
    export_schemas()
