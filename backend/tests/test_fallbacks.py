# Path: tests/test_fallbacks.py
# Description: CRUD, secret handling, spend caps, and routing tests for OpenAI-compatible fallbacks.

import json
from datetime import datetime, timezone

import httpx
import respx

from app.utils import crypto, openai_fallbacks
from app.utils.postgres import OpenAIFallbackDb, UsageRecordDb
from app.utils.postgres.base import SessionFactory


def _create(client, admin_headers, **overrides):
    payload = {
        "label": "Reserve",
        "base_url": "https://fallback.example/v1/",
        "api_key": "sk-test-secret-value",
        "monthly_spend_limit_usd": 10.0,
        "priority": 2,
        **overrides,
    }
    response = client.post("/api/v1/fallbacks", headers=admin_headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["fallback"]


def _completed_sse(model="gpt-5.6-luna", input_tokens=10, output_tokens=5):
    response = {
        "id": "resp_fallback",
        "object": "response",
        "created_at": int(datetime.now(timezone.utc).timestamp()),
        "status": "completed",
        "model": model,
        "output": [],
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }
    return f"data: {json.dumps({'type': 'response.completed', 'response': response})}\n\n".encode()


def test_fallback_secret_is_write_only_and_lifecycle_is_managed(client, admin_headers):
    created = _create(client, admin_headers)
    rendered = json.dumps(created)
    assert "sk-test-secret-value" not in rendered
    assert created["key_hint"] == "••••alue"
    assert created["base_url"] == "https://fallback.example/v1"
    assert created["status"] == "ACTIVE"

    with SessionFactory() as db:
        stored = db.get(OpenAIFallbackDb, created["id"])
        assert stored.api_key_enc != "sk-test-secret-value"
        assert crypto.decrypt(stored.api_key_enc) == "sk-test-secret-value"

    disabled = client.post(
        f"/api/v1/fallbacks/{created['id']}/disable",
        headers=admin_headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["fallback"]["status"] == "DISABLED"

    enabled = client.post(
        f"/api/v1/fallbacks/{created['id']}/enable",
        headers=admin_headers,
    )
    assert enabled.status_code == 200
    assert enabled.json()["fallback"]["status"] == "ACTIVE"

    updated = client.put(
        f"/api/v1/fallbacks/{created['id']}",
        headers=admin_headers,
        json={"label": "Renamed", "clear_monthly_spend_limit": True, "priority": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["fallback"]["label"] == "Renamed"
    assert updated.json()["fallback"]["monthly_spend_limit_usd"] is None
    with SessionFactory() as db:
        stored = db.get(OpenAIFallbackDb, created["id"])
        assert openai_fallbacks.api_key(stored) == "sk-test-secret-value"


def test_duplicate_fallback_credential_and_base_url_is_rejected(client, admin_headers):
    _create(client, admin_headers)
    response = client.post(
        "/api/v1/fallbacks",
        headers=admin_headers,
        json={
            "label": "Duplicate",
            "base_url": "https://fallback.example/v1",
            "api_key": "sk-test-secret-value",
            "priority": 1,
        },
    )
    assert response.status_code == 409


@respx.mock
def test_proxy_uses_fallback_only_when_subscription_pool_cannot_serve(
    client,
    admin_headers,
    make_user,
):
    fallback = _create(client, admin_headers, monthly_spend_limit_usd=1.0)
    route = respx.post("https://fallback.example/v1/responses").mock(
        return_value=httpx.Response(
            200,
            content=_completed_sse(),
            headers={"content-type": "text/event-stream", "x-request-id": "fallback-request"},
        )
    )
    key = make_user("fallback-user", fallback_enabled=True)

    response = client.post(
        "/api/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gpt-5.6-luna", "input": "Reply OK", "max_output_tokens": 8},
    )

    assert response.status_code == 200, response.text
    sent = json.loads(route.calls[0].request.content)
    assert sent["max_output_tokens"] == 8
    assert sent["input"] == "Reply OK"
    assert route.calls[0].request.headers["authorization"] == "Bearer sk-test-secret-value"
    with SessionFactory() as db:
        record = db.query(UsageRecordDb).one()
        assert str(record.fallback_provider_id) == fallback["id"]
        assert record.account_id is None
        assert record.billed_cost_usd > 0


@respx.mock
def test_exhausted_fallback_spend_cap_prevents_more_requests(
    client,
    admin_headers,
    make_user,
):
    fallback = _create(client, admin_headers, monthly_spend_limit_usd=0.00001)
    route = respx.post("https://fallback.example/v1/responses").mock(
        return_value=httpx.Response(
            200,
            content=_completed_sse(),
            headers={"content-type": "text/event-stream"},
        )
    )
    key = make_user("capped-user", fallback_enabled=True)
    headers = {"Authorization": f"Bearer {key}"}
    payload = {"model": "gpt-5.6-luna", "input": "Reply OK"}

    first = client.post("/api/v1/responses", headers=headers, json=payload)
    second = client.post("/api/v1/responses", headers=headers, json=payload)

    assert first.status_code == 200
    assert second.status_code == 503
    assert route.call_count == 1
    listed = client.get("/api/v1/fallbacks", headers=admin_headers).json()["fallbacks"]
    item = next(value for value in listed if value["id"] == fallback["id"])
    assert item["monthly_spend_usd"] > item["monthly_spend_limit_usd"]
    assert item["monthly_spend_remaining_usd"] == 0


@respx.mock
def test_fallback_health_check_refreshes_models_without_exposing_key(client, admin_headers):
    fallback = _create(client, admin_headers)
    route = respx.get("https://fallback.example/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [{"id": "gpt-test", "object": "model"}]})
    )

    response = client.post(
        f"/api/v1/fallbacks/{fallback['id']}/test",
        headers=admin_headers,
    )

    assert response.status_code == 200
    result = response.json()["fallback"]
    assert result["provider_health"] == "HEALTHY"
    assert result["model_count"] == 1
    assert "sk-test-secret-value" not in response.text
    assert route.calls[0].request.headers["authorization"] == "Bearer sk-test-secret-value"
