import asyncio
import json
import os
import secrets
from contextlib import ExitStack, asynccontextmanager, closing, suppress
from functools import partial
from datetime import datetime, timedelta
from hashlib import sha256
from html import escape
from typing import Annotated

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from skyassist.adapters.oag import OagFlightProvider
from skyassist.agent import JourneyAgent
from skyassist.clock import Clock, ReplayClock, SystemClock
from skyassist.config import CONFIG, DATA_DIR, Config, Mode
from skyassist.db import connect, timestamp
from skyassist.reviewer import run_reviewer
from skyassist.demo_control import demo_router
from skyassist.demo_feed import DemoFeed
from skyassist.evidence import current_field, flight_board
from skyassist.i18n import render
from skyassist.concierge import concierge_router
from skyassist.trip import trip_router
from skyassist.live_map import map_router
from skyassist.opensky import traffic_router
from skyassist.system_status import status_router
from skyassist.model_boundary import default_extractor
from skyassist.seed import load
from skyassist.models import (
    AccessibleMode, FlightDirection, GetFlightsRequest, GetFlightsResponse, GetRouteResponse,
    I18nText, ObservationField, ObservationState, PolicyPack, TimeWindow, ViewModel,
    ReasonCode, UTCInstant,
)
from skyassist.service import SESSION_HORIZON, create_app
from skyassist.voice import voice_router
from skyassist.web import mount_web, web_router

R = ReasonCode
PUBLIC_STEPS = frozenset(f"route.{key}" for key in (
    "take_lift", "board_train", "exit_train", "approach_gate", "walk_to", "cross_checkpoint", "change_floor",
))


def _load_route(conn, token, now, policy):
    row = conn.execute("""
        SELECT token.revision AS token_revision, token.route_expires_at, token.route_revoked_at,
               journey.created_at, journey.closed_at, journey.outbound_leg_id, current.view_json
        FROM journey_revisions token JOIN journeys journey ON journey.id=token.journey_id
        JOIN journey_revisions current ON current.journey_id=journey.id AND current.revision=journey.revision
        WHERE token.route_token_hash=?
        """, (sha256(token.encode()).hexdigest(),)).fetchone()
    if row is None:
        return GetRouteResponse(reason_code=R.ROUTE_UNAVAILABLE), "en"
    view = ViewModel.model_validate_json(row["view_json"])
    language = view.language if view.language in ("en", "ar") else "en"
    if (row["route_expires_at"] <= timestamp(now) or row["closed_at"] is not None
            or row["created_at"] <= timestamp(now - SESSION_HORIZON)):
        return GetRouteResponse(reason_code=R.ROUTE_EXPIRED), language
    if (row["route_revoked_at"] or not view.route or view.route.revision != row["token_revision"]
            or not secrets.compare_digest(view.qr.token or "", token)):
        return GetRouteResponse(reason_code=R.ROUTE_REVOKED), language
    for field, name in ((ObservationField.GATE, "value"), (ObservationField.TERMINAL, "terminal"),
                        (ObservationField.CONCOURSE, "concourse"), (ObservationField.LEVEL, "level")):
        fact = current_field(conn, row["outbound_leg_id"], field, now, policy)
        value = fact.observation.value if fact.state == ObservationState.FRESH else None
        if value != getattr(view.gate, name) or (field == ObservationField.GATE and value is None):
            return GetRouteResponse(reason_code=R.ROUTE_REVOKED), language
    if (not view.route.steps or any(step.accessible_mode == AccessibleMode.STAFF_HANDOFF
                                   or step.text.key not in PUBLIC_STEPS for step in view.route.steps)):
        return GetRouteResponse(reason_code=R.ROUTE_UNAVAILABLE), language
    steps = tuple(step.model_copy(update={"map_position": None}) for step in view.route.steps)
    return GetRouteResponse(route=view.route.model_copy(update={
        "floor_shapes": (), "floor_outlines": (), "steps": steps,
    })), language


def _phone(result, language, headers):
    def text(label):
        return escape(render(label, language))

    if result.route:
        title = text(I18nText(key="action.follow_route"))
        steps = "".join(f"<li>{text(step.text)} {text(step.landmark)} "
                        f"({text(I18nText(key='kiosk.floor', params={'floor': step.floor}))})</li>"
                        for step in result.route.steps)
        content = f"<ol>{steps}</ol>"
    else:
        key = {R.ROUTE_EXPIRED: "banner.route_expired", R.ROUTE_REVOKED: "banner.route_changed"}.get(
            result.reason_code, result.reason_code.i18n_key)
        title = text(I18nText(key=key))
        content = f"<p>{text(I18nText(key='action.check_gate'))}</p>"
    direction = "rtl" if language == "ar" else "ltr"
    return HTMLResponse(
        f'<!doctype html><html lang="{language}" dir="{direction}"><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title}</title><main><h1>{title}</h1>{content}</main></html>", headers=headers,
    )


def read_router(clock: Clock, config: Config, policy: PolicyPack) -> APIRouter:
    router = APIRouter()

    @router.get("/flights")
    def flights(
        response: Response,
        airport: Annotated[str, Query(pattern=r"^[A-Z]{3}$")], direction: FlightDirection,
        start: UTCInstant, end: UTCInstant,
        cursor: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        query: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    ) -> GetFlightsResponse:
        if end - start > timedelta(days=2):
            raise HTTPException(422, detail={"reason_code": R.INVALID_ARGUMENTS})
        try:
            request = GetFlightsRequest(airport=airport, direction=direction, window=TimeWindow(start=start, end=end),
                                        cursor=cursor, limit=limit, query=query)
        except ValidationError as exc:
            raise HTTPException(422, detail={"reason_code": R.INVALID_ARGUMENTS}) from exc
        response.headers["Cache-Control"] = "no-store"
        with closing(connect(config.db_path)) as conn:
            conn.execute("BEGIN")
            return flight_board(conn, request, clock.now(), policy)

    @router.get("/clock")
    def service_clock(response: Response) -> dict[str, datetime]:
        response.headers["Cache-Control"] = "no-store"
        return {"now": clock.now()}

    @router.get("/route/{token}", response_model=GetRouteResponse)
    def route(token: Annotated[str, Path(min_length=1, max_length=128)], request: Request, response: Response):
        headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "Vary": "Accept",
                   "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"}
        with closing(connect(config.db_path)) as conn:
            conn.execute("BEGIN")
            result, language = _load_route(conn, token, clock.now(), policy)
        if "text/html" in request.headers.get("accept", ""):
            return _phone(result, language, headers)
        response.headers.update(headers)
        return result

    return router


def create_integrated_app(clock: Clock, config: Config, *, agent: JourneyAgent | None = None,
                          review: str = "human") -> FastAPI:
    use_oag = agent is None and config.mode is Mode.LIVE
    agent = agent if agent is not None else JourneyAgent(clock, config, extractor=default_extractor())
    routers = (trip_router(), read_router(clock, config, agent.policy), web_router(config.mode), map_router(clock, config), voice_router(config),
               status_router(config, agent, review), traffic_router(), concierge_router(clock, config))
    feed = DemoFeed(clock, config, agent, agent.policy) if config.mode == Mode.DEMO else None
    if feed is not None:
        routers += (demo_router(clock, config, agent, load, feed),)
    app = create_app(clock, config, agent_port=agent, extra_routers=routers)
    app.state.journey_agent = agent
    app.state.review = review
    if feed is not None or review == "auto" or use_oag:
        lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def integrated_lifespan(app):
            with ExitStack() as providers:
                if use_oag:
                    client = providers.enter_context(httpx.Client(trust_env=False, timeout=8.0))
                    agent.flights = OagFlightProvider(partial(connect, config.db_path), agent.policy, client)
                async with lifespan(app):
                    tasks = []
                    try:
                        if feed is not None:
                            app.state.demo_feed = feed
                            feed.tick()
                            tasks.append(asyncio.create_task(feed.run()))
                        if review == "auto":
                            tasks.append(asyncio.create_task(run_reviewer(partial(connect, config.db_path), clock)))
                        yield
                    finally:
                        for task in tasks:
                            task.cancel()
                        for task in tasks:
                            with suppress(asyncio.CancelledError):
                                await task

        app.router.lifespan_context = integrated_lifespan
    mount_web(app)
    return app


def app_from_env() -> FastAPI:
    if CONFIG.mode == Mode.DEMO:
        starts = json.loads((DATA_DIR / "clock_starts.json").read_text(encoding="utf-8"))
        return create_integrated_app(ReplayClock(os.environ.get("SKYASSIST_CLOCK_START", "C01"),
                                                speed=float(os.environ.get("SKYASSIST_CLOCK_SPEED", "1")),
                                                starts=starts), CONFIG,
                                        review=os.environ.get("SKYASSIST_REVIEW", "auto"))
    return create_integrated_app(SystemClock(), CONFIG,
                                 review=os.environ.get("SKYASSIST_REVIEW", "human"))
