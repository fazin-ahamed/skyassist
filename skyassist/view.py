from datetime import datetime
from html import escape

from skyassist.i18n import render
from skyassist.ledger import build_ledger, verdict_for
from skyassist.models import (
    AccessibleMode, AircraftReport, AircraftView, ArrivalEstimates, AssistanceService,
    AssistanceView, BannerKind, BannerView, Case, CaseState, ConnectionFlag, ConnectionStatus,
    CutoffView, GateView, I18nText, NextActionView, ObservationState, PassengerAction,
    PermittedChoice, PolicyPack, QRView, ReasonCode, RouteView, SourceStamp, StatusView,
    TimeWindow, TransferResult, TransferView, ViewModel,
)

R = ReasonCode
A = PassengerAction
HANDOFF_PENDING = frozenset({CaseState.PENDING_APPROVAL, CaseState.APPROVED,
                             CaseState.DISPATCH_PENDING_VERIFICATION, CaseState.DISPATCHED})


def reviewed_text(text: I18nText, fallback: I18nText) -> I18nText:
    text = I18nText(key=text.key, params={
        key: escape(value) if isinstance(value, str) else value for key, value in text.params.items()
        if isinstance(value, (str, int, float))
    })
    try:
        render(text, "en")
        render(text, "ar")
    except (KeyError, ValueError, TypeError):
        return fallback
    return text


def build_view(
    *, revision: int, as_of: datetime, policy: PolicyPack, language: str = "en",
    gate: GateView = GateView(), cutoff: CutoffView = CutoffView(), result: TransferResult | None = None,
    case: Case | None = None, reasons: tuple[ReasonCode, ...] = (),
    choices: tuple[PermittedChoice, ...] = (), banners: tuple[BannerKind, ...] = (),
    route_token: str | None = None, arrivals: ArrivalEstimates = ArrivalEstimates(),
    aircraft: AircraftReport | None = None,
) -> ViewModel:
    stamp = SourceStamp(source=policy.policy_version, observed_at=as_of)
    gate_reason = {ObservationState.STALE: R.GATE_STALE, ObservationState.CONFLICT: R.GATE_CONFLICT,
                   ObservationState.UNKNOWN: R.GATE_UNKNOWN}.get(gate.state)
    pending = case is not None and case.service != AssistanceService.GATE_HOLD and case.state in HANDOFF_PENDING
    reason_codes = tuple(dict.fromkeys((
        *reasons, *(result.reason_codes if result else ()), *(case.reason_codes if case else ()),
        *((gate_reason,) if gate_reason else ()), *((R.STAFF_HANDOFF_PENDING,) if pending else ()),
    )))
    route = None
    if result and result.route and gate_reason is None:
        raw = result.route
        route = RouteView(
            route_id=raw.route_id, revision=revision, provenance=raw.provenance,
            floor_shapes=raw.floor_shapes, floor_outlines=raw.floor_outlines,
            steps=tuple(step.model_copy(update={
                "landmark": reviewed_text(step.landmark, I18nText(key="kiosk.floor", params={"floor": step.floor})),
                "text": reviewed_text(step.text, I18nText(key="route.walk_to")),
            }) for step in raw.steps),
        )
    assistance = None
    if case:
        stage = f"assistance.{case.state}"
        if case.service == AssistanceService.GATE_HOLD and case.state in (CaseState.PENDING_APPROVAL, CaseState.APPROVED):
            stage = "assistance.gate_hold_submitted"
        assistance = AssistanceView(
            state=case.state, stage=I18nText(key=stage), case_ref=case.id,
            pickup_landmark=reviewed_text(case.pickup.landmark, I18nText(key="action.confirm_pickup"))
            if case.pickup else None,
            staffed_alternative=I18nText(key="action.seek_staff"),
            approval_window=TimeWindow(start=case.created_at, end=case.approval_expires_at)
            if case.approval_expires_at else None,
            provenance=SourceStamp(source="assistance", observed_at=as_of),
        )
    status = result.status if result else ConnectionStatus.TIMING_UNVERIFIED
    if gate_reason and status != ConnectionStatus.BOARDING_CUTOFF_PASSED:
        status = ConnectionStatus.TIMING_UNVERIFIED
    transfer = TransferView.from_result(result, provenance=stamp) if route else None
    action, cause = A.WAIT_FOR_UPDATE, R.SOURCE_UNAVAILABLE
    for action_kind, triggers in (
        (A.SEEK_EMERGENCY_HELP, (R.URGENT_SAFETY,)),
        (A.SEEK_STAFF, (R.CUTOFF_PASSED, R.FLIGHT_CANCELLED, R.LOWER_BOUND_EXCEEDS_CUTOFF, R.NEGATIVE_MARGIN)),
        (A.CONFIRM_DATE, (R.AMBIGUOUS_DATE,)),
        (A.CHOOSE_FLIGHT, (R.AMBIGUOUS_FLIGHT, R.FLIGHT_UNKNOWN, R.WRONG_AIRPORT, R.OUTBOUND_MISSING)),
        (A.WAIT_FOR_HANDOFF, (R.STAFF_HANDOFF_PENDING, R.UNACCOMPANIED_MINOR)),
        (A.CHOOSE_LANDMARK, (R.AMBIGUOUS_LANDMARK, R.LOCATION_UNCONFIRMED, R.LOCATION_UNKNOWN)),
        (A.CONFIRM_PICKUP, (R.PICKUP_UNCONFIRMED, R.AMBIGUOUS_PICKUP, R.CONSENT_MISSING)),
        (A.SEEK_STAFF, (R.NO_ACCESSIBLE_PATH, R.NO_PATH, R.PICKUP_INELIGIBLE, R.MAP_UNAVAILABLE)),
        (A.REFRESH_ROUTE, (R.ROUTE_EXPIRED, R.ROUTE_REVOKED)),
        (A.CHECK_GATE, (R.GATE_STALE, R.GATE_CONFLICT, R.GATE_UNKNOWN, R.CUTOFF_MISSING)),
        (A.WAIT_FOR_UPDATE, (R.QUEUE_BOUND_STALE, R.QUEUE_BOUND_MISSING)),
    ):
        if trigger := next((item for item in triggers if item in reason_codes), None):
            action, cause = action_kind, trigger
            break
    else:
        if route:
            action, cause = A.FOLLOW_ROUTE, next(iter(reason_codes), None)
        elif reason_codes:
            cause = reason_codes[0]
    if gate_reason in (R.GATE_STALE, R.GATE_CONFLICT) or R.DATA_STALE in reason_codes:
        banners = (*banners, BannerKind.STALE_DATA)
    suppression = gate_reason or (R.STAFF_HANDOFF_PENDING if pending else R.ROUTE_UNAVAILABLE)
    public = (route and not pending and not any(step.accessible_mode == AccessibleMode.STAFF_HANDOFF
                                              for step in route.steps)
              and (action == A.FOLLOW_ROUTE or cause == R.ROUTE_REVOKED))
    translations = {"status": I18nText(key=status.i18n_key),
                    "next_action_reason": I18nText(key=cause.i18n_key if cause else "action.follow_route")}
    if cutoff.unavailable:
        translations["cutoff_flag"] = I18nText(key=ConnectionFlag.CUTOFF_UNAVAILABLE.i18n_key)
    if transfer and transfer.includes_readiness and transfer.upper_min is not None:
        translations["transfer"] = I18nText(key="transfer.total_with_readiness", params={
            "lower": transfer.lower_min, "upper": transfer.upper_min,
        })
    ledger = build_ledger(
        gate=gate, cutoff=cutoff, transfer=transfer, has_route=route is not None,
        case_state=case.state if case and case.service != AssistanceService.GATE_HOLD else None,
    )
    # a verdict is only as good as the weakest fact under it
    verified = all(p.state.value == "verified" for p in ledger if p.key in ("gate", "cutoff", "transfer"))
    return ViewModel(
        revision=revision, as_of=as_of, language=language, gate=gate, cutoff=cutoff,
        verdict=verdict_for(status, transfer, verified=verified), ledger=ledger,
        status=StatusView(status=status, reason_codes=reason_codes, provenance=stamp),
        transfer=transfer, route=route, assistance=assistance,
        next_action=NextActionView(action=action, text=I18nText(key=f"action.{action}"), choices=choices),
        banners=tuple(BannerView(kind=kind, text=I18nText(key=f"banner.{kind}"))
                      for kind in dict.fromkeys(banners)),
        qr=QRView(token=route_token) if public and route_token else QRView(suppressed_reason=suppression),
        arrivals=arrivals, aircraft=AircraftView.from_report(aircraft, as_of=as_of, policy=policy),
        i18n=translations,
    )
