"""Endpoints the browser UI needs besides the journey API, and the static mount for the built front end."""

import os
from pathlib import Path as FsPath
from typing import Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Path, Request, Response
from fastapi.staticfiles import StaticFiles

from skyassist import i18n
from skyassist.config import Mode
from skyassist.util.qr import qr_svg

WEB_OUT = FsPath(__file__).resolve().parents[1] / "web" / "out"


def web_router(mode: Mode) -> APIRouter:
    router = APIRouter()

    @router.get("/i18n/{lang}")
    def catalog(lang: Annotated[str, Path(pattern=r"^[a-z]{2}$")], response: Response) -> dict:
        if lang not in i18n.languages():
            raise HTTPException(404)
        response.headers["Cache-Control"] = "no-cache"
        return {"lang": lang, "review": i18n.review_status(lang), "mode": mode, "texts": dict(i18n.catalog(lang))}

    @router.get("/route/{token}/qr.svg")
    def route_qr(token: Annotated[str, Path(min_length=1, max_length=128)], request: Request) -> Response:
        base = os.environ.get("SKYASSIST_PUBLIC_URL") or str(request.base_url)
        svg = qr_svg(f"{base.rstrip('/')}/route/{token}", label="Route on your phone")
        # served as a standalone image, so it needs its own namespace; the inline form does not
        svg = svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
        return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    return router


def mount_web(app: FastAPI) -> None:
    if WEB_OUT.is_dir():
        app.mount("/", StaticFiles(directory=WEB_OUT, html=True), name="web")
