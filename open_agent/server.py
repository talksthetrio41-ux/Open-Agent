"""FastAPI GUI + agent API for Open Agent."""

from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from open_agent.agent import OpenAgent
from open_agent.config import (
    PROJECT_ROOT,
    ensure_access_pin,
    ensure_dirs,
    get_env,
    load_env,
    masked,
)
from open_agent.github_fs import GitHubFS

logger = logging.getLogger("OpenAgentServer")

PUBLIC_DIR = PROJECT_ROOT / "public"
INDEX_FILE = PUBLIC_DIR / "index.html"

agent = OpenAgent()
SESSION_COOKIE = "oa_session"


class QwenLoginBody(BaseModel):
    username: str
    password: str


class GithubBody(BaseModel):
    token: str
    repo: str


class ChatBody(BaseModel):
    message: str
    max_steps: Optional[int] = None


class PinBody(BaseModel):
    pin: str


def create_app() -> FastAPI:
    load_env()
    ensure_dirs()

    app = FastAPI(title="Open Agent", version="2.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if (PUBLIC_DIR / "static").is_dir():
        app.mount("/static", StaticFiles(directory=str(PUBLIC_DIR / "static")), name="static")

    @app.middleware("http")
    async def pin_gate(request: Request, call_next):
        path = request.url.path
        if path.startswith("/static/") or path in ("/api/health", "/api/unlock", "/favicon.ico"):
            return await call_next(request)
        # HTML shell is always served so the PIN modal can render
        if path == "/" or path == "/index.html":
            return await call_next(request)
        if path.startswith("/api/"):
            pin = ensure_access_pin()
            cookie = request.cookies.get(SESSION_COOKIE, "")
            header = request.headers.get("x-open-agent-pin", "")
            if cookie == _session_token(pin) or header == pin:
                return await call_next(request)
            return JSONResponse({"ok": False, "error": "locked", "need_pin": True}, status_code=401)
        return await call_next(request)

    @app.get("/api/health")
    async def health():
        return {"ok": True, "service": "open-agent"}

    @app.post("/api/unlock")
    async def unlock(body: PinBody):
        pin = ensure_access_pin()
        if (body.pin or "").strip() != pin:
            raise HTTPException(status_code=403, detail="Wrong PIN")
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            SESSION_COOKIE,
            _session_token(pin),
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 14,
        )
        return resp

    @app.get("/api/state")
    async def state():
        data = agent.public_state()
        data["qwen_username"] = data.get("qwen_username") or get_env("QWEN_USERNAME")
        data["github_token_masked"] = masked(get_env("GITHUB_TOKEN"), 6)
        data["messages"] = agent.session.history()
        data["unlocked"] = True
        return data

    @app.get("/api/boot")
    async def boot():
        """Public-ish boot probe used by the GUI after unlock."""
        return {
            "ok": True,
            "has_qwen_creds": bool(get_env("QWEN_USERNAME") and get_env("QWEN_PASSWORD")),
            "qwen_username": get_env("QWEN_USERNAME"),
            "github_repo": get_env("GITHUB_REPO"),
            "github_linked": agent.harness.fs.status().linked,
        }

    @app.post("/api/qwen/login")
    async def qwen_login(body: QwenLoginBody):
        if not body.username or not body.password:
            raise HTTPException(status_code=400, detail="Email and password required")
        try:
            result = await agent.connect_qwen(body.username.strip(), body.password)
            return {"ok": result.get("ok") == "true", **result, "state": agent.public_state()}
        except Exception as exc:
            logger.exception("qwen login failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/github")
    async def github_setup(body: GithubBody):
        try:
            msg = GitHubFS().configure(body.token.strip(), body.repo.strip())
            agent.harness.fs = GitHubFS()
            return {"ok": True, "message": msg, "state": agent.public_state()}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/github/sync")
    async def github_sync():
        try:
            msg = agent.harness.fs.sync()
            return {"ok": True, "message": msg, "state": agent.public_state()}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/chat")
    async def chat(body: ChatBody):
        async def gen():
            async for event in agent.run_user_task(body.message, max_steps=body.max_steps):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield "data: {\"type\":\"end\"}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/compact")
    async def compact():
        async def gen():
            async for event in agent.compact():
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield "data: {\"type\":\"end\"}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/clear")
    async def clear():
        await agent.clear()
        return {"ok": True, "state": agent.public_state(), "messages": agent.session.history()}

    @app.post("/api/stop")
    async def stop():
        agent._cancel.set()
        return {"ok": True}

    @app.get("/")
    async def index():
        if not INDEX_FILE.exists():
            return JSONResponse({"error": "GUI missing"}, status_code=500)
        return FileResponse(str(INDEX_FILE))

    return app


def _session_token(pin: str) -> str:
    secret = get_env("OPEN_AGENT_PIN") or pin
    # Stable per-process token derived from pin so refresh keeps the session
    raw = f"oa::{secret}::{os.getpid() // 10000}"
    return secrets.token_hex(8) and _stable_token(raw)


def _stable_token(raw: str) -> str:
    import hashlib

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


app = create_app()
