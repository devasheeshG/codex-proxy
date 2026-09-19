# Path: app/utils/usage.py
# Description: Responses API token capture and the per-user usage ledger.

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Set

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.utils.postgres import ApiKeyDb, OpenAIFallbackDb, UsageRecordDb, UserDb


@dataclass
class Usage:
    model: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_level: Optional[str] = None
    request_mode: str = "standard"


def reasoning_level_from_request(body: Mapping) -> Optional[str]:
    """Extract the Codex reasoning effort from a Responses API request."""
    reasoning = body.get("reasoning")
    candidates = []
    if isinstance(reasoning, Mapping):
        candidates.append(reasoning.get("effort"))
    elif isinstance(reasoning, str):
        candidates.append(reasoning)
    candidates.extend((body.get("reasoning_effort"), body.get("effort")))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower()
    return None


def _usage_from_response(
    response: Mapping,
    fallback_model: Optional[str] = None,
    reasoning_level: Optional[str] = None,
) -> Usage:
    block = response.get("usage")
    block = block if isinstance(block, Mapping) else {}
    details = block.get("input_tokens_details") or block.get("prompt_tokens_details")
    details = details if isinstance(details, Mapping) else block
    return Usage(
        model=response.get("model") or fallback_model,
        input_tokens=int(block.get("input_tokens") or block.get("prompt_tokens") or 0),
        output_tokens=int(block.get("output_tokens") or block.get("completion_tokens") or 0),
        cached_input_tokens=int(details.get("cached_tokens") or details.get("cached_input_tokens") or details.get("cache_read_input_tokens") or 0),
        # Responses API usage calls this `cache_write_tokens`; Codex's
        # token_count event uses the more explicit `cache_write_input_tokens`.
        # Accept both wire names (plus the Anthropic-compatible legacy alias)
        # so streamed and non-streamed responses produce the same ledger row.
        cache_write_tokens=int(
            details.get("cache_write_tokens")
            or details.get("cache_write_input_tokens")
            or details.get("input_cache_write_tokens")
            or details.get("cache_creation_input_tokens")
            or 0
        ),
        reasoning_level=reasoning_level,
    )


def _merge_usage(current: Usage, candidate: Usage) -> Usage:
    """Keep the most complete cumulative usage when later SSE events are partial."""
    return Usage(
        model=candidate.model or current.model,
        input_tokens=max(current.input_tokens, candidate.input_tokens),
        output_tokens=max(current.output_tokens, candidate.output_tokens),
        cached_input_tokens=max(current.cached_input_tokens, candidate.cached_input_tokens),
        cache_write_tokens=max(current.cache_write_tokens, candidate.cache_write_tokens),
        reasoning_level=candidate.reasoning_level or current.reasoning_level,
        # Mode is request metadata, so response-derived usage must never overwrite it.
        request_mode=current.request_mode or candidate.request_mode,
    )


class StreamUsageAccumulator:
    """Incrementally extract the final usage object from Responses API SSE."""

    def __init__(
        self,
        fallback_model: Optional[str] = None,
        reasoning_level: Optional[str] = None,
        request_mode: str = "standard",
    ) -> None:
        self._buffer = ""
        self._fallback_model = fallback_model
        self._reasoning_level = reasoning_level
        self._request_mode = request_mode
        self.usage = Usage(
            model=fallback_model,
            reasoning_level=reasoning_level,
            request_mode=request_mode,
        )
        self.event_types: Set[str] = set()
        self.usage_keys: Set[str] = set()
        self.terminal_response: Optional[Dict[str, Any]] = None
        self._output_items: Dict[int, Dict[str, Any]] = {}

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk.decode("utf-8", errors="ignore")
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._consume_line(line.strip())

    def _consume_line(self, line: str) -> None:
        if not line.startswith("data:"):
            return
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            return
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return
        if not isinstance(event, Mapping):
            return
        event_type = event.get("type")
        if isinstance(event_type, str):
            self.event_types.add(event_type)
        if event_type == "response.output_item.done" and isinstance(event.get("item"), Mapping):
            output_index = event.get("output_index")
            if isinstance(output_index, int):
                self._output_items[output_index] = dict(event["item"])
        response = event.get("response")
        if isinstance(response, Mapping) and event_type in {"response.completed", "response.failed", "response.incomplete"}:
            self.terminal_response = dict(response)
            if not self.terminal_response.get("output") and self._output_items:
                self.terminal_response["output"] = [self._output_items[index] for index in sorted(self._output_items)]
        if isinstance(response, Mapping) and isinstance(response.get("usage"), Mapping):
            self.usage_keys.update(str(key) for key in response["usage"])
            self.usage = _merge_usage(
                self.usage,
                _usage_from_response(
                    response,
                    self._fallback_model,
                    self._reasoning_level,
                ),
            )
            return
        if isinstance(event.get("usage"), Mapping):
            self.usage_keys.update(str(key) for key in event["usage"])
            self.usage = _merge_usage(
                self.usage,
                _usage_from_response(
                    event,
                    self._fallback_model,
                    self._reasoning_level,
                ),
            )
            return

        # Some Codex streams surface the client's token-count event directly.
        info = event.get("info")
        if event.get("type") == "token_count" and isinstance(info, Mapping):
            counts = info.get("last_token_usage") or info.get("total_token_usage")
            if isinstance(counts, Mapping):
                self.usage_keys.update(str(key) for key in counts)
                self.usage = _merge_usage(
                    self.usage,
                    Usage(
                        model=self.usage.model or self._fallback_model,
                        input_tokens=int(counts.get("input_tokens") or 0),
                        output_tokens=int(counts.get("output_tokens") or 0),
                        cached_input_tokens=int(counts.get("cached_input_tokens") or 0),
                        cache_write_tokens=int(
                            counts.get("cache_write_tokens")
                            or counts.get("cache_write_input_tokens")
                            or counts.get("input_cache_write_tokens")
                            or counts.get("cache_creation_input_tokens")
                            or 0
                        ),
                        reasoning_level=self._reasoning_level,
                        request_mode=self._request_mode,
                    ),
                )

    def result(self) -> Usage:
        # A valid SSE stream is allowed to end without a trailing newline.
        if self._buffer:
            final_line, self._buffer = self._buffer, ""
            self._consume_line(final_line.strip())
        return self.usage


def usage_from_json(
    body: Any,
    *,
    fallback_model: Optional[str] = None,
    reasoning_level: Optional[str] = None,
    request_mode: str = "standard",
) -> Usage:
    """Find usage in a response object, event envelope, or JSON event list."""
    result = Usage(
        model=fallback_model,
        reasoning_level=reasoning_level,
        request_mode=request_mode,
    )

    def visit(value: Any) -> None:
        nonlocal result
        if isinstance(value, Mapping):
            response = value.get("response")
            if isinstance(response, Mapping) and isinstance(response.get("usage"), Mapping):
                result = _merge_usage(
                    result,
                    _usage_from_response(response, fallback_model, reasoning_level),
                )
            if isinstance(value.get("usage"), Mapping):
                result = _merge_usage(
                    result,
                    _usage_from_response(value, fallback_model, reasoning_level),
                )
            if "input_tokens" in value or "prompt_tokens" in value:
                result = _merge_usage(
                    result,
                    _usage_from_response(
                        {"model": value.get("model"), "usage": value},
                        fallback_model,
                        reasoning_level,
                    ),
                )
            for child in value.values():
                if isinstance(child, (Mapping, list, tuple)):
                    visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    visit(body)
    return result


def record_usage(
    db: Session,
    user_id: uuid.UUID,
    api_key_id: Optional[uuid.UUID],
    account_id: Optional[uuid.UUID],
    usage: Usage,
    status_code: Optional[int],
    request_id: Optional[str],
    fallback_provider_id: Optional[uuid.UUID] = None,
    codex_session_id: Optional[str] = None,
    codex_thread_id: Optional[str] = None,
    codex_turn_id: Optional[str] = None,
    codex_root_turn_id: Optional[str] = None,
) -> None:
    from app import pricing

    now = datetime.now(timezone.utc)
    # Freeze the API-equivalent value when the request is recorded. Legacy rows
    # with a null value are still priced on read by cost_for_record below.
    billed_cost = pricing.cost_usd(
        model=usage.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        cache_write_tokens=usage.cache_write_tokens,
    )
    db.add(
        UsageRecordDb(
            id=uuid.uuid4(),
            user_id=user_id,
            api_key_id=api_key_id,
            account_id=account_id,
            fallback_provider_id=fallback_provider_id,
            model=usage.model or "unknown",
            # Responses API input_tokens already includes cached input tokens.
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            reasoning_level=usage.reasoning_level,
            request_mode=usage.request_mode,
            status_code=status_code,
            request_id=request_id,
            codex_session_id=codex_session_id,
            codex_thread_id=codex_thread_id,
            codex_turn_id=codex_turn_id,
            codex_root_turn_id=codex_root_turn_id,
            billed_cost_usd=billed_cost,
            created_at=now,
        )
    )
    user = db.query(UserDb).filter(UserDb.id == user_id).first()
    if user is not None:
        user.last_used_at = now
    if api_key_id is not None:
        key = db.query(ApiKeyDb).filter(ApiKeyDb.id == api_key_id).first()
        if key is not None:
            key.last_used_at = now
    if fallback_provider_id is not None:
        provider = db.get(OpenAIFallbackDb, fallback_provider_id)
        if provider is not None:
            provider.last_used_at = now
            provider.updated_at = now
    db.commit()


def token_sum_expr():
    return func.coalesce(func.sum(UsageRecordDb.input_tokens), 0) + func.coalesce(func.sum(UsageRecordDb.output_tokens), 0)


def input_tokens_sum_expr():
    return func.coalesce(func.sum(UsageRecordDb.input_tokens), 0)


def output_tokens_sum_expr():
    return func.coalesce(func.sum(UsageRecordDb.output_tokens), 0)


def month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_reset_at() -> datetime:
    """UTC instant when the current calendar-month usage window resets."""
    start = month_start()
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)


def monthly_token_usage(db: Session, user_id: uuid.UUID) -> int:
    total = (
        db.query(token_sum_expr())
        .filter(
            UsageRecordDb.user_id == user_id,
            UsageRecordDb.created_at >= month_start(),
        )
        .scalar()
    )
    return int(total or 0)


def monthly_token_usage_for_key(db: Session, api_key_id: uuid.UUID) -> int:
    total = (
        db.query(token_sum_expr())
        .filter(
            UsageRecordDb.api_key_id == api_key_id,
            UsageRecordDb.created_at >= month_start(),
        )
        .scalar()
    )
    return int(total or 0)


def total_token_usage(db: Session, user_id: uuid.UUID) -> int:
    total = db.query(token_sum_expr()).filter(UsageRecordDb.user_id == user_id).scalar()
    return int(total or 0)


def spend_usage(
    db: Session,
    *,
    user_id: Optional[uuid.UUID] = None,
    account_id: Optional[uuid.UUID] = None,
    month_to_date: bool = False,
) -> float:
    """Sum frozen API-equivalent request values, pricing legacy rows on read."""
    from app import pricing

    query = db.query(UsageRecordDb)
    if user_id is not None:
        query = query.filter(UsageRecordDb.user_id == user_id)
    if account_id is not None:
        query = query.filter(UsageRecordDb.account_id == account_id)
    if month_to_date:
        query = query.filter(UsageRecordDb.created_at >= month_start())
    return float(
        sum(
            float(record.billed_cost_usd) if record.billed_cost_usd is not None else pricing.cost_for_record(record)
            for record in query.yield_per(1000)
        )
    )


def spend_usage_rollups(db: Session, group_column, group_ids) -> dict[uuid.UUID, tuple[float, float]]:
    """Return total/month-to-date spend for many owners in two bounded queries.

    Current records have a frozen billed value, which PostgreSQL can aggregate
    efficiently. Only legacy rows without that value are loaded and priced in
    Python. This avoids rescanning the complete usage ledger twice for every
    user or account card.
    """
    from app import pricing

    ids = list(dict.fromkeys(group_ids))
    if not ids:
        return {}
    start = month_start()
    # Price legacy rows in SQL as well as frozen rows. This keeps list latency
    # constant even while old request history is still being backfilled.
    sql_cost = case(
        *[
            (
                UsageRecordDb.model.startswith(model),
                (
                    (UsageRecordDb.input_tokens - UsageRecordDb.cached_input_tokens - UsageRecordDb.cache_write_tokens) * rates["input"]
                    + UsageRecordDb.cached_input_tokens * rates["cached_input"]
                    + UsageRecordDb.cache_write_tokens * rates["cache_write"]
                    + UsageRecordDb.output_tokens * rates["output"]
                )
                / 1_000_000.0,
            )
            for model, rates in sorted(pricing.PRICING.items(), key=lambda item: len(item[0]), reverse=True)
        ],
        else_=0.0,
    )
    effective_cost = func.coalesce(UsageRecordDb.billed_cost_usd, sql_cost)
    rows = (
        db.query(
            group_column,
            func.coalesce(func.sum(effective_cost), 0.0),
            func.coalesce(
                func.sum(effective_cost).filter(UsageRecordDb.created_at >= start),
                0.0,
            ),
        )
        .filter(group_column.in_(ids))
        .group_by(group_column)
        .all()
    )
    result = {owner_id: [float(total or 0), float(monthly or 0)] for owner_id, total, monthly in rows}
    return {owner_id: (values[0], values[1]) for owner_id, values in result.items()}
