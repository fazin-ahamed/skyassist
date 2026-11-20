import json
import re
import string
from pathlib import Path

import pytest

from skyassist import i18n
from skyassist.models import (
    AccessibleMode, AircraftFreshness, Applicability, AssistanceService, BannerKind, CaseState,
    ConnectionFlag, ConnectionStatus, DispatchState, I18nText, ObservationState, PassengerAction,
    ReasonCode,
)

ROOT = Path(__file__).resolve().parents[1] / "data" / "i18n"
DASHES = (chr(0x2014), chr(0x2015))
ARABIC_LETTER = re.compile(r"[؀-ۿ]")
LRI, PDI = chr(0x2066), chr(0x2069)
LATIN_BY_DESIGN = {"ui.lang_toggle"}
IDENTICAL_BY_DESIGN = {"ui.brand"}
SAMPLES = {
    "gate": "B14", "flight": "DXB-402", "code": "B14", "time": "10:35", "margin": -4,
    "lower": 21, "upper": 29, "seconds": 30, "source": "demo-policy",
    "date": "2026-11-19", "origin": "FRA", "destination": "DXB", "old": "B12", "wait": 20, "n": 2, "total": 6, "used": 3, "seats": 4, "terminal": "3", "name": "OAG_API_KEY", "status": "Active",
}
PLAN_STRINGS = {
    "assistance.pending_approval": "Requesting physical assistance",
    "assistance.approved": "Assistance approved. Awaiting dispatch confirmation.",
    "assistance.dispatch_pending_verification": "Staff are checking the dispatch.",
    "assistance.dispatched": "Assistance dispatched.",
    "assistance.completed": "Assistance complete",
    "assistance.gate_hold_submitted": "Hold request submitted for review",
    "ui.request_assistance": "Request physical assistance",
}


def fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def enum_keys() -> set[str]:
    keys = {s.i18n_key for s in ConnectionStatus} | {f.i18n_key for f in ConnectionFlag}
    keys |= {r.i18n_key for r in ReasonCode}
    keys |= {f"banner.{b}" for b in BannerKind}
    keys |= {f"action.{a}" for a in PassengerAction}
    keys |= {f"assistance.{c}" for c in CaseState} | {f"assistance.stage.{c}" for c in CaseState}
    keys |= {f"mode.{m}" for m in AccessibleMode}
    keys |= {f"assistance.service.{s}" for s in AssistanceService}
    keys |= {f"dispatch.{d}" for d in DispatchState}
    keys |= {f"ui.freshness.{o}" for o in ObservationState}
    keys |= {f"arrivals.{a}" for a in Applicability}
    keys |= {f"ui.aircraft_freshness.{a}" for a in AircraftFreshness}
    return keys


def test_enum_keys_exist_in_both_languages():
    for lang in ("en", "ar"):
        missing = sorted(k for k in enum_keys() if not i18n.has(k, lang))
        assert not missing, f"{lang} missing {missing}"


def test_key_sets_match_between_languages():
    en, ar = set(i18n.catalog("en")), set(i18n.catalog("ar"))
    assert en == ar, (sorted(en - ar), sorted(ar - en))


def test_params_match_between_languages():
    en, ar = i18n.catalog("en"), i18n.catalog("ar")
    for key in en.keys() & ar.keys():
        assert fields(en[key]) == fields(ar[key]), key


def test_no_dash_characters_in_catalog_files():
    paths = list(ROOT.glob("*/*.json"))
    assert paths, "catalog files missing"
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert not any(d in text for d in DASHES), path.name


def test_arabic_values_contain_arabic_letters_except_by_design():
    for key, text in i18n.catalog("ar").items():
        if key not in LATIN_BY_DESIGN | IDENTICAL_BY_DESIGN:
            assert ARABIC_LETTER.search(text), key


def test_identical_by_design_keys_match():
    en, ar = i18n.catalog("en"), i18n.catalog("ar")
    for key in IDENTICAL_BY_DESIGN:
        assert en[key] == ar[key], key


def test_gate_flight_and_time_params_are_isolated_in_arabic():
    assert i18n.t("route.approach_gate", "ar", gate="B14") == f"توجّه إلى البوابة {LRI}B14{PDI}."
    for key, text in i18n.catalog("ar").items():
        for name in fields(text) & {"gate", "flight", "code", "time"}:
            rendered = i18n.t(key, "ar", **{n: SAMPLES[n] for n in fields(text)})
            assert f"{LRI}{SAMPLES[name]}{PDI}" in rendered, key


@pytest.mark.parametrize("status", list(ConnectionStatus), ids=lambda s: s.name)
def test_status_labels_are_exact_plan_labels_in_english(status):
    assert i18n.t(status.i18n_key, "en") == status.value


def test_cutoff_flag_label_is_exact_in_english():
    assert i18n.t(ConnectionFlag.CUTOFF_UNAVAILABLE.i18n_key, "en") == "Boarding cutoff unavailable"


@pytest.mark.parametrize(("key", "text"), PLAN_STRINGS.items())
def test_plan_verbatim_strings_in_english(key, text):
    assert i18n.t(key, "en") == text


def test_transfer_range_renders_exactly():
    text = I18nText(key="transfer.total_with_readiness", params={"lower": 21, "upper": 29})
    assert i18n.render(text, "en") == "21-29 min total, including readiness"


def test_negative_margin_keeps_its_sign():
    assert i18n.t("transfer.margin", "en", margin=-4) == "-4 min conservative margin"


def test_unknown_key_raises_with_key():
    with pytest.raises(KeyError) as err:
        i18n.t("no.such.key", "en")
    assert err.value.args == ("no.such.key",)


def test_missing_param_raises_with_key_and_no_english_fallback():
    with pytest.raises(KeyError) as err:
        i18n.t("route.approach_gate", "ar")
    assert err.value.args == ("route.approach_gate",)


def test_english_copy_avoids_marketing_words():
    banned = ("seamless", "powered", "revolutionary")
    for text in i18n.catalog("en").values():
        assert not any(word in text.lower() for word in banned), text


def test_loader_merges_every_json_file_in_language_folder():
    for lang in i18n.languages():
        for path in (ROOT / lang).glob("*.json"):
            for key, value in json.loads(path.read_text(encoding="utf-8")).items():
                if not key.startswith("_"):
                    assert i18n.catalog(lang)[key] == value, (lang, key)


def test_arabic_is_marked_unreviewed():
    assert i18n.review_status("ar") == "unreviewed"
    for path in (ROOT / "ar").glob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["_review"] == "unreviewed", path.name
