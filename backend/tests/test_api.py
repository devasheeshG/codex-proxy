# Path: tests/test_api.py
# Description: API tests for admin auth, user management, and the proxy path (non-streaming, streaming, failover).

import json
import uuid

import httpx
import respx

CODEX_RESPONSES = "https://chatgpt.com/backend-api/codex/responses"
CODEX_RESPONSES_COMPACT = f"{CODEX_RESPONSES}/compact"
CODEX_MODELS = "https://chatgpt.com/backend-api/codex/models"
CODEX_SEARCH = "https://chatgpt.com/backend-api/codex/alpha/search"


def test_login_success_and_failure(client, admin_password):
    ok = client.post("/api/v1/auth/login", json={"username": "admin", "password": admin_password})
    assert ok.status_code == 200
    assert ok.json()["token"]

    bad = client.post("/api/v1/auth/login", json={"username": "admin", "password": "definitely-wrong"})
    assert bad.status_code == 401


def test_admin_routes_require_auth(client):
    assert client.get("/api/v1/users").status_code == 401
    assert client.get("/api/v1/accounts").status_code == 401
    assert client.get("/api/v1/stats/overview").status_code == 401


def test_user_crud_and_key_management(client, admin_headers):
    created = client.post("/api/v1/users", headers=admin_headers, json={"name": "alice"})
    assert created.status_code == 201
    user_id = created.json()["user"]["id"]
    assert created.json()["user"]["key_count"] == 0
    assert created.json()["user"]["allowed_request_modes"] == ["standard", "fast", "ultrafast"]
    assert created.json()["user"]["allowed_reasoning_levels"] == [
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert created.json()["user"]["allowed_models"] is None

    updated = client.put(f"/api/v1/users/{user_id}", headers=admin_headers, json={"name": "alice-2"})
    assert updated.status_code == 200
    assert updated.json()["user"]["name"] == "alice-2"

    # One user can hold multiple keys; each secret is returned exactly once.
    k1 = client.post(f"/api/v1/users/{user_id}/keys", headers=admin_headers, json={"label": "laptop"})
    k2 = client.post(f"/api/v1/users/{user_id}/keys", headers=admin_headers, json={"label": "ci"})
    assert k1.status_code == 201 and k2.status_code == 201
    assert k1.json()["secret"].startswith("usr_")
    assert k1.json()["secret"] != k2.json()["secret"]

    keys = client.get(f"/api/v1/users/{user_id}/keys", headers=admin_headers).json()["keys"]
    assert len(keys) == 2
    assert client.get("/api/v1/users", headers=admin_headers).json()["users"][0]["key_count"] == 2

    # Deleting one key leaves the other.
    assert client.delete(f"/api/v1/users/{user_id}/keys/{keys[0]['id']}", headers=admin_headers).status_code == 204
    assert len(client.get(f"/api/v1/users/{user_id}/keys", headers=admin_headers).json()["keys"]) == 1

    assert client.delete(f"/api/v1/users/{user_id}", headers=admin_headers).status_code == 204
    assert client.get("/api/v1/users", headers=admin_headers).json()["total"] == 0


def test_user_request_policy_can_be_customized(client, admin_headers):
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "name": "restricted",
            "allowed_request_modes": ["FAST"],
            "allowed_reasoning_levels": ["LOW", "high"],
            "allowed_models": ["GPT-5.6-SOL", "gpt-5.6-sol"],
        },
    )
    assert created.status_code == 201, created.text
    user = created.json()["user"]
    assert user["allowed_request_modes"] == ["fast"]
    assert user["allowed_reasoning_levels"] == ["low", "high"]
    assert user["allowed_models"] == ["gpt-5.6-sol"]

    updated = client.put(
        f"/api/v1/users/{user['id']}",
        headers=admin_headers,
        json={
            "allowed_request_modes": ["standard"],
            "allowed_reasoning_levels": ["medium"],
            "allowed_models": ["gpt-5.4"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["user"]["allowed_request_modes"] == ["standard"]
    assert updated.json()["user"]["allowed_reasoning_levels"] == ["medium"]
    assert updated.json()["user"]["allowed_models"] == ["gpt-5.4"]
    options = client.get("/api/v1/users/model-options", headers=admin_headers)
    assert options.status_code == 200, options.text
    assert options.json()["models"] == [
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-6-astra",
        "codex-auto-review",
    ]

    unrestricted = client.put(
        f"/api/v1/users/{user['id']}",
        headers=admin_headers,
        json={"allowed_models": None},
    )
    assert unrestricted.status_code == 200, unrestricted.text
    assert unrestricted.json()["user"]["allowed_models"] is None

    empty = client.put(
        f"/api/v1/users/{user['id']}",
        headers=admin_headers,
        json={"allowed_reasoning_levels": []},
    )
    assert empty.status_code == 422

    empty_models = client.put(
        f"/api/v1/users/{user['id']}",
        headers=admin_headers,
        json={"allowed_models": []},
    )
    assert empty_models.status_code == 422


def test_user_budgets_and_model_overrides_crud(client, admin_headers):
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "name": "budgeted",
            "monthly_token_budget": 1_000,
            "lifetime_token_budget": 5_000,
            "monthly_spend_budget_usd": 12.5,
            "lifetime_spend_budget_usd": 75,
            "model_overrides": {"GPT-6-ASTRA": "GPT-5.6-SOL"},
        },
    )
    assert created.status_code == 201, created.text
    user = created.json()["user"]
    assert user["monthly_token_budget"] == 1_000
    assert user["lifetime_token_budget"] == 5_000
    assert user["monthly_spend_budget_usd"] == 12.5
    assert user["lifetime_spend_budget_usd"] == 75
    assert user["model_overrides"] == {"gpt-6-astra": "gpt-5.6-sol"}

    updated = client.put(
        f"/api/v1/users/{user['id']}",
        headers=admin_headers,
        json={
            "lifetime_token_budget": 7_500,
            "monthly_spend_budget_usd": 20,
            "model_overrides": {},
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["user"]["lifetime_token_budget"] == 7_500
    assert updated.json()["user"]["monthly_spend_budget_usd"] == 20
    assert updated.json()["user"]["model_overrides"] == {}

    no_op = client.put(
        f"/api/v1/users/{user['id']}",
        headers=admin_headers,
        json={"model_overrides": {"gpt-5.6-sol": "GPT-5.6-SOL"}},
    )
    assert no_op.status_code == 422


@respx.mock
def test_refresh_model_options_returns_fixed_catalog_without_upstream_calls(client, admin_headers, seed_account):
    seed_account("catalog-refresh")
    upstream = respx.get(CODEX_MODELS, params={"client_version": "0.144.5"}).mock(
        return_value=httpx.Response(200, json={"models": [{"slug": "unexpected"}]})
    )

    response = client.post("/api/v1/users/model-options/refresh", headers=admin_headers)

    assert response.status_code == 200, response.text
    assert response.json()["models"] == [
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-6-astra",
        "codex-auto-review",
    ]
    assert not upstream.called


def test_proxy_requires_user_key(client):
    assert client.post("/api/v1/responses", json={}).status_code == 401


@respx.mock
def test_per_user_model_override_rewrites_before_upstream_routing(client, admin_headers, seed_account):
    seed_account("model-rewrite")
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "name": "rewritten",
            "allowed_models": ["gpt-6-astra"],
            "model_overrides": {"gpt-6-astra": "gpt-5.6-sol"},
        },
    ).json()["user"]
    key = client.post(f"/api/v1/users/{created['id']}/keys", headers=admin_headers, json={"label": "test-key"}).json()["secret"]
    respx.route(host="testserver").pass_through()
    upstream = respx.post(CODEX_RESPONSES).mock(
        return_value=httpx.Response(
            200,
            json={"model": "gpt-5.6-sol", "usage": {"input_tokens": 3, "output_tokens": 2}},
        )
    )

    response = client.post(
        "/api/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gpt-6-astra", "input": "hello"},
    )

    assert response.status_code == 200, response.text
    assert json.loads(upstream.calls[0].request.content)["model"] == "gpt-5.6-sol"
    assert response.json()["model"] == "gpt-6-astra"


def test_model_override_uses_requested_name_in_pool_unavailable_error(client, admin_headers, seed_account):
    from app.utils.postgres import AccountDb
    from app.utils.postgres.base import SessionFactory

    account_id = seed_account("unavailable-model", weekly_used_pct=1.0)
    with SessionFactory() as db:
        db.get(AccountDb, account_id).model_catalog_json = '[{"slug":"gpt-5.6-sol"}]'
        db.commit()
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"name": "alias-error", "model_overrides": {"gpt-6-astra": "gpt-5.6-sol"}},
    ).json()["user"]
    key = client.post(f"/api/v1/users/{created['id']}/keys", headers=admin_headers, json={"label": "test-key"}).json()["secret"]

    response = client.post(
        "/api/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "GPT-6-ASTRA", "input": "hello"},
    )

    assert response.status_code == 503
    assert "GPT-6-ASTRA" in response.json()["detail"]
    assert "gpt-5.6-sol" not in response.text


@respx.mock
def test_model_override_hides_effective_name_in_forwarded_error(client, admin_headers, seed_account):
    seed_account("forwarded-alias-error")
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"name": "forwarded-alias-error", "model_overrides": {"gpt-6-astra": "gpt-5.6-sol"}},
    ).json()["user"]
    key = client.post(f"/api/v1/users/{created['id']}/keys", headers=admin_headers, json={"label": "test-key"}).json()["secret"]
    respx.route(host="testserver").pass_through()
    upstream = respx.post(CODEX_RESPONSES).mock(
        return_value=httpx.Response(
            422,
            json={"error": {"message": "Invalid request for model gpt-5.6-sol"}},
        )
    )

    response = client.post(
        "/api/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "GPT-6-ASTRA", "input": "hello"},
    )

    assert response.status_code == 422
    assert json.loads(upstream.calls[0].request.content)["model"] == "gpt-5.6-sol"
    assert response.json()["error"]["message"] == "Invalid request for model GPT-6-ASTRA"
    assert "gpt-5.6-sol" not in response.text


@respx.mock
def test_user_lifetime_token_and_spend_budgets_are_enforced(client, admin_headers, seed_account):
    seed_account("budget-enforcement")
    respx.route(host="testserver").pass_through()
    respx.post(CODEX_RESPONSES).mock(
        return_value=httpx.Response(
            200,
            json={"model": "gpt-5.4", "usage": {"input_tokens": 10, "output_tokens": 10}},
        )
    )

    cases = [
        ({"lifetime_token_budget": 5}, "lifetime token budget"),
        ({"monthly_spend_budget_usd": 0.0001}, "monthly spend budget"),
        ({"lifetime_spend_budget_usd": 0.0001}, "lifetime spend budget"),
    ]
    for index, (budget, expected_detail) in enumerate(cases):
        user = client.post(
            "/api/v1/users",
            headers=admin_headers,
            json={"name": f"budget-{index}", **budget},
        ).json()["user"]
        key = client.post(f"/api/v1/users/{user['id']}/keys", headers=admin_headers, json={"label": "test-key"}).json()["secret"]
        headers = {"Authorization": f"Bearer {key}"}

        assert client.post("/api/v1/responses", headers=headers, json={"model": "gpt-5.4"}).status_code == 200
        blocked = client.post("/api/v1/responses", headers=headers, json={"model": "gpt-5.4"})
        assert blocked.status_code == 403
        assert expected_detail in blocked.json()["detail"].lower()

    users = client.get("/api/v1/users", headers=admin_headers).json()["users"]
    assert all(user["total_spend_usd"] > 0 and user["monthly_spend_usd"] > 0 for user in users)
    account = client.get("/api/v1/accounts", headers=admin_headers).json()["accounts"][0]
    assert account["total_spend_usd"] > 0
    assert account["monthly_spend_usd"] > 0


@respx.mock
def test_weekly_exhaustion_redeems_earliest_credit_and_retries_request(client, seed_account, make_user):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch

    account_id = seed_account("auto-reset", weekly_used_pct=0.99)
    key = make_user("auto-reset-user")
    now = datetime.now(timezone.utc)
    credits = [
        {
            "id": "later",
            "status": "available",
            "is_supported_by_plan": True,
            "expires_at": now + timedelta(days=2),
        },
        {
            "id": "earlier",
            "status": "available",
            "is_supported_by_plan": True,
            "expires_at": now + timedelta(hours=2),
        },
    ]
    respx.route(host="testserver").pass_through()
    upstream = respx.post(CODEX_RESPONSES).mock(
        side_effect=[
            httpx.Response(
                429,
                headers={
                    "x-codex-secondary-used-percent": "100",
                    "x-codex-rate-limit-reached-type": "rate_limit_reached",
                    "x-codex-rate-limit-reset-credits-available": "2",
                },
                json={"error": {"message": "weekly limit reached"}},
            ),
            httpx.Response(
                200,
                json={"model": "gpt-5.4", "usage": {"input_tokens": 2, "output_tokens": 1}},
            ),
        ]
    )
    with (
        patch(
            "app.utils.rotation.oauth.list_reset_credits",
            return_value={"available_count": 2, "credits": credits},
        ),
        patch(
            "app.utils.rotation.oauth.consume_reset_credit",
            return_value={"code": "reset", "windows_reset": 1, "idempotency_key": "auto"},
        ) as consume,
        patch(
            "app.utils.rotation.oauth.fetch_usage",
            side_effect=[
                {
                    "five_hour": {"utilization": 0.1},
                    "weekly": {"utilization": 1.0},
                    "monthly": None,
                    "reset_credits_available": 2,
                    "limit_reached": True,
                },
                {
                    "five_hour": {"utilization": 0.1},
                    "weekly": {"utilization": 0.0},
                    "monthly": None,
                    "reset_credits_available": 1,
                    "limit_reached": False,
                },
            ],
        ),
    ):
        response = client.post(
            "/api/v1/responses",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "gpt-5.4"},
        )

    assert response.status_code == 200, response.text
    assert upstream.call_count == 2
    assert consume.call_args.kwargs["credit_id"] == "earlier"
    from app.utils.postgres import AccountDb
    from app.utils.postgres.base import SessionFactory

    with SessionFactory() as db:
        assert db.get(AccountDb, account_id).weekly_used_pct == 0.0


@respx.mock
def test_already_exhausted_account_redeems_before_selection(client, seed_account, make_user):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch

    from app.utils.postgres import AccountDb
    from app.utils.postgres.base import SessionFactory

    account_id = seed_account("already-exhausted", weekly_used_pct=1.0)
    key = make_user("already-exhausted-user")
    with SessionFactory() as db:
        account = db.get(AccountDb, account_id)
        account.reset_credits_available = 1
        db.commit()

    respx.route(host="testserver").pass_through()
    upstream = respx.post(CODEX_RESPONSES).mock(
        return_value=httpx.Response(
            200,
            json={"model": "gpt-5.4", "usage": {"input_tokens": 2, "output_tokens": 1}},
        )
    )
    credit = {
        "id": "recover-me",
        "status": "available",
        "is_supported_by_plan": True,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    with (
        patch(
            "app.utils.rotation.oauth.list_reset_credits",
            return_value={"available_count": 1, "credits": [credit]},
        ),
        patch(
            "app.utils.rotation.oauth.consume_reset_credit",
            return_value={"code": "reset", "windows_reset": 1, "idempotency_key": "auto"},
        ) as consume,
        patch(
            "app.utils.rotation.oauth.fetch_usage",
            side_effect=[
                {
                    "five_hour": {"utilization": 0.0},
                    "weekly": {"utilization": 1.0},
                    "monthly": None,
                    "reset_credits_available": 1,
                    "limit_reached": True,
                },
                {
                    "five_hour": {"utilization": 0.0},
                    "weekly": {"utilization": 0.0},
                    "monthly": None,
                    "reset_credits_available": 0,
                    "limit_reached": False,
                },
            ],
        ),
    ):
        response = client.post(
            "/api/v1/responses",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "gpt-5.4"},
        )

    assert response.status_code == 200, response.text
    assert upstream.call_count == 1
    assert consume.call_args.kwargs["credit_id"] == "recover-me"
    with SessionFactory() as db:
        account = db.get(AccountDb, account_id)
        assert account.weekly_used_pct == 0.0
        assert account.reset_credits_available == 0


@respx.mock
def test_successful_response_that_reaches_weekly_limit_redeems_without_replaying_request(
    client,
    seed_account,
    make_user,
):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch

    from app.utils.postgres import AccountDb
    from app.utils.postgres.base import SessionFactory

    account_id = seed_account("success-to-exhausted", weekly_used_pct=0.99)
    key = make_user("success-to-exhausted-user")
    respx.route(host="testserver").pass_through()
    upstream = respx.post(CODEX_RESPONSES).mock(
        return_value=httpx.Response(
            200,
            headers={
                "x-codex-secondary-used-percent": "100",
                "x-codex-rate-limit-reset-credits-available": "1",
            },
            json={"model": "gpt-5.4", "usage": {"input_tokens": 2, "output_tokens": 1}},
        )
    )
    credit = {
        "id": "on-transition",
        "status": "available",
        "is_supported_by_plan": True,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    with (
        patch(
            "app.utils.rotation.oauth.list_reset_credits",
            return_value={"available_count": 1, "credits": [credit]},
        ),
        patch(
            "app.utils.rotation.oauth.consume_reset_credit",
            return_value={"code": "reset", "windows_reset": 1, "idempotency_key": "auto"},
        ) as consume,
        patch(
            "app.utils.rotation.oauth.fetch_usage",
            side_effect=[
                {
                    "five_hour": {"utilization": 0.0},
                    "weekly": {"utilization": 1.0},
                    "monthly": None,
                    "reset_credits_available": 1,
                    "limit_reached": True,
                },
                {
                    "five_hour": {"utilization": 0.0},
                    "weekly": {"utilization": 0.0},
                    "monthly": None,
                    "reset_credits_available": 0,
                    "limit_reached": False,
                },
            ],
        ),
    ):
        response = client.post(
            "/api/v1/responses",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "gpt-5.4"},
        )

    assert response.status_code == 200, response.text
    assert upstream.call_count == 1
    assert consume.call_args.kwargs["credit_id"] == "on-transition"
    with SessionFactory() as db:
        assert db.get(AccountDb, account_id).weekly_used_pct == 0.0


def test_proxy_rejects_arbitrary_responses_subpaths(client, make_user):
    key = make_user("strict-routes")
    headers = {"Authorization": f"Bearer {key}"}

    assert client.post("/api/v1/responses/arbitrary", headers=headers, json={}).status_code == 404
    assert client.post("/api/v1/responses/resp_123/cancel", headers=headers, json={}).status_code == 404
    assert client.post("/api/v1/responses/input_tokens", headers=headers, json={}).status_code == 404


@respx.mock
def test_proxy_forwards_standalone_search_and_records_correlated_events(
    client,
    seed_account,
    make_user,
):
    from app.utils.postgres import ProxyEventDb, UsageRecordDb
    from app.utils.postgres.base import SessionFactory

    account_id = seed_account("search-account")
    key = make_user("search-user")
    request_body = {
        "id": "session-search-1",
        "model": "gpt-5.6-sol",
        "input": "Find the latest documentation",
        "commands": {"search_query": [{"q": "OpenAI Codex documentation"}]},
        "settings": {"external_web_access": True},
        "max_output_tokens": 2500,
    }
    search_response = {
        "encrypted_output": "ciphertext",
        "output": "Search results",
        "results": [
            {
                "type": "text_result",
                "ref_id": "turn0search0",
                "url": "https://developers.openai.com/codex/",
            }
        ],
    }
    turn_metadata = json.dumps(
        {
            "thread_id": "thread-search-1",
            "turn_id": "turn-search-1",
            "root_turn_id": "root-search-1",
        }
    )
    respx.route(host="testserver").pass_through()

    def search(request):
        assert request.headers["authorization"] == "Bearer upstream-access-token"
        assert request.headers["chatgpt-account-id"] == "chatgpt-search-account"
        assert request.headers["originator"] == "chatgpt_cca"
        assert request.headers["x-codex-turn-metadata"] == turn_metadata
        assert json.loads(request.content) == request_body
        return httpx.Response(
            200,
            headers={"x-request-id": "search-provider-request"},
            json=search_response,
        )

    upstream = respx.post(CODEX_SEARCH).mock(side_effect=search)
    response = client.post(
        "/api/v1/alpha/search",
        headers={
            "Authorization": f"Bearer {key}",
            "Originator": "chatgpt_cca",
            "X-Codex-Turn-Metadata": turn_metadata,
        },
        json=request_body,
    )

    assert response.status_code == 200, response.text
    assert response.json() == search_response
    assert upstream.call_count == 1
    with SessionFactory() as db:
        events = db.query(ProxyEventDb).filter(ProxyEventDb.codex_session_id == "session-search-1").order_by(ProxyEventDb.created_at.asc()).all()
        assert [event.event_type for event in events] == [
            "request.received",
            "account.attempt",
            "account.response_received",
            "response.returned",
        ]
        assert all(event.codex_thread_id == "thread-search-1" for event in events)
        assert all(event.codex_turn_id == "turn-search-1" for event in events)
        assert all(event.codex_root_turn_id == "root-search-1" for event in events)
        assert events[-1].account_id == account_id
        assert db.query(UsageRecordDb).count() == 0


@respx.mock
def test_proxy_search_fails_over_after_account_rate_limit(
    client,
    seed_account,
    make_user,
):
    from app.utils.postgres import ProxyEventDb
    from app.utils.postgres.base import SessionFactory

    first_id = seed_account("search-limited", priority=1)
    second_id = seed_account("search-healthy", priority=2)
    key = make_user("search-failover-user")
    respx.route(host="testserver").pass_through()

    def search(request):
        if request.headers["chatgpt-account-id"] == "chatgpt-search-limited":
            return httpx.Response(
                429,
                json={
                    "error": {
                        "type": "usage_limit_reached",
                        "message": "The usage limit has been reached",
                        "resets_in_seconds": 3600,
                    }
                },
            )
        return httpx.Response(
            200,
            json={"output": "Recovered", "results": []},
        )

    upstream = respx.post(CODEX_SEARCH).mock(side_effect=search)
    response = client.post(
        "/api/v1/alpha/search",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "id": "session-search-failover",
            "model": "gpt-5.6-sol",
            "commands": {"search_query": [{"q": "status"}]},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["output"] == "Recovered"
    assert upstream.call_count == 2
    with SessionFactory() as db:
        rate_limited = (
            db.query(ProxyEventDb)
            .filter(
                ProxyEventDb.codex_session_id == "session-search-failover",
                ProxyEventDb.event_type == "account.rate_limited",
            )
            .one()
        )
        returned = (
            db.query(ProxyEventDb)
            .filter(
                ProxyEventDb.codex_session_id == "session-search-failover",
                ProxyEventDb.event_type == "response.returned",
            )
            .one()
        )
        assert rate_limited.account_id == first_id
        assert returned.account_id == second_id


def test_proxy_search_rejects_invalid_json(client, make_user):
    key = make_user("search-invalid-json")
    response = client.post(
        "/api/v1/alpha/search",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        content=b"not-json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_json"


def test_events_operation_filter_separates_search_from_inference(client, admin_headers):
    from app.utils.postgres import ProxyEventDb
    from app.utils.postgres.base import SessionFactory

    with SessionFactory() as db:
        db.add_all(
            [
                ProxyEventDb(
                    id=uuid.uuid4(),
                    request_id="req-search-event",
                    event_type="response.returned",
                    metadata_json=json.dumps(
                        {"operation": "web_search", "client_protocol": "codex_search"},
                        separators=(",", ":"),
                    ),
                ),
                ProxyEventDb(
                    id=uuid.uuid4(),
                    request_id="req-inference-event",
                    event_type="response.returned",
                    metadata_json=json.dumps({"model": "gpt-5.6-sol"}, separators=(",", ":")),
                ),
            ]
        )
        db.commit()

    search = client.get("/api/v1/events?operation=web_search", headers=admin_headers)
    inference = client.get("/api/v1/events?operation=inference", headers=admin_headers)

    assert search.status_code == 200
    assert [event["request_id"] for event in search.json()["events"]] == ["req-search-event"]
    assert inference.status_code == 200
    assert [event["request_id"] for event in inference.json()["events"]] == ["req-inference-event"]


def test_events_keep_routed_model_when_usage_reports_provider_alias(client, admin_headers, make_user):
    from datetime import datetime, timezone

    from app.utils.postgres import ProxyEventDb, UsageRecordDb, UserDb
    from app.utils.postgres.base import SessionFactory

    make_user("provider-alias-events")
    with SessionFactory() as db:
        user = db.query(UserDb).filter(UserDb.name == "provider-alias-events").one()
        request_id = "req-provider-model-alias"
        db.add(
            ProxyEventDb(
                id=uuid.uuid4(),
                request_id=request_id,
                user_id=user.id,
                event_type="response.returned",
                status_code=200,
                metadata_json=json.dumps(
                    {"model": "gpt-6-astra", "requested_model": "gpt-6-astra"},
                    separators=(",", ":"),
                ),
                created_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            UsageRecordDb(
                id=uuid.uuid4(),
                user_id=user.id,
                model="gpt-5.6-luna",
                input_tokens=10,
                output_tokens=5,
                status_code=200,
                request_id=request_id,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    response = client.get("/api/v1/events?request_id=req-provider-model-alias", headers=admin_headers)
    assert response.status_code == 200, response.text
    metadata = response.json()["events"][0]["metadata"]
    assert metadata["model"] == "gpt-6-astra"
    assert metadata["requested_model"] == "gpt-6-astra"
    assert metadata["upstream_response_model"] == "gpt-5.6-luna"


@respx.mock
def test_proxy_compacts_without_generation_only_request_mutations(
    client,
    admin_headers,
    seed_account,
    make_user,
):
    seed_account("compactor")
    key = make_user("compactor-user")
    request_body = {
        "model": "gpt-5.6-sol",
        "input": [{"role": "user", "content": "Keep this context."}],
    }
    compacted = {
        "id": "resp_compact",
        "object": "response.compaction",
        "output": [{"type": "compaction", "encrypted_content": "opaque"}],
        "usage": {"input_tokens": 12, "output_tokens": 3},
    }

    respx.route(host="testserver").pass_through()

    def compact(request):
        assert json.loads(request.content) == request_body
        return httpx.Response(200, json=compacted)

    upstream = respx.post(CODEX_RESPONSES_COMPACT).mock(side_effect=compact)
    response = client.post(
        "/api/v1/responses/compact",
        headers={"Authorization": f"Bearer {key}"},
        json=request_body,
    )

    assert response.status_code == 200
    assert response.json() == compacted
    assert upstream.call_count == 1
    records = client.get("/api/v1/stats/usage", headers=admin_headers).json()
    assert records["total"] == 1
    assert records["items"][0]["input_tokens"] == 12
    assert records["items"][0]["output_tokens"] == 3


def test_account_priority_update_preserves_duplicate_priority_groups(client, admin_headers, seed_account):
    first = seed_account("first", priority=1)
    second = seed_account("second", priority=1)
    third = seed_account("third", priority=3)

    moved = client.put(
        f"/api/v1/accounts/{third}",
        headers=admin_headers,
        json={"priority": 1},
    )
    assert moved.status_code == 200
    accounts = client.get("/api/v1/accounts", headers=admin_headers).json()["accounts"]
    assert {account["label"]: account["priority"] for account in accounts} == {
        "first": 1,
        "second": 1,
        "third": 1,
    }
    assert str(first) != str(third)
    assert str(second) != str(third)


def test_account_delete_preserves_remaining_priority_lanes(client, admin_headers, seed_account):
    seed_account("delete-first", priority=1)
    middle = seed_account("delete-middle", priority=3)
    seed_account("delete-last", priority=3)

    response = client.delete(f"/api/v1/accounts/{middle}", headers=admin_headers)

    assert response.status_code == 204
    accounts = client.get("/api/v1/accounts", headers=admin_headers).json()["accounts"]
    assert {account["label"]: account["priority"] for account in accounts} == {
        "delete-first": 1,
        "delete-last": 3,
    }


def test_bulk_priority_assignment_updates_selected_accounts_and_users(client, admin_headers, seed_account):
    account_ids = [str(seed_account("bulk-a", priority=1)), str(seed_account("bulk-b", priority=2)), str(seed_account("keep", priority=3))]
    account_response = client.put(
        "/api/v1/accounts/priorities/bulk",
        headers=admin_headers,
        json={"account_ids": account_ids[:2], "priority": 4},
    )
    assert account_response.status_code == 200, account_response.text
    account_priorities = {item["label"]: item["priority"] for item in account_response.json()["accounts"]}
    assert account_priorities == {"bulk-a": 4, "bulk-b": 4, "keep": 3}

    users = [
        client.post("/api/v1/users", headers=admin_headers, json={"name": name}).json()["user"]["id"]
        for name in ("bulk-user-a", "bulk-user-b", "keep-user")
    ]
    user_response = client.put(
        "/api/v1/users/priorities/bulk",
        headers=admin_headers,
        json={"user_ids": users[:2], "priority": 3},
    )
    assert user_response.status_code == 200, user_response.text
    user_priorities = {item["name"]: item["priority"] for item in user_response.json()["users"]}
    assert user_priorities["bulk-user-a"] == 3
    assert user_priorities["bulk-user-b"] == 3
    assert user_priorities["keep-user"] == 1

    duplicate = client.put(
        "/api/v1/users/priorities/bulk",
        headers=admin_headers,
        json={"user_ids": [users[0], users[0]], "priority": 2},
    )
    assert duplicate.status_code == 422


def test_account_email_can_only_be_updated_by_oauth(client, admin_headers, seed_account):
    account_id = seed_account("oauth-owned-email")

    response = client.put(
        f"/api/v1/accounts/{account_id}",
        headers=admin_headers,
        json={"account_email": "manual@example.com"},
    )

    assert response.status_code == 422
    account = client.get("/api/v1/accounts", headers=admin_headers).json()["accounts"][0]
    assert account["account_email"] == "oauth-owned-email@example.com"


def test_bulk_priority_update_is_atomic_and_complete(client, admin_headers, seed_account):
    first = seed_account("first", priority=1)
    second = seed_account("second", priority=2)
    third = seed_account("third", priority=3)
    response = client.put(
        "/api/v1/accounts/priorities",
        headers=admin_headers,
        json={"account_ids": [str(third), str(first), str(second)]},
    )
    assert response.status_code == 200
    assert [(account["label"], account["priority"]) for account in response.json()["accounts"]] == [
        ("third", 1),
        ("first", 2),
        ("second", 3),
    ]
    invalid = client.put(
        "/api/v1/accounts/priorities",
        headers=admin_headers,
        json={"account_ids": [str(first), str(first), str(second)]},
    )
    assert invalid.status_code == 422


def test_limit_reset_is_blocked_when_only_five_hour_usage_is_above_ninety_percent(
    client,
    admin_headers,
    seed_account,
):
    from unittest.mock import patch

    account_id = seed_account("guarded-reset", five_hour_used_pct=1.0, weekly_used_pct=0.50)
    with (
        patch("app.routes.accounts._probe_account"),
        patch(
            "app.routes.accounts.oauth.list_reset_credits",
            return_value={"available_count": 0, "credits": []},
        ),
        patch("app.routes.accounts.oauth.consume_reset_credit") as consume,
    ):
        response = client.post(
            f"/api/v1/accounts/{account_id}/limit-resets/consume",
            headers=admin_headers,
            json={},
        )
    assert response.status_code == 409
    assert response.json()["detail"] == ("Weekly Codex usage must be above 90%, or the selected limit reset must expire within 12 hours.")
    consume.assert_not_called()


def test_limit_reset_is_blocked_at_exactly_ninety_percent_weekly(
    client,
    admin_headers,
    seed_account,
):
    from unittest.mock import patch

    account_id = seed_account("boundary-reset", five_hour_used_pct=0.0, weekly_used_pct=0.90)
    with (
        patch("app.routes.accounts._probe_account"),
        patch(
            "app.routes.accounts.oauth.list_reset_credits",
            return_value={"available_count": 0, "credits": []},
        ),
        patch("app.routes.accounts.oauth.consume_reset_credit") as consume,
    ):
        response = client.post(
            f"/api/v1/accounts/{account_id}/limit-resets/consume",
            headers=admin_headers,
            json={},
        )

    assert response.status_code == 409
    consume.assert_not_called()


def test_limit_reset_is_allowed_above_ninety_percent_weekly(client, admin_headers, seed_account):
    from unittest.mock import patch

    account_id = seed_account("weekly-reset", five_hour_used_pct=0.0, weekly_used_pct=0.901)
    with (
        patch("app.routes.accounts._probe_account"),
        patch(
            "app.routes.accounts.oauth.consume_reset_credit",
            return_value={
                "code": "reset",
                "windows_reset": 1,
                "idempotency_key": "request-1",
            },
        ) as consume,
    ):
        response = client.post(
            f"/api/v1/accounts/{account_id}/limit-resets/consume",
            headers=admin_headers,
            json={"credit_id": "weekly-credit", "idempotency_key": "request-1"},
        )

    assert response.status_code == 200
    consume.assert_called_once_with(
        "upstream-access-token",
        "chatgpt-weekly-reset",
        credit_id="weekly-credit",
        idempotency_key="request-1",
    )


def test_limit_reset_expiring_within_twelve_hours_bypasses_weekly_usage_guard(
    client,
    admin_headers,
    seed_account,
):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch

    account_id = seed_account("expiring-reset", five_hour_used_pct=0.0, weekly_used_pct=0.25)
    reset_credit = {
        "id": "expiring-credit",
        "reset_type": "codex",
        "status": "available",
        "is_supported_by_plan": True,
        "granted_at": datetime.now(timezone.utc) - timedelta(days=1),
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=11, minutes=59),
    }
    with (
        patch("app.routes.accounts._probe_account"),
        patch(
            "app.routes.accounts.oauth.list_reset_credits",
            return_value={"available_count": 1, "credits": [reset_credit]},
        ),
        patch(
            "app.routes.accounts.oauth.consume_reset_credit",
            return_value={
                "code": "reset",
                "windows_reset": 1,
                "idempotency_key": "request-1",
            },
        ) as consume,
    ):
        response = client.post(
            f"/api/v1/accounts/{account_id}/limit-resets/consume",
            headers=admin_headers,
            json={"idempotency_key": "request-1"},
        )

    assert response.status_code == 200
    consume.assert_called_once_with(
        "upstream-access-token",
        "chatgpt-expiring-reset",
        credit_id="expiring-credit",
        idempotency_key="request-1",
    )


def test_limit_reset_expiry_exception_excludes_expired_and_exactly_twelve_hour_credits():
    from datetime import datetime, timedelta, timezone

    from app.routes.accounts import _reset_credit_expires_soon
    from app.utils.models.api import RateLimitResetCredit

    now = datetime.now(timezone.utc)

    def credit(expires_at):
        return RateLimitResetCredit(
            id="credit",
            reset_type="codex",
            status="available",
            is_supported_by_plan=True,
            granted_at=now - timedelta(days=1),
            expires_at=expires_at,
        )

    assert _reset_credit_expires_soon(credit(now + timedelta(hours=11, minutes=59)), now=now)
    assert not _reset_credit_expires_soon(credit(now + timedelta(hours=12)), now=now)
    assert not _reset_credit_expires_soon(credit(now - timedelta(seconds=1)), now=now)


@respx.mock
def test_proxy_returns_fixed_codex_model_catalog(client, seed_account, make_user):
    seed_account("catalog")
    key = make_user("catalog-user")

    respx.route(host="testserver").pass_through()
    route = respx.get(CODEX_MODELS, params={"client_version": "0.144.5"}).mock(
        return_value=httpx.Response(200, json={"models": [{"slug": "gpt-5.4"}]})
    )

    response = client.get(
        "/api/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        params={"client_version": "0.144.5"},
    )
    assert response.status_code == 200
    assert [model["slug"] for model in response.json()["models"]] == [
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-6-astra",
        "codex-auto-review",
    ]
    assert not route.called


@respx.mock
def test_model_catalog_does_not_probe_pooled_accounts(client, seed_account, make_user):
    seed_account("go", priority=1)
    seed_account("team", priority=2)
    key = make_user("union-user")
    respx.route(host="testserver").pass_through()

    def catalog(request):
        models = [{"slug": "gpt-5.6-luna"}]
        if request.headers["chatgpt-account-id"] == "chatgpt-team":
            models.append({"slug": "gpt-5.6-sol"})
        return httpx.Response(200, json={"models": models})

    route = respx.get(CODEX_MODELS, params={"client_version": "0.144.5"}).mock(side_effect=catalog)
    response = client.get("/api/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200
    assert {model["id"] for model in response.json()["data"]} == {
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-6-astra",
        "codex-auto-review",
    }
    assert route.call_count == 0


@respx.mock
def test_model_catalog_is_filtered_by_user_allowlist(client, admin_headers, seed_account):
    seed_account("filtered-catalog")
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"name": "restricted-catalog", "allowed_models": ["gpt-5.6-sol"]},
    )
    user_id = created.json()["user"]["id"]
    key = client.post(f"/api/v1/users/{user_id}/keys", headers=admin_headers, json={"label": "test-key"}).json()["secret"]
    respx.route(host="testserver").pass_through()
    respx.get(CODEX_MODELS, params={"client_version": "0.144.5"}).mock(
        return_value=httpx.Response(
            200,
            json={"models": [{"slug": "gpt-5.6-luna"}, {"slug": "gpt-5.6-sol"}]},
        )
    )

    response = client.get("/api/v1/models", headers={"Authorization": f"Bearer {key}"})

    assert response.status_code == 200, response.text
    assert [model["id"] for model in response.json()["data"]] == ["gpt-5.6-sol"]


@respx.mock
def test_sol_skips_higher_priority_go_account(client, seed_account, make_user):
    from app.utils.postgres import AccountDb
    from app.utils.postgres.base import SessionFactory

    go_id = seed_account("go", priority=1)
    team_id = seed_account("team", priority=2)
    with SessionFactory() as db:
        db.get(AccountDb, go_id).model_catalog_json = '[{"slug":"gpt-5.6-luna"}]'
        db.get(AccountDb, team_id).model_catalog_json = '[{"slug":"gpt-5.6-luna"},{"slug":"gpt-5.6-sol"}]'
        db.commit()

    key = make_user("sol-user")
    respx.route(host="testserver").pass_through()

    def inference(request):
        assert request.headers["chatgpt-account-id"] == "chatgpt-team"
        upstream_body = json.loads(request.content)
        assert upstream_body["store"] is False
        assert "max_output_tokens" not in upstream_body
        return httpx.Response(
            200,
            json={"model": "gpt-5.6-sol", "usage": {"input_tokens": 1, "output_tokens": 1}},
        )

    route = respx.post(CODEX_RESPONSES).mock(side_effect=inference)
    response = client.post(
        "/api/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gpt-5.6-sol", "input": "hello", "max_output_tokens": 64},
    )
    assert response.status_code == 200
    assert route.call_count == 1


@respx.mock
def test_unknown_model_account_keeps_priority_and_fails_over_if_rejected(client, seed_account, make_user):
    from app.utils.postgres import AccountDb
    from app.utils.postgres.base import SessionFactory

    seed_account("new", priority=1)
    confirmed_id = seed_account("confirmed", priority=2)
    with SessionFactory() as db:
        db.get(AccountDb, confirmed_id).model_catalog_json = '[{"slug":"gpt-5.6-sol"}]'
        db.commit()

    key = make_user("new-account-user")
    respx.route(host="testserver").pass_through()
    route = respx.post(CODEX_RESPONSES).mock(
        side_effect=[
            httpx.Response(403, json={"error": {"message": "model is not available for this account"}}),
            httpx.Response(200, json={"model": "gpt-5.6-sol", "usage": {"input_tokens": 1, "output_tokens": 1}}),
        ]
    )

    response = client.post(
        "/api/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gpt-5.6-sol", "input": "hello"},
    )

    assert response.status_code == 200
    assert [call.request.headers["chatgpt-account-id"] for call in route.calls] == ["chatgpt-new", "chatgpt-confirmed"]


@respx.mock
def test_proxy_normalizes_fixed_model_catalog_for_openai_clients(client, seed_account, make_user):
    seed_account("catalog-sdk")
    key = make_user("catalog-sdk-user")

    respx.route(host="testserver").pass_through()
    route = respx.get(CODEX_MODELS, params={"client_version": "0.144.5"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {"slug": "gpt-5.4"},
                    {"slug": "gpt-5.6", "owned_by": "openai"},
                ]
            },
        )
    )

    response = client.get(
        "/api/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "object": "list",
        "data": [
            {"id": "gpt-5.6-luna", "object": "model", "created": 0, "owned_by": "openai"},
            {"id": "gpt-5.6-sol", "object": "model", "created": 0, "owned_by": "openai"},
            {"id": "gpt-5.6-terra", "object": "model", "created": 0, "owned_by": "openai"},
            {"id": "gpt-6-astra", "object": "model", "created": 0, "owned_by": "openai"},
            {"id": "codex-auto-review", "object": "model", "created": 0, "owned_by": "openai"},
        ],
    }
    assert not route.called


@respx.mock
def test_proxy_nonstreaming_records_usage(client, admin_headers, seed_account, make_user):
    seed_account("a1")
    key = make_user("bob")

    respx.route(host="testserver").pass_through()

    def upstream(request):
        assert json.loads(request.content)["reasoning"] == {"effort": "none"}
        return httpx.Response(200, json={"model": "gpt-5.4", "usage": {"input_tokens": 10, "output_tokens": 5}})

    respx.post(CODEX_RESPONSES).mock(side_effect=upstream)

    resp = client.post("/api/v1/responses", headers={"Authorization": f"Bearer {key}"}, json={"model": "gpt-5.4", "input": []})
    assert resp.status_code == 200

    usage = client.get("/api/v1/stats/usage", headers=admin_headers).json()
    assert usage["total"] == 1
    record = usage["items"][0]
    assert record["user_name"] == "bob"
    assert record["account_label"] == "a1"
    assert record["input_tokens"] == 10
    assert record["output_tokens"] == 5
    assert record["request_mode"] == "standard"
    assert record["reasoning_level"] == "none"


@respx.mock
def test_proxy_enforces_user_mode_and_thinking_policy_and_tracks_fast_mode(
    client,
    admin_headers,
    seed_account,
):
    seed_account("policy")
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "name": "policy-user",
            "allowed_request_modes": ["fast"],
            "allowed_reasoning_levels": ["high"],
            "allowed_models": ["gpt-5.6"],
        },
    )
    user_id = created.json()["user"]["id"]
    key_response = client.post(f"/api/v1/users/{user_id}/keys", headers=admin_headers, json={"label": "test-key"})
    key = key_response.json()["secret"]
    headers = {"Authorization": f"Bearer {key}"}

    respx.route(host="testserver").pass_through()
    upstream = respx.post(CODEX_RESPONSES).mock(
        return_value=httpx.Response(
            200,
            json={"model": "gpt-5.6", "usage": {"input_tokens": 3, "output_tokens": 2}},
        )
    )

    standard = client.post(
        "/api/v1/responses",
        headers=headers,
        json={"model": "gpt-5.6", "reasoning": {"effort": "high"}},
    )
    assert standard.status_code == 403
    assert "Standard request mode" in standard.json()["detail"]

    wrong_level = client.post(
        "/api/v1/responses",
        headers=headers,
        json={"model": "gpt-5.6", "service_tier": "fast", "reasoning": {"effort": "low"}},
    )
    assert wrong_level.status_code == 403
    assert "Thinking level 'low'" in wrong_level.json()["detail"]

    wrong_model = client.post(
        "/api/v1/responses",
        headers=headers,
        json={"model": "gpt-5.4", "service_tier": "fast", "reasoning": {"effort": "high"}},
    )
    assert wrong_model.status_code == 403
    assert "Model 'gpt-5.4'" in wrong_model.json()["detail"]

    missing_model = client.post(
        "/api/v1/responses",
        headers=headers,
        json={"service_tier": "fast", "reasoning": {"effort": "high"}},
    )
    assert missing_model.status_code == 403
    assert "must explicitly set an allowed model" in missing_model.json()["detail"]

    missing_level = client.post(
        "/api/v1/responses",
        headers=headers,
        json={"model": "gpt-5.6", "service_tier": "fast"},
    )
    assert missing_level.status_code == 403
    assert "Thinking level 'none'" in missing_level.json()["detail"]
    assert upstream.call_count == 0

    allowed = client.post(
        "/api/v1/responses",
        headers=headers,
        json={
            "model": "gpt-5.6",
            # The Responses API accepts both aliases for Fast mode.
            "service_tier": "priority",
            "reasoning": {"effort": "HIGH"},
        },
    )
    assert allowed.status_code == 200, allowed.text
    assert upstream.call_count == 1
    record = client.get("/api/v1/stats/usage", headers=admin_headers).json()["items"][0]
    assert record["request_mode"] == "fast"
    assert record["reasoning_level"] == "high"


@respx.mock
def test_proxy_enforces_and_tracks_ultrafast_mode(client, admin_headers, seed_account):
    seed_account("ultrafast-policy")
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "name": "ultrafast-user",
            "allowed_request_modes": ["ultrafast"],
            "allowed_reasoning_levels": ["none"],
            "allowed_models": ["gpt-5.6-sol"],
        },
    )
    user_id = created.json()["user"]["id"]
    key_response = client.post(f"/api/v1/users/{user_id}/keys", headers=admin_headers, json={"label": "test-key"})
    key = key_response.json()["secret"]
    headers = {"Authorization": f"Bearer {key}"}

    respx.route(host="testserver").pass_through()
    upstream = respx.post(CODEX_RESPONSES).mock(
        return_value=httpx.Response(
            200,
            json={"model": "gpt-5.6-sol", "usage": {"input_tokens": 3, "output_tokens": 2}},
        )
    )

    denied = client.post(
        "/api/v1/responses",
        headers=headers,
        json={"model": "gpt-5.6-sol", "service_tier": "fast"},
    )
    assert denied.status_code == 403
    assert "Fast request mode" in denied.json()["detail"]

    allowed = client.post(
        "/api/v1/responses",
        headers=headers,
        json={"model": "gpt-5.6-sol", "service_tier": "ultrafast"},
    )
    assert allowed.status_code == 200, allowed.text
    assert upstream.call_count == 1
    forwarded = json.loads(upstream.calls[0].request.content)
    assert forwarded["service_tier"] == "ultrafast"
    record = client.get("/api/v1/stats/usage", headers=admin_headers).json()["items"][0]
    assert record["request_mode"] == "ultrafast"


@respx.mock
def test_thinking_level_mix_groups_requests_per_user_and_defaults_unreported_to_none(
    client,
    admin_headers,
    seed_account,
    make_user,
):
    seed_account("thinking-mix")
    alice_key = make_user("alice")
    bob_key = make_user("bob")

    respx.route(host="testserver").pass_through()
    respx.post(CODEX_RESPONSES).mock(
        return_value=httpx.Response(
            200,
            json={"model": "gpt-5.4", "usage": {"input_tokens": 4, "output_tokens": 2}},
        )
    )

    def make_request(key, effort=None):
        body = {"model": "gpt-5.4", "input": "hello"}
        if effort is not None:
            body["reasoning"] = {"effort": effort}
        response = client.post(
            "/api/v1/responses",
            headers={"Authorization": f"Bearer {key}"},
            json=body,
        )
        assert response.status_code == 200, response.text

    make_request(alice_key, "high")
    make_request(alice_key, "HIGH")
    make_request(alice_key, "low")
    make_request(bob_key, "medium")
    make_request(bob_key)

    response = client.get("/api/v1/stats/thinking-level-mix", headers=admin_headers)
    assert response.status_code == 200
    users = {user["user_name"]: user for user in response.json()["users"]}
    assert set(users) == {"alice", "bob"}

    alice = users["alice"]
    assert alice["total_requests"] == 3
    alice_levels = {item["thinking_level"]: item for item in alice["thinking_levels"]}
    assert alice_levels["high"] == {
        "thinking_level": "high",
        "requests": 2,
        "input_tokens": 8,
        "output_tokens": 4,
    }
    assert alice_levels["low"]["requests"] == 1

    bob = users["bob"]
    assert bob["total_requests"] == 2
    bob_levels = {item["thinking_level"]: item for item in bob["thinking_levels"]}
    assert bob_levels["medium"]["requests"] == 1
    assert bob_levels["none"]["requests"] == 1

    alice_id = next(user["id"] for user in client.get("/api/v1/users", headers=admin_headers).json()["users"] if user["name"] == "alice")
    filtered = client.get(
        "/api/v1/stats/thinking-level-mix",
        headers=admin_headers,
        params={"user_id": alice_id},
    )
    assert [user["user_name"] for user in filtered.json()["users"]] == ["alice"]


@respx.mock
def test_proxy_converts_codex_stream_to_nonstreaming_openai_response(
    client,
    admin_headers,
    seed_account,
    make_user,
):
    seed_account("sdk")
    key = make_user("sdk-user")
    completed = {
        "id": "resp_test",
        "object": "response",
        "status": "completed",
        "model": "gpt-5.4",
        "output": [
            {
                "id": "msg_test",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "SDK_OK", "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 11,
            "output_tokens": 2,
            "input_tokens_details": {"cached_tokens": 5, "cache_write_tokens": 1},
        },
    }
    terminal = {**completed, "output": []}
    item = completed["output"][0]
    sse = (
        "event: response.output_item.done\n"
        f"data: {json.dumps({'type': 'response.output_item.done', 'output_index': 0, 'item': item})}\n\n"
        "event: response.completed\n"
        f"data: {json.dumps({'type': 'response.completed', 'response': terminal})}\n\n"
    )

    respx.route(host="testserver").pass_through()
    route = respx.post(CODEX_RESPONSES).mock(
        return_value=httpx.Response(
            200,
            # The live Codex backend currently labels its SSE body as JSON.
            headers={"content-type": "application/json"},
            text=sse,
        )
    )

    response = client.post(
        "/api/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gpt-5.4", "input": "Say SDK_OK", "reasoning": {"effort": "low"}},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == completed
    upstream_request = json.loads(route.calls[0].request.content)
    assert upstream_request["stream"] is True
    assert upstream_request["input"] == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "Say SDK_OK"}],
        }
    ]

    record = client.get("/api/v1/stats/usage", headers=admin_headers).json()["items"][0]
    assert record["input_tokens"] == 11
    assert record["output_tokens"] == 2
    assert record["cached_input_tokens"] == 5
    assert record["cache_write_tokens"] == 1
    assert record["reasoning_level"] == "low"


@respx.mock
def test_proxy_streaming_relays_and_records(client, admin_headers, seed_account, make_user):
    seed_account("a1")
    key = make_user("carol")

    sse = (
        'data: {"type":"response.completed","response":{"model":"gpt-5.4","usage":'
        '{"input_tokens":20,"output_tokens":42,"input_tokens_details":'
        '{"cached_tokens":3,"cache_write_tokens":4}}}}\n\n'
        "data: [DONE]\n\n"
    )

    respx.route(host="testserver").pass_through()
    route = respx.post(CODEX_RESPONSES).mock(return_value=httpx.Response(200, headers={"content-type": "application/json"}, text=sse))

    resp = client.post(
        "/api/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "x", "input": "Say hello", "stream": True, "reasoning": {"effort": "high"}},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "response.completed" in resp.text
    assert '"model":"x"' in resp.text
    assert '"model":"gpt-5.4"' not in resp.text
    assert json.loads(route.calls[0].request.content)["input"] == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "Say hello"}],
        }
    ]

    record = client.get("/api/v1/stats/usage", headers=admin_headers).json()["items"][0]
    assert record["input_tokens"] == 20
    assert record["output_tokens"] == 42
    assert record["cached_input_tokens"] == 3
    assert record["cache_write_tokens"] == 4
    assert record["reasoning_level"] == "high"
    assert record["model"] == "gpt-5.4"


@respx.mock
def test_proxy_fails_over_on_429(client, admin_headers, seed_account, make_user):
    from app.utils.models.api import AccountStatus
    from app.utils.postgres import AccountDb
    from app.utils.postgres.base import SessionFactory

    first_id = seed_account("a1")
    seed_account("a2")
    key = make_user("dave")

    respx.route(host="testserver").pass_through()
    respx.post(CODEX_RESPONSES).mock(
        side_effect=[
            httpx.Response(
                429,
                headers={"retry-after": "1"},
                json={"error": {"message": "Rate limited by OpenAI"}},
            ),
            httpx.Response(200, json={"model": "gpt-5.4", "usage": {"input_tokens": 1, "output_tokens": 1}}),
        ]
    )

    resp = client.post("/api/v1/responses", headers={"Authorization": f"Bearer {key}"}, json={"model": "gpt-5.4"})
    assert resp.status_code == 200
    with SessionFactory() as db:
        first = db.get(AccountDb, first_id)
        assert first.status == AccountStatus.COOLDOWN
        assert first.cooldown_until is not None


@respx.mock
def test_proxy_cools_down_capacity_account_and_immediately_fails_over(client, seed_account, make_user):
    from app.utils.models.api import AccountStatus
    from app.utils.postgres import AccountDb
    from app.utils.postgres.base import SessionFactory

    first_id = seed_account("capacity-first")
    seed_account("capacity-second")
    key = make_user("capacity-user")

    capacity = {
        "error": {
            "message": "Selected model is at capacity. Please try a different model.",
        }
    }
    respx.route(host="testserver").pass_through()
    route = respx.post(CODEX_RESPONSES).mock(
        side_effect=[
            httpx.Response(403, json=capacity),
            httpx.Response(200, json={"model": "gpt-5.4", "usage": {"input_tokens": 1, "output_tokens": 1}}),
        ]
    )
    headers = {"Authorization": f"Bearer {key}"}

    assert client.post("/api/v1/responses", headers=headers, json={"model": "gpt-5.4"}).status_code == 200
    account_ids = [call.request.headers["chatgpt-account-id"] for call in route.calls]
    assert account_ids == ["chatgpt-capacity-first", "chatgpt-capacity-second"]
    with SessionFactory() as db:
        first = db.get(AccountDb, first_id)
        assert first.status == AccountStatus.COOLDOWN
        assert first.cooldown_until is not None


@respx.mock
def test_proxy_fails_over_on_streamed_capacity_event(client, seed_account, make_user):
    seed_account("stream-capacity-first")
    seed_account("stream-capacity-second")
    key = make_user("stream-capacity-user")
    capacity_sse = 'data: {"type":"error","error":{"message":"Selected model is at capacity. Please try a different model."}}\n\n'

    respx.route(host="testserver").pass_through()
    route = respx.post(CODEX_RESPONSES).mock(
        side_effect=[
            httpx.Response(200, headers={"content-type": "text/event-stream"}, text=capacity_sse),
            httpx.Response(200, json={"model": "gpt-5.4", "usage": {"input_tokens": 1, "output_tokens": 1}}),
        ]
    )
    response = client.post("/api/v1/responses", headers={"Authorization": f"Bearer {key}"}, json={"model": "gpt-5.4"})
    assert response.status_code == 200
    assert len(route.calls) == 2


@respx.mock
def test_proxy_fails_over_and_cools_down_account_on_empty_404(client, seed_account, make_user):
    from app.utils.models.api import AccountStatus, ProviderHealth
    from app.utils.postgres import AccountDb
    from app.utils.postgres.base import SessionFactory

    first_id = seed_account("empty-404-first")
    seed_account("empty-404-second")
    key = make_user("empty-404-user")

    respx.route(host="testserver").pass_through()
    route = respx.post(CODEX_RESPONSES).mock(
        side_effect=[
            httpx.Response(404, content=b""),
            httpx.Response(200, json={"model": "gpt-5.4", "usage": {"input_tokens": 1, "output_tokens": 1}}),
            httpx.Response(200, json={"model": "gpt-5.4", "usage": {"input_tokens": 1, "output_tokens": 1}}),
        ]
    )
    headers = {"Authorization": f"Bearer {key}"}

    assert client.post("/api/v1/responses", headers=headers, json={"model": "gpt-5.4"}).status_code == 200
    assert client.post("/api/v1/responses", headers=headers, json={"model": "gpt-5.4"}).status_code == 200

    assert len(route.calls) == 3
    assert route.calls[0].request.headers["chatgpt-account-id"] == "chatgpt-empty-404-first"
    assert route.calls[1].request.headers["chatgpt-account-id"] == "chatgpt-empty-404-second"
    assert route.calls[2].request.headers["chatgpt-account-id"] == "chatgpt-empty-404-second"
    with SessionFactory() as db:
        account = db.get(AccountDb, first_id)
        assert account.status == AccountStatus.COOLDOWN
        assert account.provider_health == ProviderHealth.DEGRADED
        assert account.provider_health_code == "upstream_empty_404"
        assert account.cooldown_until is not None


@respx.mock
def test_proxy_preserves_nonempty_404_without_content_type(client, seed_account, make_user):
    seed_account("nonempty-404-first")
    seed_account("nonempty-404-second")
    key = make_user("nonempty-404-user")

    respx.route(host="testserver").pass_through()
    route = respx.post(CODEX_RESPONSES).mock(return_value=httpx.Response(404, content=b"request not found"))

    response = client.post(
        "/api/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gpt-5.4"},
    )

    assert response.status_code == 404
    assert response.content == b"request not found"
    assert len(route.calls) == 1


@respx.mock
def test_proxy_returns_pool_unavailable_after_all_accounts_return_empty_404(client, seed_account, make_user):
    from app.utils.models.api import AccountStatus
    from app.utils.postgres import AccountDb
    from app.utils.postgres.base import SessionFactory

    account_ids = [seed_account("empty-404-a"), seed_account("empty-404-b")]
    key = make_user("all-empty-404-user")

    respx.route(host="testserver").pass_through()
    route = respx.post(CODEX_RESPONSES).mock(side_effect=[httpx.Response(404, content=b""), httpx.Response(404, content=b"")])

    response = client.post(
        "/api/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gpt-5.4"},
    )

    assert response.status_code == 503
    assert len(route.calls) == 2
    with SessionFactory() as db:
        accounts = db.query(AccountDb).filter(AccountDb.id.in_(account_ids)).all()
        assert all(account.status == AccountStatus.COOLDOWN for account in accounts)


@respx.mock
def test_refresh_quota_reports_rate_limit_cleanly(client, admin_headers, seed_account, monkeypatch):
    # A persistently rate-limited probe must return a friendly, actionable message -- not a raw httpx 429 string.
    from app.utils import oauth

    monkeypatch.setattr(oauth.time, "sleep", lambda _seconds: None)
    account_id = seed_account("rl")

    respx.route(host="testserver").pass_through()
    respx.get(oauth.config.OAUTH_USAGE_URL).mock(return_value=httpx.Response(429, json={}))

    resp = client.post(f"/api/v1/accounts/{account_id}/refresh-quota", headers=admin_headers)
    assert resp.status_code == 502
    detail = resp.json()["detail"].lower()
    assert "rate-limit" in detail
    assert "429" not in detail


@respx.mock
def test_refresh_quota_reports_expired_refresh_token_cleanly(client, admin_headers, seed_account):
    # An account whose access token has expired must refresh first; if the stored refresh token is rejected, that
    # must surface as a friendly 502 pointing at re-authentication -- never a raw 500.
    from app.utils import oauth

    account_id = seed_account("stale", expires_in_hours=-1)

    respx.route(host="testserver").pass_through()
    respx.post(oauth.config.OAUTH_TOKEN_URL).mock(return_value=httpx.Response(401, json={"error": "invalid_grant"}))

    resp = client.post(f"/api/v1/accounts/{account_id}/refresh-quota", headers=admin_headers)
    assert resp.status_code == 502
    detail = resp.json()["detail"].lower()
    assert "re-authenticate" in detail
    account = next(item for item in client.get("/api/v1/accounts", headers=admin_headers).json()["accounts"] if item["id"] == str(account_id))
    assert account["provider_health"] == "REAUTH_REQUIRED"
    assert account["provider_health_code"] == "oauth_refresh_rejected"
    assert account["provider_health_failure_count"] >= 1


def test_reauthentication_required_account_is_not_available(seed_account):
    from app.utils import rotation
    from app.utils.models.api import ProviderHealth
    from app.utils.postgres import AccountDb
    from app.utils.postgres.base import SessionFactory

    account_id = seed_account("broken")
    with SessionFactory() as db:
        account = db.get(AccountDb, account_id)
        account.provider_health = ProviderHealth.REAUTH_REQUIRED
        assert rotation.is_available(account) is False


def test_account_label_update(client, admin_headers, seed_account):
    account_id = seed_account("orig")
    updated = client.put(f"/api/v1/accounts/{account_id}", headers=admin_headers, json={"label": "renamed"})
    assert updated.status_code == 200
    assert updated.json()["account"]["label"] == "renamed"

    labels = [a["label"] for a in client.get("/api/v1/accounts", headers=admin_headers).json()["accounts"]]
    assert labels == ["renamed"]


def test_account_labels_can_be_duplicated(client, admin_headers, seed_account):
    first_id = seed_account("shared")
    second_id = seed_account("other")

    updated = client.put(f"/api/v1/accounts/{second_id}", headers=admin_headers, json={"label": "shared"})

    assert updated.status_code == 200
    accounts = client.get("/api/v1/accounts", headers=admin_headers).json()["accounts"]
    assert [(account["id"], account["label"]) for account in accounts] == [
        (str(first_id), "shared"),
        (str(second_id), "shared"),
    ]


def _oauth_tokens(
    *,
    email: str,
    workspace_id: str,
    account_user_id: str,
    user_id: str,
    workspace_name: str | None = None,
) -> dict:
    from datetime import datetime, timedelta, timezone

    return {
        "email": email,
        "tier": "team",
        "account_id": workspace_id,
        "account_user_id": account_user_id,
        "user_id": user_id,
        "workspace_name": workspace_name,
        "is_fedramp": False,
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }


def test_oauth_add_rejects_same_email_in_same_workspace(client, admin_headers, seed_account):
    from unittest.mock import patch

    seed_account(
        "existing",
        account_email="Person@Example.com",
        workspace_id="workspace-1",
        account_user_id="membership-1",
        chatgpt_user_id="user-1",
    )
    tokens = _oauth_tokens(
        email="person@example.com",
        workspace_id="workspace-1",
        account_user_id="membership-new",
        user_id="user-new",
    )

    with patch("app.routes.accounts._tokens_from_flow", return_value=tokens):
        response = client.post(
            "/api/v1/accounts/oauth/complete",
            headers=admin_headers,
            json={"label": "duplicate", "flow_token": "flow"},
        )

    assert response.status_code == 409
    assert "already linked to this workspace" in response.json()["detail"]
    assert len(client.get("/api/v1/accounts", headers=admin_headers).json()["accounts"]) == 1


def test_oauth_add_allows_same_email_in_different_workspace(client, admin_headers, seed_account):
    from unittest.mock import patch

    seed_account("personal", account_email="person@example.com", workspace_id="personal-workspace")
    tokens = _oauth_tokens(
        email="person@example.com",
        workspace_id="team-workspace",
        account_user_id="membership-team",
        user_id="same-user",
    )

    with (
        patch("app.routes.accounts._tokens_from_flow", return_value=tokens),
        patch("app.routes.accounts._probe_account", return_value=False),
    ):
        response = client.post(
            "/api/v1/accounts/oauth/complete",
            headers=admin_headers,
            json={"label": "team", "flow_token": "flow", "workspace_name": "Acme"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["account"]["workspace_name"] == "Acme"


def test_oauth_add_allows_different_users_in_same_workspace(client, admin_headers, seed_account):
    from unittest.mock import patch

    seed_account(
        "first-member",
        account_email="first@example.com",
        workspace_id="team-workspace",
        account_user_id="membership-first",
        chatgpt_user_id="user-first",
        workspace_name="Shared Team",
        tier="team",
    )
    tokens = _oauth_tokens(
        email="second@example.com",
        workspace_id="team-workspace",
        account_user_id="membership-second",
        user_id="user-second",
    )

    with (
        patch("app.routes.accounts._tokens_from_flow", return_value=tokens),
        patch("app.routes.accounts._probe_account", return_value=False),
    ):
        response = client.post(
            "/api/v1/accounts/oauth/complete",
            headers=admin_headers,
            json={"label": "second-member", "flow_token": "flow"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["account"]["workspace_name"] == "Shared Team"


def test_oauth_add_rejects_duplicate_membership_when_email_changes(client, admin_headers, seed_account):
    from unittest.mock import patch

    seed_account(
        "existing",
        account_email="old@example.com",
        workspace_id="workspace-1",
        account_user_id="membership-1",
        chatgpt_user_id="user-1",
    )
    tokens = _oauth_tokens(
        email="new@example.com",
        workspace_id="workspace-1",
        account_user_id="membership-1",
        user_id="user-1",
    )

    with patch("app.routes.accounts._tokens_from_flow", return_value=tokens):
        response = client.post(
            "/api/v1/accounts/oauth/complete",
            headers=admin_headers,
            json={"label": "duplicate", "flow_token": "flow"},
        )

    assert response.status_code == 409


def test_reauthentication_cannot_turn_an_entry_into_a_duplicate(client, admin_headers, seed_account):
    from unittest.mock import patch

    seed_account(
        "first",
        account_email="first@example.com",
        workspace_id="workspace-1",
        account_user_id="membership-1",
        chatgpt_user_id="user-1",
    )
    second_id = seed_account(
        "second",
        account_email="second@example.com",
        workspace_id="workspace-2",
        account_user_id="membership-2",
        chatgpt_user_id="user-2",
    )
    tokens = _oauth_tokens(
        email="first@example.com",
        workspace_id="workspace-1",
        account_user_id="membership-1",
        user_id="user-1",
    )

    with patch("app.routes.accounts._tokens_from_flow", return_value=tokens):
        response = client.post(
            f"/api/v1/accounts/{second_id}/oauth/complete",
            headers=admin_headers,
            json={"flow_token": "flow"},
        )

    assert response.status_code == 409


def test_team_workspace_name_update_propagates_to_workspace(client, admin_headers, seed_account):
    first_id = seed_account(
        "first",
        account_email="first@example.com",
        workspace_id="workspace-1",
        workspace_name="Old Team",
        tier="team",
    )
    seed_account(
        "second",
        account_email="second@example.com",
        workspace_id="workspace-1",
        workspace_name="Old Team",
        tier="team",
    )

    response = client.put(
        f"/api/v1/accounts/{first_id}",
        headers=admin_headers,
        json={"workspace_name": "New Team"},
    )

    assert response.status_code == 200, response.text
    accounts = client.get("/api/v1/accounts", headers=admin_headers).json()["accounts"]
    assert {account["workspace_name"] for account in accounts} == {"New Team"}


@respx.mock
def test_me_usage_reports_own_usage_and_pool(client, admin_headers, seed_account, make_user):
    seed_account("a1", five_hour_used_pct=0.4, weekly_used_pct=0.65)
    key = make_user("erin")

    # Before any traffic: zeroed usage but the pool headroom is visible.
    me = client.get("/api/v1/me/usage", headers={"Authorization": f"Bearer {key}"})
    assert me.status_code == 200
    assert me.json()["user"] == "erin"
    assert me.json()["tokens_this_month"] == 0
    assert me.json()["pool"]["five_hour"]["used_pct"] == 0.4
    assert me.json()["pool"]["weekly"]["used_pct"] == 0.65
    assert me.json()["pool"]["five_hour"]["known_account_count"] == 1
    assert me.json()["pool"]["five_hour"]["unknown_account_count"] == 0

    respx.route(host="testserver").pass_through()
    respx.post(CODEX_RESPONSES).mock(return_value=httpx.Response(200, json={"model": "gpt-5.4", "usage": {"input_tokens": 8, "output_tokens": 4}}))
    client.post("/api/v1/responses", headers={"Authorization": f"Bearer {key}"}, json={"model": "gpt-5.4"})

    me_after = client.get("/api/v1/me/usage", headers={"Authorization": f"Bearer {key}"}).json()
    assert me_after["tokens_this_month"] == 12
    assert me_after["requests_this_month"] == 1


def test_me_usage_aggregates_available_pool(client, seed_account, make_user):
    from datetime import datetime, timedelta, timezone

    from app.utils.models.api import AccountStatus, ProviderHealth
    from app.utils.postgres import AccountDb
    from app.utils.postgres.base import SessionFactory

    first_id = seed_account("first", five_hour_used_pct=0.2, weekly_used_pct=0.3)
    second_id = seed_account("second", five_hour_used_pct=0.6, weekly_used_pct=0.5)
    seed_account("disabled", five_hour_used_pct=0.0, status=AccountStatus.DISABLED)
    seed_account("exhausted", five_hour_used_pct=0.95, rotation_threshold=0.9)
    reauth_id = seed_account("reauth", five_hour_used_pct=0.1)
    unknown_id = seed_account("unknown", five_hour_used_pct=0.0, weekly_used_pct=0.0)
    key = make_user("pool-user")

    now = datetime.now(timezone.utc)
    second_reset = now + timedelta(hours=2)
    with SessionFactory() as db:
        db.get(AccountDb, first_id).five_hour_reset_at = now + timedelta(hours=4)
        db.get(AccountDb, second_id).five_hour_reset_at = second_reset
        db.get(AccountDb, reauth_id).provider_health = ProviderHealth.REAUTH_REQUIRED
        db.get(AccountDb, unknown_id).five_hour_used_pct = None
        db.get(AccountDb, unknown_id).weekly_used_pct = None
        db.commit()

    pool = client.get("/api/v1/me/usage", headers={"Authorization": f"Bearer {key}"}).json()["pool"]
    assert pool["account_count"] == 3
    assert pool["five_hour"]["known_account_count"] == 2
    assert pool["five_hour"]["unknown_account_count"] == 1
    assert pool["weekly"]["known_account_count"] == 2
    assert pool["weekly"]["unknown_account_count"] == 1
    assert pool["five_hour"]["used_pct"] == 0.4
    assert pool["weekly"]["used_pct"] == 0.4
    reset_values = [datetime.fromisoformat(value.replace("Z", "+00:00")) for value in pool["five_hour"]["reset_at"]]
    assert reset_values == [second_reset, now + timedelta(hours=4)]
    assert datetime.fromisoformat(pool["five_hour"]["next_reset_at"].replace("Z", "+00:00")) == second_reset
    assert len(pool["accounts"]) == 3


def test_me_usage_requires_key(client):
    assert client.get("/api/v1/me/usage").status_code == 401


@respx.mock
def test_per_key_rate_limit(client, admin_headers, seed_account):
    seed_account("a1")
    user_id = client.post("/api/v1/users", headers=admin_headers, json={"name": "frank"}).json()["user"]["id"]
    key = client.post(f"/api/v1/users/{user_id}/keys", headers=admin_headers, json={"label": "k", "rate_limit_per_minute": 1}).json()["secret"]

    respx.route(host="testserver").pass_through()
    respx.post(CODEX_RESPONSES).mock(return_value=httpx.Response(200, json={"model": "gpt-5.4", "usage": {"input_tokens": 1, "output_tokens": 1}}))

    first = client.post("/api/v1/responses", headers={"Authorization": f"Bearer {key}"}, json={"model": "gpt-5.4"})
    assert first.status_code == 200
    second = client.post("/api/v1/responses", headers={"Authorization": f"Bearer {key}"}, json={"model": "gpt-5.4"})
    assert second.status_code == 429


@respx.mock
def test_per_key_monthly_budget(client, admin_headers, seed_account):
    seed_account("a1")
    user_id = client.post("/api/v1/users", headers=admin_headers, json={"name": "grace"}).json()["user"]["id"]
    key = client.post(f"/api/v1/users/{user_id}/keys", headers=admin_headers, json={"label": "k", "monthly_token_budget": 5}).json()["secret"]

    respx.route(host="testserver").pass_through()
    respx.post(CODEX_RESPONSES).mock(return_value=httpx.Response(200, json={"model": "gpt-5.4", "usage": {"input_tokens": 10, "output_tokens": 0}}))

    first = client.post("/api/v1/responses", headers={"Authorization": f"Bearer {key}"}, json={"model": "gpt-5.4"})
    assert first.status_code == 200  # consumes 10 tokens (over the budget of 5)
    second = client.post("/api/v1/responses", headers={"Authorization": f"Bearer {key}"}, json={"model": "gpt-5.4"})
    assert second.status_code == 403


def test_key_limits_crud(client, admin_headers):
    user_id = client.post("/api/v1/users", headers=admin_headers, json={"name": "heidi"}).json()["user"]["id"]
    created = client.post(
        f"/api/v1/users/{user_id}/keys",
        headers=admin_headers,
        json={"label": "k", "rate_limit_per_minute": 30, "monthly_token_budget": 1000},
    ).json()["api_key"]
    assert created["rate_limit_per_minute"] == 30
    assert created["monthly_token_budget"] == 1000

    client.put(
        f"/api/v1/users/{user_id}/keys/{created['id']}",
        headers=admin_headers,
        json={"rate_limit_per_minute": 0, "monthly_token_budget": 2000},
    )
    keys = client.get(f"/api/v1/users/{user_id}/keys", headers=admin_headers).json()["keys"]
    assert keys[0]["rate_limit_per_minute"] == 0
    assert keys[0]["monthly_token_budget"] == 2000


@respx.mock
def test_per_user_rate_limit_across_keys(client, admin_headers, seed_account):
    # The cap is per-user: it counts requests across ALL of the user's keys, not per key.
    seed_account("a1")
    user_id = client.post("/api/v1/users", headers=admin_headers, json={"name": "judy", "rate_limit_per_minute": 1}).json()["user"]["id"]
    key_a = client.post(f"/api/v1/users/{user_id}/keys", headers=admin_headers, json={"label": "a"}).json()["secret"]
    key_b = client.post(f"/api/v1/users/{user_id}/keys", headers=admin_headers, json={"label": "b"}).json()["secret"]

    respx.route(host="testserver").pass_through()
    respx.post(CODEX_RESPONSES).mock(return_value=httpx.Response(200, json={"model": "gpt-5.4", "usage": {"input_tokens": 1, "output_tokens": 1}}))

    first = client.post("/api/v1/responses", headers={"Authorization": f"Bearer {key_a}"}, json={"model": "gpt-5.4"})
    assert first.status_code == 200
    # Second request via a DIFFERENT key of the same user is still blocked by the per-user cap.
    second = client.post("/api/v1/responses", headers={"Authorization": f"Bearer {key_b}"}, json={"model": "gpt-5.4"})
    assert second.status_code == 429


@respx.mock
def test_per_user_monthly_budget_across_keys(client, admin_headers, seed_account):
    seed_account("a1")
    user_id = client.post("/api/v1/users", headers=admin_headers, json={"name": "mallory", "monthly_token_budget": 5}).json()["user"]["id"]
    key_a = client.post(f"/api/v1/users/{user_id}/keys", headers=admin_headers, json={"label": "a"}).json()["secret"]
    key_b = client.post(f"/api/v1/users/{user_id}/keys", headers=admin_headers, json={"label": "b"}).json()["secret"]

    respx.route(host="testserver").pass_through()
    respx.post(CODEX_RESPONSES).mock(return_value=httpx.Response(200, json={"model": "gpt-5.4", "usage": {"input_tokens": 10, "output_tokens": 0}}))

    first = client.post("/api/v1/responses", headers={"Authorization": f"Bearer {key_a}"}, json={"model": "gpt-5.4"})
    assert first.status_code == 200  # consumes 10 tokens (over the budget of 5)
    # A DIFFERENT key of the same user is now blocked because the user's monthly budget is exhausted.
    second = client.post("/api/v1/responses", headers={"Authorization": f"Bearer {key_b}"}, json={"model": "gpt-5.4"})
    assert second.status_code == 403


def test_user_limits_crud(client, admin_headers):
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"name": "niaj", "rate_limit_per_minute": 30, "monthly_token_budget": 1000},
    ).json()["user"]
    assert created["rate_limit_per_minute"] == 30
    assert created["monthly_token_budget"] == 1000

    user_id = created["id"]
    client.put(
        f"/api/v1/users/{user_id}",
        headers=admin_headers,
        json={"rate_limit_per_minute": 0, "monthly_token_budget": 2000},
    )
    user = client.get("/api/v1/users", headers=admin_headers).json()["users"][0]
    assert user["rate_limit_per_minute"] == 0
    assert user["monthly_token_budget"] == 2000


@respx.mock
def test_stats_endpoints_reflect_usage(client, admin_headers, seed_account, make_user):
    seed_account("a1")
    key = make_user("ivan")

    respx.route(host="testserver").pass_through()
    respx.post(CODEX_RESPONSES).mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "gpt-5.4",
                "usage": {
                    "input_tokens": 6,
                    "output_tokens": 3,
                    "input_tokens_details": {"cached_tokens": 3},
                },
            },
        )
    )
    client.post("/api/v1/responses", headers={"Authorization": f"Bearer {key}"}, json={"model": "gpt-5.4"})

    overview = client.get("/api/v1/stats/overview", headers=admin_headers).json()
    assert overview["total_accounts"] == 1
    assert overview["total_users"] == 1
    assert overview["total_keys"] == 1
    assert overview["requests"] == 1
    assert overview["tokens"] == 9
    assert overview["input_tokens"] == 6
    assert overview["output_tokens"] == 3
    assert overview["cached_input_tokens"] == 3
    assert overview["input_output_ratio"] == 2.0
    assert overview["cache_hit_rate"] == 0.5
    assert overview["input_rate_pct"] == 66.67
    assert overview["output_rate_pct"] == 33.33
    assert overview["cache_hit_rate_pct"] == 50.0
    assert "estimated_cost_usd" not in overview and "total_cost_usd" not in overview

    by_user = client.get("/api/v1/stats/by-user", headers=admin_headers).json()["users"]
    assert by_user[0]["user_name"] == "ivan"
    assert by_user[0]["tokens"] == 9

    act = client.get("/api/v1/stats/activity", headers=admin_headers).json()
    assert act["granularity"] == "day" and len(act["points"]) >= 1
    assert client.get("/api/v1/stats/hourly", headers=admin_headers).status_code == 200


@respx.mock
def test_model_mix_uses_routed_model_instead_of_provider_response_label(
    client,
    admin_headers,
    seed_account,
    make_user,
):
    from app.utils.postgres import UsageRecordDb
    from app.utils.postgres.base import SessionFactory

    seed_account("astra-account")
    key = make_user("astra-user")
    respx.route(host="testserver").pass_through()
    respx.post(CODEX_RESPONSES).mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "gpt-5.6-luna",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        )
    )

    proxied = client.post(
        "/api/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gpt-6-astra", "input": "hello"},
    )
    assert proxied.status_code == 200, proxied.text

    with SessionFactory() as db:
        usage_row = db.query(UsageRecordDb).one()
        # Mirror a streaming provider that reports an internal model alias in
        # its terminal usage payload while the proxy routed Astra.
        usage_row.model = "gpt-5.6-luna"
        db.commit()

    response = client.get("/api/v1/stats/model-mix", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["users"][0]["models"] == [
        {
            "model": "gpt-6-astra",
            "requests": 1,
            "input_tokens": 10,
            "output_tokens": 2,
        }
    ]


def test_overview_aggregates_active_pool_capacity(client, admin_headers, seed_account):
    from app.utils.models.api import AccountStatus, ProviderHealth
    from app.utils.postgres import AccountDb
    from app.utils.postgres.base import SessionFactory

    usable_ids = [
        seed_account("usable-1", five_hour_used_pct=0.1, weekly_used_pct=0.05),
        seed_account("usable-2", five_hour_used_pct=0.3, weekly_used_pct=0.15),
    ]
    disabled_id = seed_account(
        "disabled",
        status=AccountStatus.DISABLED,
        five_hour_used_pct=0.0,
        weekly_used_pct=0.0,
    )
    exhausted_id = seed_account(
        "exhausted",
        five_hour_used_pct=0.95,
        weekly_used_pct=0.10,
        rotation_threshold=0.9,
    )
    cooldown_id = seed_account(
        "cooldown",
        status=AccountStatus.COOLDOWN,
        five_hour_used_pct=0.5,
        weekly_used_pct=0.2,
        rotation_threshold=0.4,
    )
    reauth_id = seed_account("reauth-required", five_hour_used_pct=0.2, weekly_used_pct=0.25)

    with SessionFactory() as db:
        db.query(AccountDb).filter(AccountDb.id.in_([*usable_ids, disabled_id, exhausted_id, cooldown_id])).update(
            {AccountDb.provider_health: ProviderHealth.HEALTHY}
        )
        db.query(AccountDb).filter(AccountDb.id == reauth_id).update({AccountDb.provider_health: ProviderHealth.REAUTH_REQUIRED})
        db.commit()

    overview = client.get("/api/v1/stats/overview", headers=admin_headers).json()
    assert overview["total_accounts"] == 6
    assert overview["active_accounts"] == 4
    assert overview["usable_accounts"] == 2
    assert overview["five_hour_average_pct"] == 46.25
    assert overview["weekly_average_pct"] == 12.5
    assert overview["pool_used_pct"] == 0.4625
    assert overview["pool_remaining_pct"] == 0.5375


def test_user_scoped_stats_hide_unselected_users(client, admin_headers, make_user):
    make_user("alice")
    make_user("bob")
    users = client.get("/api/v1/users", headers=admin_headers).json()["users"]
    alice_id = next(user["id"] for user in users if user["name"] == "alice")

    for path in ("/api/v1/stats/by-user", "/api/v1/stats/model-mix"):
        response = client.get(path, headers=admin_headers, params={"user_id": alice_id})
        assert response.status_code == 200
        assert [str(user["user_id"]) for user in response.json()["users"]] == [alice_id]


@respx.mock
def test_stats_range_filter(client, admin_headers, seed_account, make_user):
    """start/end clamp the usage figures; a short window switches the activity series to hourly buckets."""
    from datetime import datetime, timedelta, timezone

    seed_account("a1")
    key = make_user("ivan")

    respx.route(host="testserver").pass_through()
    respx.post(CODEX_RESPONSES).mock(return_value=httpx.Response(200, json={"model": "gpt-5.4", "usage": {"input_tokens": 6, "output_tokens": 3}}))
    client.post("/api/v1/responses", headers={"Authorization": f"Bearer {key}"}, json={"model": "gpt-5.4"})

    now = datetime.now(timezone.utc)
    future = (now + timedelta(hours=1)).isoformat()
    past = (now - timedelta(hours=2)).isoformat()
    recent = (now - timedelta(minutes=30)).isoformat()

    def get(path, **params):
        return client.get(path, headers=admin_headers, params=params).json()

    # A window entirely in the future excludes the just-recorded request.
    empty = get("/api/v1/stats/overview", start=future)
    assert empty["requests"] == 0 and empty["tokens"] == 0
    assert empty["input_output_ratio"] is None and empty["cache_hit_rate"] == 0.0
    assert empty["input_rate_pct"] == 0.0 and empty["output_rate_pct"] == 0.0 and empty["cache_hit_rate_pct"] == 0.0
    # ...but the inventory counts are not range-filtered.
    assert empty["total_accounts"] == 1 and empty["total_keys"] == 1

    # A window covering now includes it.
    covered = get("/api/v1/stats/overview", start=past)
    assert covered["requests"] == 1 and covered["tokens"] == 9

    # The usage log and by-user respect the range too.
    assert get("/api/v1/stats/usage", start=future)["total"] == 0
    assert get("/api/v1/stats/usage", start=past)["total"] == 1
    busy = get("/api/v1/stats/by-user", start=future)["users"]
    assert busy[0]["user_name"] == "ivan" and busy[0]["tokens"] == 0  # user still listed, zeroed in window

    # A short window (<= 2 days) buckets the activity series by hour.
    short = get("/api/v1/stats/activity", start=recent, end=future)
    assert short["granularity"] == "hour"

    # An explicit end without a start means all time and includes the earliest request.
    all_time = get("/api/v1/stats/activity", end=future)
    assert sum(point["requests"] for point in all_time["points"]) == 1
