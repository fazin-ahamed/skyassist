"""Does each pasted key actually work? A cheap, cached check so the screen and the doctor can say so.

Results: ok, rejected (the service said no), unreachable (no answer), offline (rehearsal mode), missing (no key set).
Only a 401 counts as rejected: a key with narrow permissions or a service hiccup must not switch a feature off.
"""

import hashlib
import time

import httpx

from skyassist import env
from skyassist.network import offline_forced

TTL = 120.0
_cache: dict[tuple[str, str], tuple[float, str]] = {}
transport: httpx.AsyncBaseTransport | None = None  # tests put a mock here


def reset() -> None:
    _cache.clear()


def deepgram_base() -> str:
    return (env.value("DEEPGRAM_BASE_URL") or "https://api.deepgram.com").rstrip("/")


def openrouter_base() -> str:
    return (env.value("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")


async def _check(name: str, key: str, probe) -> str:
    if not key:
        return "missing"
    if offline_forced():
        return "offline"
    slot = (name, hashlib.sha256(key.encode()).hexdigest()[:12])
    hit = _cache.get(slot)
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=4.0, trust_env=False, transport=transport) as client:
            code = await probe(client)
        state = "rejected" if code == 401 else "unreachable" if code >= 500 else "ok"
    except httpx.HTTPError:
        state = "unreachable"
    if state != "unreachable":
        _cache[slot] = (time.monotonic(), state)
    return state


async def deepgram(key: str | None = None) -> str:
    key = env.value("DEEPGRAM_API_KEY") if key is None else key
    return await _check("deepgram", key, lambda c: _status(c, f"{deepgram_base()}/v1/projects", {"Authorization": f"Token {key}"}))


async def openrouter(key: str | None = None) -> str:
    key = env.value("OPENROUTER_API_KEY") if key is None else key
    return await _check("openrouter", key, lambda c: _status(c, f"{openrouter_base()}/auth/key", {"Authorization": f"Bearer {key}"}))


async def _status(client: httpx.AsyncClient, url: str, headers: dict) -> int:
    return (await client.get(url, headers=headers)).status_code
