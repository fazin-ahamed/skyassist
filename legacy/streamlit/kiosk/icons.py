"""Authored pictograms: 24 grid, 2.5 stroke, square caps, mitered joins (signage weight).

Rendered as CSS masks because Streamlit sanitises inline <svg> out of st.html.
"""

from urllib.parse import quote

_PATHS = {
    "check": '<path d="M4 12.5 9.5 18 20 6.5"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 6.5V12l4 2.5"/>',
    "alert": '<path d="M12 3 22 20.5H2Z"/><path d="M12 9.5v5M12 17.5v.01"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.01"/>',
    "lift": '<rect x="4" y="3" width="16" height="18"/><path d="M8.5 16.5V8M6 10.5 8.5 8l2.5 2.5M15.5 7.5V16M13 13.5l2.5 2.5 2.5-2.5"/>',
    "train": '<rect x="5" y="3" width="14" height="14"/><path d="M5 11h14M8 21l2-4M16 21l-2-4M9 7h.01M15 7h.01"/>',
    "walk": '<circle cx="13" cy="4.5" r="2"/><path d="M13 8.5 11 14l3 2v5M11 14l-3 7M13 8.5l3.5 3.5M13 8.5 9 11"/>',
    "wheelchair": '<circle cx="10" cy="4" r="2"/><path d="M10 7.5V14h6l2.5 6M10 11h5M7.5 14.5a5 5 0 1 0 7 6"/>',
    "stairs": '<path d="M3 20h5v-5h5v-5h5V5h3"/>',
    "checkpoint": '<path d="M4 4v16M20 4v16M4 12h16M8 12V8M12 12V8M16 12V8"/>',
    "person": '<circle cx="12" cy="6" r="3"/><path d="M5 21v-3a7 7 0 0 1 14 0v3"/>',
    "plane": '<path d="M21 14 13 10V4.5a1.5 1.5 0 0 0-3 0V10L2 14v2l8-1.5V19l-2.5 1.5V22l4-1 4 1v-1.5L13 19v-4.5l8 1.5Z"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"/>',
    "keyboard": '<rect x="2.5" y="6" width="19" height="12"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
    "qr": '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><path d="M14 14h3v3M21 14v.01M14 21h3M21 17v4"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17v.01"/>',
    "close": '<path d="M5 5l14 14M19 5 5 19"/>',
    "back": '<path d="M15 5 8 12l7 7"/>',
}

MODE_ICON = {
    "walk": "walk",
    "step_free": "wheelchair",
    "stairs": "stairs",
    "lift": "lift",
    "transit": "train",
    "checkpoint": "checkpoint",
    "staff_handoff": "person",
}


def _mask(name: str) -> str:
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2.5' "
        f"stroke-linecap='square' stroke-linejoin='miter'>{_PATHS[name].replace(chr(34), chr(39))}</svg>"
    )
    return f'url("data:image/svg+xml,{quote(svg)}")'


def css() -> str:
    rules = [
        f".sa-i-{n}{{-webkit-mask-image:{_mask(n)};mask-image:{_mask(n)}}}" for n in _PATHS
    ]
    base = (
        ".sa-icon{display:inline-block;flex:none;background-color:currentColor;vertical-align:-0.2em;"
        "-webkit-mask-repeat:no-repeat;mask-repeat:no-repeat;-webkit-mask-position:center;mask-position:center;"
        "-webkit-mask-size:contain;mask-size:contain}"
    )
    return base + "".join(rules)


def icon(name: str, size: int = 24, cls: str = "") -> str:
    assert name in _PATHS, name
    return f'<span class="sa-icon sa-i-{name} {cls}" style="width:{size}px;height:{size}px" aria-hidden="true"></span>'
