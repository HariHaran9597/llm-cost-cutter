from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core import load_prices


def score_output(scorer: str, expected: str, actual: str) -> tuple[float | None, str]:
    normalize = lambda value: " ".join(value.casefold().split())
    if scorer == "exact":
        passed = normalize(expected) == normalize(actual)
        return float(passed), "exact match" if passed else "output differs from expected"
    if scorer == "contains":
        passed = normalize(expected) in normalize(actual)
        return float(passed), "required text found" if passed else "required text missing"
    return None, "judge pending"


def estimate_cost(model: str, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0) -> float:
    prices = load_prices()["models"][model]
    cached = min(max(cached_input_tokens, 0), input_tokens)
    cached_rate = prices.get("cached_input", prices["input"])
    return ((input_tokens - cached) * prices["input"] + cached * cached_rate + output_tokens * prices["output"]) / 1_000_000


def result_summary(rows: list[dict[str, Any]], variants: list[dict[str, Any]], baseline_id: str) -> dict[str, Any]:
    grouped = {variant["id"]: [row for row in rows if row["variant_id"] == variant["id"]] for variant in variants}
    summaries = []
    for variant in variants:
        group = grouped[variant["id"]]
        scored = [row["score"] for row in group if row.get("score") is not None]
        passes = sum(score >= (4 if variant.get("scorer") == "judge" else 1) for score in scored)
        cost = sum(row["estimated_total_cost_usd"] for row in group)
        base = {"id": variant["id"], "name": variant["name"], "model": variant["model"], "system_prompt": variant["system_prompt"], "user_template": variant["user_template"]}
        base.update({"cases": len(group), "scored_cases": len(scored), "passed": passes, "quality_rate": passes / len(group) if group else None, "mean_judge_score": round(sum(scored) / len(scored), 2) if scored and variant.get("scorer") == "judge" else None, "estimated_total_cost_usd": round(cost, 9), "estimated_cost_per_case_usd": round(cost / len(group), 9) if group else 0, "p50_latency_ms": _percentile([row["latency_ms"] for row in group], .5), "p95_latency_ms": _percentile([row["latency_ms"] for row in group], .95), "errors": sum(row["status"] != "ok" for row in group)})
        summaries.append(base)
    baseline = next((s for s in summaries if s["id"] == baseline_id), summaries[0] if summaries else None)
    for summary in summaries:
        if baseline and baseline["estimated_total_cost_usd"]:
            summary["savings_vs_baseline_rate"] = round(1 - summary["estimated_total_cost_usd"] / baseline["estimated_total_cost_usd"], 4)
        else:
            summary["savings_vs_baseline_rate"] = 0
        summary["quality_delta_vs_baseline"] = round(summary["quality_rate"] - baseline["quality_rate"], 4) if baseline and summary["quality_rate"] is not None and baseline["quality_rate"] is not None else None
    eligible = [s for s in summaries if s["quality_rate"] is not None and baseline and s["quality_rate"] >= baseline["quality_rate"] - .05 and s["errors"] == 0 and s["scored_cases"] == s["cases"]]
    recommendation = min(eligible, key=lambda s: s["estimated_cost_per_case_usd"], default=baseline)
    return {"baseline_variant_id": baseline["id"] if baseline else None, "variants": summaries, "recommendation": recommendation["id"] if recommendation else None, "recommendation_rule": "lowest cost per case among variants within 5 percentage points of baseline quality and with no provider errors; otherwise keep baseline", "actual_latency": True}


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0
    values.sort()
    return round(values[min(len(values) - 1, int((len(values) - 1) * p))], 2)


def save_experiment(path: str, name: str, task_type: str, scorer: str, payload: dict[str, Any], results: dict[str, Any]) -> str:
    experiment_id = str(uuid.uuid4())
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO experiments VALUES (?, ?, ?, ?, ?, ?, ?)", (experiment_id, name, task_type, scorer, datetime.now(timezone.utc).isoformat(), json.dumps(payload), json.dumps(results)))
    return experiment_id


def list_experiments(path: str) -> list[dict[str, Any]]:
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT id,name,task_type,scorer,created_at,results_json FROM experiments ORDER BY created_at DESC LIMIT 50").fetchall()
    return [{"id": r[0], "name": r[1], "task_type": r[2], "scorer": r[3], "created_at": r[4], "results": json.loads(r[5])} for r in rows]


def get_experiment(path: str, experiment_id: str) -> dict[str, Any] | None:
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT id,name,task_type,scorer,created_at,payload_json,results_json FROM experiments WHERE id=?", (experiment_id,)).fetchone()
    return {"id": row[0], "name": row[1], "task_type": row[2], "scorer": row[3], "created_at": row[4], "payload": json.loads(row[5]), "results": json.loads(row[6])} if row else None


def delete_experiment(path: str, experiment_id: str) -> bool:
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM routing_policies WHERE experiment_id=?", (experiment_id,))
        cursor = db.execute("DELETE FROM experiments WHERE id=?", (experiment_id,))
    return cursor.rowcount > 0


def set_policy(path: str, task_type: str, model: str, system_prompt: str, user_template: str, experiment_id: str, variant_id: str, quality_rate: float, cost_per_case_usd: float) -> None:
    with sqlite3.connect(path) as db:
        db.execute("INSERT OR REPLACE INTO routing_policies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (task_type, model, system_prompt, user_template, experiment_id, variant_id, quality_rate, cost_per_case_usd, datetime.now(timezone.utc).isoformat()))


def get_policy(path: str, task_type: str) -> dict[str, Any] | None:
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT task_type,model,system_prompt,user_template,experiment_id,variant_id,quality_rate,cost_per_case_usd,updated_at FROM routing_policies WHERE task_type=?", (task_type,)).fetchone()
    return dict(zip(("task_type","model","system_prompt","user_template","experiment_id","variant_id","quality_rate","cost_per_case_usd","updated_at"), row)) if row else None


def fill_template(template: str, value: str) -> str:
    return template.replace("{{input}}", value)
