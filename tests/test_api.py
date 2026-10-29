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


def test_unsupported_request_fields_are_rejected():
    monkeypatch_token = "Bearer test-secret"
    main.settings.gateway_api_token = "test-secret"
    client = TestClient(main.app)
    response = client.post("/v1/chat/completions", headers={"Authorization":monkeypatch_token}, json={"messages":[{"role":"user","content":"hi"}],"stream":True})
    assert response.status_code == 422


def test_health_and_security_headers():
    client = TestClient(main.app)
    response = client.get("/health")
    assert response.json()["status"] == "ok"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_live_request_uses_provider_once_then_returns_exact_cache(tmp_path, monkeypatch):
    import json

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
