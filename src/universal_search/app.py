from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .auth import AUTH
from .ratelimit import LIMITER, client_ip
from .schema import SearchProfile
from .store import ActiveSearchLimitReached, Store
from .wizard import next_questions
from .worker import run_search

app = FastAPI(title="Universal Vehicle Search", version="0.3.0")
store = Store(os.getenv("SEARCH_DB", "data/searches.db"))
STATIC = Path(__file__).with_name("static")


class WizardRequest(BaseModel):
    profile: dict = Field(default_factory=dict)


class CreateSearchRequest(BaseModel):
    profile: dict


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


@app.middleware("http")
async def public_rate_limit(request: Request, call_next):
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    ip = client_ip(request)
    per_minute = _env_int("PUBLIC_RATE_LIMIT_PER_MINUTE", 30, 1, 1000)
    if not LIMITER.allow(ip, "api", per_minute, 60):
        return JSONResponse({"detail": "rate limit exceeded"}, status_code=429, headers={"Retry-After": "60"})
    if request.method == "POST" and request.url.path == "/api/searches":
        create_per_hour = _env_int("CREATE_SEARCH_LIMIT_PER_HOUR", 3, 1, 100)
        if not LIMITER.allow(ip, "create-search", create_per_hour, 3600):
            return JSONResponse({"detail": "search creation rate limit exceeded"}, status_code=429, headers={"Retry-After": "3600"})
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/wizard/next")
def wizard_next(req: WizardRequest):
    profile = SearchProfile.from_dict(req.profile)
    return {
        "questions": [q.to_dict() for q in next_questions(profile)],
        "errors": profile.validate() if profile.make and profile.model and profile.markets else [],
    }


def _app_user(token: str | None) -> str:
    if not AUTH.configured:
        raise HTTPException(503, detail="new search creation is closed until access codes are configured")
    owner_id = AUTH.authenticate(token)
    if not owner_id:
        raise HTTPException(
            401,
            detail="invalid application access code",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return owner_id


def _admin(token: str | None) -> None:
    if not AUTH.admin_configured:
        raise HTTPException(503, detail="admin access is not configured")
    if not AUTH.authenticate_admin(token):
        raise HTTPException(401, detail="invalid admin access code")


@app.post("/api/searches")
def create_search(
    req: CreateSearchRequest,
    x_app_token: str | None = Header(default=None),
):
    owner_id = _app_user(x_app_token)
    profile = SearchProfile.from_dict(req.profile)
    errors = profile.validate()
    if errors:
        raise HTTPException(422, detail=errors)
    max_active = _env_int("MAX_ACTIVE_SEARCHES_PER_USER", 3, 1, 20)
    try:
        search_id, owner_token, bind_code = store.create_search(
            profile, owner_id=owner_id, max_active=max_active
        )
    except ActiveSearchLimitReached as exc:
        raise HTTPException(
            429,
            detail=f"active search limit reached ({max_active}); stop a search or wait for TTL expiry",
        ) from exc
    row = store.get_search(search_id)
    return {
        "id": search_id,
        "owner_token": owner_token,
        "telegram_bind_code": bind_code,
        "telegram_command": f"/bind {bind_code}",
        "expires_at": row["expires_at"] if row else None,
    }


def _authorized(search_id: str, token: str | None, owner_id: str | None = None) -> dict:
    if not token or not store.authorize(search_id, token):
        raise HTTPException(403, detail="invalid owner token")
    row = store.get_search(search_id)
    assert row is not None
    result = dict(row)
    if owner_id and result["owner_id"] not in {owner_id, "legacy"}:
        raise HTTPException(403, detail="search belongs to another application user")
    return result


@app.get("/api/searches/{search_id}")
def get_search(search_id: str, x_search_token: str | None = Header(default=None)):
    row = _authorized(search_id, x_search_token)
    return {
        "id": row["id"],
        "profile": json.loads(row["profile_json"]),
        "last_run_at": row["last_run_at"],
        "next_run_at": row["next_run_at"],
        "expires_at": row["expires_at"],
        "enabled": bool(row["enabled"]),
        "telegram_bound": bool(row["telegram_chat_id"]),
    }


@app.get("/api/searches/{search_id}/results")
def get_results(search_id: str, x_search_token: str | None = Header(default=None)):
    _authorized(search_id, x_search_token)
    return {"results": store.list_results(search_id)}


@app.post("/api/searches/{search_id}/run")
def run_now(
    search_id: str,
    x_search_token: str | None = Header(default=None),
    x_app_token: str | None = Header(default=None),
):
    owner_id = _app_user(x_app_token)
    row = _authorized(search_id, x_search_token, owner_id)
    profile = SearchProfile.from_dict(json.loads(row["profile_json"]))
    result = run_search(store, search_id, profile, row["telegram_chat_id"])
    if result.get("skipped"):
        raise HTTPException(409, detail=result.get("reason") or "run unavailable")
    return result


@app.delete("/api/searches/{search_id}")
def stop_search(
    search_id: str,
    x_search_token: str | None = Header(default=None),
    x_app_token: str | None = Header(default=None),
):
    owner_id = _app_user(x_app_token)
    _authorized(search_id, x_search_token, owner_id)
    store.disable_search(search_id)
    return {"id": search_id, "enabled": False}


@app.get("/api/admin/usage")
def usage_report(
    day: str | None = None,
    limit: int = 100,
    x_admin_token: str | None = Header(default=None),
):
    _admin(x_admin_token)
    selected_day = None
    if day:
        try:
            selected_day = date.fromisoformat(day)
        except ValueError as exc:
            raise HTTPException(422, detail="day must use YYYY-MM-DD") from exc
    return store.usage_report(selected_day, limit)
