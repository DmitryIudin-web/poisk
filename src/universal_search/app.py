from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .ratelimit import LIMITER, client_ip
from .schema import SearchProfile
from .store import Store
from .wizard import next_questions
from .worker import run_search

app = FastAPI(title="Universal Vehicle Search", version="0.2.0")
store = Store(os.getenv("SEARCH_DB", "data/searches.db"))
STATIC = Path(__file__).with_name("static")


class WizardRequest(BaseModel):
    profile: dict = Field(default_factory=dict)


class CreateSearchRequest(BaseModel):
    profile: dict


@app.middleware("http")
async def public_rate_limit(request: Request, call_next):
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    ip = client_ip(request)
    per_minute = int(os.getenv("PUBLIC_RATE_LIMIT_PER_MINUTE", "60"))
    if not LIMITER.allow(ip, "api", per_minute, 60):
        return JSONResponse({"detail": "rate limit exceeded"}, status_code=429, headers={"Retry-After": "60"})
    if request.method == "POST" and request.url.path == "/api/searches":
        create_per_hour = int(os.getenv("CREATE_SEARCH_LIMIT_PER_HOUR", "12"))
        if not LIMITER.allow(ip, "create-search", create_per_hour, 3600):
            return JSONResponse({"detail": "search creation rate limit exceeded"}, status_code=429, headers={"Retry-After": "3600"})
    return await call_next(request)


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


@app.post("/api/searches")
def create_search(req: CreateSearchRequest):
    profile = SearchProfile.from_dict(req.profile)
    errors = profile.validate()
    if errors:
        raise HTTPException(422, detail=errors)
    search_id, owner_token, bind_code = store.create_search(profile)
    return {
        "id": search_id,
        "owner_token": owner_token,
        "telegram_bind_code": bind_code,
        "telegram_command": f"/bind {bind_code}",
    }


def _authorized(search_id: str, token: str | None) -> dict:
    if not token or not store.authorize(search_id, token):
        raise HTTPException(403, detail="invalid owner token")
    row = store.get_search(search_id)
    assert row is not None
    return dict(row)


@app.get("/api/searches/{search_id}")
def get_search(search_id: str, x_search_token: str | None = Header(default=None)):
    row = _authorized(search_id, x_search_token)
    return {
        "id": row["id"],
        "profile": json.loads(row["profile_json"]),
        "last_run_at": row["last_run_at"],
        "next_run_at": row["next_run_at"],
        "telegram_bound": bool(row["telegram_chat_id"]),
    }


@app.get("/api/searches/{search_id}/results")
def get_results(search_id: str, x_search_token: str | None = Header(default=None)):
    _authorized(search_id, x_search_token)
    return {"results": store.list_results(search_id)}


@app.post("/api/searches/{search_id}/run")
def run_now(search_id: str, x_search_token: str | None = Header(default=None)):
    row = _authorized(search_id, x_search_token)
    profile = SearchProfile.from_dict(json.loads(row["profile_json"]))
    return run_search(store, search_id, profile, row["telegram_chat_id"])
