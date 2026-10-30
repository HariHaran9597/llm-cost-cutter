from __future__ import annotations

import asyncio
import hmac
import json
import time
import uuid
import re
import sqlite3
import os
from contextlib import asynccontextmanager
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from dotenv import load_dotenv

from app.core import Settings, daily_spend, get_cache, init_db, request_hash, choose_model, set_cache, token_estimate, usage_summary, write_usage, load_prices
from app.lab import delete_experiment, estimate_cost, fill_template, get_experiment, get_policy, list_experiments, result_summary, save_experiment, score_output, set_policy

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


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    input: str = Field(min_length=1, max_length=6000)
    expected: str = Field(min_length=1, max_length=3000)


class EvalVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=100)
    system_prompt: str = Field(min_length=1, max_length=4000)
    user_template: str = Field(default="{{input}}", min_length=9, max_length=8000)

    @model_validator(mode="after")
    def template_has_input(self):
        if "{{input}}" not in self.user_template:
            raise ValueError("user_template must contain {{input}}")
        return self


class ExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    task_type: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9][a-z0-9_-]+$")
    cases: list[EvalCase] = Field(min_length=1, max_length=2000)
    variants: list[EvalVariant] = Field(min_length=2, max_length=4)
    scorer: Literal["exact", "contains", "judge"] = "exact"
    judge_model: str | None = None
    rubric: str | None = Field(default=None, max_length=2000)
    max_output_tokens: int = Field(default=128, ge=16, le=512)
    max_budget_usd: float = Field(default=0.10, gt=0, le=5)
    provider_api_keys: dict[str, SecretStr] = Field(default_factory=dict, exclude=True)

    @field_validator("provider_api_keys")
    @classmethod
    def validate_provider_keys(cls, value):
        allowed = {"openai", "anthropic", "moonshot", "groq"}
        if len(value) > len(allowed) or any(provider not in allowed or len(key.get_secret_value()) > 500 for provider, key in value.items()):
            raise ValueError("provider key settings are invalid")
        return value

    @model_validator(mode="after")
    def validate_experiment(self):
        if self.scorer == "judge" and (not self.judge_model or not self.rubric):
            raise ValueError("judge scoring requires judge_model and rubric")
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("case ids must be unique")
        if len({v.id for v in self.variants}) != len(self.variants):
            raise ValueError("variant ids must be unique")
        return self


class PromoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_id: str
    variant_id: str


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db(settings.database_path)
    yield


app = FastAPI(title="LLM Cost Cutter", version="0.2.0", docs_url=None, redoc_url=None, lifespan=lifespan)


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


@app.get("/api/config")
def api_config():
    prices = load_prices()
    return {"models": prices["models"], "providers": prices["providers"], "price_source": prices["source"], "prices_checked_on": prices["checked_on"], "live_enabled": not settings.demo_mode, "demo_mode": settings.demo_mode, "judge_model": settings.judge_model}


@app.get("/api/models/available")
async def api_available_models(authorization: str | None = Header(default=None)):
    await authorize(authorization)
    configured = load_prices()["models"]
    if settings.demo_mode or not settings.upstream_api_key:
        return {"verified":False,"models":configured,"unpriced_models":[],"message":"Provider model access is not checked while live mode is disabled."}
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response=await client.get(f"{settings.upstream_base_url}/models",headers={"Authorization":f"Bearer {settings.upstream_api_key}"})
        if response.status_code>=400:
            return {"verified":False,"models":configured,"unpriced_models":[],"message":f"Provider model access could not be checked (HTTP {response.status_code}); verify the configured models manually."}
        available={item.get("id") for item in response.json().get("data",[]) if isinstance(item,dict)}
        matched={name:rate for name,rate in configured.items() if name in available}
        unpriced=sorted(name for name in available if isinstance(name,str) and name.startswith(("gpt-", "o")) and name not in configured)
        return {"verified":True,"models":matched,"unpriced_models":unpriced,"message":"Only models returned by the provider and present in the local price table are selectable."}
    except (httpx.HTTPError,ValueError):
        return {"verified":False,"models":configured,"unpriced_models":[],"message":"Provider model access could not be checked; verify the configured models manually."}


def _preflight_cost(body: ExperimentRequest) -> float:
    total = 0.0
    for variant in body.variants:
        for case in body.cases:
            messages = [{"role":"system","content":variant.system_prompt}, {"role":"user","content":fill_template(variant.user_template, case.input)}]
            input_count = token_estimate(messages)
            total += estimate_cost(variant.model, input_count, body.max_output_tokens)
            if body.scorer == "judge":
                judge_text = body.rubric + case.expected + case.input + (body.max_output_tokens * 4 * "x")
                total += estimate_cost(body.judge_model, token_estimate([{"role":"user","content":judge_text}]), 128)
    return total


def _provider_cost(model: str, usage: dict, messages: list[dict[str, str]], output: str) -> tuple[int, int, float, bool]:
    input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", token_estimate(messages))))
    output_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", token_estimate([{"role":"assistant","content":output}]))))
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    cached = int(details.get("cached_tokens", 0))
    return input_tokens, output_tokens, estimate_cost(model, input_tokens, output_tokens, cached), bool(usage)


async def _provider_completion(model: str, messages: list[dict[str, str]], max_tokens: int, api_key: str | None = None) -> dict:
    model_config = load_prices()["models"][model]
    provider = model_config.get("provider", "openai")
    key = api_key or (settings.upstream_api_key if provider == "openai" else os.getenv({"anthropic": "ANTHROPIC_API_KEY", "moonshot": "MOONSHOT_API_KEY", "groq": "GROQ_API_KEY"}.get(provider, ""), ""))
    if not key:
        raise RuntimeError(f"add a {provider} API key in BYOK settings")
    provider_model = model_config.get("provider_model", model)
    if provider == "anthropic":
        system = "\n\n".join(message["content"] for message in messages if message["role"] == "system")
        payload = {"model": provider_model, "max_tokens": max_tokens, "system": system, "messages": [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]}
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    else:
        provider_config = load_prices()["providers"].get(provider)
        if not provider_config:
            raise RuntimeError("provider configuration is missing")
        payload = {"model": provider_model, "messages": messages, "temperature": 0, "max_completion_tokens": max_tokens, "store": False} if provider == "openai" else {"model": provider_model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
        if provider == "groq":
            payload.update({"max_completion_tokens": max_tokens, "reasoning_effort": "none", "reasoning_format": "hidden"})
        base_url = settings.upstream_base_url if provider == "openai" else provider_config["base_url"]
        url = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        response = await client.post(url, json=payload, headers=headers)
    if response.status_code >= 400:
        raise RuntimeError(f"provider returned HTTP {response.status_code}")
    data = response.json()
    if provider == "anthropic":
        output = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        usage = {"input_tokens": data.get("usage", {}).get("input_tokens", 0), "output_tokens": data.get("usage", {}).get("output_tokens", 0)}
    else:
        output = data.get("choices", [{}])[0].get("message", {}).get("content")
        usage = data.get("usage", {})
    if not isinstance(output, str):
        raise RuntimeError("provider response did not contain text")
    return {"output": output, "usage": usage}


def _parse_judge(text: str) -> tuple[float | None, str]:
    match = re.search(r"\{.*?\}", text, re.S)
    if not match:
        return None, "judge returned invalid JSON; review manually"
    try:
        payload = json.loads(match.group())
        score = float(payload["score"])
        if not 1 <= score <= 5:
            raise ValueError
        return score, str(payload.get("reason", ""))[:800]
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None, "judge returned invalid score; review manually"


@app.post("/api/experiments/run")
async def run_experiment(body: ExperimentRequest, authorization: str | None = Header(default=None)):
    await authorize(authorization)
    if settings.demo_mode:
        raise HTTPException(status_code=503, detail="live experiments are disabled in demo mode")
    prices = load_prices()["models"]
    requested_models = {variant.model for variant in body.variants}
    if body.judge_model:
        requested_models.add(body.judge_model)
    unknown = requested_models - prices.keys()
    if unknown:
        raise HTTPException(status_code=422, detail={"message":"add a price entry before using these models", "models":sorted(unknown)})
    if len(body.cases) * len(body.variants) * (2 if body.scorer == "judge" else 1) > 12000:
        raise HTTPException(status_code=422, detail="experiment exceeds the 12,000-call safety limit")
    preflight = _preflight_cost(body)
    if preflight > body.max_budget_usd:
        raise HTTPException(status_code=422, detail={"message":"preflight estimate exceeds the experiment budget", "estimated_max_usd":round(preflight, 6), "max_budget_usd":body.max_budget_usd})
    await throttle()
    provider_slots = asyncio.Semaphore(4)

    async def run_one(variant: EvalVariant, case: EvalCase) -> dict:
            messages = [{"role":"system","content":variant.system_prompt}, {"role":"user","content":fill_template(variant.user_template, case.input)}]
            started = time.perf_counter()
            row = {"case_id":case.id,"variant_id":variant.id,"model":variant.model,"status":"ok","output":"","score":None,"score_reason":"","input_tokens":0,"output_tokens":0,"provider_usage_reported":False,"latency_ms":0,"judge_latency_ms":0,"estimated_model_cost_usd":0,"estimated_judge_cost_usd":0,"estimated_total_cost_usd":0}
            try:
                provider = prices[variant.model].get("provider", "openai")
                provider_key = body.provider_api_keys.get(provider)
                async with provider_slots:
                    result = await _provider_completion(variant.model, messages, body.max_output_tokens, provider_key.get_secret_value() if provider_key else None)
                row["output"] = result["output"]
                row["input_tokens"], row["output_tokens"], row["estimated_model_cost_usd"], row["provider_usage_reported"] = _provider_cost(variant.model, result["usage"], messages, result["output"])
                row["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
                if body.scorer in {"exact", "contains"}:
                    row["score"], row["score_reason"] = score_output(body.scorer, case.expected, result["output"])
                else:
                    judge_started = time.perf_counter()
                    judge_messages = [
                        {"role":"system","content":"You are a strict evaluator. Grade only the response against the reference and rubric. Return JSON only with score as an integer 1-5 and reason as a short string. A 4 or 5 means the answer satisfies the task without a material error."},
                        {"role":"user","content":f"Rubric:\n{body.rubric}\n\nInput:\n{case.input}\n\nReference:\n{case.expected}\n\nCandidate response:\n{result['output']}"},
                    ]
                    judge_provider = prices[body.judge_model].get("provider", "openai")
                    judge_key = body.provider_api_keys.get(judge_provider)
                    async with provider_slots:
                        judged = await _provider_completion(body.judge_model, judge_messages, 128, judge_key.get_secret_value() if judge_key else None)
                    row["judge_latency_ms"] = round((time.perf_counter() - judge_started) * 1000, 2)
                    row["score"], row["score_reason"] = _parse_judge(judged["output"])
                    _, _, row["estimated_judge_cost_usd"], _ = _provider_cost(body.judge_model, judged["usage"], judge_messages, judged["output"])
                row["estimated_total_cost_usd"] = row["estimated_model_cost_usd"] + row["estimated_judge_cost_usd"]
            except (httpx.TimeoutException, httpx.HTTPError, RuntimeError, ValueError) as exc:
                row["status"] = "error"
                row["error"] = str(exc)[:200]
                row["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            return row

    rows = await asyncio.gather(*(run_one(variant, case) for variant in body.variants for case in body.cases))
    baseline_id = body.variants[0].id
    report = result_summary(rows, [dict(v.model_dump(), scorer=body.scorer) for v in body.variants], baseline_id)
    payload = body.model_dump(exclude={"provider_api_keys"})
    total_estimated_cost=sum(r["estimated_total_cost_usd"] for r in rows)
    experiment_id = save_experiment(settings.database_path, body.name, body.task_type, body.scorer, payload, {"report":report,"rows":rows,"preflight_estimate_usd":round(preflight,9),"estimated_total_cost_usd":round(total_estimated_cost,9)})
    return {"id":experiment_id,"name":body.name,"task_type":body.task_type,"scorer":body.scorer,"created_at":datetime.now(timezone.utc).isoformat(),"preflight_estimate_usd":round(preflight,9),"estimated_total_cost_usd":round(total_estimated_cost,9),"report":report,"rows":rows}


@app.get("/api/experiments")
async def api_experiments(authorization: str | None = Header(default=None)):
    await authorize(authorization)
    return list_experiments(settings.database_path)


@app.get("/api/experiments/{experiment_id}")
async def api_experiment(experiment_id: str, authorization: str | None = Header(default=None)):
    await authorize(authorization)
    experiment = get_experiment(settings.database_path, experiment_id)
    if not experiment:
        raise HTTPException(status_code=404, detail="experiment not found")
    return experiment


@app.delete("/api/experiments/{experiment_id}")
async def api_delete_experiment(experiment_id: str, authorization: str | None = Header(default=None)):
    await authorize(authorization)
    if not delete_experiment(settings.database_path, experiment_id):
        raise HTTPException(status_code=404, detail="experiment not found")
    return {"deleted":True}


@app.get("/api/policies")
async def api_policies(authorization: str | None = Header(default=None)):
    await authorize(authorization)
    with sqlite3.connect(settings.database_path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT task_type,model,experiment_id,variant_id,quality_rate,cost_per_case_usd AS estimated_cost_per_case_usd,updated_at FROM routing_policies ORDER BY task_type")]


@app.post("/api/policies/promote")
async def api_promote_policy(body: PromoteRequest, authorization: str | None = Header(default=None)):
    await authorize(authorization)
    experiment = get_experiment(settings.database_path, body.experiment_id)
    if not experiment:
        raise HTTPException(status_code=404, detail="experiment not found")
    candidate = next((row for row in experiment["results"]["report"]["variants"] if row["id"] == body.variant_id), None)
    if not candidate or candidate["quality_rate"] is None or candidate["errors"] or candidate["scored_cases"] != candidate["cases"]:
        raise HTTPException(status_code=422, detail="candidate has incomplete quality scores or contains provider errors")
    source_variant = next((variant for variant in experiment["payload"]["variants"] if variant["id"] == body.variant_id), None)
    set_policy(settings.database_path, experiment["task_type"], candidate["model"], source_variant["system_prompt"], source_variant["user_template"], experiment["id"], candidate["id"], candidate["quality_rate"], candidate["estimated_cost_per_case_usd"])
    return {"promoted":True,"policy":get_policy(settings.database_path, experiment["task_type"])}


@app.post("/v1/chat/completions")
async def chat_completions(body: ChatRequest, request: Request, authorization: str | None = Header(default=None)):
    await authorize(authorization)
    await throttle()
    if settings.demo_mode:
        raise HTTPException(status_code=503, detail="live inference is disabled in demo mode")
    messages = [m.model_dump() for m in body.messages]
    joined = "\n".join(m["content"] for m in messages)
    task_type = request.headers.get("x-costcutter-task", "").lower()
    policy = get_policy(settings.database_path, task_type) if task_type and re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,39}", task_type) else None
    if policy:
        model, reason = policy["model"], f"evaluated_policy:{task_type}"
        for message in messages:
            if message["role"] == "system":
                message["content"] = policy["system_prompt"]
                break
        else:
            messages.insert(0, {"role":"system", "content":policy["system_prompt"]})
        user_message = next((message for message in reversed(messages) if message["role"] == "user"), None)
        if user_message:
            user_message["content"] = fill_template(policy["user_template"], user_message["content"])
    else:
        model, reason = choose_model(joined, settings.cheap_model, settings.baseline_model, request.headers.get("x-costcutter-tier"))
    provider = load_prices()["models"].get(model, {}).get("provider", "openai")
    if provider != "openai" and not os.getenv({"anthropic": "ANTHROPIC_API_KEY", "moonshot": "MOONSHOT_API_KEY", "groq": "GROQ_API_KEY"}.get(provider, "")):
        raise HTTPException(status_code=503, detail=f"{provider} provider key is not configured for gateway use")
    if provider == "openai" and not settings.upstream_api_key:
        raise HTTPException(status_code=503, detail="upstream provider is not configured")
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
    if provider != "openai":
        try:
            completion = await _provider_completion(model, messages, body.max_tokens)
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        usage = completion["usage"]
        input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", token_estimate(messages))))
        output_text = completion["output"]
        output_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", token_estimate([{"content": output_text}]))))
        cost = estimate_cost(model, input_tokens, output_tokens)
        elapsed = (time.perf_counter() - started) * 1000
        result = {"id": f"costcutter-{uuid.uuid4()}", "model": model, "choices": [{"message": {"role": "assistant", "content": output_text}, "finish_reason": "stop"}], "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens, "total_tokens": input_tokens + output_tokens}}
        result["costcutter"] = {"model": model, "route_reason": reason, "cache_hit": False, "input_tokens": input_tokens, "output_tokens": output_tokens, "estimated_cost_usd": cost, "cost_basis": "provider usage" if usage else "estimated token count; provider omitted usage", "latency_ms": round(elapsed, 2)}
        set_cache(settings.database_path, key, result)
        write_usage(settings.database_path, {"id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(), "request_hash": key, "model": model, "route_reason": reason, "cache_hit": 0, "input_tokens": input_tokens, "output_tokens": output_tokens, "latency_ms": elapsed, "estimated_cost_usd": cost, "outcome": "ok"})
        return result
    payload = {"model": model, "messages": messages, "temperature": body.temperature, "max_completion_tokens": body.max_tokens}
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
            usage_details = usage.get("prompt_tokens_details") or {}
            cost = estimate_cost(model, input_tokens, output_tokens, int(usage_details.get("cached_tokens", 0)))
            elapsed = (time.perf_counter() - started) * 1000
            metadata = {"model": model, "route_reason": reason, "cache_hit": False, "input_tokens": input_tokens, "output_tokens": output_tokens, "estimated_cost_usd": cost, "cost_basis": "provider usage" if usage else "estimated token count; provider omitted usage", "latency_ms": round(elapsed, 2)}
            result["costcutter"] = metadata
            set_cache(settings.database_path, key, result)
            write_usage(settings.database_path, {"id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(), "request_hash": key, "model": model, "route_reason": reason, "cache_hit": 0, "input_tokens": input_tokens, "output_tokens": output_tokens, "latency_ms": elapsed, "estimated_cost_usd": cost, "outcome": "ok"})
            return result
        except httpx.TimeoutException:
            last_error = "upstream timed out"
            if attempt < settings.max_retries:
                continue
    raise HTTPException(status_code=504, detail=last_error or "upstream request failed")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
