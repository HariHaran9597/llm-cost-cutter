from __future__ import annotations

import asyncio
import hmac
import json
import time
import uuid
from contextlib import asynccontextmanager
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from dotenv import load_dotenv

from app.core import Settings, daily_spend, estimated_cost, get_cache, init_db, request_hash, choose_model, set_cache, token_estimate, usage_summary, write_usage

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
load_dotenv(ROOT / ".env")
settings = Settings()
WINDOW: deque[float] = deque()
WINDOW_LOCK = asyncio.Lock()


class ChatMessage(BaseModel):
    role: Literal["system", "user"]
    content: str = Field(min_length=1, max_length=12000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=512, ge=1, le=2048)

    @model_validator(mode="after")
    def require_user_message(self):
        if not any(m.role == "user" for m in self.messages):
            raise ValueError("at least one user message is required")
        return self


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db(settings.database_path)
    yield


app = FastAPI(title="LLM Cost Cutter", version="0.1.0", docs_url=None, redoc_url=None, lifespan=lifespan)


async def authorize(authorization: str | None) -> None:
    if not settings.gateway_api_token or not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="valid gateway bearer token required")
    if not hmac.compare_digest(authorization[7:], settings.gateway_api_token):
        raise HTTPException(status_code=401, detail="valid gateway bearer token required")


async def throttle() -> None:
    now = time.monotonic()
    async with WINDOW_LOCK:
        while WINDOW and WINDOW[0] <= now - 60:
            WINDOW.popleft()
        if len(WINDOW) >= settings.max_requests_per_minute:
            raise HTTPException(status_code=429, detail="local request limit reached")
        WINDOW.append(now)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    return response


@app.get("/health")
def health():
    return {"status": "ok", "demo_mode": settings.demo_mode}


@app.get("/api/usage")
async def api_usage(authorization: str | None = Header(default=None)):
    await authorize(authorization)
    return usage_summary(settings.database_path)


@app.post("/v1/chat/completions")
async def chat_completions(body: ChatRequest, request: Request, authorization: str | None = Header(default=None)):
    await authorize(authorization)
    await throttle()
    if settings.demo_mode:
        raise HTTPException(status_code=503, detail="live inference is disabled in demo mode")
    if not settings.upstream_api_key:
        raise HTTPException(status_code=503, detail="upstream provider is not configured")
    messages = [m.model_dump() for m in body.messages]
    joined = "\n".join(m["content"] for m in messages)
    model, reason = choose_model(joined, settings.cheap_model, settings.baseline_model, request.headers.get("x-costcutter-tier"))
    key = request_hash(messages, model, body.temperature, body.max_tokens)
    started = time.perf_counter()
    cached = get_cache(settings.database_path, key)
    if cached:
        elapsed = (time.perf_counter() - started) * 1000
        cached["costcutter"] = {"model": model, "route_reason": "exact_cache_hit", "cache_hit": True, "estimated_cost_usd": 0, "latency_ms": round(elapsed, 2)}
        write_usage(settings.database_path, {"id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(), "request_hash": key, "model": model, "route_reason": "exact_cache_hit", "cache_hit": 1, "input_tokens": 0, "output_tokens": 0, "latency_ms": elapsed, "estimated_cost_usd": 0, "outcome": "ok"})
        return cached
    if daily_spend(settings.database_path) >= settings.max_daily_spend_usd:
        raise HTTPException(status_code=429, detail="daily estimated spend limit reached")
    payload = {"model": model, "messages": messages, "temperature": body.temperature, "max_tokens": body.max_tokens}
    last_error = None
    for attempt in range(settings.max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
                upstream = await client.post(f"{settings.upstream_base_url}/chat/completions", json=payload, headers={"Authorization": f"Bearer {settings.upstream_api_key}"})
            if upstream.status_code >= 500 and attempt < settings.max_retries:
                await asyncio.sleep(.15 * (attempt + 1))
                continue
            if upstream.status_code >= 400:
                raise HTTPException(status_code=502, detail=f"upstream returned HTTP {upstream.status_code}")
            result = upstream.json()
            usage = result.get("usage", {})
            input_tokens = int(usage.get("prompt_tokens", token_estimate(messages)))
            output_text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            output_tokens = int(usage.get("completion_tokens", token_estimate([{"content": output_text}])))
            cost = estimated_cost(model, input_tokens, output_tokens)
            elapsed = (time.perf_counter() - started) * 1000
            metadata = {"model": model, "route_reason": reason, "cache_hit": False, "input_tokens": input_tokens, "output_tokens": output_tokens, "estimated_cost_usd": cost, "latency_ms": round(elapsed, 2)}
            result["costcutter"] = metadata
            set_cache(settings.database_path, key, result)
            write_usage(settings.database_path, {"id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(), "request_hash": key, "model": model, "route_reason": reason, "cache_hit": 0, "input_tokens": input_tokens, "output_tokens": output_tokens, "latency_ms": elapsed, "estimated_cost_usd": cost, "outcome": "ok"})
            return result
        except httpx.TimeoutException:
            last_error = "upstream timed out"
            if attempt < settings.max_retries:
                continue
    raise HTTPException(status_code=504, detail=last_error or "upstream request failed")


@app.get("/api/demo-report")
def demo_report():
    path = ROOT / "docs" / "benchmark-report.json"
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
