from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import os
import random
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import load_prices  # noqa: E402
from app.main import _provider_completion  # noqa: E402
from app.statistics import paired_bootstrap_delta, wilson_interval  # noqa: E402

SOURCE_REVISION = "9d081458ff52e53cf7e848f414e6e9344e4e6696"
SOURCE_URL = f"https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/{SOURCE_REVISION}/banking_data/test.csv"
SOURCE_SHA256 = "d12d6e3bc4c3103966ae786dc435913c0c563dfa328f5a3646d0e62cfeeb474d"
SEED = 20261030
PER_INTENT = 5
MODELS = ["gpt-6-luna", "claude-haiku-5-5", "kimi-k2.6"]
SYSTEM = """Classify the banking customer message into exactly one intent from the list below. Return only the exact intent label, with no punctuation or explanation.
Allowed intents: {intents}"""


def load_cases() -> list[dict[str, str]]:
    source = ROOT / "benchmarks" / "banking77_test.csv"
    if not source.exists():
        raise SystemExit(f"Missing pinned dataset file: {source}")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise SystemExit(f"Dataset checksum mismatch: expected {SOURCE_SHA256}, got {digest}")
    with source.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    buckets: dict[str, list[str]] = {}
    for row in rows:
        buckets.setdefault(row["category"], []).append(row["text"])
    rng = random.Random(SEED)
    cases = []
    for label in sorted(buckets):
        candidates = buckets[label][:]
        rng.shuffle(candidates)
        for text in candidates[:PER_INTENT]:
            case_id = hashlib.sha256(text.encode()).hexdigest()[:16]
            cases.append({"id": case_id, "input": text, "expected": label})
    return cases


def build_prompt(cases: list[dict[str, str]]) -> str:
    return SYSTEM.format(intents=", ".join(sorted({case["expected"] for case in cases})))


def cost_for(model: str, in_tokens: int, out_tokens: int) -> float:
    rate = load_prices()["models"][model]
    return (in_tokens * rate["input"] + out_tokens * rate["output"]) / 1_000_000


def estimated_budget(cases: list[dict[str, str]]) -> dict[str, float]:
    prompt = build_prompt(cases)
    results = {}
    for model in MODELS:
        total = 0.0
        for case in cases:
            input_tokens = math.ceil((len(prompt) + len(case["input"])) / 3.2) + 8
            total += cost_for(model, input_tokens, 32)
        results[model] = round(total, 6)
    return results


def accuracy_metrics(rows: list[dict]) -> dict:
    labels = sorted({row["expected"] for row in rows})
    counts = {label: Counter() for label in labels}
    for row in rows:
        counts[row["expected"]]["total"] += 1
        if row["passed"]:
            counts[row["expected"]]["tp"] += 1
        elif row["output"] is not None:
            if row["output"] in counts:
                counts[row["output"]]["fp"] += 1
            counts[row["expected"]]["fn"] += 1
        else:
            counts[row["expected"]]["fn"] += 1
    f1s = []
    for label in labels:
        tp, fp, fn = counts[label]["tp"], counts[label]["fp"], counts[label]["fn"]
        f1s.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0)
    passed = sum(row["passed"] for row in rows)
    ci = wilson_interval(passed, len(rows))
    latencies = sorted(row["latency_ms"] for row in rows if row["status"] == "ok")
    errors = sum(row["status"] != "ok" for row in rows)
    cost = sum(row.get("estimated_cost_usd", 0) for row in rows)
    return {
        "cases": len(rows), "correct": passed, "accuracy": passed / len(rows),
        "accuracy_ci_95_wilson": list(ci) if ci else None,
        "macro_f1": sum(f1s) / len(f1s) if f1s else None,
        "errors": errors, "successful_calls": len(rows) - errors,
        "estimated_cost_usd": round(cost, 8),
        "estimated_cost_per_1000_requests_usd": round(cost / len(rows) * 1000, 6) if rows else None,
        "p50_latency_ms_successes_only": latencies[int((len(latencies) - 1) * .50)] if latencies else None,
        "p95_latency_ms_successes_only": latencies[math.ceil(.95 * len(latencies)) - 1] if latencies else None,
    }


async def run(args: argparse.Namespace) -> None:
    cases = load_cases()
    estimates = estimated_budget(cases)
    print(json.dumps({"dataset": "PolyAI BANKING77 official test split", "sample_cases": len(cases), "intents": 77, "per_intent": PER_INTENT, "seed": SEED, "models": MODELS, "approximate_cost_usd": estimates}, indent=2))
    if not args.execute:
        print("Dry run only. Add --execute --max-budget-usd <cap> to submit live calls.")
        return
    ceiling = sum(estimates.values())
    if args.max_budget_usd is None or args.max_budget_usd < ceiling:
        raise SystemExit(f"Set --max-budget-usd at or above the preflight estimate (${ceiling:.4f}); no provider calls were made.")
    env_by_provider = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "moonshot": "MOONSHOT_API_KEY"}
    prices = load_prices()["models"]
    missing = sorted({env_by_provider[prices[model]["provider"]] for model in MODELS if not os.getenv(env_by_provider[prices[model]["provider"]])})
    if missing:
        raise SystemExit(f"Missing provider keys: {', '.join(missing)}. No provider calls were made.")

    labels_prompt = build_prompt(cases)
    all_rows: dict[str, list[dict]] = {}
    slots = asyncio.Semaphore(4)
    for model in MODELS:
        provider = prices[model]["provider"]
        key = os.environ[env_by_provider[provider]]
        rows = []
        print(f"Running {model}: {len(cases)} requests", flush=True)
        async def run_one(case: dict[str, str]) -> dict:
            messages = [{"role": "system", "content": labels_prompt}, {"role": "user", "content": case["input"]}]
            started = time.perf_counter()
            row = {"case_id": case["id"], "expected": case["expected"], "status": "error", "output": None, "passed": False, "latency_ms": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0}
            try:
                async with slots:
                    result = await _provider_completion(model, messages, 32, key)
                output = result["output"].strip().strip("`\"' ")
                usage = result["usage"]
                input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", math.ceil((len(labels_prompt) + len(case["input"])) / 3.2) + 8)))
                output_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", math.ceil(len(output) / 3.2))))
                row.update({"status": "ok", "output": output, "passed": output.casefold() == case["expected"].casefold(), "input_tokens": input_tokens, "output_tokens": output_tokens, "estimated_cost_usd": cost_for(model, input_tokens, output_tokens)})
            except Exception as exc:
                row["error"] = str(exc)[:200]
            row["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            return row

        rows = await asyncio.gather(*(run_one(case) for case in cases))
        print(f"  {len(rows)}/{len(cases)} completed", flush=True)
        all_rows[model] = rows

    report = {model: accuracy_metrics(rows) for model, rows in all_rows.items()}
    baseline = all_rows[MODELS[0]]
    for model, rows in all_rows.items():
        report[model]["paired_accuracy_delta_ci_95_vs_gpt_luna"] = list(paired_bootstrap_delta([r["passed"] for r in baseline], [r["passed"] for r in rows], [r["case_id"] for r in rows]))
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    result_bundle = {
        "benchmark": "BANKING77 zero-shot intent classification",
        "run_date_utc": generated.isoformat(), "sample_seed": SEED,
        "dataset": {"name": "BANKING77", "source": SOURCE_URL, "source_revision": SOURCE_REVISION, "source_sha256": SOURCE_SHA256, "license": "CC BY 4.0", "official_test_rows": 3080, "evaluated_rows": len(cases), "sampling": f"uniform stratified sample of {PER_INTENT} per each of 77 labels from the official held-out test split"},
        "protocol": {"prompt": labels_prompt, "temperature": 0, "max_output_tokens": 32, "scorer": "case-insensitive exact intent label after trimming surrounding quotes/backticks", "timeouts_and_errors_count_as_incorrect": True, "pricing_date": load_prices()["checked_on"], "price_table": "config/pricing.json", "confidence_intervals": "95% Wilson for accuracy; paired deterministic bootstrap 10,000 resamples for accuracy deltas", "latency_percentile_population": "successful calls only"},
        "models": report, "items": all_rows,
    }
    out_dir = ROOT / "benchmark-results" / generated.strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "result.json").write_text(json.dumps(result_bundle, indent=2), encoding="utf-8")
    print(f"Saved real result bundle: {out_dir / 'result.json'}")
    print(json.dumps(report, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Reproduce a 385-case stratified BANKING77 three-provider run.")
    parser.add_argument("--execute", action="store_true", help="Submit live calls (default is a zero-cost dry run).")
    parser.add_argument("--max-budget-usd", type=float, help="Required caller-approved maximum spend; must cover the preflight estimate.")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
