import io

import segno

MAX_TEXT = 500
QUIET_ZONE = 4


def _qr(text: str) -> segno.QRCode:
    if not 0 < len(text) <= MAX_TEXT:
        raise ValueError(f"text must be 1 to {MAX_TEXT} characters")
    return segno.make(text, error="q", micro=False)


def qr_svg(text: str, *, label: str, dark: str = "#122C30", light: str = "#FFFFFF") -> str:
    svg = _qr(text).svg_inline(
        scale=1, border=QUIET_ZONE, dark=dark, light=light, title=label, omitsize=True
    )
    return svg.replace("<svg ", '<svg role="img" ', 1)


def qr_png_bytes(text: str, scale: int = 8) -> bytes:
    buf = io.BytesIO()
    _qr(text).save(buf, kind="png", scale=scale, border=QUIET_ZONE)
    return buf.getvalue()


def _luminance(hex_color: str) -> float:
    channels = []
    for i in (0, 2, 4):
        c = int(hex_color.removeprefix("#")[i : i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    la, lb = _luminance(hex_a), _luminance(hex_b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
