"""What is running, what is local, and what is missing. The screen and the operator read this to say so honestly."""

import asyncio
import os
import secrets

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from skyassist import env, keycheck
from skyassist.config import Config, Mode
from skyassist.models import ReasonCode
from skyassist.network import internet_reachable, offline_forced, set_offline
from skyassist.service import DEMO_COORDINATOR_TOKEN, Auth, _bearer, _credential, _digest, _error


class OfflineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offline: bool


def status_router(config: Config, agent, review: str = "human") -> APIRouter:
    router = APIRouter()
    demo = config.mode == Mode.DEMO
    coordinator = _digest(_credential("SKYASSIST_COORDINATOR_TOKEN", DEMO_COORDINATOR_TOKEN if demo else None) or "")

    @router.get("/system/status")
    async def status(response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        online = await asyncio.to_thread(internet_reachable)
        chain = getattr(agent.extractor, "state", lambda: {"providers": [], "last_used": "template", "last_used_at": None})()
        configured = {"voice": bool(env.value("DEEPGRAM_API_KEY")), "openrouter": bool(env.value("OPENROUTER_API_KEY"))}
        keys = {"deepgram": await keycheck.deepgram(), "openrouter": await keycheck.openrouter()}
        setup = [name for name, ok in (("DEEPGRAM_API_KEY", configured["voice"]), ("OPENROUTER_API_KEY", configured["openrouter"])) if not ok]
        offline_reasons = []
        if not online:
            offline_reasons = [name for name, ok in (("voice", configured["voice"]), ("hosted_model", configured["openrouter"]),
                                                     ("live_aircraft", True)) if ok]
        return {
            "mode": config.mode.value, "simulated": demo, "internet": online, "offline_forced": offline_forced(),
            "review": review,
            "core": "local",  # flights, gates, routes, timing and assistance never need the internet
            "understanding": {"last_used": chain.get("last_used", "template"), "last_used_at": chain.get("last_used_at"),
                              "providers": chain.get("providers", []), "template": True},
            "configured": configured, "keys": keys, "setup": setup,
            "unavailable_online_features": offline_reasons,
        }

    @router.post("/system/offline")
    async def offline(body: OfflineBody, authorization: Auth = None) -> dict:
        if not demo or not secrets.compare_digest(_digest(_bearer(authorization)), coordinator):
            raise _error(403, ReasonCode.WRONG_ROLE)
        set_offline(body.offline)
        return {"offline_forced": offline_forced()}

    return router
