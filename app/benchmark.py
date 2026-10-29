from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from app.core import ROOT, estimated_cost


def exact(a: str, b: str) -> bool:
    return " ".join(a.casefold().split()) == " ".join(b.casefold().split())


def percentile(values: list[float], p: float) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    return round(values[min(len(values) - 1, int((len(values) - 1) * p))], 2)


def run_benchmark() -> dict:
    dataset = json.loads((ROOT / "data" / "benchmark.json").read_text(encoding="utf-8"))
    cheap, baseline = "gpt-4.1-mini", "gpt-4.1"
    rows = []
    for index, task in enumerate(dataset["tasks"]):
        base_answer = task["baseline"]
        opt_model = baseline if task["type"] == "reasoning" else cheap
        opt_answer = task["baseline"] if opt_model == baseline else task["cheap"]
        tokens_in, tokens_out = task["input_tokens"], task["output_tokens"]
        baseline_cost = estimated_cost(baseline, tokens_in, tokens_out)
        optimized_cost = estimated_cost(opt_model, tokens_in, tokens_out)
        # Fixed deterministic replay timings are explicitly labeled simulated, not live provider latency.
        baseline_ms = 720 + (index % 7) * 19
        optimized_ms = (460 if opt_model == cheap else 720) + (index % 5) * 17
        rows.append({
            "id": task["id"], "type": task["type"], "selected_model": opt_model,
            "route_reason": "reasoning_task_baseline" if opt_model == baseline else "bounded_task_cheap_model",
            "baseline_pass": exact(base_answer, task["expected"]),
            "optimized_pass": exact(opt_answer, task["expected"]),
            "baseline_cost_usd": baseline_cost, "optimized_cost_usd": optimized_cost,
            "baseline_latency_ms_simulated": baseline_ms, "optimized_latency_ms_simulated": optimized_ms,
        })
    n = len(rows)
    baseline_cost = sum(r["baseline_cost_usd"] for r in rows)
    optimized_cost = sum(r["optimized_cost_usd"] for r in rows)
    report = {
        "benchmark_id": dataset["id"], "dataset_description": dataset["description"],
        "n": n, "scoring": "normalized exact match against hand-authored expected output; numerator is passes, denominator is n",
        "baseline": {"model": baseline, "pass_count": sum(r["baseline_pass"] for r in rows), "quality_rate": sum(r["baseline_pass"] for r in rows)/n, "estimated_cost_usd": baseline_cost, "latency_p50_ms_simulated": percentile([r["baseline_latency_ms_simulated"] for r in rows], .5), "latency_p95_ms_simulated": percentile([r["baseline_latency_ms_simulated"] for r in rows], .95)},
        "optimized": {"model_policy": "GPT-4.1 mini for extraction/classification/summarization tasks; GPT-4.1 for reasoning", "pass_count": sum(r["optimized_pass"] for r in rows), "quality_rate": sum(r["optimized_pass"] for r in rows)/n, "quality_retention_vs_baseline": (sum(r["optimized_pass"] for r in rows)/n)/(sum(r["baseline_pass"] for r in rows)/n) if sum(r["baseline_pass"] for r in rows) else 0, "estimated_cost_usd": optimized_cost, "estimated_cost_reduction_rate": 1-optimized_cost/baseline_cost if baseline_cost else 0, "latency_p50_ms_simulated": percentile([r["optimized_latency_ms_simulated"] for r in rows], .5), "latency_p95_ms_simulated": percentile([r["optimized_latency_ms_simulated"] for r in rows], .95), "model_mix": {"gpt-4.1-mini": sum(r["selected_model"] == cheap for r in rows), "gpt-4.1": sum(r["selected_model"] == baseline for r in rows)}},
        "price_source_checked_on": json.loads((ROOT / "config" / "pricing.json").read_text(encoding="utf-8"))["checked_on"],
        "limitations": ["hand-authored dataset of 24 examples; not representative production traffic", "exact match is a strict but incomplete quality measure", "token counts are provided fixtures, not provider tokenizer output", "latencies are deterministic replay placeholders, not measured network/model latency", "estimated token charges exclude tools, discounts, cached input, taxes, and provider-specific fees"],
        "rows": rows,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()
    report = run_benchmark()
    if args.write_report:
        target = ROOT / "docs" / "benchmark-report.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        public_target = ROOT / "web" / "data" / "benchmark-report.json"
        public_target.parent.mkdir(parents=True, exist_ok=True)
        public_target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("benchmark_id", "n", "baseline", "optimized", "limitations")}, indent=2))


if __name__ == "__main__":
    main()
