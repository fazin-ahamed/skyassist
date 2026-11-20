"""Web search through Exa, for questions the airport's own data cannot answer (a pharmacy, a visa rule, the weather).

Web results are never used for anything about the passenger's own journey: gates, times, cutoffs, routes and requests
come from the ledger only. An answer that used the web says so and links its sources, and the grounding check lets it
repeat only numbers that appear in the snippets it was given.
"""

import re
import time

import httpx

from skyassist import env
from skyassist.network import offline_forced

URL = "https://api.exa.ai/search"
TTL = 300.0
_cache: dict[str, tuple[float, list[dict]]] = {}


def configured() -> bool:
    return bool(env.value("EXA_API_KEY")) and not offline_forced()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


async def search(query: str, *, transport: httpx.AsyncBaseTransport | None = None, limit: int = 4) -> list[dict]:
    """[{title, url, snippet}] best first. Empty when there is no key, no network, or nothing useful came back."""
    key = env.value("EXA_API_KEY")
    if not key or offline_forced():
        return []
    q = " ".join(query.split())[:200]
    cached = _cache.get(q.casefold())
    if cached and time.monotonic() - cached[0] < TTL:
        return cached[1]
    scoped = q if re.search(r"\b(dubai|dxb|airport)\b", q, re.I) else f"{q} Dubai International Airport"
    body = {"query": scoped, "numResults": limit, "type": "auto", "contents": {"text": {"maxCharacters": 700}}}
    try:
        async with httpx.AsyncClient(transport=transport, trust_env=False, timeout=8) as client:
            response = await client.post(URL, json=body, headers={"x-api-key": key, "Content-Type": "application/json"})
            response.raise_for_status()
            rows = response.json().get("results", [])
    except (httpx.HTTPError, ValueError):
        return []
    found = []
    for row in rows:
        snippet = _clean(row.get("text") or " ".join(row.get("highlights") or []))
        if snippet and row.get("url"):
            found.append({"title": _clean(row.get("title") or row["url"])[:120], "url": row["url"], "snippet": snippet[:400]})
    _cache[q.casefold()] = (time.monotonic(), found)
    return found
