# Path: app/utils/models/api/stats.py
# Description: Pydantic models for the dashboard statistics routes (overview, usage log, and activity analytics).

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import Query
from pydantic import BaseModel

# Shared time-range filter -----------------------------------------------------


class TimeRange(BaseModel):
    """An optional [start, end) window over UsageRecord.created_at, parsed from query params.

    Both bounds are optional: each endpoint applies its own sensible default when a bound is
    omitted (e.g. overview falls back to month-to-date, activity to the last 30 days).
    """

    start: Optional[datetime]
    end: Optional[datetime]
    user_id: Optional[uuid.UUID]
    model: Optional[str]

    @classmethod
    def get_request(
        cls,
        start: Optional[datetime] = Query(default=None, description="Inclusive lower bound (ISO 8601, UTC)."),
        end: Optional[datetime] = Query(default=None, description="Exclusive upper bound (ISO 8601, UTC)."),
        user_id: Optional[uuid.UUID] = Query(default=None, description="Filter to a single user."),
        model: Optional[str] = Query(default=None, description="Filter to a single effective model."),
    ) -> TimeRange:
        return cls(start=start, end=end, user_id=user_id, model=model)


class UsageRecord(BaseModel):
    """One served (or attempted) request and its token attribution."""

    id: uuid.UUID
    user_id: uuid.UUID
    user_name: Optional[str]
    api_key_id: Optional[uuid.UUID]
    api_key_label: Optional[str]
    account_id: Optional[uuid.UUID]
    account_label: Optional[str]
    fallback_provider_id: Optional[uuid.UUID]
    fallback_provider_label: Optional[str]
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    reasoning_level: Optional[str]
    request_mode: str
    cost_usd: float  # API-equivalent cost of this request (what it would cost on the pay-as-you-go API).
    status_code: Optional[int]
    created_at: datetime
    codex_session_id: Optional[str] = None
    codex_thread_id: Optional[str] = None
    codex_turn_id: Optional[str] = None
    codex_root_turn_id: Optional[str] = None

    @classmethod
    def from_db(
        cls,
        record_db,
        user_name: Optional[str],
        api_key_label: Optional[str],
        account_label: Optional[str],
        fallback_provider_label: Optional[str] = None,
    ) -> UsageRecord:
        from app import pricing

        return cls(
            id=record_db.id,
            user_id=record_db.user_id,
            user_name=user_name,
            api_key_id=record_db.api_key_id,
            api_key_label=api_key_label,
            account_id=record_db.account_id,
            account_label=account_label,
            fallback_provider_id=record_db.fallback_provider_id,
            fallback_provider_label=fallback_provider_label,
            model=record_db.model,
            input_tokens=record_db.input_tokens,
            output_tokens=record_db.output_tokens,
            cached_input_tokens=record_db.cached_input_tokens,
            cache_write_tokens=record_db.cache_write_tokens,
            reasoning_level=record_db.reasoning_level,
            request_mode=record_db.request_mode,
            cost_usd=(float(record_db.billed_cost_usd) if record_db.billed_cost_usd is not None else pricing.cost_for_record(record_db)),
            status_code=record_db.status_code,
            codex_session_id=record_db.codex_session_id,
            codex_thread_id=record_db.codex_thread_id,
            codex_turn_id=record_db.codex_turn_id,
            codex_root_turn_id=record_db.codex_root_turn_id,
            created_at=record_db.created_at,
        )


# GET /stats/usage


class ListUsageRequest(BaseModel):
    limit: int
    offset: int
    start: Optional[datetime]
    end: Optional[datetime]
    user_id: Optional[uuid.UUID]
    model: Optional[str]

    @classmethod
    def get_request(
        cls,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        start: Optional[datetime] = Query(default=None, description="Inclusive lower bound (ISO 8601, UTC)."),
        end: Optional[datetime] = Query(default=None, description="Exclusive upper bound (ISO 8601, UTC)."),
        user_id: Optional[uuid.UUID] = Query(default=None, description="Filter to a single user."),
        model: Optional[str] = Query(default=None, description="Filter to a single effective model."),
    ) -> ListUsageRequest:
        return cls(limit=limit, offset=offset, start=start, end=end, user_id=user_id, model=model)


class ListUsageResponse(BaseModel):
    items: List[UsageRecord]
    total: int
    limit: int
    offset: int


# GET /stats/overview


class OverviewResponse(BaseModel):
    # Pool inventory -- not range-dependent.
    total_accounts: int
    active_accounts: int
    usable_accounts: int
    five_hour_average_pct: float
    weekly_average_pct: float
    # Current active-pool capacity (0..1), independent of the selected analytics range.
    pool_used_pct: float
    pool_remaining_pct: float
    total_users: int
    active_users: int
    total_keys: int
    # Usage over the selected range (defaults to month-to-date when no range is given).
    tokens: int  # input + output only
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    input_output_ratio: Optional[float]  # input tokens per output token; null when there is no output
    cache_hit_rate: float  # cached input / all input, normalized to 0..1
    input_rate_pct: float  # input / (input + output), expressed as 0..100
    output_rate_pct: float  # output / (input + output), expressed as 0..100
    cache_hit_rate_pct: float  # cached input / all input, expressed as 0..100
    # API-equivalent value of the range's usage (what it would cost on the pay-as-you-go API). ROI signal, not owed.
    api_equivalent_cost_usd: float
    requests: int


# GET /stats/activity


class ActivityPoint(BaseModel):
    ts: str  # ISO 8601 UTC bucket start ("2026-06-21T00:00:00+00:00" for a day, "...T14:00:00+00:00" for an hour)
    requests: int
    tokens: int


class ActivityResponse(BaseModel):
    granularity: str  # "hour" or "day" -- chosen from the range span so short windows aren't a single bar.
    points: List[ActivityPoint]


# GET /stats/hourly


class HourlyUserSlice(BaseModel):
    user_id: uuid.UUID
    user_name: str
    requests: int


class HourlyPoint(BaseModel):
    hour: int  # 0..23 (UTC)
    requests: int
    tokens: int
    by_user: List[HourlyUserSlice] = []


class HourlyUser(BaseModel):
    user_id: uuid.UUID
    user_name: str


class HourlyResponse(BaseModel):
    points: List[HourlyPoint]
    users: List[HourlyUser] = []


# GET /stats/by-user


class UserUsage(BaseModel):
    user_id: uuid.UUID
    user_name: Optional[str]
    requests: int
    tokens: int
    input_tokens: int
    output_tokens: int
    last_used_at: Optional[datetime]


class ByUserResponse(BaseModel):
    users: List[UserUsage]


# GET /stats/model-mix


class ModelSlice(BaseModel):
    model: str
    requests: int
    input_tokens: int
    output_tokens: int


class UserModelMix(BaseModel):
    user_id: uuid.UUID
    user_name: Optional[str]
    total_requests: int
    models: List[ModelSlice]


class ModelMixResponse(BaseModel):
    users: List[UserModelMix]


# GET /stats/thinking-level-mix


class ThinkingLevelSlice(BaseModel):
    thinking_level: Optional[str]
    requests: int
    input_tokens: int
    output_tokens: int


class UserThinkingLevelMix(BaseModel):
    user_id: uuid.UUID
    user_name: Optional[str]
    total_requests: int
    thinking_levels: List[ThinkingLevelSlice]


class ThinkingLevelMixResponse(BaseModel):
    users: List[UserThinkingLevelMix]


# GET /stats/distributions


class PercentileBreakdown(BaseModel):
    min: int
    p25: int
    p50: int
    p75: int
    p90: int
    p95: int
    max: int


class DistributionResponse(BaseModel):
    input_tokens: PercentileBreakdown
    output_tokens: PercentileBreakdown
    total_requests: int
