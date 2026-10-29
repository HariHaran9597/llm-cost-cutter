import json

from app.benchmark import run_benchmark
from app.core import estimated_cost, get_cache, init_db, request_hash, set_cache, choose_model, usage_summary, write_usage


def test_cost_formula_uses_input_and_output_rates():
    assert estimated_cost("gpt-4.1-mini", 1_000_000, 1_000_000) == 2.0


def test_router_falls_back_on_ambiguous_and_complex_requests():
    assert choose_model("What is the answer?", "cheap", "strong", None) == ("strong", "unrecognized_task_baseline_fallback")
    assert choose_model("Extract the ID: X-42", "cheap", "strong", None)[0] == "cheap"
    assert choose_model("Summarize this; explain why it happened", "cheap", "strong", None)[0] == "strong"
    assert choose_model("Classify this", "cheap", "strong", "quality")[0] == "strong"


def test_cache_key_is_exact_and_bound_to_model_and_parameters():
    messages = [{"role": "user", "content": "same"}]
    key = request_hash(messages, "cheap", .2, 100)
    assert key != request_hash(messages, "strong", .2, 100)
    assert key != request_hash(messages, "cheap", .3, 100)
    assert key != request_hash([{"role": "user", "content": "different"}], "cheap", .2, 100)


def test_sqlite_cache_and_usage_summary_do_not_require_prompt_storage(tmp_path):
    path = str(tmp_path / "usage.sqlite3")
    init_db(path)
    response = {"choices": [{"message": {"content": "hello"}}]}
    set_cache(path, "hash", response)
    assert get_cache(path, "hash") == response
    write_usage(path, {"id":"event-1", "created_at":"2026-10-29T00:00:00+00:00", "request_hash":"hash", "model":"cheap", "route_reason":"exact_cache_hit", "cache_hit":1, "input_tokens":0, "output_tokens":0, "latency_ms":1.5, "estimated_cost_usd":0, "outcome":"ok"})
    summary = usage_summary(path)
    assert summary["requests"] == 1
    assert summary["cache_hits"] == 1
    assert "hello" not in json.dumps(summary)


def test_benchmark_has_explicit_denominators_and_fallback_quality():
    report = run_benchmark()
    assert report["n"] == 24
    assert report["baseline"]["pass_count"] == 24
    assert report["optimized"]["pass_count"] == 24
    assert report["optimized"]["model_mix"] == {"gpt-4.1-mini": 18, "gpt-4.1": 6}
    assert report["optimized"]["estimated_cost_reduction_rate"] > .5
    assert any("not representative" in issue for issue in report["limitations"])
