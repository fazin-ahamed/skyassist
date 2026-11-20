"""The hosted language model, with a fallback.

One place that talks to OpenRouter. It tries the primary model, then the fallback, and gives up quietly so callers fall
back to the built-in rules or the ledger template. A model never decides a fact; callers check what it returns.

  OPENROUTER_MODEL           primary   (default nvidia/nemotron-3.5-lightning:free)
  OPENROUTER_FALLBACK_MODEL  fallback  (default recursive/flint-v1.0:free)
"""

import httpx

from skyassist import env, keycheck
from skyassist.network import offline_forced

PRIMARY = "nvidia/nemotron-3.5-lightning:free"
FALLBACK = "recursive/flint-v1.0:free"


def models() -> list[str]:
    primary = env.value("OPENROUTER_MODEL") or PRIMARY
    fallback = env.value("OPENROUTER_FALLBACK_MODEL") or FALLBACK
    return [primary] if fallback == primary else [primary, fallback]


def configured() -> bool:
    return bool(env.value("OPENROUTER_API_KEY")) and not offline_forced()


def _text(payload: dict) -> str:
    message = payload["choices"][0]["message"]
    return (message.get("content") or "").strip()


async def chat(
    messages: list[dict], *, max_tokens: int = 300, temperature: float = 0.2, timeout: float = 12,
    json_mode: bool = False, transport: httpx.AsyncBaseTransport | None = None, url: str | None = None,
) -> tuple[str, str] | None:
    """(text, model that answered), or None when no model gave a usable answer."""
    key = env.value("OPENROUTER_API_KEY")
    if not key or offline_forced():
        return None
    target = url or env.value("OPENROUTER_URL") or f"{keycheck.openrouter_base()}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "X-Title": "SkyAssist"}
    try:
        async with httpx.AsyncClient(transport=transport, trust_env=False, timeout=timeout) as client:
            for model in models():
                # reasoning models (nemotron) think before they answer: a small budget ends mid-thought with no answer
                body = {"model": model, "temperature": temperature, "max_tokens": max(max_tokens, 2200), "messages": messages}
                if json_mode:
                    body["response_format"] = {"type": "json_object"}
                try:
                    response = await client.post(target, json=body, headers=headers)
                    if response.status_code == 400 and json_mode:  # some free models cannot do json mode: ask in the prompt only
                        body.pop("response_format")
                        response = await client.post(target, json=body, headers=headers)
                    if response.status_code != 200:
                        continue  # no credit, rate limited, unknown model, or down: try the next one
                    text = _text(response.json())
                except (httpx.HTTPError, KeyError, IndexError, ValueError, AttributeError, TypeError):
                    continue
                if text:
                    return text, model
    except httpx.HTTPError:
        return None
    return None
