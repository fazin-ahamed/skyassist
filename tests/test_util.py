import inspect
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skyassist.util.maps_url import maps_pin_url
from skyassist.util.qr import QUIET_ZONE, contrast_ratio, qr_png_bytes, qr_svg


def test_maps_pin_url_rounds_to_six_decimals():
    assert maps_pin_url(25.25321234567, 55.3) == (
        "https://www.google.com/maps/search/?api=1&query=25.253212%2C55.300000"
    )


@pytest.mark.parametrize(
    "lat, lon, query",
    [
        (90, 180, "90.000000%2C180.000000"),
        (-90, -180, "-90.000000%2C-180.000000"),
    ],
)
def test_maps_pin_url_accepts_boundaries(lat, lon, query):
    assert maps_pin_url(lat, lon).endswith("query=" + query)


@pytest.mark.parametrize(
    "lat, lon",
    [
        (90.000001, 0),
        (-90.1, 0),
        (0, 180.5),
        (0, -180.1),
        (math.nan, 0),
        (0, math.nan),
        (math.inf, 0),
        (0, -math.inf),
    ],
)
def test_maps_pin_url_rejects_invalid(lat, lon):
    with pytest.raises(ValueError):
        maps_pin_url(lat, lon)


def test_qr_svg_structure_and_quiet_zone():
    # 50 bytes need version 3 at level L and version 4 at level Q
    root = ET.fromstring(qr_svg("A" * 50, label="Gate B14"))
    assert root.tag == "svg"
    assert root.get("role") == "img"
    assert root.find("title").text == "Gate B14"
    assert root.get("viewBox") == "0 0 41 41"
    modules = root.findall("path[@class='qrline']")
    assert modules[0].get("d").startswith(f"M{QUIET_ZONE} {QUIET_ZONE + 0.5}")


def test_qr_svg_has_no_external_references():
    svg = qr_svg("https://example.org", label="Gate")
    assert "href" not in svg
    assert "url(" not in svg
    assert "http" not in svg


def test_qr_svg_escapes_label():
    label = 'Gate <B14> & "gate"'
    svg = qr_svg("SKY", label=label)
    assert ET.fromstring(svg).find("title").text == label
    assert "<B14>" not in svg


@pytest.mark.parametrize("text", ["", "x" * 501])
def test_qr_rejects_empty_or_long_text(text):
    with pytest.raises(ValueError):
        qr_svg(text, label="x")
    with pytest.raises(ValueError):
        qr_png_bytes(text)


def test_qr_accepts_500_characters():
    assert qr_svg("x" * 500, label="x")


def test_qr_png_bytes_is_png():
    assert qr_png_bytes("SKY", scale=4).startswith(b"\x89PNG\r\n\x1a\n")


def test_contrast_ratio_matches_wcag():
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21)
    assert contrast_ratio("#FFFFFF", "#000000") == pytest.approx(21)


def test_default_qr_colors_meet_aaa_contrast():
    params = inspect.signature(qr_svg).parameters
    assert contrast_ratio(params["dark"].default, params["light"].default) >= 7
