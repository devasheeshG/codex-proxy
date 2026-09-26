# Path: app/routes/stats.py
# Description: Dashboard statistics -- overview counts, the paginated usage log, and activity analytics
#              (daily/hourly series, hour-of-day distribution, per-user rollups). All accept an optional
#              [start, end) range over UsageRecord.created_at; each endpoint applies a sensible default
#              when a bound is omitted. Admin-only.

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends
from sqlalchemy import JSON, and_, cast, func, select
from sqlalchemy.orm import Session

from app import pricing
from app.logger import get_logger
from app.utils import rotation, security, usage
from app.utils.models.api import (
    AccountStatus,
    ActivityPoint,
    ActivityResponse,
    ByUserResponse,
    DistributionResponse,
    HourlyPoint,
    HourlyResponse,
    HourlyUser,
    HourlyUserSlice,
    ListUsageRequest,
    ListUsageResponse,
    OverviewResponse,
    PercentileBreakdown,
    ProviderHealth,
    TimeRange,
    UsageRecord,
    UserUsage,
)
from app.utils.models.api.stats import (
    ModelMixResponse,
    ModelSlice,
    ThinkingLevelMixResponse,
    ThinkingLevelSlice,
    UserModelMix,
    UserThinkingLevelMix,
)
from app.utils.postgres import AccountDb, ApiKeyDb, OpenAIFallbackDb, ProxyEventDb, UsageRecordDb, UserDb, get_db

# Get the logger
logger = get_logger()

router = APIRouter(tags=["Stats"], prefix="/stats")


def _tokens_sum():
    return usage.token_sum_expr()


def _routed_model_expr():
    """Use the proxy's routed model for presentation, falling back for legacy rows.

    Usage records intentionally retain the model label reported by the provider
    because pricing and accounting consume that value. Dashboard model analytics
    must instead describe what the proxy routed, which is stored on the terminal
    response event.
    """
    event_model = (
        select(func.json_extract_path_text(cast(ProxyEventDb.metadata_json, JSON), "model"))
        .where(
            ProxyEventDb.request_id == UsageRecordDb.request_id,
            ProxyEventDb.event_type == "response.returned",
        )
        .order_by(ProxyEventDb.created_at.desc())
        .limit(1)
        .correlate(UsageRecordDb)
        .scalar_subquery()
    )
    return func.coalesce(event_model, UsageRecordDb.model)


def _scope_clauses(
    start: Optional[datetime],
    end: Optional[datetime],
    user_id: Optional[uuid.UUID] = None,
    model: Optional[str] = None,
) -> List:
    """WHERE clauses scoping the stats queries: the [start, end) window (each bound optional) plus an optional
    user filter. count_tokens calls are no longer recorded so no model-null exclusion is needed.
    """
    clauses: List = []
    if start is not None:
        clauses.append(UsageRecordDb.created_at >= start)
    if end is not None:
        clauses.append(UsageRecordDb.created_at < end)
    if user_id is not None:
        clauses.append(UsageRecordDb.user_id == user_id)
    if model:
        clauses.append(UsageRecordDb.model == model.strip())
    return clauses


@router.get(
    "/overview",
    response_model=OverviewResponse,
    responses={
        200: {"description": "Overview retrieved successfully"},
        401: {"description": "Admin authentication required"},
    },
)
def overview(
    rng: TimeRange = Depends(TimeRange.get_request),  # noqa: B008
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> OverviewResponse:
    """Headline account/user/key counts plus tokens, request volume, and API-equivalent value over the range.

    With no range the usage figures default to month-to-date. The account/user/key counts are pool inventory and
    are never range-filtered.
    """
    now = datetime.now(timezone.utc)
    start, end = rng.start, rng.end
    if start is None and end is None:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    clauses = _scope_clauses(start, end, rng.user_id, rng.model)

    requests = db.query(UsageRecordDb).filter(*clauses).count()

    # Per-model token sums over the range, including cache-read/write subsets.
    rows = (
        db.query(
            UsageRecordDb.model,
            func.coalesce(func.sum(UsageRecordDb.input_tokens), 0),
            func.coalesce(func.sum(UsageRecordDb.output_tokens), 0),
            func.coalesce(func.sum(UsageRecordDb.cached_input_tokens), 0),
            func.coalesce(func.sum(UsageRecordDb.cache_write_tokens), 0),
        )
        .filter(*clauses)
        .group_by(UsageRecordDb.model)
        .all()
    )

    in_tok = out_tok = cached_in_tok = 0
    cost = 0.0
    for model, mi, mo, cached, cache_write in rows:
        mi, mo, cached, cache_write = int(mi), int(mo), int(cached), int(cache_write)
        in_tok += mi
        out_tok += mo
        cached_in_tok += max(0, cached)
        cost += pricing.cost_usd(
            model=model,
            input_tokens=mi,
            output_tokens=mo,
            cached_input_tokens=cached,
            cache_write_tokens=cache_write,
        )

    accounts = db.query(AccountDb).all()
    # Dashboard inventory counts every enabled account with valid authentication,
    # including accounts in a temporary cooldown. Immediate routing eligibility is
    # reported separately through usable_accounts.
    active_accounts = [
        account for account in accounts if account.status != AccountStatus.DISABLED and account.provider_health != ProviderHealth.REAUTH_REQUIRED
    ]
    usable_accounts = [account for account in accounts if rotation.is_usable(account, now)]

    def average_window_pct(field: str) -> float:
        values = [getattr(account, field) for account in active_accounts if getattr(account, field) is not None]
        return round(sum(values) / len(values) * 100.0, 2) if values else 0.0

    five_hour_average_pct = average_window_pct("five_hour_used_pct")
    weekly_average_pct = average_window_pct("weekly_used_pct")
    if active_accounts:
        pool_used_pct = round(
            sum(max(0.0, min(1.0, rotation.account_load(account))) for account in active_accounts) / len(active_accounts),
            6,
        )
        pool_remaining_pct = round(1.0 - pool_used_pct, 6)
    else:
        pool_used_pct = 0.0
        pool_remaining_pct = 0.0

    total_flow = in_tok + out_tok
    input_rate_pct = round(in_tok / total_flow * 100.0, 2) if total_flow > 0 else 0.0
    output_rate_pct = round(out_tok / total_flow * 100.0, 2) if total_flow > 0 else 0.0
    cache_hit_rate = min(cached_in_tok, in_tok) / in_tok if in_tok > 0 else 0.0

    return OverviewResponse(
        total_accounts=len(accounts),
        active_accounts=len(active_accounts),
        usable_accounts=len(usable_accounts),
        five_hour_average_pct=five_hour_average_pct,
        weekly_average_pct=weekly_average_pct,
        pool_used_pct=pool_used_pct,
        pool_remaining_pct=pool_remaining_pct,
        total_users=db.query(UserDb).count(),
        active_users=db.query(UserDb).filter(UserDb.active.is_(True)).count(),
        total_keys=db.query(ApiKeyDb).count(),
        tokens=in_tok + out_tok,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cached_input_tokens=cached_in_tok,
        input_output_ratio=round(in_tok / out_tok, 4) if out_tok > 0 else None,
        cache_hit_rate=round(cache_hit_rate, 6),
        input_rate_pct=input_rate_pct,
        output_rate_pct=output_rate_pct,
        cache_hit_rate_pct=round(cache_hit_rate * 100.0, 2),
        api_equivalent_cost_usd=round(cost, 2),
        requests=requests,
    )


def _usage_rows(db: Session, rng: TimeRange, limit: Optional[int] = None, offset: int = 0):
    """Usage-log rows (newest first) joined to user/key/account labels, filtered to the range."""
    query = (
        db.query(
            UsageRecordDb,
            UserDb.name,
            ApiKeyDb.label,
            AccountDb.label,
            OpenAIFallbackDb.label,
        )
        .outerjoin(UserDb, UsageRecordDb.user_id == UserDb.id)
        .outerjoin(ApiKeyDb, UsageRecordDb.api_key_id == ApiKeyDb.id)
        .outerjoin(AccountDb, UsageRecordDb.account_id == AccountDb.id)
        .outerjoin(OpenAIFallbackDb, UsageRecordDb.fallback_provider_id == OpenAIFallbackDb.id)
        .filter(*_scope_clauses(rng.start, rng.end, rng.user_id, rng.model))
        .order_by(UsageRecordDb.created_at.desc())
        .offset(offset)
    )
    if limit is not None:
        query = query.limit(limit)
    return query.all()


@router.get(
    "/usage",
    response_model=ListUsageResponse,
    responses={
        200: {"description": "Usage log retrieved successfully"},
        401: {"description": "Admin authentication required"},
    },
)
def usage_log(
    request: ListUsageRequest = Depends(ListUsageRequest.get_request),  # noqa: B008
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ListUsageResponse:
    """Request log over the range, newest first, each row carrying the user name, key label, and account label."""
    rng = TimeRange(start=request.start, end=request.end, user_id=request.user_id, model=request.model)
    total = db.query(UsageRecordDb).filter(*_scope_clauses(rng.start, rng.end, rng.user_id, rng.model)).count()
    rows = _usage_rows(db, rng, limit=request.limit, offset=request.offset)
    items = [
        UsageRecord.from_db(
            record,
            user_name,
            key_label,
            account_label,
            fallback_provider_label,
        )
        for record, user_name, key_label, account_label, fallback_provider_label in rows
    ]
    return ListUsageResponse(items=items, total=total, limit=request.limit, offset=request.offset)


@router.get(
    "/activity",
    response_model=ActivityResponse,
    responses={
        200: {"description": "Activity series retrieved successfully"},
        401: {"description": "Admin authentication required"},
    },
)
def activity(
    rng: TimeRange = Depends(TimeRange.get_request),  # noqa: B008
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ActivityResponse:
    """Requests and tokens bucketed over the range, zero-filled. Short windows (<=2 days) bucket by hour, longer by day."""

    def _utc(dt: datetime) -> datetime:
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    end = _utc(rng.end) if rng.end is not None else now
    if rng.start is not None:
        start = _utc(rng.start)
    elif rng.end is not None:
        # An explicit end with no start is the dashboard's "All time" range. Anchor the
        # series at the first matching request instead of silently truncating it to 30 days.
        earliest = db.query(func.min(UsageRecordDb.created_at)).filter(*_scope_clauses(None, end, rng.user_id, rng.model)).scalar()
        start = _utc(earliest) if earliest is not None else end - timedelta(days=1)
    else:
        start = end - timedelta(days=30)
    if end <= start:
        end = start + timedelta(days=1)

    # Pick a granularity so a short window isn't collapsed into one or two bars.
    by_hour = (end - start) <= timedelta(days=2)
    granularity = "hour" if by_hour else "day"
    unit = timedelta(hours=1) if by_hour else timedelta(days=1)

    def floor(dt: datetime) -> datetime:
        if by_hour:
            return dt.replace(minute=0, second=0, microsecond=0)
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    bucket_col = func.date_trunc("hour" if by_hour else "day", UsageRecordDb.created_at)
    rows = (
        db.query(bucket_col.label("bucket"), _tokens_sum().label("tokens"), func.count(UsageRecordDb.id).label("requests"))
        .filter(*_scope_clauses(start, end, rng.user_id, rng.model))
        .group_by(bucket_col)
        .all()
    )

    def key(dt: datetime) -> datetime:
        # Normalise to a naive-UTC bucket key so DB rows (which may be tz-naive) and our walk line up.
        return floor(dt).replace(tzinfo=None)

    by_bucket = {key(row.bucket): (int(row.tokens or 0), int(row.requests or 0)) for row in rows}

    points = []
    cursor = floor(start)
    while cursor < end:
        tokens, requests = by_bucket.get(cursor.replace(tzinfo=None), (0, 0))
        points.append(ActivityPoint(ts=cursor.astimezone(timezone.utc).isoformat(), requests=requests, tokens=tokens))
        cursor = cursor + unit

    return ActivityResponse(granularity=granularity, points=points)


@router.get(
    "/hourly",
    response_model=HourlyResponse,
    responses={
        200: {"description": "Hour-of-day distribution retrieved successfully"},
        401: {"description": "Admin authentication required"},
    },
)
def hourly(
    rng: TimeRange = Depends(TimeRange.get_request),  # noqa: B008
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> HourlyResponse:
    """Request and token totals bucketed by hour of day (0..23, UTC) over the range -- shows peak hours.

    Includes a per-user breakdown so the frontend can render stacked bars.
    """
    hour_col = func.extract("hour", UsageRecordDb.created_at)
    clauses = _scope_clauses(rng.start, rng.end, rng.user_id, rng.model)

    totals = (
        db.query(hour_col.label("hour"), _tokens_sum().label("tokens"), func.count(UsageRecordDb.id).label("requests"))
        .filter(*clauses)
        .group_by(hour_col)
        .all()
    )
    by_hour = {int(row.hour): (int(row.tokens or 0), int(row.requests or 0)) for row in totals}

    per_user_rows = (
        db.query(
            hour_col.label("hour"),
            UserDb.id.label("user_id"),
            UserDb.name.label("user_name"),
            func.count(UsageRecordDb.id).label("requests"),
        )
        .join(UserDb, UsageRecordDb.user_id == UserDb.id)
        .filter(*clauses)
        .group_by(hour_col, UserDb.id, UserDb.name)
        .all()
    )

    user_hour: Dict[Tuple[int, str], HourlyUserSlice] = {}
    seen_users: Dict[str, str] = {}
    for row in per_user_rows:
        h = int(row.hour)
        uid = str(row.user_id)
        seen_users[uid] = row.user_name
        user_hour[(h, uid)] = HourlyUserSlice(user_id=row.user_id, user_name=row.user_name, requests=int(row.requests or 0))

    user_roster = sorted(seen_users.items(), key=lambda kv: kv[1])

    points = []
    for hour in range(24):
        tokens, requests = by_hour.get(hour, (0, 0))
        slices = [user_hour[(hour, uid)] for uid, _ in user_roster if (hour, uid) in user_hour]
        points.append(HourlyPoint(hour=hour, requests=requests, tokens=tokens, by_user=slices))

    users = [HourlyUser(user_id=uuid.UUID(uid), user_name=name) for uid, name in user_roster]
    return HourlyResponse(points=points, users=users)


@router.get(
    "/by-user",
    response_model=ByUserResponse,
    responses={
        200: {"description": "Per-user usage retrieved successfully"},
        401: {"description": "Admin authentication required"},
    },
)
def by_user(
    rng: TimeRange = Depends(TimeRange.get_request),  # noqa: B008
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ByUserResponse:
    """Per-user totals (tokens, requests, last activity) over the range, busiest first.

    The range condition lives in the JOIN (not WHERE) so every user is still listed when no user filter is selected,
    with sums limited to the window. A user filter narrows the roster itself to prevent unrelated zero-usage users.
    """
    join_on = and_(UsageRecordDb.user_id == UserDb.id, *_scope_clauses(rng.start, rng.end, rng.user_id, rng.model))
    query = db.query(
        UserDb.id.label("user_id"),
        UserDb.name.label("user_name"),
        _tokens_sum().label("tokens"),
        func.count(UsageRecordDb.id).label("requests"),
        func.max(UsageRecordDb.created_at).label("last_used_at"),
        func.coalesce(func.sum(UsageRecordDb.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(UsageRecordDb.output_tokens), 0).label("output_tokens"),
    ).outerjoin(UsageRecordDb, join_on)
    if rng.user_id is not None:
        query = query.filter(UserDb.id == rng.user_id)
    rows = query.group_by(UserDb.id, UserDb.name).order_by(_tokens_sum().desc()).all()

    users = [
        UserUsage(
            user_id=row.user_id,
            user_name=row.user_name,
            requests=int(row.requests or 0),
            tokens=int(row.tokens or 0),
            input_tokens=int(row.input_tokens or 0),
            output_tokens=int(row.output_tokens or 0),
            last_used_at=row.last_used_at,
        )
        for row in rows
    ]
    return ByUserResponse(users=users)


@router.get(
    "/distributions",
    response_model=DistributionResponse,
    responses={
        200: {"description": "Token distribution retrieved successfully"},
        401: {"description": "Admin authentication required"},
    },
)
def distributions(
    rng: TimeRange = Depends(TimeRange.get_request),  # noqa: B008
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> DistributionResponse:
    """Percentile breakdown (min, P25, P50, P75, P90, P95, max) of per-request input and output tokens over the range."""
    clauses = _scope_clauses(rng.start, rng.end, rng.user_id, rng.model)

    row = (
        db.query(
            func.count(UsageRecordDb.id).label("total"),
            func.coalesce(func.min(UsageRecordDb.input_tokens), 0),
            func.coalesce(func.percentile_cont(0.25).within_group(UsageRecordDb.input_tokens), 0),
            func.coalesce(func.percentile_cont(0.50).within_group(UsageRecordDb.input_tokens), 0),
            func.coalesce(func.percentile_cont(0.75).within_group(UsageRecordDb.input_tokens), 0),
            func.coalesce(func.percentile_cont(0.90).within_group(UsageRecordDb.input_tokens), 0),
            func.coalesce(func.percentile_cont(0.95).within_group(UsageRecordDb.input_tokens), 0),
            func.coalesce(func.max(UsageRecordDb.input_tokens), 0),
            func.coalesce(func.min(UsageRecordDb.output_tokens), 0),
            func.coalesce(func.percentile_cont(0.25).within_group(UsageRecordDb.output_tokens), 0),
            func.coalesce(func.percentile_cont(0.50).within_group(UsageRecordDb.output_tokens), 0),
            func.coalesce(func.percentile_cont(0.75).within_group(UsageRecordDb.output_tokens), 0),
            func.coalesce(func.percentile_cont(0.90).within_group(UsageRecordDb.output_tokens), 0),
            func.coalesce(func.percentile_cont(0.95).within_group(UsageRecordDb.output_tokens), 0),
            func.coalesce(func.max(UsageRecordDb.output_tokens), 0),
        )
        .filter(*clauses)
        .one()
    )

    total = int(row[0])
    zero = PercentileBreakdown(min=0, p25=0, p50=0, p75=0, p90=0, p95=0, max=0)
    if total == 0:
        return DistributionResponse(input_tokens=zero, output_tokens=zero, total_requests=0)

    return DistributionResponse(
        input_tokens=PercentileBreakdown(
            min=int(row[1]),
            p25=int(row[2]),
            p50=int(row[3]),
            p75=int(row[4]),
            p90=int(row[5]),
            p95=int(row[6]),
            max=int(row[7]),
        ),
        output_tokens=PercentileBreakdown(
            min=int(row[8]),
            p25=int(row[9]),
            p50=int(row[10]),
            p75=int(row[11]),
            p90=int(row[12]),
            p95=int(row[13]),
            max=int(row[14]),
        ),
        total_requests=total,
    )


@router.get(
    "/model-mix",
    response_model=ModelMixResponse,
    responses={
        200: {"description": "Per-user model breakdown retrieved successfully"},
        401: {"description": "Admin authentication required"},
    },
)
def model_mix(
    rng: TimeRange = Depends(TimeRange.get_request),  # noqa: B008
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ModelMixResponse:
    """Per-user model breakdown (requests, input/output tokens) over the range, busiest model first per user."""
    routed_model = _routed_model_expr()
    scope = _scope_clauses(rng.start, rng.end, rng.user_id)
    if rng.model:
        scope.append(routed_model == rng.model.strip())
    join_on = and_(UsageRecordDb.user_id == UserDb.id, *scope)
    query = db.query(
        UserDb.id.label("user_id"),
        UserDb.name.label("user_name"),
        routed_model.label("model"),
        func.count(UsageRecordDb.id).label("requests"),
        func.coalesce(func.sum(UsageRecordDb.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(UsageRecordDb.output_tokens), 0).label("output_tokens"),
    ).outerjoin(UsageRecordDb, join_on)
    if rng.user_id is not None:
        query = query.filter(UserDb.id == rng.user_id)
    rows = query.group_by(UserDb.id, UserDb.name, routed_model).order_by(UserDb.name, func.count(UsageRecordDb.id).desc()).all()

    users_map: dict = {}
    for row in rows:
        uid = str(row.user_id)
        if uid not in users_map:
            users_map[uid] = UserModelMix(
                user_id=row.user_id,
                user_name=row.user_name,
                total_requests=0,
                models=[],
            )
        reqs = int(row.requests or 0)
        if row.model is not None and reqs > 0:
            users_map[uid].total_requests += reqs
            users_map[uid].models.append(
                ModelSlice(
                    model=row.model,
                    requests=reqs,
                    input_tokens=int(row.input_tokens or 0),
                    output_tokens=int(row.output_tokens or 0),
                )
            )

    users = sorted(users_map.values(), key=lambda u: u.total_requests, reverse=True)
    return ModelMixResponse(users=users)


@router.get(
    "/thinking-level-mix",
    response_model=ThinkingLevelMixResponse,
    responses={
        200: {"description": "Per-user thinking-level breakdown retrieved successfully"},
        401: {"description": "Admin authentication required"},
    },
)
def thinking_level_mix(
    rng: TimeRange = Depends(TimeRange.get_request),  # noqa: B008
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ThinkingLevelMixResponse:
    """Per-user thinking-level breakdown (request and token totals) over the selected range.

    A null thinking level is retained as its own bucket so the totals include older requests and
    clients that did not report an effort level.
    """
    normalized_level = func.nullif(func.lower(func.trim(UsageRecordDb.reasoning_level)), "")
    rows = (
        db.query(
            UserDb.id.label("user_id"),
            UserDb.name.label("user_name"),
            normalized_level.label("thinking_level"),
            func.count(UsageRecordDb.id).label("requests"),
            func.coalesce(func.sum(UsageRecordDb.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(UsageRecordDb.output_tokens), 0).label("output_tokens"),
        )
        .join(UserDb, UsageRecordDb.user_id == UserDb.id)
        .filter(*_scope_clauses(rng.start, rng.end, rng.user_id, rng.model))
        .group_by(UserDb.id, UserDb.name, normalized_level)
        .order_by(UserDb.name, func.count(UsageRecordDb.id).desc(), normalized_level)
        .all()
    )

    users_map: Dict[str, UserThinkingLevelMix] = {}
    for row in rows:
        uid = str(row.user_id)
        if uid not in users_map:
            users_map[uid] = UserThinkingLevelMix(
                user_id=row.user_id,
                user_name=row.user_name,
                total_requests=0,
                thinking_levels=[],
            )
        reqs = int(row.requests or 0)
        users_map[uid].total_requests += reqs
        users_map[uid].thinking_levels.append(
            ThinkingLevelSlice(
                thinking_level=row.thinking_level,
                requests=reqs,
                input_tokens=int(row.input_tokens or 0),
                output_tokens=int(row.output_tokens or 0),
            )
        )

    users = sorted(
        users_map.values(),
        key=lambda user: (-user.total_requests, (user.user_name or "").lower()),
    )
    return ThinkingLevelMixResponse(users=users)
