"""Typed demo view models, one per screen state. Validated by the real ViewModel contract."""

from __future__ import annotations

from datetime import UTC, datetime

from skyassist.models import (
    AccessibleMode as M,
    AircraftView,
    ArrivalEstimate,
    ArrivalEstimates,
    Applicability,
    AssistanceView,
    BannerKind,
    BannerView,
    CaseState,
    ConnectionStatus as S,
    CutoffView,
    FloorShape,
    GateView,
    I18nText,
    MapPoint,
    NextActionView,
    ObservationState,
    PassengerAction as A,
    QRView,
    ReasonCode as R,
    RouteStep,
    RouteView,
    SecondsRange,
    SourceStamp,
    StatusView,
    TimeWindow,
    TransferView,
    ViewModel,
)

NOW = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)
CUTOFF = datetime(2026, 11, 19, 6, 35, tzinfo=UTC)
STAMP = SourceStamp(source="demo-feed", observed_at=NOW)
POLICY = SourceStamp(source="demo-policy", observed_at=NOW)


def tx(key: str, **params) -> I18nText:
    return I18nText(key=key, params=params)


def gate(value="B14", concourse="B", level="2", state=ObservationState.FRESH) -> GateView:
    if state is not ObservationState.FRESH:
        return GateView(state=state)
    prov = {k: STAMP for k in ("value", "concourse", "level")}
    return GateView(value=value, concourse=concourse, level=level, state=state, provenance=prov)


def cutoff(at=CUTOFF) -> CutoffView:
    return CutoffView(value=at, source="airline-feed", observed_at=NOW, unavailable=False)


def step(n, floor, landmark, mode, key, **params) -> RouteStep:
    return RouteStep(number=n, floor=floor, landmark=tx(f"demo.landmark.{landmark}"), accessible_mode=mode, text=tx(key, **params))


def shape(floor, *pts) -> FloorShape:
    return FloorShape(terminal="1", concourse="B", floor=floor, points=tuple(MapPoint(x=x, y=y) for x, y in pts))


def route(rid, rev, steps, shapes) -> RouteView:
    return RouteView(
        route_id=rid, revision=rev, steps=tuple(steps), floor_shapes=tuple(shapes), provenance=STAMP,
        simulation_label=tx("route.simulation_fictional_terminal"),
    )


SHAPES = (
    shape("2", (0, 60), (40, 60), (70, 20), (100, 20)),
    shape("1", (0, 30), (50, 30), (100, 30)),
)
ROUTE_C01 = route("c01-b14", 2, [
    step(1, "2", "c22", M.STAFF_HANDOFF, "route.wait_at_pickup"),
    step(2, "2", "lift_c", M.LIFT, "route.take_lift"),
    step(3, "1", "train_c", M.TRANSIT, "route.board_train"),
    step(4, "1", "train_b", M.TRANSIT, "route.exit_train"),
    step(5, "2", "b14", M.STEP_FREE, "route.approach_gate", gate="B14"),
], SHAPES)
ROUTE_DIRECT = route("c03-b14", 1, [
    step(1, "2", "b14", M.WALK, "route.walk_to"),
    step(2, "2", "walkway_b", M.WALK, "route.walk_to"),
    step(3, "2", "b14", M.WALK, "route.approach_gate", gate="B14"),
], SHAPES[:1])
ROUTE_B18 = route("c02-b18", 3, [
    step(1, "2", "c22", M.STAFF_HANDOFF, "route.wait_at_pickup"),
    step(2, "2", "lift_c", M.LIFT, "route.take_lift"),
    step(3, "1", "train_c", M.TRANSIT, "route.board_train"),
    step(4, "1", "train_b", M.TRANSIT, "route.exit_train"),
    step(5, "2", "b18", M.STEP_FREE, "route.approach_gate", gate="B18"),
], SHAPES)
ROUTE_CHECKPOINT = route("c10-b14", 1, [
    step(1, "2", "a7", M.WALK, "route.walk_to"),
    step(2, "2", "security_b", M.CHECKPOINT, "route.cross_checkpoint"),
    step(3, "2", "b14", M.WALK, "route.approach_gate", gate="B14"),
], SHAPES[:1])


def assist(state: CaseState, stage: str, alt: bool = True) -> AssistanceView:
    return AssistanceView(
        state=state, stage=tx(stage), provenance=STAMP, pickup_landmark=tx("demo.landmark.c22"), case_ref="demo-c01",
        staffed_alternative=tx("action.seek_staff") if alt else None,
        approval_window=TimeWindow(start=NOW, end=datetime(2026, 11, 19, 6, 5, tzinfo=UTC)),
    )


def view(status: S, reasons=(), *, transfer=None, rt=None, gt=None, co=None, assistance=None, action=A.FOLLOW_ROUTE,
         banners=(), token=None, qr_reason=R.ROUTE_UNAVAILABLE, rev=1, **extra) -> ViewModel:
    return ViewModel(
        revision=rev, as_of=NOW, gate=gt or gate(), cutoff=co or cutoff(),
        status=StatusView(status=status, reason_codes=tuple(reasons), provenance=POLICY),
        transfer=transfer, route=rt, assistance=assistance,
        next_action=NextActionView(action=action, text=tx(f"action.{action.value}")),
        banners=tuple(banners),
        qr=QRView(token=token) if token else QRView(suppressed_reason=qr_reason),
        **extra,
    )


def transfer(lo, hi, margin, conditional=False) -> TransferView:
    return TransferView(lower_min=lo, upper_min=hi, includes_readiness=True, margin_min=margin, conditional=conditional, provenance=POLICY)


CHANGED = BannerView(kind=BannerKind.ROUTE_CHANGED, text=tx("banner.route_changed"))
STALE = BannerView(kind=BannerKind.STALE_DATA, text=tx("banner.stale_data"))
PENDING = assist(CaseState.PENDING_APPROVAL, "assistance.stage.pending_approval")
C01 = dict(transfer=transfer(21, 29, 6, True), rt=ROUTE_C01, action=A.WAIT_FOR_HANDOFF, qr_reason=R.STAFF_HANDOFF_PENDING)

STATES: dict[str, ViewModel] = {
    "c01_pending": view(S.TIGHT_CONNECTION, [R.TIGHT_MARGIN, R.STAFF_HANDOFF_PENDING], assistance=PENDING, banners=[CHANGED], rev=2, **C01),
    "c01_approved": view(S.TIGHT_CONNECTION, [R.TIGHT_MARGIN], assistance=assist(CaseState.APPROVED, "assistance.stage.approved"), rev=3, **C01),
    "c01_verifying": view(S.TIGHT_CONNECTION, [R.TIGHT_MARGIN], assistance=assist(CaseState.DISPATCH_PENDING_VERIFICATION, "assistance.stage.dispatch_pending_verification"), rev=4, **C01),
    "c01_dispatched": view(S.TIGHT_CONNECTION, [R.TIGHT_MARGIN], assistance=assist(CaseState.DISPATCHED, "assistance.stage.dispatched"), rev=5, **C01),
    "c01_declined": view(S.TIGHT_CONNECTION, [R.TIGHT_MARGIN, R.APPROVAL_DECLINED], assistance=assist(CaseState.DECLINED, "assistance.stage.declined"), action=A.SEEK_STAFF, rev=4, transfer=transfer(21, 29, 6), rt=ROUTE_C01),
    "c02_rerouted": view(S.TIME_AVAILABLE, [R.GATE_CHANGED], gt=gate("B18"), transfer=transfer(10, 16, 16, True), rt=ROUTE_B18, assistance=PENDING, banners=[CHANGED], action=A.WAIT_FOR_HANDOFF, qr_reason=R.STAFF_HANDOFF_PENDING, rev=6),
    "c03_available": view(S.TIME_AVAILABLE, [], transfer=transfer(8, 11, 29), rt=ROUTE_DIRECT, token="demo-token-c03a1b2c"),
    "c04_unlikely": view(S.TRANSFER_UNLIKELY_BEFORE_CUTOFF, [R.LOWER_BOUND_EXCEEDS_CUTOFF], co=cutoff(datetime(2026, 11, 19, 6, 8, tzinfo=UTC)), transfer=transfer(12, 22, -14), rt=ROUTE_DIRECT, action=A.SEEK_STAFF, assistance=None, qr_reason=R.ROUTE_UNAVAILABLE),
    "c04_passed": view(S.BOARDING_CUTOFF_PASSED, [R.CUTOFF_PASSED], co=cutoff(datetime(2026, 11, 19, 5, 55, tzinfo=UTC)), action=A.SEEK_STAFF),
    "c05_no_cutoff": view(S.TIMING_UNVERIFIED, [R.CUTOFF_MISSING], co=CutoffView(), rt=ROUTE_DIRECT, token="demo-token-c05d4e5f"),
    "c06_stale": view(S.TIMING_UNVERIFIED, [R.GATE_STALE], gt=gate(state=ObservationState.STALE), action=A.CHECK_GATE, banners=[STALE], qr_reason=R.GATE_STALE),
    "c06_conflict": view(S.TIMING_UNVERIFIED, [R.GATE_CONFLICT], gt=gate(state=ObservationState.CONFLICT), action=A.CHECK_GATE, banners=[STALE], qr_reason=R.GATE_CONFLICT),
    "c08_lift_closed": view(S.TIMING_UNVERIFIED, [R.LIFT_CLOSED, R.NO_ACCESSIBLE_PATH], action=A.SEEK_STAFF, qr_reason=R.NO_ACCESSIBLE_PATH),
    "c10_queue_stale": view(S.TIMING_UNVERIFIED, [R.QUEUE_BOUND_STALE], rt=ROUTE_CHECKPOINT, token="demo-token-c10a6b7c"),
    "arrivals": view(
        S.TIMING_UNVERIFIED, [R.ESTIMATE_UNAVAILABLE], rt=None,
        arrivals=ArrivalEstimates(
            baggage=ArrivalEstimate(applicability=Applicability.APPLICABLE, state=ObservationState.FRESH, wait=SecondsRange(lower=900, upper=1500), provenance=STAMP),
            immigration=ArrivalEstimate(applicability=Applicability.UNKNOWN, state=ObservationState.UNKNOWN),
            security=ArrivalEstimate(applicability=Applicability.NOT_APPLICABLE, state=ObservationState.FRESH),
        ),
    ),
}
STATE_LABELS = {k: k.replace("_", " ") for k in STATES}
AIRCRAFT_DEMO = AircraftView()
