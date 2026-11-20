"""Is the internet there? One answer for every optional online feature.

SkyAssist is built to keep working with the network gone. Everything that decides something (flights, gates, routes,
timing, assistance) runs against local data. Only three things ever reach out: voice (Deepgram), a hosted language
model (OpenRouter) and live aircraft (OpenSky). Each of them asks this module before trying, and each falls back on
its own when the answer is no.

SKYASSIST_OFFLINE=1 starts the process in rehearsal mode, and `set_offline()` flips it while running, so a demo can
show a disconnected kiosk without pulling a cable.
"""

import os
import socket
import time

_forced = {"offline": os.environ.get("SKYASSIST_OFFLINE", "").strip().lower() in ("1", "true", "yes", "on")}
_cache = {"at": 0.0, "ok": True}
PROBES = (("1.1.1.1", 443), ("8.8.8.8", 443))


def offline_forced() -> bool:
    return _forced["offline"]


def set_offline(on: bool) -> None:
    _forced["offline"] = bool(on)
    _cache["at"] = 0.0


def internet_reachable(ttl: float = 15.0) -> bool:
    """A quick TCP connect, cached. Blocking: call it from a thread in async code."""
    if _forced["offline"]:
        return False
    now = time.monotonic()
    if now - _cache["at"] < ttl:
        return _cache["ok"]
    ok = False
    for host, port in PROBES:
        try:
            socket.create_connection((host, port), timeout=1.0).close()
            ok = True
            break
        except OSError:
            continue
    _cache.update(at=now, ok=ok)
    return ok
