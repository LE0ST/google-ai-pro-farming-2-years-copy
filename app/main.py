"""Read-only GaiConsole application: account labels and subscription diagnostics."""

from __future__ import annotations

import logging
import re
import secrets as random_secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import db
from .config import settings
from .runner import browser, diagnosis, itinerary, visual_evidence
from .security import COOKIE_NAME, require_same_origin, require_token

log = logging.getLogger("gai.read_only")
_LABEL = re.compile(r"^[^\x00-\x1f\x7f]{1,160}$")
_ACCOUNT_ID = re.compile(r"^acc_[0-9a-f]{12}$")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings.ensure_dirs()
    settings.load_or_create_token()
    await db.migrate()
    log.warning("read-only console ready at http://%s:%d", settings.bind_host, settings.bind_port)
    log.warning("console token is stored outside the repository; token value is not logged")
    yield


HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


def create_app() -> FastAPI:
    app = FastAPI(title="GaiConsole Read-only Diagnostics", lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    guard = [Depends(require_token)]
    post_guard = [Depends(require_token), Depends(require_same_origin)]

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"ok": True, "mode": "read_only_diagnostics", "browser": browser.describe()})

    @app.get("/", dependencies=guard)
    async def index(request: Request):
        if getattr(request.state, "set_cookie_token", None):
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(COOKIE_NAME, request.state.set_cookie_token,
                                httponly=True, samesite="strict")
            return response
        accounts = await db.fetch_all("SELECT id, email, status, step, step_message FROM accounts ORDER BY created_at")
        return templates.TemplateResponse(request, "index.html", {
            "accounts": [dict(row) for row in accounts],
            "diagnoses": diagnosis.runner.results,
        })

    @app.post("/profiles/add", dependencies=post_guard)
    async def profiles_add(label: str = Form(...)) -> JSONResponse:
        label = label.strip()
        if not _LABEL.fullmatch(label):
            return JSONResponse({"error": "Enter a non-empty display label without control characters."}, status_code=400)
        account_id = "acc_" + random_secrets.token_hex(6)
        now = int(time.time())
        profile = settings.profiles_dir / f"profile_{account_id}"
        if profile.resolve().parent != settings.profiles_dir.resolve():
            return JSONResponse({"error": "Invalid profile path."}, status_code=400)
        profile.mkdir(parents=True, exist_ok=False)
        try:
            await db.execute(
                "INSERT INTO accounts (id, email, status, step, step_message, created_at, updated_at) "
                "VALUES (?, ?, 'ready', 0, '', ?, ?)", (account_id, label, now, now)
            )
        except Exception:
            profile.rmdir()
            return JSONResponse({"error": "A profile with that label could not be created."}, status_code=409)
        return JSONResponse({"id": account_id, "label": label, "status": "ready"}, status_code=201)

    @app.post("/accounts/{acc_id}/diagnose", dependencies=post_guard)
    async def accounts_diagnose(acc_id: str):
        error = await diagnosis.runner.start(acc_id)
        if error:
            return JSONResponse({"error": error}, status_code=409)
        return JSONResponse({"status": "running"}, status_code=202)

    @app.get("/api/diagnosis/{acc_id}", dependencies=guard)
    async def api_diagnosis(acc_id: str):
        if not _ACCOUNT_ID.fullmatch(acc_id):
            return JSONResponse({"error": "invalid account identifier"}, status_code=400)
        result = diagnosis.runner.results.get(acc_id) or diagnosis.read_report(acc_id)
        return JSONResponse(result or {"status": "not_started", "detector_version": diagnosis.detector_version()})

    @app.get("/api/log/{acc_id}", dependencies=guard)
    async def api_log(acc_id: str):
        if not _ACCOUNT_ID.fullmatch(acc_id):
            return JSONResponse({"error": "invalid account identifier"}, status_code=400)
        result = diagnosis.runner.results.get(acc_id) or diagnosis.read_report(acc_id)
        return JSONResponse({"events": result.get("sources", []), "status": result.get("status", "not_started")})

    @app.get("/api/state", dependencies=guard)
    async def api_state() -> JSONResponse:
        rows = await db.fetch_all("SELECT id, email, status, step, step_message FROM accounts ORDER BY created_at")
        accounts = {}
        for row in rows:
            account_id = row["id"]
            report = diagnosis.runner.results.get(account_id) or diagnosis.read_report(account_id)
            accounts[account_id] = {
                "label": row["email"], "status": row["status"], "step": row["step"],
                "message": row["step_message"], "diagnosis": report.get("status", "not_started"),
                "itinerary": itinerary.public_status(settings.artifacts_dir, account_id),
                "visual_evidence": visual_evidence.read(settings.artifacts_dir, account_id),
            }
        return JSONResponse({"mode": "read_only_diagnostics", "accounts": accounts,
                             "current": diagnosis.runner.current})

    return app


app = create_app()
