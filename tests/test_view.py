from datetime import timedelta

import pytest
from pydantic import BaseModel

from skyassist.clock import SimClock
from skyassist.i18n import render
from skyassist.models import (
    AircraftReport, ArrivalEstimate, ArrivalEstimates, BannerKind, Case, CaseState, ConnectionStatus,
    CutoffView, GateView, I18nText, Location, PassengerAction, PermittedChoice,
    PolicyPack, ReasonCode, RouteResult, RouteStep, SecondsRange, SourceStamp, TransferResult,
)
from skyassist.view import build_view

NOW = SimClock().now()
STAMP = SourceStamp(source="inline", observed_at=NOW)
GATE = GateView(value="B14", state="fresh", provenance={"value": STAMP})
RESULT = TransferResult(
    status="Tight connection", ready=SecondsRange(lower=240, upper=360), lower=1260, upper=1740,
    margin=360, time_remaining=2100, reason_codes=(ReasonCode.TIGHT_MARGIN,),
    route=RouteResult(route_id="route", revision=1, provenance=STAMP, node_ids=("c22", "b14"),
                      movement=SecondsRange(lower=420, upper=540), wait=SecondsRange(lower=180, upper=420),
                      buffer=420, steps=(RouteStep(number=1, floor="2", landmark=I18nText(key="demo.landmark.b14"),
                                                   accessible_mode="step_free", text=I18nText(
                                                       key="route.approach_gate", params={"gate": "B14"})),)),
)


def build(**kwargs):
    return build_view(**(dict(revision=2, as_of=NOW, policy=PolicyPack(), gate=GATE) | kwargs))


def assert_catalog(value):
    if isinstance(value, I18nText):
        assert render(value, "en") and render(value, "ar")
    elif isinstance(value, BaseModel):
        for _, item in value:
            assert_catalog(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_catalog(item)
    elif isinstance(value, dict):
        for item in value.values():
            assert_catalog(item)


@pytest.mark.parametrize("state,suppression", [("stale", "GATE_STALE"), ("conflict", "GATE_CONFLICT"),
                                                ("unknown", "GATE_UNKNOWN")])
def test_invalid_gate_never_routes(state, suppression):
    candidate = build(gate=GateView(state=state), result=RESULT, route_token="must-not-leak")
    assert candidate.route is None and candidate.transfer is None
    assert candidate.qr.token is None and candidate.qr.suppressed_reason == suppression
    assert candidate.next_action.action == "check_gate"
    assert_catalog(candidate)


@pytest.mark.parametrize("reason,action", [
    (ReasonCode.FLIGHT_UNKNOWN, "choose_flight"), (ReasonCode.AMBIGUOUS_DATE, "confirm_date"),
    (ReasonCode.AMBIGUOUS_LANDMARK, "choose_landmark"), (ReasonCode.PICKUP_UNCONFIRMED, "confirm_pickup"),
    (ReasonCode.STAFF_HANDOFF_PENDING, "wait_for_handoff"), (ReasonCode.NO_ACCESSIBLE_PATH, "seek_staff"),
    (ReasonCode.ROUTE_EXPIRED, "refresh_route"), (ReasonCode.URGENT_SAFETY, "seek_emergency_help"),
    (ReasonCode.SOURCE_UNAVAILABLE, "wait_for_update"),
])
def test_action_and_localized_reason(reason, action):
    view = build(reasons=(reason,))
    assert view.next_action.action == action
    assert view.i18n["next_action_reason"].key == reason.i18n_key
    assert_catalog(view)


def test_view_maps_transfer_cutoff_arrivals_aircraft_and_all_banners():
    estimate = ArrivalEstimate(applicability="applicable", state="fresh", wait=SecondsRange(lower=60, upper=120),
                               provenance=STAMP)
    aircraft = AircraftReport(matched_leg_id="in", source="inline", observed_at=NOW - timedelta(seconds=61),
                              position={"latitude": 25, "longitude": 55})
    view = build(result=RESULT, cutoff=CutoffView(value=NOW + timedelta(minutes=35), source="inline",
                                                 observed_at=NOW, unavailable=False),
                 route_token="test", banners=tuple(BannerKind),
                 arrivals=ArrivalEstimates(immigration=estimate), aircraft=aircraft)
    assert view.next_action.action == "follow_route" and view.qr.token == "test"
    assert (view.transfer.lower_min, view.transfer.upper_min, view.transfer.margin_min) == (21, 29, 6)
    assert view.route.revision == 2 and view.cutoff.value == NOW + timedelta(minutes=35)
    assert view.arrivals.baggage is None and view.arrivals.security is None and view.arrivals.immigration == estimate
    assert view.aircraft.freshness == "last_reported"
    assert build(as_of=NOW + timedelta(seconds=119), aircraft=aircraft).aircraft.position is None
    assert_catalog(view)
    for status in ConnectionStatus:
        assert_catalog(build(result=RESULT.model_copy(update={"status": status})))
    for reason in ReasonCode:
        assert_catalog(build(reasons=(reason,)))
    for action in PassengerAction:
        assert_catalog(I18nText(key=f"action.{action}"))


@pytest.mark.parametrize("state", list(CaseState))
def test_all_assistance_stages_use_reviewed_text(state):
    case = Case(id="case", journey_id="journey", airport="DXB", service="wheelchair", state=state,
                request_revision=1, journey_revision=2, created_at=NOW, idempotency_key="key",
                pickup=Location(airport="DXB", node_id="c22", landmark=I18nText(key="demo.landmark.c22")))
    view = build(case=case, choices=(PermittedChoice(id="c22", label=case.pickup.landmark),))
    assert view.assistance.state == state
    assert_catalog(view)
    assert_catalog(build(case=case.model_copy(update={"service": "gate_hold"})))
