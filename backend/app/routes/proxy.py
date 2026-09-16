# Path: app/routes/proxy.py
# Description: Authenticated Responses API relay with priority-ordered, quota-aware account failover.

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Mapping, Optional, Set, Tuple
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app import config
from app.logger import get_logger
from app.routes.me import build_pool_status
from app.utils import (
    account_limiter,
    chat_completions,
    egress,
    events,
    notifications,
    openai_fallbacks,
    provider_health,
    request_context,
    request_policy,
    rotation,
    security,
    usage,
    warmup,
)
from app.utils.models.api import AccountStatus
from app.utils.postgres import AccountDb, ApiKeyDb, OpenAIFallbackDb, UsageRecordDb, UserDb, get_db, get_db_cm

logger = get_logger()
settings = config.get_settings()
router = APIRouter(tags=["Proxy"])

_STRIP_REQUEST_HEADERS = {
    "host",
    "content-length",
    "authorization",
    "x-api-key",
    "chatgpt-account-id",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "proxy-authorization",
    "proxy-authenticate",
    "accept-encoding",
}
_STRIP_RESPONSE_HEADERS = {
    "content-length",
    "content-encoding",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "set-cookie",
}

# Provider quota headers describe the one account that was attempted.  They
# must not leak that account's private window into a pooled response; they are
# replaced below with the same aggregate values exposed by GET /me/usage.
_POOL_QUOTA_RESPONSE_HEADERS = {
    "x-codex-primary-used-percent",
    "x-codex-primary-reset-at",
    "x-codex-secondary-used-percent",
    "x-codex-secondary-reset-at",
    "x-codex-rate-limit-reached-type",
    "x-codex-rate-limit-reset-credits-available",
}


def _pool_quota_response_headers(db: Session, headers: Mapping[str, str]) -> Dict[str, str]:
    """Rewrite provider quota headers to match the shared-pool /me/usage snapshot."""
    response_headers = {key: value for key, value in headers.items() if key.lower() not in _POOL_QUOTA_RESPONSE_HEADERS}
    pool = build_pool_status(db)
    if pool is None:
        return response_headers

    five_hour = pool.five_hour
    if five_hour.used_pct is not None:
        response_headers["x-codex-primary-used-percent"] = f"{five_hour.used_pct * 100:.6f}".rstrip("0").rstrip(".")
    if five_hour.next_reset_at is not None:
        response_headers["x-codex-primary-reset-at"] = str(int(five_hour.next_reset_at.timestamp()))

    weekly = pool.weekly
    if weekly.used_pct is not None:
        response_headers["x-codex-secondary-used-percent"] = f"{weekly.used_pct * 100:.6f}".rstrip("0").rstrip(".")
    if weekly.next_reset_at is not None:
        response_headers["x-codex-secondary-reset-at"] = str(int(weekly.next_reset_at.timestamp()))
    return response_headers


ResponseOperation = Literal["create", "compact"]
_RESPONSE_OPERATIONS: Dict[str, ResponseOperation] = {
    "/responses": "create",
    "/responses/compact": "compact",
}


def _recover_exhausted_accounts_with_cached_credits(db: Session) -> None:
    """Restore exhausted accounts before availability filtering hides them."""
    for account in rotation.weekly_reset_recovery_candidates(db):
        try:
            target = egress.get_pool().resolve(account.egress_target_id)
            access_token = rotation.ensure_fresh_token(db, account, egress_target=target)
            if rotation.auto_redeem_weekly_reset(db, account, access_token, egress_target=target):
                logger.info("Automatically redeemed a weekly limit reset for pooled account %s", account.label)
        except Exception:  # noqa: BLE001
            # Release a possible row lock and leave the remaining accounts
            # recoverable in this request even if one provider call fails.
            db.rollback()
            logger.exception("Automatic weekly limit reset recovery failed for pooled account %s", account.label)


def _upstream_headers(
    incoming: Mapping[str, str],
    access_token: str,
    account,
) -> Dict[str, str]:
    headers = {key: value for key, value in incoming.items() if key.lower() not in _STRIP_REQUEST_HEADERS}
    headers["Authorization"] = f"Bearer {access_token}"
    headers["ChatGPT-Account-Id"] = account.chatgpt_account_id
    headers["User-Agent"] = incoming.get("user-agent") or config.CODEX_USER_AGENT
    if account.chatgpt_account_is_fedramp:
        headers["X-OpenAI-Fedramp"] = "true"
    return headers


def _upstream_url(request: Request, upstream_path: str) -> str:
    if upstream_path not in _RESPONSE_OPERATIONS:
        raise HTTPException(status_code=404, detail="Unsupported proxy path")
    url = f"{config.UPSTREAM_CODEX_BASE_URL.rstrip('/')}{upstream_path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    return url


def _model_catalog_url(request: Request) -> Tuple[str, bool]:
    """Add Codex's required version while retaining raw CLI catalog behavior."""
    params = list(request.query_params.multi_items())
    client_supplied_version = any(key == "client_version" for key, _ in params)
    if not client_supplied_version:
        params.append(("client_version", settings.CODEX_CLIENT_VERSION))
    query = urlencode(params, doseq=True)
    return f"{config.UPSTREAM_CODEX_BASE_URL}/models?{query}", client_supplied_version


def _openai_model_catalog(body: object) -> Optional[dict]:
    """Translate Codex's model catalog into the OpenAI Models list schema."""
    if not isinstance(body, Mapping) or not isinstance(body.get("models"), list):
        return None
    models = []
    for raw_model in body["models"]:
        if not isinstance(raw_model, Mapping):
            continue
        model_id = raw_model.get("id") or raw_model.get("slug")
        if not isinstance(model_id, str) or not model_id:
            continue
        created = raw_model.get("created")
        models.append(
            {
                "id": model_id,
                "object": "model",
                "created": created if isinstance(created, int) else 0,
                "owned_by": raw_model.get("owned_by") or "openai",
            }
        )
    return {"object": "list", "data": models}


def _catalog_models(body: object) -> Optional[List[Dict]]:
    if not isinstance(body, Mapping) or not isinstance(body.get("models"), list):
        return None
    return [dict(model) for model in body["models"] if isinstance(model, Mapping)]


def _merge_model_catalogs(catalogs: List[Dict]) -> Optional[Dict]:
    """Union native Codex catalogs, preferring richer catalogs while retaining native metadata."""
    usable = [(catalog, _catalog_models(catalog)) for catalog in catalogs]
    usable = [(catalog, models) for catalog, models in usable if models is not None]
    if not usable:
        return None
    usable.sort(key=lambda item: len(item[1]), reverse=True)
    merged = dict(usable[0][0])
    models: List[Dict] = []
    seen: Set[str] = set()
    for _, entries in usable:
        for model in entries:
            model_id = model.get("slug") or model.get("id")
            if not isinstance(model_id, str) or not model_id or model_id in seen:
                continue
            seen.add(model_id)
            models.append(model)
    merged["models"] = models
    return merged


def _filter_model_catalog(catalog: Dict, allowed_models: Optional[List[str]]) -> Dict:
    """Hide models outside the authenticated user's allowlist from discovery."""
    if allowed_models is None:
        return catalog
    allowed = set(allowed_models)
    filtered = dict(catalog)
    filtered["models"] = [
        model for model in (_catalog_models(catalog) or []) if str(model.get("slug") or model.get("id") or "").strip().lower() in allowed
    ]
    return filtered


async def _is_model_unavailable(candidate: httpx.Response) -> bool:
    """Recognize account-specific model-access failures without masking malformed client requests."""
    if candidate.status_code not in {400, 403, 404}:
        return False
    body = (await candidate.aread()).decode(errors="replace").lower()
    model_failure = any(term in body for term in ("not supported", "unsupported", "not available", "no access", "does not exist"))
    return "model" in body and model_failure


async def _is_capacity_unavailable(candidate: httpx.Response) -> bool:
    """Recognize explicit model-capacity responses that may be retried on another account.

    Rate-limit responses deliberately are not capacity responses.  They must
    reach the quota branch below so provider headers and ``Retry-After`` are
    persisted correctly.
    """
    if getattr(candidate, "_proxy_capacity_error", False) or candidate.extensions.get("proxy_capacity_error", False):
        return True
    if candidate.status_code not in {400, 403, 404, 409, 429, 500, 502, 503, 529}:
        return False
    body = (await candidate.aread()).decode(errors="replace").lower()
    clear_phrase = any(
        phrase in body
        for phrase in (
            "at capacity",
            "try a different model",
            "service exhausted",
            "temporarily overloaded",
        )
    )
    return clear_phrase or ("capacity" in body and "model" in body) or ("overloaded" in body and "model" in body)


class _PrefetchedResponse:
    """Small response proxy that replays bytes consumed while checking an SSE error prefix."""

    def __init__(self, response: httpx.Response, prefix: bytes, iterator, capacity_error: bool) -> None:
        self._response = response
        self._prefix = prefix
        self._iterator = iterator
        self._capacity_error = capacity_error
        self._body: Optional[bytes] = None

    @property
    def _proxy_capacity_error(self) -> bool:
        return self._capacity_error

    def __getattr__(self, name):
        return getattr(self._response, name)

    async def aclose(self) -> None:
        await self._response.aclose()

    async def aread(self) -> bytes:
        if self._body is None:
            self._body = self._prefix + b"".join([chunk async for chunk in self._iterator])
            self._prefix = b""
        return self._body

    async def aiter_bytes(self):
        if self._body is not None:
            if self._body:
                yield self._body
            return
        if self._prefix:
            yield self._prefix
            self._prefix = b""
        async for chunk in self._iterator:
            yield chunk


_CAPACITY_PHRASES = (
    "at capacity",
    "try a different model",
    "service exhausted",
    "temporarily overloaded",
)
_FAIL_MARKERS = (
    "response.failed",
    "event: error",
    '"type":"error"',
    '"type": "error"',
    '"error":{"',
    '"error": {"',
    '"status":"failed"',
    '"status": "failed"',
)
_OUTPUT_DELTA_MARKERS = (
    "response.output_text.delta",
    "response.content_part.delta",
    "response.function_call_arguments.delta",
    "response.reasoning_summary_text.delta",
    "response.reasoning_text.delta",
    "output_text.delta",
    "content_part.delta",
)
_SUCCESS_MARKERS = ("response.completed",)


def _is_pre_output_failure(lowered: str) -> bool:
    """Return True if the lowered SSE text shows a failure with no output."""
    has_failed = any(m in lowered for m in _FAIL_MARKERS)
    has_output = any(m in lowered for m in _OUTPUT_DELTA_MARKERS)
    return has_failed and not has_output


async def _empty_aiter():
    """Yield nothing -- used as the remaining-bytes iterator for fully-read responses."""
    return
    yield  # noqa: RET504  -- makes this an async generator


async def _prepare_candidate(candidate: httpx.Response):
    """Inspect the beginning of a successful SSE response for early failures.

    The proxy must not start forwarding a stream to the client until there is
    reasonable confidence the upstream will produce output.  This function
    buffers SSE frames and keeps reading until one of three outcomes:

    1. An **output-delta** event is seen  -> the model is generating; start
       streaming (return ``capacity_error=False``).
    2. A **response.failed** / capacity phrase is seen *without* any output
       delta  -> the request failed before generating; the caller can safely
       retry on the next account (``capacity_error=True``).
    3. A safety bound is reached (64 KB or 90 s total prefetch time) without
       either signal -> treat this attempt as failed and rotate accounts.

    The original bug forwarded the buffered keepalives after a prefix timeout;
    OpenAI could then emit a late ``response.failed`` capacity event directly
    to Codex, which ended the user's run instead of triggering account failover.
    We now hold the stream until real output/completion and classify every
    pre-output timeout or failure as retryable inside the proxy.
    """
    if candidate.status_code != 200:
        return candidate
    content_type = candidate.headers.get("content-type", "").lower()

    # The Codex upstream sometimes omits the Content-Type header entirely
    # even though it sends a valid SSE stream.  Detect this by peeking at
    # the first bytes: if they start with "event:" or "data:", treat the
    # response as an SSE stream regardless of the header.
    is_sse = "text/event-stream" in content_type

    if not is_sse:
        raw = await candidate.aread()
        lowered = raw.decode(errors="replace").lower()

        # If the body looks like SSE events, reclassify.
        if raw.lstrip().startswith((b"event:", b"data:")):
            is_sse = True
        else:
            candidate.extensions["proxy_capacity_error"] = any(p in lowered for p in _CAPACITY_PHRASES) or _is_pre_output_failure(lowered)
            return candidate

    if is_sse and hasattr(candidate, "_content") and candidate._content:
        # We already consumed the body via aread() above; wrap it as a
        # _PrefetchedResponse so the SSE prefetch logic can inspect it.
        lowered = candidate._content.decode(errors="replace").lower()
        is_capacity_error = any(p in lowered for p in _CAPACITY_PHRASES) or _is_pre_output_failure(lowered)
        if is_capacity_error:
            return _PrefetchedResponse(
                candidate,
                candidate._content,
                _empty_aiter(),
                True,
            )
        return _PrefetchedResponse(
            candidate,
            candidate._content,
            _empty_aiter(),
            False,
        )

    iterator = candidate.aiter_bytes().__aiter__()
    prefix = bytearray()
    prefetch_deadline = asyncio.get_event_loop().time() + 90
    first_byte_timeout = 60
    chunks_read = 0

    while len(prefix) < 64 * 1024:
        elapsed = asyncio.get_event_loop().time()
        if elapsed >= prefetch_deadline:
            logger.warning("Upstream produced no output before the SSE prefetch deadline; treating as capacity unavailable")
            return _PrefetchedResponse(candidate, bytes(prefix), iterator, True)
        remaining = max(1, prefetch_deadline - elapsed)
        try:
            timeout = min(
                first_byte_timeout if not prefix else 30,
                remaining,
            )
            chunk = await asyncio.wait_for(
                iterator.__anext__(),
                timeout=timeout,
            )
        except StopAsyncIteration:
            break
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning("Upstream produced no output within the SSE prefetch timeout; treating as capacity unavailable")
            return _PrefetchedResponse(candidate, bytes(prefix), iterator, True)
        chunks_read += 1
        prefix.extend(chunk)
        lowered = bytes(prefix).decode(errors="replace").lower()

        # Explicit capacity phrases -> always retry.
        if any(p in lowered for p in _CAPACITY_PHRASES):
            return _PrefetchedResponse(
                candidate,
                bytes(prefix),
                iterator,
                True,
            )

        has_output = any(m in lowered for m in _OUTPUT_DELTA_MARKERS)
        completed = any(m in lowered for m in _SUCCESS_MARKERS)

        # Real output is flowing -> response is healthy, start streaming.
        if has_output or completed:
            return _PrefetchedResponse(candidate, bytes(prefix), iterator, False)

        if _is_pre_output_failure(lowered):
            return _PrefetchedResponse(candidate, bytes(prefix), iterator, True)

    logger.warning("Upstream filled the SSE prefetch buffer without producing output; treating as capacity unavailable")
    return _PrefetchedResponse(candidate, bytes(prefix), iterator, True)


async def _is_empty_upstream_404(candidate: httpx.Response) -> bool:
    """Recognize the account-scoped empty 404 observed from the Codex backend."""
    if candidate.status_code != 404:
        return False
    return not (await candidate.aread()).strip()


def _emit_event(
    request: Request,
    event_type: str,
    *,
    user_id=None,
    api_key_id=None,
    account_id=None,
    fallback_provider_id=None,
    status_code=None,
    message=None,
    metadata=None,
) -> None:
    """Persist a timeline event without allowing observability failures to affect routing."""
    request_id = getattr(request.state, "proxy_event_request_id", None)
    if request_id:
        context = getattr(request.state, "proxy_event_context", {})
        merged_metadata = {**context, **(metadata or {})}
        events.record_event(
            event_type,
            request_id,
            user_id=user_id,
            api_key_id=api_key_id,
            account_id=account_id,
            fallback_provider_id=fallback_provider_id,
            status_code=status_code,
            message=message,
            metadata=merged_metadata,
        )


def _record_usage_safe(
    user_id: uuid.UUID,
    api_key_id: Optional[uuid.UUID],
    account_id: Optional[uuid.UUID],
    usage_obj: usage.Usage,
    status_code: Optional[int],
    request_id: Optional[str],
    fallback_provider_id: Optional[uuid.UUID] = None,
) -> None:
    try:
        with get_db_cm() as db:
            usage.record_usage(
                db,
                user_id,
                api_key_id,
                account_id,
                usage_obj,
                status_code,
                request_id,
                fallback_provider_id,
            )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to record proxied token usage")


def _set_archive_metadata(request: Request, **fields: object) -> None:
    """Attach non-sensitive request metadata for request-scoped diagnostics."""
    metadata = getattr(request.state, "archive_metadata", None)
    if isinstance(metadata, dict):
        metadata.update({key: value for key, value in fields.items() if value is not None})


def _archive_usage(request: Request, usage_obj: usage.Usage) -> None:
    _set_archive_metadata(
        request,
        usage={
            "model": usage_obj.model,
            "input_tokens": usage_obj.input_tokens,
            "output_tokens": usage_obj.output_tokens,
            "cached_input_tokens": usage_obj.cached_input_tokens,
            "cache_write_tokens": usage_obj.cache_write_tokens,
            "reasoning_level": usage_obj.reasoning_level,
            "request_mode": usage_obj.request_mode,
        },
    )


def _openai_error_response(
    message: str,
    status_code: int,
    *,
    error_type: str,
    param: Optional[str] = None,
    code: Optional[str] = None,
    background: Optional[BackgroundTasks] = None,
) -> Response:
    return Response(
        content=json.dumps(
            {
                "error": {
                    "message": message,
                    "type": error_type,
                    "param": param,
                    "code": code,
                }
            },
            separators=(",", ":"),
        ),
        status_code=status_code,
        media_type="application/json",
        background=background,
    )


def _enforce_client_limits(db: Session, key: ApiKeyDb, user: Optional[UserDb]) -> None:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(seconds=60)

    if user is not None and user.rate_limit_per_minute and user.rate_limit_per_minute > 0:
        count = (
            db.query(UsageRecordDb)
            .filter(
                UsageRecordDb.user_id == user.id,
                UsageRecordDb.created_at >= window_start,
            )
            .count()
        )
        if count >= user.rate_limit_per_minute:
            notifications.enqueue_client_limit(
                db,
                "user_rate_limit",
                user_name=user.name,
                limit=user.rate_limit_per_minute,
                dedupe_key=str(user.id),
                dashboard_url=settings.FRONTEND_ORIGIN,
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="User rate limit exceeded; slow down.",
                headers={"Retry-After": "60"},
            )
    if user is not None and user.monthly_token_budget and user.monthly_token_budget > 0:
        if usage.monthly_token_usage(db, user.id) >= user.monthly_token_budget:
            notifications.enqueue_client_limit(
                db,
                "user_monthly_budget",
                user_name=user.name,
                limit=user.monthly_token_budget,
                dedupe_key=f"{user.id}:{now:%Y-%m}",
                dashboard_url=settings.FRONTEND_ORIGIN,
            )
            db.commit()
            raise HTTPException(status_code=403, detail="User monthly token budget exhausted.")
    if user is not None and user.lifetime_token_budget and user.lifetime_token_budget > 0:
        if usage.total_token_usage(db, user.id) >= user.lifetime_token_budget:
            raise HTTPException(status_code=403, detail="User lifetime token budget exhausted.")
    if user is not None and user.monthly_spend_budget_usd and user.monthly_spend_budget_usd > 0:
        if usage.spend_usage(db, user_id=user.id, month_to_date=True) >= user.monthly_spend_budget_usd:
            raise HTTPException(status_code=403, detail="User monthly spend budget exhausted.")
    if user is not None and user.lifetime_spend_budget_usd and user.lifetime_spend_budget_usd > 0:
        if usage.spend_usage(db, user_id=user.id) >= user.lifetime_spend_budget_usd:
            raise HTTPException(status_code=403, detail="User lifetime spend budget exhausted.")

    rate_limit = key.rate_limit_per_minute if key.rate_limit_per_minute is not None else settings.DEFAULT_KEY_RATE_LIMIT_PER_MINUTE
    if rate_limit and rate_limit > 0:
        count = (
            db.query(UsageRecordDb)
            .filter(
                UsageRecordDb.api_key_id == key.id,
                UsageRecordDb.created_at >= window_start,
            )
            .count()
        )
        if count >= rate_limit:
            notifications.enqueue_client_limit(
                db,
                "api_key_rate_limit",
                user_name=user.name if user is not None else "Unknown user",
                key_label=key.label,
                key_prefix=key.key_prefix,
                limit=rate_limit,
                dedupe_key=str(key.id),
                dashboard_url=settings.FRONTEND_ORIGIN,
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="API key rate limit exceeded; slow down.",
                headers={"Retry-After": "60"},
            )
    if key.monthly_token_budget and key.monthly_token_budget > 0:
        if usage.monthly_token_usage_for_key(db, key.id) >= key.monthly_token_budget:
            notifications.enqueue_client_limit(
                db,
                "api_key_monthly_budget",
                user_name=user.name if user is not None else "Unknown user",
                key_label=key.label,
                key_prefix=key.key_prefix,
                limit=key.monthly_token_budget,
                dedupe_key=f"{key.id}:{now:%Y-%m}",
                dashboard_url=settings.FRONTEND_ORIGIN,
            )
            db.commit()
            raise HTTPException(status_code=403, detail="API key monthly token budget exhausted.")


def _enforce_request_policy(
    user: UserDb,
    request_mode: request_policy.RequestMode,
    reasoning_level: Optional[str],
    model: object,
) -> None:
    allowed_modes = request_policy.decode_choices(
        user.allowed_request_modes_json,
        request_policy.ALL_REQUEST_MODES,
    )
    if request_mode not in allowed_modes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{request_mode.capitalize()} request mode is not allowed for this user.",
        )

    allowed_levels = request_policy.decode_choices(
        user.allowed_reasoning_levels_json,
        request_policy.ALL_REASONING_LEVELS,
    )
    if reasoning_level is None:
        if set(allowed_levels) != set(request_policy.ALL_REASONING_LEVELS):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=("This user has restricted thinking levels; the request must explicitly set reasoning.effort to an allowed value."),
            )
    elif reasoning_level not in allowed_levels:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Thinking level '{reasoning_level}' is not allowed for this user.",
        )

    _enforce_model_policy(user, model)


def _enforce_model_policy(user: UserDb, model: object) -> None:
    """Apply the per-user model allowlist to every model-aware proxy operation."""
    allowed_models = request_policy.decode_models(user.allowed_models_json)
    if allowed_models is None:
        return
    if not isinstance(model, str) or not model.strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This user has restricted models; the request must explicitly set an allowed model.",
        )
    normalized_model = model.strip().lower()
    if normalized_model not in allowed_models:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Model '{model}' is not allowed for this user.",
        )


def _override_model(user: UserDb, model: object) -> object:
    """Resolve a user-scoped requested model alias to its upstream model ID."""
    if not isinstance(model, str) or not model.strip():
        return model
    overrides = request_policy.decode_model_overrides(user.model_overrides_json)
    return overrides.get(model.strip().lower(), model)


def _restore_requested_model_in_error(
    raw: bytes,
    status_code: int,
    requested_model: object,
    effective_model: object,
) -> bytes:
    """Keep a per-user upstream override private in client-visible errors."""
    if status_code < 400 or not isinstance(requested_model, str) or not isinstance(effective_model, str):
        return raw
    if not requested_model or requested_model == effective_model:
        return raw
    return re.sub(
        re.escape(effective_model).encode(),
        lambda _match: requested_model.encode(),
        raw,
        flags=re.IGNORECASE,
    )


async def _send_candidate(
    connection: egress.EgressConnection,
    request: Request,
    body: bytes,
    upstream_url: str,
    access_token: str,
    account,
) -> httpx.Response:
    lease = account_limiter.try_acquire(
        account.id,
        settings.MAX_CONCURRENT_REQUESTS_PER_ACCOUNT,
        getattr(request.state, "user_priority", 1),
    )
    headers = _upstream_headers(request.headers, access_token, account)
    upstream_request = connection.client.build_request(
        request.method,
        upstream_url,
        headers=headers,
        content=body,
    )
    try:
        candidate = await _prepare_candidate(await connection.client.send(upstream_request, stream=True))
    except Exception:
        lease.release()
        await connection.aclose()
        raise
    original_aclose = candidate.aclose

    async def close_with_lease():
        try:
            await original_aclose()
        finally:
            lease.release()
            await connection.aclose()

    candidate.aclose = close_with_lease
    candidate.extensions["proxy_egress_target"] = connection.target
    return candidate


def _fallback_headers(incoming: Mapping[str, str], provider: OpenAIFallbackDb) -> Dict[str, str]:
    headers = {key: value for key, value in incoming.items() if key.lower() not in _STRIP_REQUEST_HEADERS}
    headers["Authorization"] = f"Bearer {openai_fallbacks.api_key(provider)}"
    return headers


async def _send_fallback_candidate(
    connection: egress.EgressConnection,
    request: Request,
    body: bytes,
    upstream_path: str,
    provider: OpenAIFallbackDb,
) -> httpx.Response:
    lease = account_limiter.try_acquire(
        provider.id,
        settings.MAX_CONCURRENT_REQUESTS_PER_ACCOUNT,
        getattr(request.state, "user_priority", 1),
    )
    url = openai_fallbacks.endpoint(provider, upstream_path)
    if request.url.query:
        url = f"{url}?{request.url.query}"
    upstream_request = connection.client.build_request(
        request.method,
        url,
        headers=_fallback_headers(request.headers, provider),
        content=body,
    )
    try:
        candidate = await _prepare_candidate(await connection.client.send(upstream_request, stream=True))
    except Exception:
        lease.release()
        await connection.aclose()
        raise
    original_aclose = candidate.aclose

    async def close_with_lease():
        try:
            await original_aclose()
        finally:
            lease.release()
            await connection.aclose()

    candidate.aclose = close_with_lease
    candidate.extensions["proxy_egress_target"] = connection.target
    return candidate


async def _send_account_candidate(
    db: Session,
    connection_request: Request,
    body: bytes,
    upstream_url: str,
    account,
    *,
    force_refresh: bool = False,
) -> httpx.Response:
    """Open one account request through that account's configured egress target."""
    connection = egress.get_pool().acquire(
        account.egress_target_id,
        priority=getattr(connection_request.state, "user_priority", 1),
    )
    try:
        access_token = rotation.ensure_fresh_token(
            db,
            account,
            force_refresh=force_refresh,
            egress_target=connection.target,
        )
        db.commit()
        return await _send_candidate(
            connection,
            connection_request,
            body,
            upstream_url,
            access_token,
            account,
        )
    except Exception:
        await connection.aclose()
        raise


async def _send_fallback_with_egress(
    connection_request: Request,
    body: bytes,
    upstream_path: str,
    provider: OpenAIFallbackDb,
) -> httpx.Response:
    """Send a pay-as-you-go fallback through an automatically selected target."""
    connection = egress.get_pool().acquire(
        None,
        priority=getattr(connection_request.state, "user_priority", 1),
    )
    try:
        return await _send_fallback_candidate(
            connection,
            connection_request,
            body,
            upstream_path,
            provider,
        )
    except Exception:
        await connection.aclose()
        raise


def _fallback_retry_after(response: httpx.Response, default: int = 60) -> int:
    raw = response.headers.get("retry-after")
    try:
        return max(1, int(float(raw))) if raw is not None else default
    except (TypeError, ValueError):
        return default


def _codex_catalog_from_openai_models(payload: object) -> Optional[Dict]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        return None
    models = []
    for raw_model in payload["data"]:
        if not isinstance(raw_model, Mapping):
            continue
        model_id = raw_model.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        models.append({**raw_model, "slug": model_id})
    return {"models": models}


@router.get("/models")
async def proxy_models(
    request: Request,
    key: ApiKeyDb = Depends(security.authenticate_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    """Return the fixed supported catalog without probing pooled accounts.

    This endpoint is called frequently by Codex clients. It previously walked
    every account and fetched its provider catalog synchronously, turning a
    harmless discovery request into 5-8 seconds of upstream work. Model
    discovery is now a local, deterministic operation; quota/catalog refreshes
    remain separate administrative/background concerns.
    """
    from app.model_catalog import codex_catalog

    user = db.query(UserDb).filter(UserDb.id == key.user_id).first()
    if user is None:
        raise HTTPException(status_code=401, detail="API key owner no longer exists")
    request.state.user_priority = user.priority

    _, client_supplied_version = _model_catalog_url(request)
    catalog = _filter_model_catalog(codex_catalog(), request_policy.decode_models(user.allowed_models_json))
    response_body = catalog if client_supplied_version else _openai_model_catalog(catalog)
    return Response(
        content=json.dumps(response_body, separators=(",", ":")).encode(),
        status_code=200,
        media_type="application/json",
    )


@router.post("/responses")
@router.post("/responses/compact")
@router.post("/chat/completions")
async def proxy_responses(
    request: Request,
    background_tasks: BackgroundTasks,
    key: ApiKeyDb = Depends(security.authenticate_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    """Relay Responses or translate Chat Completions through a pooled ChatGPT account."""
    # Keep one stable ID across every account attempt. Middleware's ID is also
    # returned to the client, but this local fallback keeps events correlated
    # in tests and deployments where archive middleware is disabled.
    request.state.proxy_event_request_id = request_context.get_request_context().get("request_id") or f"req_{uuid.uuid4().hex}"
    client_prefix = f"{config.API_PREFIX}/v1"
    requested_path = request.url.path.removeprefix(client_prefix)
    is_chat_completion = requested_path == "/chat/completions"
    upstream_path = "/responses" if is_chat_completion else requested_path
    operation = _RESPONSE_OPERATIONS.get(upstream_path)
    if operation is None:
        raise HTTPException(status_code=404, detail="Unsupported proxy path")

    # Streaming finalizers can run after the request-scoped ORM session has
    # committed or closed.  Capture scalar identifiers now so disconnect
    body = await request.body()
    fallback_body = body
    upstream_url = _upstream_url(request, upstream_path)
    user = db.query(UserDb).filter(UserDb.id == key.user_id).first()
    if user is None:
        raise HTTPException(status_code=401, detail="API key owner no longer exists")
    request.state.user_priority = user.priority
    _set_archive_metadata(
        request,
        user_id=user.id,
        api_key_id=key.id,
        operation=operation,
        client_protocol="chat_completions" if is_chat_completion else "responses",
    )
    _enforce_client_limits(db, key, user)

    request_model = None
    requested_model = None
    request_reasoning = None
    request_mode: request_policy.RequestMode = "standard"
    convert_stream_to_response = False
    client_requested_stream = False
    chat_metadata: Optional[chat_completions.ChatRequestMetadata] = None
    try:
        parsed_request = json.loads(body)
        if is_chat_completion:
            try:
                parsed_request, chat_metadata = chat_completions.request_to_responses(parsed_request)
            except chat_completions.ChatCompletionTranslationError as exc:
                return _openai_error_response(
                    exc.message,
                    status.HTTP_400_BAD_REQUEST,
                    error_type="invalid_request_error",
                    param=exc.param,
                    code=exc.code,
                )
            client_requested_stream = chat_metadata.stream
            convert_stream_to_response = not chat_metadata.stream
        if isinstance(parsed_request, dict):
            request_changed = is_chat_completion
            requested_model = parsed_request.get("model")
            request_model = _override_model(user, requested_model)
            if request_model != requested_model:
                parsed_request["model"] = request_model
                request_changed = True
            if operation == "create":
                request_reasoning = usage.reasoning_level_from_request(parsed_request)
                if request_reasoning is None:
                    reasoning = parsed_request.get("reasoning")
                    if reasoning is None:
                        parsed_request["reasoning"] = {"effort": "none"}
                        request_reasoning = "none"
                        request_changed = True
                    elif isinstance(reasoning, Mapping):
                        parsed_request["reasoning"] = {**reasoning, "effort": "none"}
                        request_reasoning = "none"
                        request_changed = True
                request_mode = request_policy.request_mode_from_request(parsed_request)
                # ChatGPT subscription inference never supports server-side response
                # storage, while OpenAI-compatible SDKs commonly omit this field.
                if "store" not in parsed_request:
                    parsed_request["store"] = False
                    request_changed = True
                stream_value = parsed_request.get("stream")
                if not is_chat_completion:
                    client_requested_stream = stream_value is True
                if "stream" not in parsed_request or stream_value is False:
                    parsed_request["stream"] = True
                    request_changed = True
                    convert_stream_to_response = True
                # Preserve a public Responses-compatible body before applying
                # ChatGPT-subscription-only compatibility transformations.
                fallback_body = json.dumps(parsed_request, separators=(",", ":")).encode()
                # The public Responses API accepts this client-side bound, but the
                # ChatGPT Codex backend rejects the field instead of ignoring it.
                if "max_output_tokens" in parsed_request:
                    parsed_request.pop("max_output_tokens")
                    request_changed = True
                # The public Responses API accepts a plain string, while the Codex
                # subscription backend requires the equivalent message-list form.
                if isinstance(parsed_request.get("input"), str):
                    parsed_request["input"] = [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": parsed_request["input"],
                                }
                            ],
                        }
                    ]
                    request_changed = True
            if request_changed:
                body = json.dumps(parsed_request, separators=(",", ":")).encode()
                if operation != "create":
                    fallback_body = body
    except (json.JSONDecodeError, UnicodeDecodeError):
        if is_chat_completion:
            return _openai_error_response(
                "The request body must be valid JSON.",
                status.HTTP_400_BAD_REQUEST,
                error_type="invalid_request_error",
                code="invalid_json",
            )

    if operation == "create":
        _enforce_request_policy(user, request_mode, request_reasoning, requested_model)
    else:
        _enforce_model_policy(user, requested_model)
    request.state.proxy_event_context = {
        "model": request_model,
        "requested_model": requested_model,
        "thinking_level": request_reasoning,
        "user_priority": user.priority,
    }
    _emit_event(request, "request.received", user_id=user.id, api_key_id=key.id, metadata={"path": request.url.path, "method": request.method})
    _set_archive_metadata(
        request,
        requested_model=requested_model,
        upstream_model=request_model,
        reasoning_level=request_reasoning,
        request_mode=request_mode,
    )

    # Accounts already stored at 100% are excluded by normal selection. Give
    # any account with a known reset credit a chance to recover first.
    _recover_exhausted_accounts_with_cached_credits(db)

    excluded: Set[uuid.UUID] = set()
    response: Optional[httpx.Response] = None
    chosen_id: Optional[uuid.UUID] = None
    chosen_fallback_id: Optional[uuid.UUID] = None

    max_attempts = db.query(AccountDb).count()
    model_rejected = False

    for _ in range(max_attempts):
        account = rotation.select_account(db, user, excluded, model=str(request_model) if request_model else None)
        if account is None:
            break
        if not account.chatgpt_account_id:
            excluded.add(account.id)
            continue

        _emit_event(request, "account.attempt", user_id=user.id, api_key_id=key.id, account_id=account.id, metadata={"priority": account.priority})

        try:
            candidate = await _send_account_candidate(db, request, body, upstream_url, account)

            # A token can be revoked before its JWT expiry. Refresh once and
            # retry this account before failing over.
            if candidate.status_code == 401:
                await candidate.aclose()
                candidate = await _send_account_candidate(
                    db,
                    request,
                    body,
                    upstream_url,
                    account,
                    force_refresh=True,
                )
                if candidate.status_code == 401:
                    await candidate.aclose()
                    provider_health.persist_failure(db, account.id, provider_health.reauthentication_error())
                    excluded.add(account.id)
                    continue
        except (account_limiter.AccountBusy, egress.EgressUnavailable) as exc:
            logger.info("Pooled account %s cannot acquire request capacity: %s", account.label, exc)
            _emit_event(
                request,
                "account.busy",
                user_id=user.id,
                api_key_id=key.id,
                account_id=account.id,
                message=str(exc),
            )
            excluded.add(account.id)
            continue
        except Exception:  # noqa: BLE001
            logger.exception("Pooled account %s failed before receiving a response", account.label)
            _emit_event(
                request,
                "account.error",
                user_id=user.id,
                api_key_id=key.id,
                account_id=account.id,
                message="Upstream request failed before a response",
            )
            excluded.add(account.id)
            continue

        if await _is_capacity_unavailable(candidate):
            _emit_event(
                request,
                "account.capacity",
                user_id=user.id,
                api_key_id=key.id,
                account_id=account.id,
                status_code=candidate.status_code,
                message="Provider reported model capacity",
            )
            await candidate.aclose()
            rotation.mark_cooldown(db, account, account.cooldown_seconds)
            _emit_event(
                request,
                "account.cooldown",
                user_id=user.id,
                api_key_id=key.id,
                account_id=account.id,
                message=f"Cooling down for {account.cooldown_seconds}s",
            )
            logger.warning(
                "Pooled account %s received explicit model-capacity response; cooling down and failing over",
                account.label,
            )
            db.commit()
            excluded.add(account.id)
            model_rejected = bool(request_model) or model_rejected
            continue

        reached_type = rotation.update_quota_from_headers(account, candidate.headers)
        quota_failure = candidate.status_code == 429 or reached_type in rotation.HARD_LIMIT_REACHED_TYPES
        if (account.weekly_used_pct or 0) >= 1.0:
            try:
                target = candidate.extensions.get("proxy_egress_target")
                access_token = rotation.ensure_fresh_token(db, account, egress_target=target)
                redeemed = rotation.auto_redeem_weekly_reset(
                    db,
                    account,
                    access_token,
                    egress_target=target,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Automatic weekly limit reset failed for pooled account %s", account.label)
                redeemed = False
            if redeemed and quota_failure:
                await candidate.aclose()
                candidate = await _send_account_candidate(db, request, body, upstream_url, account)
                reached_type = rotation.update_quota_from_headers(account, candidate.headers)
        provider_health.mark_response(account, candidate)
        if reached_type in rotation.HARD_LIMIT_REACHED_TYPES:
            notifications.enqueue_account_hard_limit(db, account, settings.FRONTEND_ORIGIN)
        else:
            notifications.enqueue_account_threshold(db, account, settings.FRONTEND_ORIGIN)
        db.commit()
        if candidate.status_code == 429 or reached_type in rotation.HARD_LIMIT_REACHED_TYPES:
            retry_after = rotation.parse_retry_after(
                candidate.headers,
                account.cooldown_seconds,
            )
            await candidate.aclose()
            rotation.mark_cooldown(db, account, retry_after)
            _emit_event(
                request,
                "account.rate_limited",
                user_id=user.id,
                api_key_id=key.id,
                account_id=account.id,
                status_code=429,
                message=f"Cooling down for {retry_after}s",
            )
            excluded.add(account.id)
            continue
        if request_model and await _is_model_unavailable(candidate):
            await candidate.aclose()
            excluded.add(account.id)
            model_rejected = True
            continue
        # An empty-body 404 typically means the
        # account's session or subscription is broken at the infrastructure
        # level, not that the client sent a bad request.  Fail over to the
        # next pooled account instead of forwarding the opaque error. Park it
        # briefly so a new client request does not immediately select it again.
        if await _is_empty_upstream_404(candidate):
            failed_account_id = account.id
            retry_after = rotation.parse_retry_after(candidate.headers, account.cooldown_seconds)
            await candidate.aclose()
            logger.warning(
                "Pooled account %s returned empty 404; cooling down for %ss and failing over",
                account.label,
                retry_after,
            )
            provider_health.persist_failure(
                db,
                failed_account_id,
                RuntimeError("Upstream returned an empty 404 response"),
                context="upstream_empty_404",
            )
            failed_account = db.get(AccountDb, failed_account_id)
            if failed_account is not None:
                rotation.mark_cooldown(db, failed_account, retry_after)
            excluded.add(failed_account_id)
            continue

        if candidate.status_code < 400:
            background_tasks.add_task(warmup.warm_pool_if_needed)
        response = candidate
        chosen_id = account.id
        _emit_event(request, "account.selected", user_id=user.id, api_key_id=key.id, account_id=account.id, status_code=candidate.status_code)
        break

    # Pay-as-you-go credentials are true fallbacks: they are considered only
    # after every eligible ChatGPT subscription account has failed or exhausted.
    excluded_fallbacks: Set[uuid.UUID] = set()
    fallback_attempts = db.query(OpenAIFallbackDb).count() if user.fallback_enabled else 0
    for _ in range(fallback_attempts):
        if response is not None:
            break
        fallback = openai_fallbacks.select_provider(
            db,
            excluded_fallbacks,
            model=str(request_model) if request_model else None,
        )
        if fallback is None:
            break
        _emit_event(request, "fallback.attempt", user_id=user.id, api_key_id=key.id, fallback_provider_id=fallback.id)
        try:
            candidate = await _send_fallback_with_egress(
                request,
                fallback_body,
                upstream_path,
                fallback,
            )
        except account_limiter.AccountBusy:
            logger.info("Fallback provider %s is at its in-flight request ceiling; trying the next provider", fallback.label)
            excluded_fallbacks.add(fallback.id)
            _emit_event(
                request,
                "fallback.busy",
                user_id=user.id,
                api_key_id=key.id,
                fallback_provider_id=fallback.id,
                message="Per-provider concurrency ceiling reached",
            )
            continue
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fallback provider %s failed before receiving a response", fallback.label)
            openai_fallbacks.mark_cooldown(fallback, 60, f"Request failed: {exc}")
            db.commit()
            excluded_fallbacks.add(fallback.id)
            _emit_event(request, "fallback.error", user_id=user.id, api_key_id=key.id, fallback_provider_id=fallback.id, message=str(exc))
            continue

        if await _is_capacity_unavailable(candidate):
            _emit_event(
                request,
                "fallback.capacity",
                user_id=user.id,
                api_key_id=key.id,
                fallback_provider_id=fallback.id,
                status_code=candidate.status_code,
                message="Provider reported model capacity",
            )
            await candidate.aclose()
            openai_fallbacks.mark_cooldown(fallback, 60, "Upstream reported model capacity.")
            logger.warning(
                "Fallback provider %s received explicit model-capacity response; cooling down and failing over",
                fallback.label,
            )
            db.commit()
            excluded_fallbacks.add(fallback.id)
            model_rejected = bool(request_model) or model_rejected
            continue

        if candidate.status_code == 401:
            await candidate.aclose()
            openai_fallbacks.mark_invalid(
                fallback,
                f"Credential rejected with HTTP {candidate.status_code}.",
            )
            db.commit()
            excluded_fallbacks.add(fallback.id)
            continue
        if candidate.status_code == 429 or candidate.status_code >= 500:
            retry_after = _fallback_retry_after(candidate)
            status_code = candidate.status_code
            await candidate.aclose()
            openai_fallbacks.mark_cooldown(
                fallback,
                retry_after,
                f"Upstream returned HTTP {status_code}.",
            )
            db.commit()
            excluded_fallbacks.add(fallback.id)
            continue
        if request_model and await _is_model_unavailable(candidate):
            await candidate.aclose()
            excluded_fallbacks.add(fallback.id)
            model_rejected = True
            continue
        if candidate.status_code == 403:
            await candidate.aclose()
            openai_fallbacks.mark_cooldown(
                fallback,
                60,
                "Upstream denied this request with HTTP 403.",
            )
            db.commit()
            excluded_fallbacks.add(fallback.id)
            continue
        if await _is_empty_upstream_404(candidate):
            await candidate.aclose()
            openai_fallbacks.mark_cooldown(fallback, 60, "Upstream returned an empty HTTP 404.")
            db.commit()
            excluded_fallbacks.add(fallback.id)
            continue

        if candidate.is_success:
            openai_fallbacks.mark_healthy(fallback)
            db.commit()
        response = candidate
        chosen_fallback_id = fallback.id
        _emit_event(
            request, "fallback.selected", user_id=user.id, api_key_id=key.id, fallback_provider_id=fallback.id, status_code=candidate.status_code
        )
        break

    if response is None:
        _emit_event(
            request,
            "request.exhausted",
            user_id=user.id,
            api_key_id=key.id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="No subscription account or API fallback could serve the request",
        )
        notifications.enqueue_elevated_503(db, settings.FRONTEND_ORIGIN)
        known_model = bool(request_model) and any(
            rotation.supports_model(account, str(request_model)) is True
            for account in db.query(AccountDb).filter(AccountDb.status != AccountStatus.DISABLED).all()
        )
        if request_model and (model_rejected or known_model):
            client_model = requested_model if isinstance(requested_model, str) else request_model
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Model '{client_model}' is temporarily unavailable across the pooled accounts.",
                headers={"Retry-After": "30"},
            )
        notifications.enqueue_pool_unavailable(db, settings.FRONTEND_ORIGIN)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="All pooled Codex accounts and configured API fallbacks are exhausted, unavailable, or rate-limited.",
            headers={"Retry-After": "30"},
        )

    response_headers = _pool_quota_response_headers(
        db,
        {key: value for key, value in response.headers.items() if key.lower() not in _STRIP_RESPONSE_HEADERS},
    )
    upstream_request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
    content_type = response.headers.get("content-type", "")
    response_status = response.status_code
    _emit_event(
        request,
        "response.returned",
        user_id=user.id,
        api_key_id=key.id,
        account_id=chosen_id,
        fallback_provider_id=chosen_fallback_id,
        status_code=response_status,
    )
    _set_archive_metadata(
        request,
        upstream_request_id=upstream_request_id,
        account_id=chosen_id,
        fallback_provider_id=chosen_fallback_id,
    )
    # Usage and operational events must share the proxy correlation ID. Using
    # the provider's request ID here previously made model/token/cost enrichment
    # impossible because no event row carried that unrelated upstream value.
    request_id = request.state.proxy_event_request_id

    if convert_stream_to_response and response_status < 400:
        raw = await response.aread()
        await response.aclose()
        accumulator = usage.StreamUsageAccumulator(
            fallback_model=request_model,
            reasoning_level=request_reasoning,
            request_mode=request_mode,
        )
        accumulator.feed(raw)
        parsed_usage = accumulator.result()
        terminal_response = accumulator.terminal_response
        if terminal_response is None:
            try:
                direct_response = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                direct_response = None
            if isinstance(direct_response, Mapping):
                terminal_response = dict(direct_response)
                parsed_usage = usage.usage_from_json(
                    direct_response,
                    fallback_model=request_model,
                    reasoning_level=request_reasoning,
                    request_mode=request_mode,
                )
        _record_usage_safe(
            key.user_id,
            key.id,
            chosen_id,
            parsed_usage,
            response_status,
            request_id,
            chosen_fallback_id,
        )
        _archive_usage(request, parsed_usage)
        if terminal_response is None:
            logger.error(
                "Codex stream ended without a terminal response; event_types=%s",
                sorted(accumulator.event_types),
            )
            return Response(
                content=json.dumps(
                    {
                        "error": {
                            "message": "Codex upstream ended without a terminal Responses API event.",
                            "type": "proxy_error",
                        }
                    },
                    separators=(",", ":"),
                ),
                status_code=status.HTTP_502_BAD_GATEWAY,
                media_type="application/json",
                background=background_tasks,
            )
        response_headers = {header: value for header, value in response_headers.items() if header.lower() != "content-type"}
        response_body: object = terminal_response
        if is_chat_completion and chat_metadata is not None:
            try:
                response_body = chat_completions.response_to_chat_completion(terminal_response, chat_metadata)
            except chat_completions.ChatCompletionTranslationError as exc:
                return _openai_error_response(
                    exc.message,
                    status.HTTP_502_BAD_GATEWAY,
                    error_type="upstream_error",
                    code="response_translation_failed",
                    background=background_tasks,
                )
        return Response(
            content=json.dumps(response_body, separators=(",", ":")),
            status_code=response_status,
            headers=response_headers,
            media_type="application/json",
            background=background_tasks,
        )

    if is_chat_completion and client_requested_stream and response_status < 400 and chat_metadata is not None:
        accumulator = usage.StreamUsageAccumulator(
            fallback_model=request_model,
            reasoning_level=request_reasoning,
            request_mode=request_mode,
        )
        translator = chat_completions.ChatCompletionStreamTranslator(request_model, chat_metadata)

        async def chat_stream_body():
            try:
                async for chunk in response.aiter_bytes():
                    accumulator.feed(chunk)
                    for translated in translator.feed(chunk):
                        yield translated
                for translated in translator.finish():
                    yield translated
            finally:
                await response.aclose()
                _record_usage_safe(
                    key.user_id,
                    key.id,
                    chosen_id,
                    accumulator.result(),
                    response_status,
                    request_id,
                    chosen_fallback_id,
                )
                _archive_usage(request, accumulator.result())

        response_headers = {header: value for header, value in response_headers.items() if header.lower() != "content-type"}
        return StreamingResponse(
            chat_stream_body(),
            status_code=response_status,
            headers=response_headers,
            media_type="text/event-stream",
            background=background_tasks,
        )

    if "text/event-stream" not in content_type and not (client_requested_stream and response_status < 400):
        raw = await response.aread()
        await response.aclose()
        raw = _restore_requested_model_in_error(raw, response_status, requested_model, request_model)
        parsed_usage = usage.Usage(
            model=request_model,
            reasoning_level=request_reasoning,
            request_mode=request_mode,
        )
        try:
            parsed_usage = usage.usage_from_json(
                json.loads(raw),
                fallback_model=request_model,
                reasoning_level=request_reasoning,
                request_mode=request_mode,
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            accumulator = usage.StreamUsageAccumulator(
                fallback_model=request_model,
                reasoning_level=request_reasoning,
                request_mode=request_mode,
            )
            accumulator.feed(raw)
            parsed_usage = accumulator.result()
        if parsed_usage.input_tokens == 0 and parsed_usage.output_tokens == 0:
            logger.warning(
                "No token usage found in non-SSE Codex response; content_type=%s bytes=%d",
                content_type,
                len(raw),
            )
        _record_usage_safe(
            key.user_id,
            key.id,
            chosen_id,
            parsed_usage,
            response_status,
            request_id,
            chosen_fallback_id,
        )
        _archive_usage(request, parsed_usage)
        return Response(
            content=raw,
            status_code=response_status,
            headers=response_headers,
            media_type=content_type or "application/json",
            background=background_tasks,
        )

    accumulator = usage.StreamUsageAccumulator(
        fallback_model=request_model,
        reasoning_level=request_reasoning,
        request_mode=request_mode,
    )

    async def stream_body():
        try:
            async for chunk in response.aiter_bytes():
                accumulator.feed(chunk)
                yield chunk
        finally:
            await response.aclose()
            captured_usage = accumulator.result()
            if captured_usage.input_tokens == 0 and captured_usage.output_tokens == 0:
                logger.warning(
                    "No token usage found in Codex stream; event_types=%s usage_keys=%s",
                    sorted(accumulator.event_types),
                    sorted(accumulator.usage_keys),
                )
            _record_usage_safe(
                key.user_id,
                key.id,
                chosen_id,
                captured_usage,
                response_status,
                request_id,
                chosen_fallback_id,
            )
            _archive_usage(request, captured_usage)

    response_headers = {header: value for header, value in response_headers.items() if header.lower() != "content-type"}
    return StreamingResponse(
        stream_body(),
        status_code=response_status,
        headers=response_headers,
        media_type="text/event-stream",
        background=background_tasks,
    )
