from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .schema import SearchProfile
from .store import Store
from .wizard import next_questions
from .worker import run_search

app = FastAPI(title="Universal Vehicle Search", version="0.1.0")
store = Store(os.getenv("SEARCH_DB", "data/searches.db"))
STATIC = Path(__file__).with_name("static")


class WizardRequest(BaseModel):
    profile: dict = Field(default_factory=dict)


class CreateSearchRequest(BaseModel):
    profile: dict


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
