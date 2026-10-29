from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PRICE_FILE = ROOT / "config" / "pricing.json"


def load_prices() -> dict[str, Any]:
    return json.loads(PRICE_FILE.read_text(encoding="utf-8"))


def estimated_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = load_prices()["models"][model]
    return (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000


def normalized_messages(messages: list[dict[str, str]]) -> str:
    return json.dumps([{ "role": x["role"], "content": x["content"].strip()} for x in messages], ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def request_hash(messages: list[dict[str, str]], model: str, temperature: float, max_tokens: int) -> str:
    payload = json.dumps({"messages": json.loads(normalized_messages(messages)), "model": model, "temperature": temperature, "max_tokens": max_tokens}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def choose_model(text: str, cheap_model: str, baseline_model: str, requested_tier: str | None) -> tuple[str, str]:
    if requested_tier == "quality":
        return baseline_model, "caller_requested_quality_tier"
    content = text.lower()
    if any(term in content for term in ("reason", "prove", "compare", "trade-off", "tradeoff", "calculate", "why", "explain", "plan", "analyze", "analyse", "how many", "how much", "percent", "%", "remain")):
        return baseline_model, "complexity_cue_baseline_fallback"
    if len(content) > 1800:
        return baseline_model, "long_input_baseline_fallback"
    if any(term in content for term in ("extract ", "classify ", "summarize ", "summarise ")):
        return cheap_model, "bounded_request_eligible_for_cheap_model"
    return baseline_model, "unrecognized_task_baseline_fallback"


@dataclass
class Settings:
    upstream_base_url: str = os.getenv("UPSTREAM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    upstream_api_key: str = os.getenv("UPSTREAM_API_KEY", "")
    gateway_api_token: str = os.getenv("GATEWAY_API_TOKEN", "")
    cheap_model: str = os.getenv("CHEAP_MODEL", "gpt-4.1-mini")
    baseline_model: str = os.getenv("BASELINE_MODEL", "gpt-4.1")
    demo_mode: bool = os.getenv("LLMCUTTER_DEMO_MODE", "true").lower() in {"true", "1", "yes"}
    max_requests_per_minute: int = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "30"))
    max_daily_spend_usd: float = float(os.getenv("MAX_DAILY_SPEND_USD", "5.00"))
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
    max_retries: int = int(os.getenv("MAX_RETRIES", "1"))
    database_path: str = os.getenv("DATABASE_PATH", str(ROOT / "data" / "usage.sqlite3"))


def init_db(path: str) -> None:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS usage (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, request_hash TEXT NOT NULL,
            model TEXT NOT NULL, route_reason TEXT NOT NULL, cache_hit INTEGER NOT NULL,
            input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
            latency_ms REAL NOT NULL, estimated_cost_usd REAL NOT NULL,
            outcome TEXT NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS usage_created_at_idx ON usage(created_at)")
        db.execute("CREATE TABLE IF NOT EXISTS exact_cache (cache_key TEXT PRIMARY KEY, response_json TEXT NOT NULL, created_at TEXT NOT NULL)")


def write_usage(path: str, row: dict[str, Any]) -> None:
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO usage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(row[k] for k in (
            "id", "created_at", "request_hash", "model", "route_reason", "cache_hit", "input_tokens", "output_tokens", "latency_ms", "estimated_cost_usd", "outcome"
        )))


def get_cache(path: str, key: str) -> dict[str, Any] | None:
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT response_json FROM exact_cache WHERE cache_key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def set_cache(path: str, key: str, response: dict[str, Any]) -> None:
    with sqlite3.connect(path) as db:
        db.execute("INSERT OR REPLACE INTO exact_cache VALUES (?, ?, ?)", (key, json.dumps(response), datetime.now(timezone.utc).isoformat()))


def usage_summary(path: str) -> dict[str, Any]:
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT model, cache_hit, latency_ms, estimated_cost_usd, outcome FROM usage ORDER BY created_at DESC LIMIT 1000").fetchall()
    costs = [r[3] for r in rows if r[4] == "ok"]
    latencies = sorted(r[2] for r in rows if r[4] == "ok")
    def percentile(p: float) -> float:
        return round(latencies[min(len(latencies)-1, int((len(latencies)-1)*p))], 2) if latencies else 0
    return {
        "requests": len(rows), "cache_hits": sum(r[1] for r in rows),
        "cache_hit_rate": round(sum(r[1] for r in rows)/len(rows), 4) if rows else 0,
        "estimated_spend_usd": round(sum(costs), 8), "p50_latency_ms": percentile(.50), "p95_latency_ms": percentile(.95),
        "model_mix": {model: sum(1 for r in rows if r[0] == model) for model in sorted({r[0] for r in rows})},
    }


def daily_spend(path: str) -> float:
    start = datetime.now(timezone.utc).date().isoformat()
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM usage WHERE created_at >= ? AND outcome='ok'", (start,)).fetchone()
    return float(row[0])


def token_estimate(messages: list[dict[str, str]]) -> int:
    # Conservative fallback for compatible providers that omit usage metadata.
    return max(1, sum(len(re.findall(r"\w+|[^\w\s]", m["content"])) for m in messages))
