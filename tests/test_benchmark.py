from collections import Counter

from benchmarks.run_banking77 import accuracy_metrics, estimated_budget, load_cases


def test_banking77_sample_is_stratified_reproducible_and_pinned():
    cases = load_cases()
    assert len(cases) == 385
    assert len({case["id"] for case in cases}) == len(cases)
    assert set(Counter(case["expected"] for case in cases).values()) == {5}
    assert cases == load_cases()


def test_benchmark_dry_run_prices_three_provider_models():
    costs = estimated_budget(load_cases())
    assert set(costs) == {"gpt-6-luna", "claude-haiku-5-5", "groq-qwen3.8-27b"}
    assert all(cost > 0 for cost in costs.values())


def test_benchmark_metrics_count_provider_errors_as_incorrect():
    rows = [
        {"expected": "billing", "output": "billing", "passed": True, "status": "ok", "latency_ms": 20, "estimated_cost_usd": .0001},
        {"expected": "delivery", "output": None, "passed": False, "status": "error", "latency_ms": 90, "estimated_cost_usd": 0},
    ]
    result = accuracy_metrics(rows)
    assert result["cases"] == 2 and result["correct"] == 1
    assert result["errors"] == 1 and result["successful_calls"] == 1
    assert result["estimated_cost_per_1000_requests_usd"] == .05
    assert result["p95_latency_ms_successes_only"] == 20
