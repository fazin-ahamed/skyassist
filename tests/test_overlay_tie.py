from datetime import UTC, datetime

from skyassist.models import Overlay
from skyassist.routing import _latest

T = datetime(2026, 11, 19, 6, tzinfo=UTC)


def overlay(source, closed):
    return Overlay(id=f"{source}-{closed}", airport="DXB", edge_id="e", source=source, observed_at=T,
                   valid_until=datetime(2026, 11, 19, 12, tzinfo=UTC), closed=closed)


def test_same_source_same_instant_later_report_wins():
    latest = _latest([("e", overlay("ops", True)), ("e", overlay("ops", False))], T)
    assert [o.closed for o in latest["e"]] == [False]


def test_different_sources_same_instant_stay_in_conflict():
    latest = _latest([("e", overlay("ops", True)), ("e", overlay("other", False))], T)
    assert sorted(o.closed for o in latest["e"]) == [False, True]
