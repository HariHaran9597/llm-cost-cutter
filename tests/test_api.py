from fastapi.testclient import TestClient

from app import main


def test_chat_requires_token():
    client = TestClient(main.app)
    response = client.post("/v1/chat/completions", json={"messages":[{"role":"user","content":"Extract the ID: X-42"}]})
    assert response.status_code == 401


def test_demo_never_calls_upstream(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "database_path", str(tmp_path / "db.sqlite"))
    monkeypatch.setattr(main.settings, "gateway_api_token", "test-secret")
    monkeypatch.setattr(main.settings, "demo_mode", True)
    client = TestClient(main.app)
    response = client.post("/v1/chat/completions", headers={"Authorization":"Bearer test-secret"}, json={"messages":[{"role":"user","content":"Extract the ID: X-42"}]})
    assert response.status_code == 503
    assert response.json()["detail"] == "live inference is disabled in demo mode"


def test_unsupported_request_fields_are_rejected(monkeypatch):
    monkeypatch_token = "Bearer test-secret"
    monkeypatch.setattr(main.settings,"gateway_api_token","test-secret")
    client = TestClient(main.app)
    response = client.post("/v1/chat/completions", headers={"Authorization":monkeypatch_token}, json={"messages":[{"role":"user","content":"hi"}],"stream":True})
    assert response.status_code == 422


def test_health_and_security_headers():
    client = TestClient(main.app)
    response = client.get("/health")
    assert response.json()["status"] == "ok"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert client.get("/").status_code == 200
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200


def test_live_request_uses_provider_once_then_returns_exact_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "database_path", str(tmp_path / "usage.sqlite"))
    monkeypatch.setattr(main.settings, "gateway_api_token", "cache-secret")
    monkeypatch.setattr(main.settings, "demo_mode", False)
    monkeypatch.setattr(main.settings, "upstream_api_key", "upstream-test-key")
    monkeypatch.setattr(main.settings, "max_daily_spend_usd", 1)
    calls = []

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"id":"mock", "choices":[{"message":{"role":"assistant","content":"INV-42"}}], "usage":{"prompt_tokens":10,"completion_tokens":2}}

    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, url, json=None, headers=None):
            calls.append((url, json, headers))
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)
    headers = {"Authorization":"Bearer cache-secret"}
    body = {"messages":[{"role":"user","content":"Extract invoice for private-case-7: INV-42"}]}
    with TestClient(main.app) as client:
        first = client.post("/v1/chat/completions", headers=headers, json=body)
        second = client.post("/v1/chat/completions", headers=headers, json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["costcutter"]["cache_hit"] is False
    assert second.json()["costcutter"]["cache_hit"] is True
    assert len(calls) == 1
    assert calls[0][1]["model"] == main.settings.cheap_model
    stored = open(main.settings.database_path, "rb").read()
    assert b"private-case-7" not in stored


def test_real_experiment_flow_compares_candidates_saves_runs_promotes_policy_and_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings,"database_path",str(tmp_path/"lab.sqlite"))
    monkeypatch.setattr(main.settings,"gateway_api_token","lab-secret")
    monkeypatch.setattr(main.settings,"demo_mode",False)
    monkeypatch.setattr(main.settings,"upstream_api_key","mocked-key")
    provider_calls=[]

    async def fake_completion(model,messages,max_tokens,api_key=None):
        provider_calls.append((model,messages,max_tokens))
        text=messages[-1]["content"].lower()
        answer="billing" if "charged twice" in text else "delivery"
        return {"output":answer,"usage":{"prompt_tokens":24,"completion_tokens":2}}

    monkeypatch.setattr(main,"_provider_completion",fake_completion)
    headers={"Authorization":"Bearer lab-secret"}
    body={
        "name":"Support issue labels","task_type":"support-routing","scorer":"exact","max_budget_usd":0.1,
        "cases":[{"id":"charge","input":"I was charged twice","expected":"billing"},{"id":"parcel","input":"My parcel is delayed","expected":"delivery"}],
        "variants":[
            {"id":"baseline","name":"Baseline prompt","model":"gpt-6.1-sol","system_prompt":"Label this support issue"},
            {"id":"lower-cost","name":"Lower cost model","model":"gpt-6-luna","system_prompt":"Label this support issue"}
        ]
    }
    with TestClient(main.app) as client:
        response=client.post("/api/experiments/run",headers=headers,json=body)
        assert response.status_code==200,response.text
        run=response.json()
        assert len(provider_calls)==4
        assert run["report"]["variants"][0]["quality_rate"]==1
        assert run["report"]["variants"][1]["quality_rate"]==1
        assert run["report"]["recommendation"]=="lower-cost"
        assert run["estimated_total_cost_usd"]>0
        assert all(row["provider_usage_reported"] for row in run["rows"])
        history=client.get("/api/experiments",headers=headers)
        assert history.status_code==200 and len(history.json())==1
        promoted=client.post("/api/policies/promote",headers=headers,json={"experiment_id":run["id"],"variant_id":"lower-cost"})
        assert promoted.status_code==200,promoted.text
        assert promoted.json()["policy"]["model"]=="gpt-6-luna"

        class FakeResponse:
            status_code=200
            def json(self):return {"choices":[{"message":{"role":"assistant","content":"billing"}}],"usage":{"prompt_tokens":24,"completion_tokens":2}}
        class FakeClient:
            def __init__(self,**kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return None
            async def post(self,url,json=None,headers=None):
                provider_calls.append((json["model"],json["messages"],json.get("max_tokens")))
                return FakeResponse()
        monkeypatch.setattr(main.httpx,"AsyncClient",FakeClient)
        routed=client.post("/v1/chat/completions",headers={**headers,"x-costcutter-task":"support-routing"},json={"messages":[{"role":"user","content":"I was charged twice"}]})
        assert routed.status_code==200,routed.text
        assert routed.json()["costcutter"]["model"]=="gpt-6-luna"
        assert routed.json()["costcutter"]["route_reason"]=="evaluated_policy:support-routing"
        assert provider_calls[-1][1][0]["content"]=="Label this support issue"


def test_byok_keys_are_ephemeral_and_anthropic_wire_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "database_path", str(tmp_path / "byok.sqlite"))
    monkeypatch.setattr(main.settings, "gateway_api_token", "byok-secret")
    monkeypatch.setattr(main.settings, "demo_mode", False)
    captured = []

    async def fake_completion(model, messages, max_tokens, api_key=None):
        captured.append((model, api_key))
        return {"output": "billing", "usage": {"input_tokens": 12, "output_tokens": 1}}

    monkeypatch.setattr(main, "_provider_completion", fake_completion)
    body = {
        "name": "BYOK test", "task_type": "byok-check", "max_budget_usd": 0.1,
        "cases": [{"id": "one", "input": "I was charged twice", "expected": "billing"}],
        "variants": [
            {"id": "a", "name": "OpenAI baseline", "model": "gpt-6-luna", "system_prompt": "Classify"},
            {"id": "b", "name": "Claude candidate", "model": "claude-haiku-5-5", "system_prompt": "Classify"},
        ],
        "provider_api_keys": {"openai": "temporary-openai-key", "anthropic": "temporary-anthropic-key"},
    }
    with TestClient(main.app) as client:
        response = client.post("/api/experiments/run", headers={"Authorization": "Bearer byok-secret"}, json=body)
        assert response.status_code == 200, response.text
    assert captured == [("gpt-6-luna", "temporary-openai-key"), ("claude-haiku-5-5", "temporary-anthropic-key")]
    stored = main.get_experiment(main.settings.database_path, response.json()["id"])
    assert "provider_api_keys" not in stored["payload"]
    assert "temporary-anthropic-key" not in response.text
    assert "temporary-openai-key" not in open(main.settings.database_path, "rb").read().decode("latin1")


def test_anthropic_adapter_keeps_api_key_in_header_and_normalizes_usage(monkeypatch):
    captured = []
    class FakeResponse:
        status_code = 200
        def json(self):
            return {"content": [{"type": "text", "text": "billing"}], "usage": {"input_tokens": 19, "output_tokens": 2}}
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, url, json=None, headers=None):
            captured.append((url, json, headers))
            return FakeResponse()
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)
    import asyncio
    result = asyncio.run(main._provider_completion("claude-haiku-5-5", [{"role":"system","content":"System"},{"role":"user","content":"Ticket"}], 32, "ephemeral-test-key"))
    assert result == {"output": "billing", "usage": {"input_tokens": 19, "output_tokens": 2}}
    url, payload, headers = captured[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert payload["system"] == "System" and payload["messages"][0]["role"] == "user"
    assert headers["x-api-key"] == "ephemeral-test-key"


def test_experiment_budget_rejects_before_any_provider_call(tmp_path,monkeypatch):
    monkeypatch.setattr(main.settings,"database_path",str(tmp_path/"budget.sqlite"))
    monkeypatch.setattr(main.settings,"gateway_api_token","budget-secret")
    monkeypatch.setattr(main.settings,"demo_mode",False)
    monkeypatch.setattr(main.settings,"upstream_api_key","mocked-key")
    calls=[]
    async def fake(*args,**kwargs):calls.append(True);return {"output":"ok","usage":{}}
    monkeypatch.setattr(main,"_provider_completion",fake)
    body={"name":"Over cap","task_type":"budget-check","max_budget_usd":0.000001,"cases":[{"id":"1","input":"A long sample input to compare model responses with a substantial prompt and context.","expected":"ok"}],"variants":[{"id":"a","name":"A","model":"gpt-6-astra","system_prompt":"Use the entire context and provide the answer"},{"id":"b","name":"B","model":"gpt-6.1-sol","system_prompt":"Use the entire context and provide the answer"}]}
    with TestClient(main.app) as client:
        response=client.post("/api/experiments/run",headers={"Authorization":"Bearer budget-secret"},json=body)
    assert response.status_code==422
    assert "exceeds" in response.json()["detail"]["message"]
    assert calls==[]


def test_judge_cost_is_counted_and_invalid_judge_scores_are_not_promotable(tmp_path,monkeypatch):
    monkeypatch.setattr(main.settings,"database_path",str(tmp_path/"judge.sqlite"))
    monkeypatch.setattr(main.settings,"gateway_api_token","judge-secret")
    monkeypatch.setattr(main.settings,"demo_mode",False)
    monkeypatch.setattr(main.settings,"upstream_api_key","mocked-key")
    calls=[]
    async def fake_completion(model,messages,max_tokens,api_key=None):
        calls.append(model)
        if "strict evaluator" in messages[0]["content"]:
            return {"output":"not json","usage":{"prompt_tokens":50,"completion_tokens":3}}
        return {"output":"billing","usage":{"prompt_tokens":20,"completion_tokens":2}}
    monkeypatch.setattr(main,"_provider_completion",fake_completion)
    body={"name":"Judge example","task_type":"judge-demo","scorer":"judge","judge_model":"gpt-6.1-sol","rubric":"The selected category must match the ticket intent.","max_budget_usd":0.1,"cases":[{"id":"c1","input":"Double charge","expected":"billing"}],"variants":[{"id":"base","name":"Baseline","model":"gpt-6.1-sol","system_prompt":"Choose a category"},{"id":"cheap","name":"Cheap","model":"gpt-6-luna","system_prompt":"Choose a category"}]}
    with TestClient(main.app) as client:
        response=client.post("/api/experiments/run",headers={"Authorization":"Bearer judge-secret"},json=body)
        assert response.status_code==200,response.text
        run=response.json()
        assert calls==["gpt-6.1-sol","gpt-6.1-sol","gpt-6-luna","gpt-6.1-sol"]
        assert run["rows"][0]["score"] is None
        assert run["rows"][0]["estimated_judge_cost_usd"]>0
        promoted=client.post("/api/policies/promote",headers={"Authorization":"Bearer judge-secret"},json={"experiment_id":run["id"],"variant_id":"cheap"})
        assert promoted.status_code==422


def test_model_picker_is_filtered_to_account_access_and_prices(tmp_path,monkeypatch):
    monkeypatch.setattr(main.settings,"database_path",str(tmp_path/"models.sqlite"))
    monkeypatch.setattr(main.settings,"gateway_api_token","model-secret")
    monkeypatch.setattr(main.settings,"demo_mode",False)
    monkeypatch.setattr(main.settings,"upstream_api_key","provider-secret")
    class FakeResponse:
        status_code=200
        def json(self):return {"data":[{"id":"gpt-6-luna"},{"id":"gpt-unknown-new"}]}
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):return None
        async def get(self,url,headers=None):
            assert headers["Authorization"]=="Bearer provider-secret"
            return FakeResponse()
    monkeypatch.setattr(main.httpx,"AsyncClient",FakeClient)
    with TestClient(main.app) as client:
        response=client.get("/api/models/available",headers={"Authorization":"Bearer model-secret"})
    assert response.status_code==200
    assert response.json()["verified"] is True
    assert list(response.json()["models"])==["gpt-6-luna"]
    assert response.json()["unpriced_models"]==["gpt-unknown-new"]
    assert "provider-secret" not in response.text
