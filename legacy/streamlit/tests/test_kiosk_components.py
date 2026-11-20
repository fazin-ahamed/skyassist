import json
import re
from pathlib import Path

import pytest

from kiosk import components as C
from kiosk import demo_views as D
from kiosk.components import Lang
from skyassist.models import ViewModel

I18N = Path(__file__).resolve().parents[1] / "data" / "i18n"
EM = chr(8212)
PARAM = re.compile(r"\{(\w+)\}")


def _render_all(view: ViewModel, L: Lang) -> str:
    out = [
        C.gate_plate(view.gate, L), C.cutoff_block(view.cutoff, L), C.status_block(view, L), C.transfer_block(view, L),
        C.next_action_block(view, L), C.banners_block(view, L), C.assistance_block(view.assistance, L),
        C.qr_block(view, L, "http://kiosk.local:8000"), C.aircraft_block(view, L), C.arrivals_block(view, L),
    ]
    if view.route:
        out += [C.route_map(view.route, L), C.route_steps(view.route, L)]
    return "".join(out)


@pytest.mark.parametrize("lang", ["en", "ar"])
@pytest.mark.parametrize("name", list(D.STATES))
def test_every_state_renders(name, lang):
    html = _render_all(D.STATES[name], Lang(lang))
    assert EM not in html
    assert "<script" not in html.lower()


def test_states_roundtrip_contract():
    for view in D.STATES.values():
        assert ViewModel.model_validate_json(view.model_dump_json()) == view


def test_c01_shows_exact_range_and_status():
    html = _render_all(D.STATES["c01_pending"], Lang("en"))
    assert "21-29 min total, including readiness" in html
    assert "Tight connection" in html
    assert "6 min" in html


def test_unverified_states_withhold_margin_and_instructions():
    for name in ("c05_no_cutoff", "c10_queue_stale", "c06_stale", "c06_conflict"):
        html = _render_all(D.STATES[name], Lang("en"))
        assert "sa-transfer" not in html
    stale = _render_all(D.STATES["c06_stale"], Lang("en"))
    assert "sa-steps" not in stale and "sa-qr__code" not in stale and "B14" not in stale


def test_html_escapes_untrusted_text():
    row = C.BoardRow("<img src=x onerror=1>", "<b>x</b>", "10:00", "B1", "ok", "ok")
    html = C.board_cells(row, Lang("en"))
    assert "<img" not in html and "<b>" not in html


def test_kiosk_i18n_param_parity_and_no_dash():
    en = json.loads((I18N / "en" / "kiosk.json").read_text(encoding="utf-8"))
    ar = json.loads((I18N / "ar" / "kiosk.json").read_text(encoding="utf-8"))
    ar.pop("_review")
    assert en.keys() == ar.keys()
    for key in en:
        assert set(PARAM.findall(en[key])) == set(PARAM.findall(ar[key])), key
        assert EM not in en[key] + ar[key]
