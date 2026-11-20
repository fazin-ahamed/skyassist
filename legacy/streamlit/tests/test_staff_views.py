from datetime import UTC, datetime

from kiosk.components import Lang
from kiosk.demo_views import cutoff, gate
from skyassist.models import Case, CaseState, Location, ReasonCode, StaffCaseItem, AssistanceService, I18nText
from staff import views as V

NOW = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)
EM = chr(8212)


def item(state=CaseState.PENDING_APPROVAL, confirmed=True, remaining=2100):
    pickup = Location(airport="DXB", node_id="c22", landmark=I18nText(key="demo.landmark.c22")) if confirmed else None
    case = Case(
        id="abcdef123456", journey_id="j1", airport="DXB", service=AssistanceService.WHEELCHAIR, state=state,
        request_revision=1, journey_revision=2, created_at=NOW, idempotency_key="k1", pickup=pickup,
        pickup_confirmed=confirmed, consent=True, approval_expires_at=NOW,
        reason_codes=(ReasonCode.STAFF_HANDOFF_PENDING,),
    )
    return StaffCaseItem(case=case, gate=gate(), cutoff=cutoff(), time_remaining=remaining)


def test_row_and_detail_render():
    L = Lang("en")
    for s in CaseState:
        html = V.case_row(item(s), L, True) + V.case_detail(item(s), L)
        assert EM not in html and "<script" not in html


def test_detail_shows_facts():
    html = V.case_detail(item(), Lang("en"))
    assert "Gate C22" in html and "B14" in html and "35 min" in html and "abcdef12" in html


def test_unconfirmed_pickup_blocks_with_warning():
    html = V.case_detail(item(confirmed=False), Lang("en"))
    assert "sa-banner--tight" in html


def test_cutoff_passed_and_missing():
    L = Lang("en")
    assert "Boarding cutoff passed" in V.time_left(item(remaining=-5), L)
    assert "Boarding cutoff unavailable" in V.time_left(item(remaining=None), L)
