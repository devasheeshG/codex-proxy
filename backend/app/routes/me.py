# Path: app/routes/me.py
# Description: Self-service endpoint for an API key holder -- their own token/request usage and the pool headroom their
#              next request will hit. Authenticated by the usr_ key itself rather than the admin session.

from datetime import datetime, timezone
from typing import Sequence

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.utils import rotation, security, usage
from app.utils.models.api import MeUsageResponse, PoolAccountStatus, PoolStatus, PoolWindow
from app.utils.postgres import AccountDb, ApiKeyDb, UsageRecordDb, UserDb, get_db

router = APIRouter(tags=["Me"], prefix="/me")


def _aggregate_window(
    accounts: Sequence[AccountDb],
    usage_attribute: str,
    reset_attribute: str,
    now: datetime,
) -> PoolWindow:
    """Aggregate one quota window while preserving unknowns and every reset."""
    values: list[float] = []
    resets: list[datetime] = []
    upcoming_resets: list[datetime] = []
    for account in accounts:
        value = getattr(account, usage_attribute)
        if value is not None:
            values.append(max(0.0, min(1.0, float(value))))
        reset_at = getattr(account, reset_attribute)
        if reset_at is None:
            continue
        if reset_at.tzinfo is None:
            reset_at = reset_at.replace(tzinfo=timezone.utc)
        resets.append(reset_at)
        if reset_at > now:
            upcoming_resets.append(reset_at)
    average = round(sum(values) / len(values), 6) if values else None
    return PoolWindow(
        used_pct=average,
        known_account_count=len(values),
        unknown_account_count=len(accounts) - len(values),
        reset_at=sorted(resets),
        next_reset_at=min(upcoming_resets, default=None),
    )


def build_pool_status(db: Session, now: datetime | None = None) -> PoolStatus | None:
    """Build the shared-pool quota snapshot used by both ``/me/usage`` and proxy headers.

    Keeping this calculation in one place prevents inference responses from exposing
    the selected account's private quota while ``/me/usage`` reports a pool average.
    """
    now = now or datetime.now(timezone.utc)
    all_accounts = db.query(AccountDb).all()
    available_accounts = [account for account in all_accounts if rotation.is_available(account, now)]
    # Keep telemetry visible while the whole pool is exhausted/cooling down;
    # disabled and reauthentication-required accounts do not contribute capacity.
    if not available_accounts:
        available_accounts = [
            account for account in all_accounts if account.status.value != "DISABLED" and account.provider_health.value != "REAUTH_REQUIRED"
        ]
    if not available_accounts:
        return None

    five_hour = _aggregate_window(
        available_accounts,
        "five_hour_used_pct",
        "five_hour_reset_at",
        now,
    )
    weekly = _aggregate_window(
        available_accounts,
        "weekly_used_pct",
        "weekly_reset_at",
        now,
    )
    return PoolStatus(
        account_count=len(available_accounts),
        five_hour=five_hour,
        weekly=weekly,
        accounts=[
            PoolAccountStatus(
                id=account.id,
                label=account.label,
                email=account.account_email,
                priority=account.priority,
                five_hour_used_pct=account.five_hour_used_pct,
                five_hour_reset_at=account.five_hour_reset_at,
                weekly_used_pct=account.weekly_used_pct,
                weekly_reset_at=account.weekly_reset_at,
            )
            for account in sorted(available_accounts, key=rotation.account_selection_key)
        ],
    )


@router.get(
    "/usage",
    response_model=MeUsageResponse,
    responses={
        200: {"description": "Caller's own usage and current pool headroom"},
        401: {"description": "Missing or invalid API key"},
        403: {"description": "API key or user is disabled"},
    },
)
def my_usage(
    key: ApiKeyDb = Depends(security.authenticate_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> MeUsageResponse:
    """Report the calling key's usage and average headroom across the currently available pool."""
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    user = db.query(UserDb).filter(UserDb.id == key.user_id).first()

    tokens_today, requests_today = (
        db.query(usage.token_sum_expr(), func.count(UsageRecordDb.id))
        .filter(UsageRecordDb.api_key_id == key.id, UsageRecordDb.created_at >= day_start)
        .one()
    )
    tokens_month, requests_month = (
        db.query(usage.token_sum_expr(), func.count(UsageRecordDb.id))
        .filter(UsageRecordDb.api_key_id == key.id, UsageRecordDb.created_at >= month_start)
        .one()
    )
    tokens_today, requests_today = int(tokens_today or 0), int(requests_today or 0)
    tokens_month, requests_month = int(tokens_month or 0), int(requests_month or 0)

    remaining = None
    if key.monthly_token_budget and key.monthly_token_budget > 0:
        remaining = max(key.monthly_token_budget - tokens_month, 0)

    pool = build_pool_status(db, now)

    return MeUsageResponse(
        user=user.name if user is not None else "",
        key_label=key.label,
        requests_today=requests_today,
        tokens_today=tokens_today,
        requests_this_month=requests_month,
        tokens_this_month=tokens_month,
        monthly_token_budget=key.monthly_token_budget,
        tokens_remaining_this_month=remaining,
        pool=pool,
    )
