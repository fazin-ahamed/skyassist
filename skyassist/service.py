"""HTTP boundary and background runner for SkyAssist."""

import asyncio
import json
import logging
import os
import secrets
from collections.abc import Iterable
from contextlib import asynccontextmanager, closing, suppress
from datetime import timedelta
from functools import partial
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Protocol
from uuid import uuid4

from fastapi import APIRouter, FastAPI, Header, HTTPException, Path as PathParam, Query, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from skyassist.assist import (
    AssistanceError, audit, case_from_row, claim_and_dispatch, create_case, decide,
    enqueue_event, expire_due, initial_view, journey_row, latest_revision, report_dispatch,
)
from skyassist.clock import Clock, SystemClock
from skyassist.config import CONFIG, Config, Mode
from skyassist.db import connect, init_db, timestamp, transaction
from skyassist.models import (
    AssistanceRequest, AssistanceResponse, CallbackOutcome, CaseState, CreateJourneyRequest,
    CreateJourneyResponse, DecisionRequest, DecisionResponse, DispatchStatusRequest,
    DispatchStatusResponse, EventType, GetJourneyRequest, GetJourneyResponse,
    GetStaffCasesRequest, GetStaffCasesResponse, JourneyInputRequest, JourneyInputResponse,
    ReasonCode, Role, StaffCaseItem, ViewModel,
)

DEMO_COORDINATOR_TOKEN = "demo-coordinator"
DEMO_INTEGRATION_TOKEN = "demo-integration"
SESSION_HORIZON = timedelta(hours=8)
LOG = logging.getLogger(__name__)
type Auth = Annotated[str | None, Header(alias="Authorization")]
type ResourceId = Annotated[str, PathParam(min_length=1, max_length=128)]
R = ReasonCode


class AgentPort(Protocol):
    async def run_once(self) -> bool:
        """Process one durable journey event. Return whether work was done."""
        ...

    def passenger_view(self, conn, journey, view: ViewModel) -> ViewModel: ...

    def normalize_landmark(self, conn, airport: str, text: str) -> str: ...


def _digest(token):
    return sha256(token.encode()).hexdigest()


def _bearer(header):
    scheme, _, token = (header or "").partition(" ")
    return token if scheme.lower() == "bearer" else ""


def _error(status, reason):
    return HTTPException(status_code=status, detail={"reason_code": reason})


def _credential(name, demo_default):
    return os.environ.get(name) or demo_default


def create_app(clock: Clock, config: Config, agent_port: AgentPort | None = None,
               extra_routers: Iterable[APIRouter] = ()) -> FastAPI:
    demo = config.mode == Mode.DEMO
    coordinator = _credential("SKYASSIST_COORDINATOR_TOKEN", DEMO_COORDINATOR_TOKEN if demo else None)
    integration = _credential("SKYASSIST_INTEGRATION_TOKEN", DEMO_INTEGRATION_TOKEN if demo else None)
    if not coordinator or not integration or secrets.compare_digest(_digest(coordinator), _digest(integration)):
        raise ValueError("SkyAssist requires distinct coordinator and integration tokens")
    if config.mode != CONFIG.mode:
        raise ValueError("App config mode must match SKYASSIST_MODE")
    credentials = {Role.COORDINATOR: _digest(coordinator), Role.INTEGRATION: _digest(integration)}
    open_db = partial(connect, config.db_path)
    outbox_ready = asyncio.Event()

    def require_role(authorization, role):
        token = _bearer(authorization)
        if not secrets.compare_digest(_digest(token), credentials[role]):
            raise _error(403, R.WRONG_ROLE)

    def session(conn, journey_id, authorization, *, allow_closed=False):
        row = conn.execute("SELECT value FROM meta WHERE key=?", (f"session:{journey_id}",)).fetchone()
        digest = _digest(_bearer(authorization))
        if not secrets.compare_digest(digest, row[0] if row else "0" * 64) or row is None:
            raise _error(403, R.WRONG_ROLE)
        journey = journey_row(conn, journey_id)
        now = clock.now()
        if allow_closed and journey is not None and journey["closed_at"] is not None:
            return journey
        if journey is None or journey["closed_at"] is not None or (
            journey["created_at"] <= timestamp(now - SESSION_HORIZON)
        ):
            raise _error(403, R.SESSION_EXPIRED)
        return journey

    def path_scope(actual, expected):
        if actual != expected:
            raise _error(422, R.INVALID_ARGUMENTS)

    def sweep():
        with closing(open_db()) as conn:
            expire_due(conn, clock=clock)

    async def run_outbox():
        while True:
            try:
                outbox_ready.clear()
                await asyncio.to_thread(sweep)
                worked = await claim_and_dispatch(open_db, clock=clock, demo=demo,
                                                  call_assistance=app.state.call_assistance)
                if not worked:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(outbox_ready.wait(), timeout=0.1)
            except Exception as exc:
                LOG.error("Assistance worker step failed (%s)", type(exc).__name__)
                await asyncio.sleep(1)
                await asyncio.sleep(0.5)

    async def run_agent():
        while True:
            try:
                worked = await agent_port.run_once()
                await asyncio.sleep(0 if worked else 0.1)
            except Exception as exc:
                LOG.error("Journey agent step failed (%s)", type(exc).__name__)
                await asyncio.sleep(0.5)

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(init_db, config.db_path)
        tasks = [asyncio.create_task(run_outbox())]
        if agent_port is not None:
            tasks.append(asyncio.create_task(run_agent()))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(lifespan=lifespan)
    app.state.call_assistance = None

    @app.middleware("http")
    async def private_passenger_responses(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.split("/", 2)[1] in ("journeys", "route"):
            response.headers["Cache-Control"] = "no-store"
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    @app.exception_handler(AssistanceError)
    async def assistance_error(request, exc):
        return JSONResponse(status_code=409, content={"detail": {"reason_code": exc.reason}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if request.method == "POST" and request.url.path.endswith("/assistance"):
            fields = {error["loc"] for error in exc.errors()}
            if ("body", "consent") in fields:
                return JSONResponse(status_code=422, content={"detail": {"reason_code": R.CONSENT_MISSING}})
            if ("body", "pickup_confirmed") in fields or ("body", "pickup_node_id") in fields:
                return JSONResponse(status_code=422, content={
                    "detail": {"reason_code": R.PICKUP_UNCONFIRMED, "next_action": "seek_staff"},
                })
        if request.url.path.split("/", 2)[1] == "journeys":
            return JSONResponse(status_code=422, content={"detail": {"reason_code": R.INVALID_ARGUMENTS}})
        return await request_validation_exception_handler(request, exc)

    @app.post("/journeys", status_code=201)
    def create_journey(body: CreateJourneyRequest, response: Response) -> CreateJourneyResponse:
        journey_id = uuid4().hex
        token = secrets.token_urlsafe(32)
        now = timestamp(clock.now())
        with closing(open_db()) as conn, transaction(conn):
            for leg_id, column in ((body.inbound_leg_id, "destination"), (body.outbound_leg_id, "origin")):
                if leg_id is not None and conn.execute(
                    f"SELECT 1 FROM flight_legs WHERE id=? AND {column}=?", (leg_id, body.airport),
                ).fetchone() is None:
                    raise _error(422, R.FLIGHT_UNKNOWN)
            conn.execute(
                """INSERT INTO journeys (id, airport, profile, party_needs_json, flow, language,
                                         created_at, inbound_leg_id, outbound_leg_id, booking_ref)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (journey_id, body.airport, body.profile, body.party_needs.model_dump_json(),
                 body.flow, body.language, now, body.inbound_leg_id, body.outbound_leg_id,
                 body.booking_ref.upper() if body.booking_ref else None),
            )
            view = initial_view(journey_row(conn, journey_id), clock)
            conn.execute(
                """INSERT INTO journey_revisions (journey_id, revision, created_at, policy_version, view_json)
                   VALUES (?, 0, ?, 'service', ?)""",
                (journey_id, now, view.model_dump_json()),
            )
            conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (f"session:{journey_id}", _digest(token)))
        response.headers["Authorization"] = f"Bearer {token}"
        response.headers["Cache-Control"] = "no-store"
        return CreateJourneyResponse(journey_id=journey_id, revision=0)

    @app.delete("/journeys/{journey_id}", status_code=204)
    def close_journey(journey_id: ResourceId, authorization: Auth = None) -> Response:
        with closing(open_db()) as conn, transaction(conn):
            journey = session(conn, journey_id, authorization, allow_closed=True)
            if journey["closed_at"] is None:
                now = timestamp(clock.now())
                conn.execute("UPDATE journeys SET closed_at=?, next_refresh_at=NULL WHERE id=?", (now, journey_id))
                conn.execute(
                    "UPDATE journey_revisions SET route_revoked_at=? WHERE journey_id=? "
                    "AND route_token_hash IS NOT NULL AND route_revoked_at IS NULL",
                    (now, journey_id),
                )
        return Response(status_code=204)

    @app.post("/journeys/{journey_id}/inputs", status_code=202)
    def submit_input(journey_id: ResourceId, body: JourneyInputRequest,
                     authorization: Auth = None) -> JourneyInputResponse:
        with closing(open_db()) as conn, transaction(conn):
            journey = session(conn, journey_id, authorization)
            path_scope(body.journey_id, journey_id)
            prior = conn.execute(
                "SELECT id, processed_at FROM events WHERE journey_id=? AND client_input_id=?",
                (journey_id, body.client_input_id),
            ).fetchone()
            if prior:
                event_id, processed = prior
            else:
                now = timestamp(clock.now())
                if agent_port is not None and body.landmark_text:
                    body = body.model_copy(update={"landmark_text": agent_port.normalize_landmark(
                        conn, journey["airport"], body.landmark_text,
                    )})
                event_id = enqueue_event(conn, journey_id, EventType.PASSENGER_INPUT, now,
                                         payload=body.model_dump_json(), client_input_id=body.client_input_id)
                audit(conn, event_id, now, "journey_input", "queued", journey_id=journey_id, role=Role.PASSENGER)
                processed = None
            return JourneyInputResponse(journey_id=journey_id, event_id=event_id,
                                        revision=journey["revision"], work_pending=processed is None)

    @app.get("/journeys/{journey_id}", response_model=GetJourneyResponse)
    def get_journey(journey_id: ResourceId, response: Response,
                    known_revision: Annotated[int | None, Query(ge=0)] = None,
                    authorization: Auth = None) -> GetJourneyResponse | Response:
        query = GetJourneyRequest(journey_id=journey_id, known_revision=known_revision)
        response.headers["Cache-Control"] = "no-store"
        with closing(open_db()) as conn:
            conn.execute("BEGIN")
            journey = session(conn, query.journey_id, authorization)
            latest = latest_revision(conn, query.journey_id)
            revision = latest["revision"] if latest else journey["revision"]
            stored = ViewModel.model_validate_json(latest["view_json"]) if latest else initial_view(journey, clock)
            view = agent_port.passenger_view(conn, journey, stored) if agent_port is not None else stored
            if query.known_revision == revision and view is stored:
                result = GetJourneyResponse(revision=revision, unchanged=True)
                return JSONResponse(result.model_dump(exclude_none=True), headers=response.headers)
            return GetJourneyResponse(revision=revision, unchanged=False, view=view)

    @app.post("/journeys/{journey_id}/assistance")
    def request_assistance(journey_id: ResourceId, body: AssistanceRequest,
                           authorization: Auth = None) -> AssistanceResponse:
        with closing(open_db()) as conn:
            session(conn, journey_id, authorization)
            path_scope(body.journey_id, journey_id)
            return create_case(conn, clock=clock, bind_to_outbound="target_leg_id" not in body.model_fields_set,
                               **body.model_dump())

    @app.get("/staff/cases")
    def staff_cases(
        response: Response,
        airport: Annotated[str, Query(pattern=r"^[A-Z]{3}$")],
        states: Annotated[list[CaseState] | None, Query()] = None,
        cursor: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        authorization: Auth = None,
    ) -> GetStaffCasesResponse:
        require_role(authorization, Role.COORDINATOR)
        response.headers["Cache-Control"] = "no-store"
        conditions = {} if states is None else {"states": tuple(states)}
        query = GetStaffCasesRequest(airport=airport, cursor=cursor, limit=limit, **conditions)
        now = clock.now()
        with closing(open_db()) as conn:
            placeholders = ",".join("?" for _ in query.states)
            rows = conn.execute(
                f"""SELECT * FROM cases WHERE airport=? AND id>? AND state IN ({placeholders})
                    ORDER BY id LIMIT ?""",
                (query.airport, query.cursor or "", *query.states, query.limit + 1),
            ).fetchall()
            items = []
            for row in rows[:query.limit]:
                case = case_from_row(conn, row)
                followups = conn.execute(
                    """SELECT reason_codes_json FROM audit WHERE case_id=?
                       AND action IN ('scope_amendment', 'cancellation_requested') ORDER BY id""",
                    (case.id,),
                )
                reasons = list(case.reason_codes)
                for entry in followups:
                    reasons.extend(ReasonCode(reason) for reason in json.loads(entry[0]))
                case = case.model_copy(update={"reason_codes": tuple(dict.fromkeys(reasons))})
                latest = latest_revision(conn, case.journey_id)
                view = (ViewModel.model_validate_json(latest["view_json"]) if latest
                        else initial_view(journey_row(conn, case.journey_id), clock))
                remaining = None if view.cutoff.value is None else int((view.cutoff.value - now).total_seconds())
                items.append(StaffCaseItem(case=case, gate=view.gate, cutoff=view.cutoff, time_remaining=remaining))
            return GetStaffCasesResponse(as_of=now, cases=tuple(items),
                                         next_cursor=items[-1].case.id if len(rows) > query.limit else None)

    @app.post("/staff/cases/{case_id}/decisions")
    async def staff_decision(case_id: ResourceId, body: DecisionRequest, authorization: Auth = None) -> DecisionResponse:
        require_role(authorization, Role.COORDINATOR)
        path_scope(body.case_id, case_id)

        def commit():
            with closing(open_db()) as conn:
                return decide(conn, body, clock=clock, role=Role.COORDINATOR)

        result = await asyncio.to_thread(commit)
        if result.outcome == CallbackOutcome.APPLIED and result.state == CaseState.APPROVED:
            outbox_ready.set()
        return result

    @app.post("/staff/cases/{case_id}/dispatch-status")
    def integration_status(case_id: ResourceId, body: DispatchStatusRequest,
                           authorization: Auth = None) -> DispatchStatusResponse:
        require_role(authorization, Role.INTEGRATION)
        path_scope(body.case_id, case_id)
        with closing(open_db()) as conn:
            return report_dispatch(conn, body, clock=clock, role=Role.INTEGRATION)

    if demo:
        @app.post("/staff/cases/{case_id}/dispatch")
        def demo_dispatch(case_id: ResourceId, body: DispatchStatusRequest,
                          authorization: Auth = None) -> DispatchStatusResponse:
            require_role(authorization, Role.COORDINATOR)
            path_scope(body.case_id, case_id)
            with closing(open_db()) as conn:
                return report_dispatch(conn, body, clock=clock, role=Role.COORDINATOR, demo_staff=True)

    for router in extra_routers:
        app.include_router(router)
    return app


def app_from_env() -> FastAPI:
    """Uvicorn factory. SKYASSIST_DB_PATH selects the process database."""
    path = Path(os.environ.get("SKYASSIST_DB_PATH", CONFIG.db_path))
    return create_app(SystemClock(), Config(CONFIG.mode, path))
