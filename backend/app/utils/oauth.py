# Path: app/utils/oauth.py
# Description: Codex device login, ChatGPT token refresh, quota probes, and banked limit resets.

from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional

import httpx

from app import config
from app.utils import egress

USAGE_PROBE_ATTEMPTS = 4
_USAGE_PROBE_BACKOFF = (0.5, 1.0, 2.0)
_USAGE_PROBE_MAX_SLEEP = 3.0
_RETRYABLE_PROBE_STATUSES = frozenset({429, 500, 502, 503, 504})


class DeviceAuthorizationPending(Exception):
    """The user has not approved the device code yet."""


def _json_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "User-Agent": config.CODEX_USER_AGENT,
    }


def _account_headers(access_token: str, account_id: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "ChatGPT-Account-Id": account_id,
        "User-Agent": config.CODEX_USER_AGENT,
    }


def _decode_jwt_payload(token: str) -> Dict[str, Any]:
    """Decode unverified OAuth claims used only for display/routing metadata."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode())
    except (IndexError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("OAuth server returned an invalid JWT") from exc


def _expiry_from_token(access_token: str, expires_in: Optional[int] = None) -> datetime:
    try:
        raw_exp = _decode_jwt_payload(access_token).get("exp")
        if isinstance(raw_exp, (int, float)):
            return datetime.fromtimestamp(float(raw_exp), tz=timezone.utc)
    except ValueError:
        pass
    seconds = expires_in if isinstance(expires_in, int) and expires_in > 0 else 3600
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _first_nonempty_string(source: Mapping[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_identity(id_token: str, access_token: Optional[str] = None) -> Dict[str, Any]:
    """Extract stable user/workspace identity and display metadata from OAuth claims."""
    claims = _decode_jwt_payload(id_token)
    profile = claims.get("https://api.openai.com/profile")
    id_auth = claims.get("https://api.openai.com/auth")
    profile = profile if isinstance(profile, Mapping) else {}
    id_auth = id_auth if isinstance(id_auth, Mapping) else {}

    # Access and ID tokens can carry different subsets of the same identity.
    # Prefer ID-token values, but always merge both instead of consulting the
    # access token only when the workspace id is absent.
    access_auth: Mapping[str, Any] = {}
    if access_token:
        access_claims = _decode_jwt_payload(access_token)
        raw_access_auth = access_claims.get("https://api.openai.com/auth")
        if isinstance(raw_access_auth, Mapping):
            access_auth = raw_access_auth
    auth = {**access_auth, **id_auth}

    account_id = auth.get("chatgpt_account_id")
    if not isinstance(account_id, str) or not account_id:
        raise ValueError("OAuth token does not contain a ChatGPT account id")

    return {
        "account_id": account_id,
        "account_user_id": _first_nonempty_string(auth, "chatgpt_account_user_id"),
        "user_id": _first_nonempty_string(auth, "chatgpt_user_id", "user_id"),
        "email": _first_nonempty_string(claims, "email") or _first_nonempty_string(profile, "email"),
        "tier": _first_nonempty_string(auth, "chatgpt_plan_type"),
        # Workspace display names are not part of the documented Codex account
        # response today. Capture known claim spellings opportunistically so a
        # future/provider-specific claim can populate the dashboard safely.
        "workspace_name": _first_nonempty_string(
            auth,
            "chatgpt_workspace_name",
            "chatgpt_account_name",
            "chatgpt_organization_name",
            "workspace_name",
            "organization_name",
        ),
        "is_fedramp": bool(auth.get("chatgpt_account_is_fedramp", False)),
    }


def request_device_code() -> Dict[str, Any]:
    """Start the official Codex device authorization flow."""
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            config.OAUTH_DEVICE_CODE_URL,
            json={"client_id": config.OAUTH_CLIENT_ID},
            headers=_json_headers(),
        )
        response.raise_for_status()
        data = response.json()

    interval_raw = data.get("interval", 5)
    try:
        interval = max(1, int(interval_raw))
    except (TypeError, ValueError):
        interval = 5

    device_auth_id = data.get("device_auth_id")
    user_code = data.get("user_code") or data.get("usercode")
    if not isinstance(device_auth_id, str) or not isinstance(user_code, str):
        raise ValueError("Device authorization response is missing required fields")

    return {
        "verification_url": config.OAUTH_DEVICE_VERIFICATION_URL,
        "user_code": user_code,
        "device_auth_id": device_auth_id,
        "interval": interval,
    }


def poll_device_code(
    device_auth_id: str,
    user_code: str,
    *,
    egress_target: egress.EgressTarget | None = None,
) -> Dict[str, str]:
    """Poll once for device approval; raise DeviceAuthorizationPending while waiting."""
    with egress.sync_client(egress_target, timeout=30.0) if egress_target else httpx.Client(timeout=30.0) as client:
        response = client.post(
            config.OAUTH_DEVICE_POLL_URL,
            json={"device_auth_id": device_auth_id, "user_code": user_code},
            headers=_json_headers(),
        )

    if response.status_code in {403, 404}:
        raise DeviceAuthorizationPending
    response.raise_for_status()
    data = response.json()
    required = ("authorization_code", "code_verifier")
    if any(not isinstance(data.get(key), str) or not data[key] for key in required):
        raise ValueError("Device authorization completion is missing required fields")
    return data


def exchange_device_code(
    device_auth_id: str,
    user_code: str,
    *,
    egress_target: egress.EgressTarget | None = None,
) -> Dict[str, Any]:
    """Complete an approved device flow and return normalized Codex OAuth tokens."""
    approved = poll_device_code(device_auth_id, user_code, egress_target=egress_target)
    body = {
        "grant_type": "authorization_code",
        "code": approved["authorization_code"],
        "redirect_uri": config.OAUTH_DEVICE_REDIRECT_URI,
        "client_id": config.OAUTH_CLIENT_ID,
        "code_verifier": approved["code_verifier"],
    }
    with egress.sync_client(egress_target, timeout=30.0) if egress_target else httpx.Client(timeout=30.0) as client:
        response = client.post(
            config.OAUTH_TOKEN_URL,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": config.CODEX_USER_AGENT,
            },
        )
        response.raise_for_status()
        data = response.json()
    return _normalize_tokens(data)


def _normalize_tokens(data: Mapping[str, Any], old_refresh_token: str = "") -> Dict[str, Any]:
    access_token = data.get("access_token")
    id_token = data.get("id_token")
    refresh_token = data.get("refresh_token") or old_refresh_token
    if not isinstance(access_token, str) or not isinstance(id_token, str):
        raise ValueError("OAuth token response is missing access_token or id_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise ValueError("OAuth token response is missing refresh_token")
    identity = extract_identity(id_token, access_token)
    return {
        "access_token": access_token,
        "id_token": id_token,
        "refresh_token": refresh_token,
        "expires_at": _expiry_from_token(access_token, data.get("expires_in")),
        **identity,
    }


def refresh_access_token(refresh_token: str, *, egress_target: egress.EgressTarget | None = None) -> Dict[str, Any]:
    """Refresh a ChatGPT OAuth token, preserving a non-rotated refresh token."""
    with egress.sync_client(egress_target, timeout=30.0) if egress_target else httpx.Client(timeout=30.0) as client:
        response = client.post(
            config.OAUTH_TOKEN_URL,
            json={
                "client_id": config.OAUTH_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers=_json_headers(),
        )
        response.raise_for_status()
        data = response.json()

    # Some refresh responses omit id_token. In that case the caller retains
    # the account metadata already stored in the database.
    access_token = data.get("access_token")
    if not isinstance(access_token, str):
        raise ValueError("OAuth refresh response is missing access_token")
    normalized: Dict[str, Any] = {
        "access_token": access_token,
        "refresh_token": data.get("refresh_token") or refresh_token,
        "expires_at": _expiry_from_token(access_token, data.get("expires_in")),
    }
    id_token = data.get("id_token")
    if isinstance(id_token, str):
        normalized["id_token"] = id_token
        normalized.update(extract_identity(id_token, access_token))
    return normalized


def _normalize_window(window: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(window, Mapping):
        return None
    raw_percent = window.get("used_percent")
    utilization = None
    if isinstance(raw_percent, (int, float)):
        utilization = max(0.0, min(1.0, float(raw_percent) / 100.0))
    raw_reset = window.get("reset_at")
    reset_at = None
    if isinstance(raw_reset, (int, float)):
        reset_at = datetime.fromtimestamp(float(raw_reset), tz=timezone.utc)
    raw_window_seconds = window.get("limit_window_seconds")
    window_seconds = float(raw_window_seconds) if isinstance(raw_window_seconds, (int, float)) else None
    raw_reset_after = window.get("reset_after_seconds")
    reset_after_seconds = float(raw_reset_after) if isinstance(raw_reset_after, (int, float)) else None
    return {
        "utilization": utilization,
        "reset_at": reset_at,
        "window_seconds": window_seconds,
        "reset_after_seconds": reset_after_seconds,
        # ChatGPT reports an untouched five-hour window as 0% used with a
        # rolling reset exactly one full window from every probe. That is a
        # cold-window placeholder, not an active reset countdown.
        "is_cold": (utilization == 0.0 and window_seconds is not None and reset_after_seconds is not None and reset_after_seconds >= window_seconds),
    }


FIVE_HOUR_WINDOW_SECONDS = 5 * 60 * 60
WEEKLY_WINDOW_SECONDS = 7 * 24 * 60 * 60
MONTHLY_WINDOW_SECONDS = 30 * 24 * 60 * 60


def _classify_rate_limit_windows(rate_limit: Mapping[str, Any]) -> Dict[str, Optional[Dict[str, Any]]]:
    """Map provider primary/secondary windows to their actual durations.

    The live endpoint currently returns a five-hour primary window and a
    seven-day secondary window. Duration-based classification protects the
    proxy if OpenAI changes their ordering; primary/secondary are used only as
    fallbacks for older payloads that omit ``limit_window_seconds``.
    """
    candidates = {
        "primary": _normalize_window(rate_limit.get("primary_window")),
        "secondary": _normalize_window(rate_limit.get("secondary_window")),
    }
    classified: Dict[str, Optional[Dict[str, Any]]] = {
        "five_hour": None,
        "weekly": None,
        "monthly": None,
    }
    assigned_sources: set[str] = set()
    for source, window in candidates.items():
        if window is None:
            continue
        seconds = window.get("window_seconds")
        if seconds == FIVE_HOUR_WINDOW_SECONDS:
            classified["five_hour"] = window
            assigned_sources.add(source)
        elif seconds == WEEKLY_WINDOW_SECONDS:
            classified["weekly"] = window
            assigned_sources.add(source)
        elif seconds == MONTHLY_WINDOW_SECONDS:
            classified["monthly"] = window
            assigned_sources.add(source)

    # An explicit, unknown duration must remain unknown; only payloads that
    # omit duration entirely use primary/secondary compatibility fallbacks.
    if (
        classified["five_hour"] is None
        and candidates["primary"] is not None
        and "primary" not in assigned_sources
        and candidates["primary"].get("window_seconds") is None
    ):
        classified["five_hour"] = candidates["primary"]
        assigned_sources.add("primary")
    if (
        classified["weekly"] is None
        and candidates["secondary"] is not None
        and "secondary" not in assigned_sources
        and candidates["secondary"].get("window_seconds") is None
    ):
        classified["weekly"] = candidates["secondary"]
    return classified


def _probe_retry_delay(response: httpx.Response, prior_attempts: int) -> float:
    delay = _USAGE_PROBE_BACKOFF[min(prior_attempts, len(_USAGE_PROBE_BACKOFF) - 1)]
    raw = response.headers.get("retry-after")
    if raw:
        try:
            delay = max(delay, float(raw))
        except ValueError:
            pass
    return min(delay, _USAGE_PROBE_MAX_SLEEP)


def fetch_usage(
    access_token: str,
    account_id: str,
    *,
    egress_target: egress.EgressTarget | None = None,
) -> Dict[str, Any]:
    """Fetch normalized five-hour/weekly Codex limits and reset-credit count."""
    with egress.sync_client(egress_target, timeout=15.0) if egress_target else httpx.Client(timeout=15.0) as client:
        for attempt in range(USAGE_PROBE_ATTEMPTS):
            response = client.get(
                config.OAUTH_USAGE_URL,
                headers=_account_headers(access_token, account_id),
            )
            if response.status_code in _RETRYABLE_PROBE_STATUSES and attempt + 1 < USAGE_PROBE_ATTEMPTS:
                time.sleep(_probe_retry_delay(response, attempt))
                continue
            response.raise_for_status()
            data = response.json()
            rate_limit = data.get("rate_limit")
            rate_limit = rate_limit if isinstance(rate_limit, Mapping) else {}
            resets = data.get("rate_limit_reset_credits")
            resets = resets if isinstance(resets, Mapping) else {}
            windows = _classify_rate_limit_windows(rate_limit)
            return {
                **windows,
                "tier": data.get("plan_type"),
                "reset_credits_available": resets.get("available_count"),
                "limit_reached": bool(rate_limit.get("limit_reached", False)),
            }
    raise RuntimeError("unreachable")


def fetch_model_catalog(
    access_token: str,
    account_id: str,
    *,
    client_version: str,
    is_fedramp: bool = False,
    egress_target: egress.EgressTarget | None = None,
) -> Dict[str, Any]:
    """Fetch and validate the native Codex model catalog for one account."""
    headers = _account_headers(access_token, account_id)
    if is_fedramp:
        headers["X-OpenAI-Fedramp"] = "true"
    with egress.sync_client(egress_target, timeout=30.0) if egress_target else httpx.Client(timeout=30.0) as client:
        response = client.get(
            f"{config.UPSTREAM_CODEX_BASE_URL}/models",
            params={"client_version": client_version},
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        raise ValueError("Codex model catalog response does not contain a model list")
    return data


def list_reset_credits(
    access_token: str,
    account_id: str,
    *,
    egress_target: egress.EgressTarget | None = None,
) -> Dict[str, Any]:
    with egress.sync_client(egress_target, timeout=15.0) if egress_target else httpx.Client(timeout=15.0) as client:
        response = client.get(
            config.OAUTH_RESET_CREDITS_URL,
            headers=_account_headers(access_token, account_id),
        )
        response.raise_for_status()
        data = response.json()
    credits = data.get("credits")
    return {
        "available_count": int(data.get("available_count") or 0),
        "credits": credits if isinstance(credits, list) else [],
    }


def consume_reset_credit(
    access_token: str,
    account_id: str,
    *,
    credit_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    egress_target: egress.EgressTarget | None = None,
) -> Dict[str, Any]:
    """Redeem one banked reset credit using an idempotent upstream request."""
    body: Dict[str, str] = {
        "redeem_request_id": idempotency_key or str(uuid.uuid4()),
    }
    if credit_id:
        body["credit_id"] = credit_id
    with egress.sync_client(egress_target, timeout=15.0) if egress_target else httpx.Client(timeout=15.0) as client:
        response = client.post(
            config.OAUTH_RESET_CONSUME_URL,
            json=body,
            headers=_account_headers(access_token, account_id),
        )
        response.raise_for_status()
        data = response.json()
    code = data.get("code")
    if code not in {"reset", "nothing_to_reset", "no_credit", "already_redeemed"}:
        raise ValueError("Reset service returned an unknown outcome")
    return {
        "code": code,
        "windows_reset": int(data.get("windows_reset") or 0),
        "idempotency_key": body["redeem_request_id"],
    }
