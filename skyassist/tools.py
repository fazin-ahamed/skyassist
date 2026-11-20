import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from random import random
from time import sleep
from types import MappingProxyType

from pydantic import TypeAdapter, ValidationError

from skyassist.clock import Clock, as_utc
from skyassist.db import timestamp
from skyassist.evidence import resolve_flight
from skyassist.models import (
    CalculateTerminalTransferTimeInput, CalculateTerminalTransferTimeOutput,
    CallAssistanceInput, CallAssistanceOutput, Contract, GetAircraftPositionInput,
    GetAircraftPositionOutput, GetArrivalEstimatesInput, GetArrivalEstimatesOutput,
    GetFlightAndGateStatusInput, GetFlightAndGateStatusOutput, PolicyPack, ReasonCode,
    ReceiveAssistanceDecisionInput, ReceiveAssistanceDecisionOutput,
    RequestMobilityDispatchAsyncInput, RequestMobilityDispatchAsyncOutput, Success,
    ToolError, ToolErrorClass, ToolResult,
)
from skyassist.providers import (
    AircraftProvider, ArrivalEstimateProvider, FlightProvider, MockAircraftProvider,
    MockArrivalEstimateProvider, MockFlightProvider, ProviderFailure, ProviderFailureKind,
    matched_leg,
)

MAX_ARGUMENT_BYTES = 16_384
MAX_ARGUMENT_DEPTH = 12
SCHEMA_VERSION = "1"
REPAIR_RULE_VERSION = "1"


@dataclass(frozen=True)
class ToolSpec:
    input: type[Contract]
    output: type[Contract]
    policy_owned: bool = False


TOOLS = MappingProxyType({
    "get_flight_and_gate_status": ToolSpec(GetFlightAndGateStatusInput, GetFlightAndGateStatusOutput),
    "calculate_terminal_transfer_time": ToolSpec(
        CalculateTerminalTransferTimeInput, CalculateTerminalTransferTimeOutput,
    ),
    "get_arrival_estimates": ToolSpec(GetArrivalEstimatesInput, GetArrivalEstimatesOutput),
    "get_aircraft_position": ToolSpec(GetAircraftPositionInput, GetAircraftPositionOutput),
    "request_mobility_dispatch_async": ToolSpec(
        RequestMobilityDispatchAsyncInput, RequestMobilityDispatchAsyncOutput, policy_owned=True,
    ),
    "call_assistance": ToolSpec(CallAssistanceInput, CallAssistanceOutput, policy_owned=True),
    "receive_assistance_decision": ToolSpec(
        ReceiveAssistanceDecisionInput, ReceiveAssistanceDecisionOutput, policy_owned=True,
    ),
})
_OUTPUTS = {name: TypeAdapter(ToolResult[spec.output]) for name, spec in TOOLS.items()}


def _error(diagnostic: ReasonCode, error_class: ToolErrorClass = ToolErrorClass.UNAVAILABLE,
           retry_after_seconds: int | None = None) -> ToolError:
    return ToolError(error_class=error_class, diagnostic=diagnostic, retry_after_seconds=retry_after_seconds)


def validate_policy_input(tool_id: str, value: Contract) -> Contract | ToolError:
    spec = TOOLS.get(tool_id) if isinstance(tool_id, str) else None
    if spec is None or not spec.policy_owned:
        return _error(ReasonCode.UNAUTHORIZED_TOOL)
    if type(value) is spec.input:
        try:
            return spec.input.model_validate(value.model_dump(warnings="error"), strict=True)
        except ValueError:
            pass
    return _error(ReasonCode.INVALID_ARGUMENTS)


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON key")
    return result


def _invalid_constant(_: str) -> None:
    raise ValueError("non-finite JSON value")


def _check_depth(value: object, depth: int = 1) -> None:
    if isinstance(value, (dict, list)):
        if depth > MAX_ARGUMENT_DEPTH:
            raise ValueError("JSON nesting limit")
        for item in value.values() if isinstance(value, dict) else value:
            _check_depth(item, depth + 1)


def _decode(raw: str | bytes) -> object:
    if not isinstance(raw, (str, bytes)) or len(raw) > MAX_ARGUMENT_BYTES:
        raise ValueError("JSON size limit")
    payload = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(payload) > MAX_ARGUMENT_BYTES:
        raise ValueError("JSON size limit")
    value = json.loads(payload.decode("utf-8"), object_pairs_hook=_object, parse_constant=_invalid_constant)
    _check_depth(value)
    return value


def _arguments(
    spec: ToolSpec, raw: str | bytes,
) -> tuple[Contract | ToolError, list[str]]:
    try:
        data = _decode(raw)
    except (ValueError, RecursionError):
        return _error(ReasonCode.INVALID_ARGUMENTS), []
    try:
        return spec.input.model_validate_json(raw, strict=True), []
    except ValidationError as first:
        if not isinstance(data, dict):
            return _error(ReasonCode.INVALID_ARGUMENTS), []
        issues = {(issue["loc"], issue["type"]) for issue in first.errors(include_input=False)}

    rules = []
    if (("airport",), "string_pattern_mismatch") in issues:
        original = data["airport"]
        trimmed = original.strip(" \t\r\n")
        if len(trimmed) == 3 and trimmed.isascii() and trimmed.isalpha():
            if trimmed != original:
                rules.append("airport_trim")
            if trimmed != trimmed.upper():
                rules.append("airport_case")
            data["airport"] = trimmed.upper()
    if spec.input is GetFlightAndGateStatusInput and (("refs",), "tuple_type") in issues:
        if isinstance(data["refs"], str):
            try:
                refs = _decode(data["refs"])
                if isinstance(refs, list):
                    data["refs"] = refs
                    _check_depth(data)
                    rules.append("refs_json_list")
            except (ValueError, RecursionError):
                return _error(ReasonCode.INVALID_ARGUMENTS), rules
    if rules:
        try:
            return spec.input.model_validate_json(json.dumps(data, allow_nan=False), strict=True), rules
        except (ValueError, RecursionError):
            pass
    return _error(ReasonCode.INVALID_ARGUMENTS), rules


_FAILURES = {
    ProviderFailureKind.TRANSIENT: (ReasonCode.SOURCE_UNAVAILABLE, ToolErrorClass.UNAVAILABLE),
    ProviderFailureKind.TIMEOUT: (ReasonCode.TIMEOUT, ToolErrorClass.TIMEOUT),
    ProviderFailureKind.RATE_LIMIT: (ReasonCode.RATE_LIMITED, ToolErrorClass.RATE_LIMITED),
    ProviderFailureKind.QUOTA: (ReasonCode.BUDGET_EXHAUSTED, ToolErrorClass.RATE_LIMITED),
    ProviderFailureKind.AUTH: (ReasonCode.SOURCE_UNAVAILABLE, ToolErrorClass.UNAVAILABLE),
    ProviderFailureKind.UNAVAILABLE: (ReasonCode.SOURCE_UNAVAILABLE, ToolErrorClass.UNAVAILABLE),
}


@dataclass
class _AdapterState:
    adapter: object
    disabled: bool = False
    failures: int = 0
    retry_at: datetime | None = None
    blocked: ToolError | None = None


class ReadRecovery:
    def __init__(self, clock: Clock, *, wait: Callable[[float], None] = sleep,
                 jitter: Callable[[], float] = random) -> None:
        self.clock = clock
        self._wait = wait
        self._jitter = jitter
        self._states: dict[int, _AdapterState] = {}

    def read[T](
        self, adapter: object, operation: Callable[[datetime], ToolResult[T]],
        deadline: datetime, *, background: bool = False,
    ) -> tuple[ToolResult[T], int]:
        state = self._states.setdefault(id(adapter), _AdapterState(adapter))
        now = self.clock.now()
        timeout = _error(ReasonCode.TIMEOUT, ToolErrorClass.TIMEOUT)
        if now >= deadline:
            return timeout, 0
        if state.disabled:
            return _error(ReasonCode.SOURCE_UNAVAILABLE), 0
        if state.blocked is not None and state.retry_at is not None and state.retry_at > now:
            delay = ceil((state.retry_at - now).total_seconds())
            blocked = state.blocked
            return _error(blocked.diagnostic, blocked.error_class, delay), 0

        for attempt in range(1, 2 if background else 3):
            kind = None
            try:
                result = operation(deadline)
                if isinstance(result, ToolError):
                    result = ToolError.model_validate_json(result.model_dump_json(warnings="error"), strict=True)
            except ProviderFailure as exc:
                kind = exc.kind
                result = _error(*_FAILURES[kind], exc.retry_after_seconds)
            except TimeoutError:
                kind, result = ProviderFailureKind.TIMEOUT, timeout
            except Exception:
                return _error(ReasonCode.SOURCE_UNAVAILABLE), attempt

            now = self.clock.now()
            if kind is ProviderFailureKind.AUTH:
                state.disabled = True
            if isinstance(result, ToolError):
                retry = kind in (
                    ProviderFailureKind.TRANSIENT, ProviderFailureKind.TIMEOUT, ProviderFailureKind.RATE_LIMIT,
                ) or (kind is None and result.diagnostic in (ReasonCode.TIMEOUT, ReasonCode.RATE_LIMITED))
                delay = result.retry_after_seconds or 0
                if background and retry:
                    state.failures = min(state.failures + 1, 6)
                    delay = max(delay, ceil(2 ** (state.failures - 1) * (1 + self._jitter())))
                    result = _error(result.diagnostic, result.error_class, delay)
                if delay:
                    horizon = datetime.max.replace(tzinfo=UTC)
                    state.retry_at = (horizon if delay >= (horizon - now).total_seconds()
                                      else now + timedelta(seconds=delay))
                    state.blocked = result
                if now >= deadline:
                    return timeout, attempt
                if not retry or background or attempt == 2 or delay >= (deadline - now).total_seconds():
                    return result, attempt
                if delay:
                    self._wait(delay)
                    now = self.clock.now()
                    if now >= deadline:
                        return timeout, attempt
                    if now < state.retry_at:
                        return result, attempt
            else:
                state.failures = 0
                state.retry_at = None
                state.blocked = None
                return (timeout if now >= deadline else result), attempt


type TransferRead = Callable[[CalculateTerminalTransferTimeInput], ToolResult[CalculateTerminalTransferTimeOutput]]


class ToolTurn:
    def __init__(
        self, conn: sqlite3.Connection, clock: Clock, policy: PolicyPack, *, event_id: str,
        airport: str, as_of: datetime | None = None, recovery: ReadRecovery | None = None,
        flights: FlightProvider | None = None, arrivals: ArrivalEstimateProvider | None = None,
        aircraft: AircraftProvider | None = None, transfer: TransferRead | None = None,
        confirmed_transfer: CalculateTerminalTransferTimeInput | None = None,
    ) -> None:
        if conn.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone() is None:
            raise ValueError("tool calls require an existing event")
        self.conn = conn
        self.clock = clock
        self.policy = policy
        self.event_id = event_id
        self.airport = airport
        start = clock.now()
        self.as_of = as_utc(as_of) if as_of is not None else start
        self.deadline = start + timedelta(seconds=policy.turn_wall_seconds)
        self.recovery = recovery if recovery is not None else ReadRecovery(clock)
        self.flights = flights if flights is not None else MockFlightProvider(conn, policy)
        self.arrivals = arrivals if arrivals is not None else MockArrivalEstimateProvider(conn, policy)
        self.aircraft = aircraft if aircraft is not None else MockAircraftProvider(conn, policy)
        self.transfer = transfer
        self.confirmed_transfer = confirmed_transfer
        self._reads = 0
        self._retried_model = False

    def claim_model_retry(self, error: ToolError) -> bool:
        if (error.diagnostic is not ReasonCode.INVALID_ARGUMENTS or self._retried_model
                or self.policy.model_retries == 0 or self._reads >= self.policy.turn_reads
                or self.clock.now() >= self.deadline):
            return False
        self._retried_model = True
        return True

    def call(self, tool_id: str, raw: str | bytes, *, background: bool = False) -> ToolResult[Contract]:
        started = self.clock.now()
        spec = TOOLS.get(tool_id) if isinstance(tool_id, str) else None
        rules = []
        attempts = 0
        if spec is None:
            result = _error(ReasonCode.UNSUPPORTED_INPUT)
        elif spec.policy_owned:
            result = _error(ReasonCode.UNAUTHORIZED_TOOL)
        else:
            arguments, rules = _arguments(spec, raw)
            if isinstance(arguments, ToolError):
                result = arguments
            elif self._reads >= self.policy.turn_reads or self.clock.now() >= self.deadline:
                result = _error(ReasonCode.BUDGET_EXHAUSTED)
            else:
                self._reads += 1
                result, attempts = self._run(arguments, rules, background=background)
                if isinstance(result, (Success, ToolError)):
                    try:
                        raw_result = result.model_dump_json(warnings="error")
                        result = _OUTPUTS[tool_id].validate_json(raw_result, strict=True)
                    except ValueError:
                        result = _error(ReasonCode.SOURCE_UNAVAILABLE)
                else:
                    result = _error(ReasonCode.SOURCE_UNAVAILABLE)
        ended = self.clock.now()
        error = result if isinstance(result, ToolError) else None
        outcome = ("error" if attempts else "refused") if error else ("repaired" if rules else "success")
        self.conn.execute("""
            INSERT INTO audit (event_id, occurred_at, action, outcome, tool_id, schema_version,
                               repair_rule_version, rule_id, error_class, attempts,
                               duration_seconds, reason_codes_json)
            VALUES (?, ?, 'tool_call', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.event_id, timestamp(ended), outcome, tool_id if spec is not None else "unknown",
              SCHEMA_VERSION, REPAIR_RULE_VERSION, "+".join(rules) or None,
              error.error_class if error else None, attempts,
              max(0, int((ended - started).total_seconds())),
              json.dumps([error.diagnostic] if error else [])))
        return result

    def _run(
        self, args: Contract, rules: list[str], *, background: bool,
    ) -> tuple[ToolResult[Contract], int]:
        if isinstance(args, GetFlightAndGateStatusInput):
            if args.as_of != self.as_of:
                return _error(ReasonCode.INVALID_ARGUMENTS), 0
            if args.airport != self.airport:
                return _error(ReasonCode.WRONG_AIRPORT, ToolErrorClass.MISSING), 0
            refs = []
            for ref in args.refs:
                leg = resolve_flight(self.conn, ref, self.airport, self.as_of)
                if isinstance(leg, ToolError):
                    return leg, 0
                refs.append(leg.id)
            if tuple(refs) != args.refs:
                rules.append("flight_alias")
                args = GetFlightAndGateStatusInput(refs=tuple(refs), airport=args.airport, as_of=args.as_of)
            return self.recovery.read(self.flights, lambda deadline: self.flights.flight_status(
                args, deadline=deadline,
            ), self.deadline, background=background)
        if isinstance(args, CalculateTerminalTransferTimeInput):
            if args.as_of != self.as_of:
                return _error(ReasonCode.INVALID_ARGUMENTS), 0
            if self.transfer is None:
                return _error(ReasonCode.ROUTE_UNAVAILABLE), 0
            if any(self.conn.execute(
                "SELECT 1 FROM nodes WHERE id = ? AND airport = ?", (node, self.airport),
            ).fetchone() is None for node in (args.origin_id, args.gate_id)):
                return _error(ReasonCode.LOCATION_UNKNOWN, ToolErrorClass.MISSING), 0
            if args != self.confirmed_transfer:
                return _error(ReasonCode.LOCATION_UNCONFIRMED, ToolErrorClass.MISSING), 0
            try:
                result = self.transfer(args)
            except Exception:
                result = _error(ReasonCode.SOURCE_UNAVAILABLE)
            if self.clock.now() >= self.deadline:
                result = _error(ReasonCode.TIMEOUT, ToolErrorClass.TIMEOUT)
            return result, 1
        if isinstance(args, GetArrivalEstimatesInput):
            leg = matched_leg(self.conn, args.arrival)
            if leg is None:
                return _error(ReasonCode.FLIGHT_UNKNOWN, ToolErrorClass.MISSING), 0
            if args.airport != self.airport or leg.destination != self.airport:
                return _error(ReasonCode.WRONG_AIRPORT, ToolErrorClass.MISSING), 0
            args = GetArrivalEstimatesInput(arrival=leg, airport=args.airport, journey_type=args.journey_type)
            return self.recovery.read(self.arrivals, lambda deadline: self.arrivals.arrival_estimates(
                args, as_of=self.as_of, deadline=deadline,
            ), self.deadline, background=background)
        if isinstance(args, GetAircraftPositionInput):
            leg = matched_leg(self.conn, args.leg)
            if leg is None:
                return _error(ReasonCode.AIRCRAFT_UNMATCHED, ToolErrorClass.MISSING), 0
            if self.airport not in (leg.origin, leg.destination):
                return _error(ReasonCode.WRONG_AIRPORT, ToolErrorClass.MISSING), 0
            return self.recovery.read(self.aircraft, lambda deadline: self.aircraft.aircraft_position(
                GetAircraftPositionInput(leg=leg), as_of=self.as_of, deadline=deadline,
            ), self.deadline, background=background)
        return _error(ReasonCode.UNAUTHORIZED_TOOL), 0
