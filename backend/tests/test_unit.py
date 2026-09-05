# Path: tests/test_unit.py
# Description: Codex device auth, quota/reset normalization, and Responses API usage parsing.

import base64
import json

import httpx
import pytest
import respx

from app import pricing
from app.utils import oauth
from app.utils.request_policy import request_mode_from_request
from app.utils.usage import (
    StreamUsageAccumulator,
    reasoning_level_from_request,
    usage_from_json,
)


def _jwt(payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{encoded}.signature"


@respx.mock
def test_request_device_code_uses_official_codex_flow():
    route = respx.post(oauth.config.OAUTH_DEVICE_CODE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"device_auth_id": "device-1", "user_code": "ABCD-EFGH", "interval": "7"},
        )
    )
    result = oauth.request_device_code()
    assert route.called
    assert json.loads(route.calls[0].request.content) == {"client_id": oauth.config.OAUTH_CLIENT_ID}
    assert result["verification_url"] == "https://auth.openai.com/codex/device"
    assert result["user_code"] == "ABCD-EFGH"
    assert result["interval"] == 7


@respx.mock
def test_device_poll_pending_is_distinct():
    respx.post(oauth.config.OAUTH_DEVICE_POLL_URL).mock(return_value=httpx.Response(403))
    with pytest.raises(oauth.DeviceAuthorizationPending):
        oauth.poll_device_code("device", "code")


def test_extract_identity_reads_chatgpt_claims():
    token = _jwt(
        {
            "email": "person@example.com",
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct-123",
                "chatgpt_account_user_id": "membership-123",
                "chatgpt_user_id": "user-123",
                "chatgpt_plan_type": "pro",
                "chatgpt_workspace_name": "Example Team",
                "chatgpt_account_is_fedramp": False,
            },
        }
    )
    identity = oauth.extract_identity(token)
    assert identity == {
        "account_id": "acct-123",
        "account_user_id": "membership-123",
        "user_id": "user-123",
        "email": "person@example.com",
        "tier": "pro",
        "workspace_name": "Example Team",
        "is_fedramp": False,
    }


def test_extract_identity_merges_access_token_membership_claims():
    id_token = _jwt(
        {
            "email": "person@example.com",
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "workspace-1",
                "chatgpt_plan_type": "team",
            },
        }
    )
    access_token = _jwt(
        {
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "workspace-1",
                "chatgpt_account_user_id": "membership-1",
                "chatgpt_user_id": "user-1",
            },
        }
    )

    identity = oauth.extract_identity(id_token, access_token)

    assert identity["account_user_id"] == "membership-1"
    assert identity["user_id"] == "user-1"
    assert identity["tier"] == "team"


@respx.mock
def test_fetch_usage_retries_and_normalizes_windows(monkeypatch):
    monkeypatch.setattr(oauth.time, "sleep", lambda _seconds: None)
    route = respx.get(oauth.config.OAUTH_USAGE_URL).mock(
        side_effect=[
            httpx.Response(429, json={}),
            httpx.Response(
                200,
                json={
                    "plan_type": "plus",
                    "rate_limit": {
                        "primary_window": {
                            "used_percent": 7,
                            "reset_at": 1_900_000_000,
                            "reset_after_seconds": 17_400,
                            "limit_window_seconds": 18_000,
                        },
                        "secondary_window": {
                            "used_percent": 51,
                            "reset_at": 1_900_100_000,
                            "limit_window_seconds": 604_800,
                        },
                    },
                    "rate_limit_reset_credits": {"available_count": 2},
                },
            ),
        ]
    )
    result = oauth.fetch_usage("access-token", "account-id")
    assert route.call_count == 2
    assert route.calls[-1].request.headers["chatgpt-account-id"] == "account-id"
    assert result["five_hour"]["utilization"] == pytest.approx(0.07)
    assert result["five_hour"]["window_seconds"] == 18_000
    assert result["five_hour"]["reset_after_seconds"] == 17_400
    assert result["five_hour"]["is_cold"] is False
    assert result["weekly"]["utilization"] == pytest.approx(0.51)
    assert result["weekly"]["window_seconds"] == 604_800
    assert result["tier"] == "plus"
    assert result["reset_credits_available"] == 2


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, 0.0), (1, 0.01), (24, 0.24), (100, 1.0), (150, 1.0)],
)
def test_normalize_window_treats_used_percent_as_percentage(raw, expected):
    result = oauth._normalize_window({"used_percent": raw})
    assert result["utilization"] == pytest.approx(expected)


def test_normalize_window_identifies_provider_cold_placeholder():
    result = oauth._normalize_window(
        {
            "used_percent": 0,
            "reset_at": 1_900_000_000,
            "reset_after_seconds": 18_000,
            "limit_window_seconds": 18_000,
        }
    )

    assert result["is_cold"] is True


def test_rate_limit_window_classification_uses_duration_before_provider_order():
    result = oauth._classify_rate_limit_windows(
        {
            "primary_window": {"used_percent": 60, "limit_window_seconds": 604_800},
            "secondary_window": {"used_percent": 20, "limit_window_seconds": 18_000},
        }
    )

    assert result["five_hour"]["utilization"] == pytest.approx(0.20)
    assert result["weekly"]["utilization"] == pytest.approx(0.60)


def test_rate_limit_window_classification_preserves_explicit_monthly_window():
    result = oauth._classify_rate_limit_windows({"primary_window": {"used_percent": 0, "limit_window_seconds": 2_592_000}})
    assert result["monthly"]["utilization"] == pytest.approx(0.0)
    assert result["five_hour"] is None
    assert result["weekly"] is None


@respx.mock
def test_consume_reset_credit_is_idempotent():
    route = respx.post(oauth.config.OAUTH_RESET_CONSUME_URL).mock(return_value=httpx.Response(200, json={"code": "reset", "windows_reset": 2}))
    result = oauth.consume_reset_credit(
        "access",
        "account",
        credit_id="credit-1",
        idempotency_key="request-1",
    )
    assert result == {
        "code": "reset",
        "windows_reset": 2,
        "idempotency_key": "request-1",
    }
    assert json.loads(route.calls[0].request.content) == {
        "redeem_request_id": "request-1",
        "credit_id": "credit-1",
    }


def test_stream_accumulator_extracts_responses_usage():
    accumulator = StreamUsageAccumulator(
        fallback_model="gpt-5.4",
        reasoning_level="high",
    )
    accumulator.feed(
        b'data: {"type":"response.completed","response":{"model":"gpt-5.4","usage":'
        b'{"input_tokens":20,"output_tokens":42,"input_tokens_details":'
        b'{"cached_tokens":3,"cache_write_tokens":4}}}}\n\n'
    )
    result = accumulator.result()
    assert result.model == "gpt-5.4"
    assert result.input_tokens == 20
    assert result.cached_input_tokens == 3
    assert result.cache_write_tokens == 4
    assert result.output_tokens == 42
    assert result.reasoning_level == "high"
    assert accumulator.terminal_response == {
        "model": "gpt-5.4",
        "usage": {
            "input_tokens": 20,
            "output_tokens": 42,
            "input_tokens_details": {
                "cached_tokens": 3,
                "cache_write_tokens": 4,
            },
        },
    }


def test_stream_accumulator_accepts_final_event_without_newline():
    accumulator = StreamUsageAccumulator(fallback_model="gpt-5.4")
    accumulator.feed(b'data: {"type":"response.completed","response":{"usage":{"input_tokens":12,"output_tokens":3}}}')
    result = accumulator.result()
    assert result.input_tokens == 12
    assert result.output_tokens == 3


def test_stream_accumulator_reconstructs_terminal_output_from_done_items():
    accumulator = StreamUsageAccumulator(fallback_model="gpt-5.4")
    accumulator.feed(
        b'data: {"type":"response.output_item.done","output_index":0,"item":'
        b'{"id":"msg_1","type":"message","content":[{"type":"output_text","text":"OK"}]}}\n'
        b'data: {"type":"response.completed","response":{"id":"resp_1","object":"response",'
        b'"status":"completed","output":[],"usage":{"input_tokens":2,"output_tokens":1}}}\n'
    )
    accumulator.result()
    assert accumulator.terminal_response is not None
    assert accumulator.terminal_response["output"][0]["id"] == "msg_1"
    assert accumulator.terminal_response["output"][0]["content"][0]["text"] == "OK"


def test_stream_accumulator_does_not_erase_usage_with_later_partial_event():
    accumulator = StreamUsageAccumulator(fallback_model="gpt-5.4")
    accumulator.feed(
        b'data: {"type":"response.completed","response":{"usage":'
        b'{"input_tokens":12,"output_tokens":3,"input_tokens_details":{"cached_tokens":2}}}}\n'
        b'data: {"type":"response.done","usage":{}}\n'
    )
    result = accumulator.result()
    assert result.input_tokens == 12
    assert result.output_tokens == 3
    assert result.cached_input_tokens == 2


def test_stream_accumulator_accepts_codex_token_count_event():
    accumulator = StreamUsageAccumulator(fallback_model="gpt-5.6")
    accumulator.feed(
        b'data: {"type":"token_count","info":{"last_token_usage":'
        b'{"input_tokens":12598,"cached_input_tokens":3840,"cache_write_tokens":512,"output_tokens":7}}}\n'
    )
    result = accumulator.result()
    assert result.input_tokens == 12598
    assert result.cached_input_tokens == 3840
    assert result.cache_write_tokens == 512
    assert result.output_tokens == 7


def test_stream_accumulator_accepts_codex_cache_write_input_tokens_name():
    accumulator = StreamUsageAccumulator(fallback_model="gpt-5.6")
    accumulator.feed(
        b'data: {"type":"token_count","info":{"last_token_usage":'
        b'{"input_tokens":100,"cached_input_tokens":40,"cache_write_input_tokens":60,"output_tokens":7}}}\n'
    )
    result = accumulator.result()
    assert result.input_tokens == 100
    assert result.cached_input_tokens == 40
    assert result.cache_write_tokens == 60
    assert result.output_tokens == 7


def test_reasoning_level_from_codex_request():
    assert reasoning_level_from_request({"reasoning": {"effort": "HIGH"}}) == "high"
    assert reasoning_level_from_request({"reasoning_effort": "medium"}) == "medium"
    assert reasoning_level_from_request({"model": "gpt-5.6"}) is None


def test_request_mode_from_codex_request():
    assert request_mode_from_request({"service_tier": "fast"}) == "fast"
    assert request_mode_from_request({"service_tier": "PRIORITY"}) == "fast"
    assert request_mode_from_request({"service_tier": "ULTRAFAST"}) == "ultrafast"
    assert request_mode_from_request({"service_tier": "default"}) == "standard"
    assert request_mode_from_request({}) == "standard"


def test_usage_from_nonstreaming_response():
    result = usage_from_json(
        {
            "model": "gpt-5.4",
            "usage": {
                "input_tokens": 7,
                "output_tokens": 9,
                "input_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 1},
            },
        }
    )
    assert result.model == "gpt-5.4"
    assert result.input_tokens == 7
    assert result.output_tokens == 9
    assert result.cached_input_tokens == 2
    assert result.cache_write_tokens == 1


def test_usage_from_json_event_list_and_direct_token_block():
    result = usage_from_json(
        [
            {"type": "response.created", "response": {"id": "r1"}},
            {
                "type": "response.completed",
                "response": {
                    "model": "gpt-5.6-sol",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 4,
                        "input_tokens_details": {"cached_tokens": 80},
                    },
                },
            },
        ]
    )
    assert result.model == "gpt-5.6-sol"
    assert result.input_tokens == 100
    assert result.cached_input_tokens == 80
    assert result.output_tokens == 4

    direct = usage_from_json(
        {
            "last_token_usage": {
                "input_tokens": 11,
                "cached_input_tokens": 7,
                "output_tokens": 2,
            }
        }
    )
    assert direct.input_tokens == 11
    assert direct.cached_input_tokens == 7
    assert direct.output_tokens == 2


def test_pricing_separates_fresh_cache_read_and_cache_write_input():
    value = pricing.cost_usd(
        "gpt-5.6-sol",
        input_tokens=100,
        output_tokens=10,
        cached_input_tokens=20,
        cache_write_tokens=30,
    )
    expected = (50 * 5.0 + 20 * 0.5 + 30 * 6.25 + 10 * 30.0) / 1_000_000
    assert value == pytest.approx(expected)


def test_pricing_supports_gpt_6_astra_standard_short_context_rates():
    value = pricing.cost_usd(
        "gpt-6-astra",
        input_tokens=100,
        output_tokens=10,
        cached_input_tokens=20,
        cache_write_tokens=30,
    )
    expected = (50 * 10.0 + 20 * 1.0 + 30 * 12.5 + 10 * 50.0) / 1_000_000
    assert value == pytest.approx(expected)
