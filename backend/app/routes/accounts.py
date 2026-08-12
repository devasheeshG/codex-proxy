# Path: app/routes/accounts.py
# Description: Admin routes for pooled Codex accounts, quota, device login, and banked limit resets.

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.logger import get_logger
from app.utils import crypto, notifications, oauth, provider_health, rotation, security, usage, warmup
from app.utils.models.api import (
    Account,
    AccountResponse,
    AccountStatus,
    BulkAccountPriorityRequest,
    ConsumeLimitResetRequest,
    ConsumeLimitResetResponse,
    ListAccountsResponse,
    OAuthCompleteRequest,
    OAuthStartResponse,
    RateLimitResetCredit,
    RateLimitResetCreditsResponse,
    ReauthCompleteRequest,
    ReorderAccountsRequest,
    UpdateAccountRequest,
)
from app.utils.postgres import AccountDb, UsageRecordDb, get_db

logger = get_logger()
router = APIRouter(tags=["Accounts"], prefix="/accounts")

_DEVICE_FLOW_TTL = timedelta(minutes=15)
_LIMIT_RESET_MIN_WEEKLY_USAGE = 0.90
_LIMIT_RESET_EXPIRY_WINDOW = timedelta(hours=12)
_WORKSPACE_PLAN_TYPES = frozenset({"team", "business"})


def _clean_optional_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _is_workspace_plan(tier: object) -> bool:
    return isinstance(tier, str) and tier.strip().lower() in _WORKSPACE_PLAN_TYPES


def _duplicate_identity_account(
    db: Session,
    tokens: dict,
    *,
    exclude_account_id: uuid.UUID | None = None,
) -> AccountDb | None:
    """Find the same ChatGPT member in the same workspace using stable fallbacks."""
    conditions = []
    account_user_id = _clean_optional_name(tokens.get("account_user_id"))
    workspace_id = _clean_optional_name(tokens.get("account_id"))
    user_id = _clean_optional_name(tokens.get("user_id"))
    email = _clean_optional_name(tokens.get("email"))
    if account_user_id:
        conditions.append(AccountDb.chatgpt_account_user_id == account_user_id)
    if workspace_id and user_id:
        conditions.append(and_(AccountDb.chatgpt_account_id == workspace_id, AccountDb.chatgpt_user_id == user_id))
    if workspace_id and email:
        conditions.append(
            and_(
                AccountDb.chatgpt_account_id == workspace_id,
                func.lower(AccountDb.account_email) == email.casefold(),
            )
        )
    if not conditions:
        return None
    query = db.query(AccountDb).filter(or_(*conditions))
    if exclude_account_id is not None:
        query = query.filter(AccountDb.id != exclude_account_id)
    return query.first()


def _assert_unique_identity(
    db: Session,
    tokens: dict,
    *,
    exclude_account_id: uuid.UUID | None = None,
) -> None:
    duplicate = _duplicate_identity_account(db, tokens, exclude_account_id=exclude_account_id)
    if duplicate is None:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"This ChatGPT user is already linked to this workspace as '{duplicate.label}'. Re-authenticate that entry instead of adding a duplicate."
        ),
    )


def _workspace_name_for_tokens(
    db: Session,
    tokens: dict,
    *,
    requested_name: object = None,
    current_account: AccountDb | None = None,
) -> str | None:
    if not _is_workspace_plan(tokens.get("tier")):
        if _clean_optional_name(requested_name):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A Team/workspace name can only be set for a Team or Business subscription.",
            )
        return None

    requested = _clean_optional_name(requested_name)
    if requested:
        return requested
    claimed = _clean_optional_name(tokens.get("workspace_name"))
    if claimed:
        return claimed

    workspace_id = _clean_optional_name(tokens.get("account_id"))
    if current_account is not None and current_account.chatgpt_account_id == workspace_id:
        current_name = _clean_optional_name(current_account.workspace_name)
        if current_name:
            return current_name
    if not workspace_id:
        return None
    known = db.query(AccountDb.workspace_name).filter(AccountDb.chatgpt_account_id == workspace_id, AccountDb.workspace_name.isnot(None)).first()
    return _clean_optional_name(known[0]) if known else None


def _identity_commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This ChatGPT user is already linked to this workspace. Re-authenticate the existing entry instead.",
        ) from exc


def _reset_credit_expires_soon(
    credit: RateLimitResetCredit,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether an available, supported reset will expire in under 12 hours."""
    if credit.status != "available" or credit.is_supported_by_plan is False or credit.expires_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    expires_at = credit.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return current < expires_at < current + _LIMIT_RESET_EXPIRY_WINDOW


def _account_or_404(db: Session, account_id: uuid.UUID) -> AccountDb:
    account = db.query(AccountDb).filter(AccountDb.id == account_id).first()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return account


def _build_account(db: Session, account: AccountDb) -> Account:
    return Account.from_db(
        account,
        total_spend_usd=usage.spend_usage(db, account_id=account.id),
        monthly_spend_usd=usage.spend_usage(db, account_id=account.id, month_to_date=True),
    )


def _build_accounts(db: Session, accounts: list[AccountDb]) -> list[Account]:
    """Build account cards with one batched spend aggregation."""
    spend = usage.spend_usage_rollups(db, UsageRecordDb.account_id, [account.id for account in accounts])
    return [Account.from_db(account, *spend.get(account.id, (0.0, 0.0))) for account in accounts]


def _next_priority(db: Session) -> int:
    highest = db.query(AccountDb).order_by(AccountDb.priority.desc()).first()
    return (highest.priority if highest is not None else 0) + 1


def _move_to_priority(db: Session, account: AccountDb, requested: int) -> None:
    ordered = (
        db.query(AccountDb)
        .filter(AccountDb.id != account.id)
        .order_by(AccountDb.priority.asc(), AccountDb.created_at.asc(), AccountDb.id.asc())
        .all()
    )
    ordered.insert(min(max(requested - 1, 0), len(ordered)), account)
    for position, candidate in enumerate(ordered, start=1):
        candidate.priority = position


def _flow_token(device: dict) -> str:
    payload = {
        "device_auth_id": device["device_auth_id"],
        "user_code": device["user_code"],
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }
    return crypto.encrypt(json.dumps(payload, separators=(",", ":")))


def _tokens_from_flow(flow_token: str) -> dict:
    try:
        payload = json.loads(crypto.decrypt(flow_token))
        issued_at = datetime.fromisoformat(str(payload["issued_at"]).replace("Z", "+00:00"))
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - issued_at > _DEVICE_FLOW_TTL:
            raise ValueError("Device authorization expired; start a new login")
        return oauth.exchange_device_code(payload["device_auth_id"], payload["user_code"])
    except oauth.DeviceAuthorizationPending as exc:
        raise HTTPException(status_code=425, detail="Waiting for device authorization") from exc
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid login flow: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Codex login failed ({exc.response.status_code})",
        ) from exc


def _apply_tokens(account: AccountDb, tokens: dict, *, workspace_name: str | None) -> None:
    account.account_email = tokens.get("email") or account.account_email
    account.tier = tokens.get("tier") or account.tier
    account.chatgpt_account_id = tokens["account_id"]
    account.chatgpt_account_user_id = tokens.get("account_user_id")
    account.chatgpt_user_id = tokens.get("user_id")
    account.workspace_name = workspace_name
    account.chatgpt_account_is_fedramp = bool(tokens.get("is_fedramp", False))
    account.access_token_enc = crypto.encrypt(tokens["access_token"])
    account.refresh_token_enc = crypto.encrypt(tokens["refresh_token"])
    account.expires_at = tokens["expires_at"]
    account.status = AccountStatus.ACTIVE
    account.cooldown_until = None
    account.provider_health = provider_health.ProviderHealth.UNKNOWN
    account.provider_health_code = None
    account.provider_health_message = None
    account.provider_health_failure_count = 0
    account.updated_at = datetime.now(timezone.utc)


def _probe_account(account: AccountDb, access_token: str) -> bool:
    if not account.chatgpt_account_id:
        raise provider_health.ProviderReauthenticationRequired("The account identity is incomplete. Re-authenticate this account.")
    limit_reached = rotation.apply_usage_probe(
        account,
        oauth.fetch_usage(access_token, account.chatgpt_account_id),
    )
    provider_health.mark_success(account)
    return limit_reached


@router.get("", response_model=ListAccountsResponse)
def list_accounts(
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ListAccountsResponse:
    accounts = db.query(AccountDb).order_by(AccountDb.priority.asc(), AccountDb.created_at.asc()).all()
    changed = any(rotation.normalize_expired_cooldown(account) for account in accounts)
    if changed:
        db.commit()
    return ListAccountsResponse(accounts=_build_accounts(db, accounts))


@router.put("/priorities", response_model=ListAccountsResponse)
def reorder_accounts(
    request: ReorderAccountsRequest,
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ListAccountsResponse:
    """Atomically replace the complete account priority order."""
    accounts = db.query(AccountDb).all()
    requested = request.account_ids
    if len(requested) != len(set(requested)) or set(requested) != {account.id for account in accounts}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Priority order must contain every account exactly once.",
        )
    by_id = {account.id: account for account in accounts}
    now = datetime.now(timezone.utc)
    for priority, account_id in enumerate(requested, start=1):
        by_id[account_id].priority = priority
        by_id[account_id].updated_at = now
    db.commit()
    ordered = [by_id[account_id] for account_id in requested]
    return ListAccountsResponse(accounts=_build_accounts(db, ordered))


@router.put("/priorities/bulk", response_model=ListAccountsResponse)
def bulk_set_account_priority(
    request: BulkAccountPriorityRequest,
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ListAccountsResponse:
    """Assign one priority to multiple accounts atomically without renumbering others."""
    if len(request.account_ids) != len(set(request.account_ids)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Account IDs must be unique")
    selected = db.query(AccountDb).filter(AccountDb.id.in_(request.account_ids)).with_for_update().all()
    selected_ids = {account.id for account in selected}
    requested_ids = set(request.account_ids)
    if selected_ids != requested_ids:
        missing = requested_ids - selected_ids
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Account(s) not found: {', '.join(map(str, missing))}")
    now = datetime.now(timezone.utc)
    for account in selected:
        account.priority = request.priority
        account.updated_at = now
    db.commit()
    ordered = db.query(AccountDb).order_by(AccountDb.priority.asc(), AccountDb.created_at.asc()).all()
    return ListAccountsResponse(accounts=_build_accounts(db, ordered))


@router.post("/oauth/start", response_model=OAuthStartResponse)
def start_oauth(_: str = Depends(security.require_admin)) -> OAuthStartResponse:  # noqa: B008
    """Start Codex's device-code flow without touching the machine's existing login."""
    try:
        device = oauth.request_device_code()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not start Codex login ({exc.response.status_code})",
        ) from exc
    return OAuthStartResponse(
        verification_url=device["verification_url"],
        user_code=device["user_code"],
        flow_token=_flow_token(device),
        interval=device["interval"],
    )


@router.post("/oauth/complete", response_model=AccountResponse)
def complete_oauth(
    request: OAuthCompleteRequest,
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> AccountResponse:
    label = request.label.strip()
    if not label:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Label is required")

    tokens = _tokens_from_flow(request.flow_token)
    _assert_unique_identity(db, tokens)
    workspace_name = _workspace_name_for_tokens(db, tokens, requested_name=request.workspace_name)
    now = datetime.now(timezone.utc)
    account = AccountDb(
        id=uuid.uuid4(),
        label=label,
        account_email=tokens.get("email"),
        tier=tokens.get("tier"),
        chatgpt_account_id=tokens["account_id"],
        chatgpt_account_user_id=tokens.get("account_user_id"),
        chatgpt_user_id=tokens.get("user_id"),
        workspace_name=workspace_name,
        chatgpt_account_is_fedramp=bool(tokens.get("is_fedramp", False)),
        access_token_enc=crypto.encrypt(tokens["access_token"]),
        refresh_token_enc=crypto.encrypt(tokens["refresh_token"]),
        expires_at=tokens["expires_at"],
        status=AccountStatus.ACTIVE,
        reset_credits_available=0,
        five_hour_rotation_threshold=config.DEFAULT_FIVE_HOUR_ROTATION_THRESHOLD,
        weekly_rotation_threshold=config.DEFAULT_WEEKLY_ROTATION_THRESHOLD,
        rotation_threshold=config.DEFAULT_ROTATION_THRESHOLD,
        cooldown_seconds=config.DEFAULT_COOLDOWN_SECONDS,
        max_failover_attempts=config.DEFAULT_MAX_FAILOVER_ATTEMPTS,
        priority=_next_priority(db),
        created_at=now,
        updated_at=now,
    )
    limit_reached = False
    try:
        limit_reached = _probe_account(account, tokens["access_token"])
    except Exception as exc:  # noqa: BLE001
        provider_health.mark_failure(account, exc)
        logger.warning("Initial quota probe failed for newly authorized account")

    db.add(account)
    _identity_commit(db)
    try:
        notifications.enqueue_account_added(db, account, config.get_settings().FRONTEND_ORIGIN)
        if limit_reached:
            notifications.enqueue_account_hard_limit(db, account, config.get_settings().FRONTEND_ORIGIN)
        else:
            notifications.enqueue_account_threshold(db, account, config.get_settings().FRONTEND_ORIGIN)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("Could not queue notifications for newly authorized account")
    logger.info("Added pooled Codex account %s", account.label)
    return AccountResponse(account=_build_account(db, account))


@router.post("/{account_id}/oauth/complete", response_model=AccountResponse)
def reauth_account(
    account_id: uuid.UUID,
    request: ReauthCompleteRequest,
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> AccountResponse:
    account = _account_or_404(db, account_id)
    tokens = _tokens_from_flow(request.flow_token)
    _assert_unique_identity(db, tokens, exclude_account_id=account.id)
    workspace_name = _workspace_name_for_tokens(db, tokens, current_account=account)
    _apply_tokens(account, tokens, workspace_name=workspace_name)
    limit_reached = False
    try:
        limit_reached = _probe_account(account, tokens["access_token"])
    except Exception as exc:  # noqa: BLE001
        provider_health.mark_failure(account, exc)
        logger.warning("Quota probe failed after re-authenticating account %s", account.label)
    if limit_reached:
        notifications.enqueue_account_hard_limit(db, account, config.get_settings().FRONTEND_ORIGIN)
    else:
        notifications.enqueue_account_threshold(db, account, config.get_settings().FRONTEND_ORIGIN)
    _identity_commit(db)
    logger.info("Re-authenticated pooled Codex account %s", account.label)
    return AccountResponse(account=_build_account(db, account))


@router.put("/{account_id}", response_model=AccountResponse)
def update_account(
    account_id: uuid.UUID,
    request: UpdateAccountRequest,
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> AccountResponse:
    account = _account_or_404(db, account_id)
    rotation.normalize_expired_cooldown(account)
    if request.label is not None:
        label = request.label.strip()
        if not label:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Label cannot be blank")
        account.label = label
    if "workspace_name" in request.model_fields_set:
        workspace_name = _clean_optional_name(request.workspace_name)
        if workspace_name and not _is_workspace_plan(account.tier):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A Team/workspace name can only be set for a Team or Business subscription.",
            )
        if account.chatgpt_account_id:
            db.query(AccountDb).filter(AccountDb.chatgpt_account_id == account.chatgpt_account_id).update(
                {AccountDb.workspace_name: workspace_name, AccountDb.updated_at: datetime.now(timezone.utc)},
                synchronize_session=False,
            )
        else:
            account.workspace_name = workspace_name
    if request.five_hour_rotation_threshold is not None:
        account.five_hour_rotation_threshold = request.five_hour_rotation_threshold
    if request.weekly_rotation_threshold is not None:
        account.weekly_rotation_threshold = request.weekly_rotation_threshold
    if request.rotation_threshold is not None:
        account.rotation_threshold = request.rotation_threshold
        account.five_hour_rotation_threshold = request.rotation_threshold
        account.weekly_rotation_threshold = request.rotation_threshold
    else:
        account.rotation_threshold = min(account.five_hour_rotation_threshold, account.weekly_rotation_threshold)
    if request.authenticated_override is not None:
        account.authenticated_override = request.authenticated_override
    if request.warmup_enabled is not None:
        if account.warmup_enabled != request.warmup_enabled:
            account.warmup_next_at = None
        account.warmup_enabled = request.warmup_enabled
    if request.cooldown_seconds is not None:
        account.cooldown_seconds = request.cooldown_seconds
    if request.max_failover_attempts is not None:
        account.max_failover_attempts = request.max_failover_attempts
    if request.priority is not None:
        # Priority levels are intentionally non-unique; editing one account
        # must not renumber every other account in the pool.
        account.priority = request.priority
    account.updated_at = datetime.now(timezone.utc)
    notifications.enqueue_account_threshold(db, account, config.get_settings().FRONTEND_ORIGIN)
    db.commit()
    return AccountResponse(account=_build_account(db, account))


@router.post("/{account_id}/refresh-quota", response_model=AccountResponse)
def refresh_quota(
    account_id: uuid.UUID,
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> AccountResponse:
    account = _account_or_404(db, account_id)
    try:
        access_token = rotation.ensure_fresh_token(db, account)
        limit_reached = _probe_account(account, access_token)
    except provider_health.ProviderReauthenticationRequired as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        provider_health.persist_failure(db, account.id, exc)
        if exc.response.status_code == 429:
            detail = "OpenAI's usage endpoint is temporarily rate-limiting quota probes; try again shortly."
        elif str(exc.request.url) == config.OAUTH_TOKEN_URL:
            detail = "Token refresh was rejected; re-authenticate this account."
        else:
            detail = f"Quota probe failed ({exc.response.status_code})"
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc
    except Exception as exc:  # noqa: BLE001
        provider_health.persist_failure(db, account.id, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Quota probe failed: {exc}") from exc
    account.updated_at = datetime.now(timezone.utc)
    if limit_reached:
        notifications.enqueue_account_hard_limit(db, account, config.get_settings().FRONTEND_ORIGIN)
    else:
        notifications.enqueue_account_threshold(db, account, config.get_settings().FRONTEND_ORIGIN)
    db.commit()
    return AccountResponse(account=_build_account(db, account))


@router.post("/{account_id}/warmup", response_model=AccountResponse)
def warmup_account(
    account_id: uuid.UUID,
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> AccountResponse:
    """Start this account's five-hour provider window immediately."""
    account = _account_or_404(db, account_id)
    try:
        warmup.warm_account(db, account)
    except warmup.WarmupNotEligible as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except warmup.WarmupBusy as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except warmup.WarmupFailed as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return AccountResponse(account=_build_account(db, account))


@router.get("/{account_id}/limit-resets", response_model=RateLimitResetCreditsResponse)
def list_limit_resets(
    account_id: uuid.UUID,
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> RateLimitResetCreditsResponse:
    account = _account_or_404(db, account_id)
    try:
        access_token = rotation.ensure_fresh_token(db, account)
        if not account.chatgpt_account_id:
            raise provider_health.ProviderReauthenticationRequired("The account identity is incomplete. Re-authenticate this account.")
        data = oauth.list_reset_credits(access_token, account.chatgpt_account_id)
        provider_health.mark_success(account)
    except provider_health.ProviderReauthenticationRequired as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        provider_health.persist_failure(db, account.id, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not load limit resets; the provider check failed.") from exc
    account.reset_credits_available = data["available_count"]
    account.updated_at = datetime.now(timezone.utc)
    db.commit()
    return RateLimitResetCreditsResponse.model_validate(data)


@router.post("/{account_id}/limit-resets/consume", response_model=ConsumeLimitResetResponse)
def consume_limit_reset(
    account_id: uuid.UUID,
    request: ConsumeLimitResetRequest,
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ConsumeLimitResetResponse:
    """Redeem one real banked reset credit; this never mutates quota locally."""
    account = _account_or_404(db, account_id)
    try:
        access_token = rotation.ensure_fresh_token(db, account)
        _probe_account(account, access_token)
        db.commit()
    except provider_health.ProviderReauthenticationRequired as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        provider_health.persist_failure(db, account.id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not verify current Codex usage windows; the limit reset was not consumed: {exc}",
        ) from exc
    credit_id = request.credit_id
    weekly_usage = account.weekly_used_pct
    if weekly_usage is None or weekly_usage <= _LIMIT_RESET_MIN_WEEKLY_USAGE:
        try:
            if not account.chatgpt_account_id:
                raise provider_health.ProviderReauthenticationRequired("The account identity is incomplete. Re-authenticate this account.")
            reset_data = oauth.list_reset_credits(access_token, account.chatgpt_account_id)
            reset_credits = RateLimitResetCreditsResponse.model_validate(reset_data).credits
        except provider_health.ProviderReauthenticationRequired as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            provider_health.persist_failure(db, account.id, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not verify the limit reset's expiration; it was not consumed: {exc}",
            ) from exc
        expiring_credit = next(
            (credit for credit in reset_credits if (credit_id is None or credit.id == credit_id) and _reset_credit_expires_soon(credit)),
            None,
        )
        if expiring_credit is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Weekly Codex usage must be above 90%, or the selected limit reset must expire within 12 hours.",
            )
        credit_id = expiring_credit.id
    try:
        if not account.chatgpt_account_id:
            raise provider_health.ProviderReauthenticationRequired("The account identity is incomplete. Re-authenticate this account.")
        result = oauth.consume_reset_credit(
            access_token,
            account.chatgpt_account_id,
            credit_id=credit_id,
            idempotency_key=request.idempotency_key,
        )
        _probe_account(account, access_token)
    except provider_health.ProviderReauthenticationRequired as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        provider_health.persist_failure(db, account.id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Limit reset was rejected ({exc.response.status_code})",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        provider_health.persist_failure(db, account.id, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Limit reset failed: {exc}") from exc
    account.updated_at = datetime.now(timezone.utc)
    db.commit()
    return ConsumeLimitResetResponse(account=_build_account(db, account), **result)


@router.post("/{account_id}/disable", response_model=AccountResponse)
def disable_account(
    account_id: uuid.UUID,
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> AccountResponse:
    account = _account_or_404(db, account_id)
    account.status = AccountStatus.DISABLED
    account.cooldown_until = None
    account.warmup_next_at = None
    account.updated_at = datetime.now(timezone.utc)
    db.commit()
    return AccountResponse(account=_build_account(db, account))


@router.post("/{account_id}/enable", response_model=AccountResponse)
def enable_account(
    account_id: uuid.UUID,
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> AccountResponse:
    account = _account_or_404(db, account_id)
    account.status = AccountStatus.ACTIVE
    account.cooldown_until = None
    account.warmup_next_at = None
    account.updated_at = datetime.now(timezone.utc)
    db.commit()
    return AccountResponse(account=_build_account(db, account))


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    account_id: uuid.UUID,
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> None:
    account = _account_or_404(db, account_id)
    db.query(UsageRecordDb).filter(UsageRecordDb.account_id == account_id).update(
        {UsageRecordDb.account_id: None},
        synchronize_session=False,
    )
    db.delete(account)
    db.flush()
    remaining = db.query(AccountDb).order_by(AccountDb.priority.asc(), AccountDb.created_at.asc()).all()
    for position, candidate in enumerate(remaining, start=1):
        candidate.priority = position
    db.commit()
