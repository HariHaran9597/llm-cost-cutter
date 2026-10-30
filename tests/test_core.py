import json

from app.core import estimated_cost, get_cache, init_db, request_hash, set_cache, choose_model, usage_summary, write_usage
from app.lab import delete_experiment, estimate_cost, get_experiment, get_policy, result_summary, save_experiment, score_output, set_policy


def test_provider_cost_estimate_uses_current_configured_rate():
    assert estimated_cost("gpt-6-luna", 1_000_000, 1_000_000) == .6
    assert estimate_cost("gpt-6-luna", 1_000_000, 1_000_000, cached_input_tokens=500_000) == .555


def test_fallback_router_is_conservative():
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


def test_deterministic_scorers_are_case_and_whitespace_insensitive():
    assert score_output("exact", "Billing", " billing\n")[0] == 1
    assert score_output("contains", "billing", "Category: billing")[0] == 1
    assert score_output("exact", "delivery", "account")[0] == 0


def test_recommendation_requires_quality_retention_and_no_provider_errors():
    variants = [
        {"id":"base","name":"Baseline","model":"gpt-6.1-sol","system_prompt":"same","user_template":"{{input}}","scorer":"exact"},
        {"id":"cheap","name":"Cheap","model":"gpt-6-luna","system_prompt":"same","user_template":"{{input}}","scorer":"exact"},
    ]
    rows=[]
    for variant_id, score, cost, error in [("base",1,.01,False),("base",1,.01,False),("cheap",1,.002,False),("cheap",1,.002,False)]:
        rows.append({"variant_id":variant_id,"score":score,"estimated_total_cost_usd":cost,"latency_ms":30,"status":"error" if error else "ok"})
    report=result_summary(rows,variants,"base")
    assert report["recommendation"] == "cheap"
    rows[-1]["status"]="error"
    assert result_summary(rows,variants,"base")["recommendation"] == "base"


def test_local_experiment_history_and_promoted_policy_lifecycle(tmp_path):
    path=str(tmp_path/"usage.sqlite")
    init_db(path)
    payload={"cases":[{"id":"1","input":"ticket","expected":"billing"}],"variants":[{"id":"base","model":"gpt-6-luna","system_prompt":"Classify","user_template":"{{input}}"}]}
    results={"report":{"variants":[{"id":"base","model":"gpt-6-luna","quality_rate":1,"estimated_cost_per_case_usd":.001,"errors":0}]},"rows":[]}
    experiment_id=save_experiment(path,"tickets","support-routing","exact",payload,results)
    assert get_experiment(path,experiment_id)["payload"] == payload
    set_policy(path,"support-routing","gpt-6-luna","Classify","{{input}}",experiment_id,"base",1,.001)
    assert get_policy(path,"support-routing")["model"] == "gpt-6-luna"
    assert delete_experiment(path,experiment_id)
    assert get_experiment(path,experiment_id) is None
    assert get_policy(path,"support-routing") is None


def test_sqlite_cache_and_usage_summary_are_separate_from_experiment_data(tmp_path):
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
