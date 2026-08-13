# Path: app/utils/openai_fallbacks.py
# Description: Selection, health, budget, and secret helpers for OpenAI-compatible fallbacks.

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Set
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.utils import crypto, usage
from app.utils.models.api import AccountStatus, OpenAIFallback, ProviderHealth
from app.utils.postgres import OpenAIFallbackDb, UsageRecordDb


def normalize_base_url(value: object) -> str:
    """Return a safe, stable HTTP(S) API root without a trailing slash."""
    raw = str(value).strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Base URL must be an absolute http:// or https:// URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Base URL cannot contain credentials, a query string, or a fragment.")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


def credential_hash(base_url: str, api_key: str) -> str:
    return hashlib.sha256(f"{normalize_base_url(base_url)}\0{api_key}".encode()).hexdigest()


def key_hint(api_key: str) -> str:
    """Return a non-secret recognition hint; the full credential is never recoverable via API."""
    cleaned = api_key.strip()
    suffix = cleaned[-4:] if len(cleaned) >= 4 else "••••"
    return f"••••{suffix}"


def api_key(provider: OpenAIFallbackDb) -> str:
    return crypto.decrypt(provider.api_key_enc)


def endpoint(provider: OpenAIFallbackDb, path: str) -> str:
    return f"{provider.base_url.rstrip('/')}/{path.lstrip('/')}"


def monthly_spend(db: Session, provider_id: uuid.UUID) -> float:
    value = (
        db.query(func.coalesce(func.sum(UsageRecordDb.billed_cost_usd), 0.0))
        .filter(
            UsageRecordDb.fallback_provider_id == provider_id,
            UsageRecordDb.created_at >= usage.month_start(),
        )
        .scalar()
    )
    return max(float(value or 0.0), 0.0)


def is_budget_available(db: Session, provider: OpenAIFallbackDb) -> bool:
    limit = provider.monthly_spend_limit_usd
    return limit is None or monthly_spend(db, provider.id) < float(limit)


def model_ids(provider: OpenAIFallbackDb) -> Optional[Set[str]]:
    if not provider.model_catalog_json:
        return None
    try:
        models = json.loads(provider.model_catalog_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(models, list):
        return None
    result = {
        str(model.get("id") or model.get("slug")).strip().lower()
        for model in models
        if isinstance(model, dict) and (model.get("id") or model.get("slug"))
    }
    return result or None


def supports_model(provider: OpenAIFallbackDb, model: Optional[str]) -> Optional[bool]:
    if not model:
        return None
    known = model_ids(provider)
    return None if known is None else model.strip().lower() in known


def normalize_expired_cooldowns(db: Session, now: Optional[datetime] = None) -> None:
    current = now or datetime.now(timezone.utc)
    providers = db.query(OpenAIFallbackDb).filter(OpenAIFallbackDb.status == AccountStatus.COOLDOWN).all()
    changed = False
    for provider in providers:
        cooldown_until = provider.cooldown_until
        if cooldown_until is not None and cooldown_until.tzinfo is None:
            cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
        if cooldown_until is not None and cooldown_until > current:
            continue
        provider.status = AccountStatus.ACTIVE
        provider.cooldown_until = None
        provider.updated_at = current
        changed = True
    if changed:
        db.commit()


def select_provider(
    db: Session,
    excluded: Set[uuid.UUID],
    *,
    model: Optional[str] = None,
) -> Optional[OpenAIFallbackDb]:
    """Choose the first enabled, credential-valid, under-budget fallback."""
    normalize_expired_cooldowns(db)
    providers = (
        db.query(OpenAIFallbackDb)
        .filter(
            OpenAIFallbackDb.status == AccountStatus.ACTIVE,
            OpenAIFallbackDb.provider_health != ProviderHealth.REAUTH_REQUIRED,
        )
        .order_by(OpenAIFallbackDb.priority.asc(), OpenAIFallbackDb.created_at.asc())
        .all()
    )
    for provider in providers:
        if provider.id in excluded:
            continue
        if supports_model(provider, model) is False:
            continue
        if not is_budget_available(db, provider):
            continue
        return provider
    return None


def mark_healthy(provider: OpenAIFallbackDb) -> None:
    now = datetime.now(timezone.utc)
    provider.provider_health = ProviderHealth.HEALTHY
    provider.provider_health_message = None
    provider.provider_health_checked_at = now
    provider.status = AccountStatus.ACTIVE
    provider.cooldown_until = None
    provider.updated_at = now


def mark_invalid(provider: OpenAIFallbackDb, message: str) -> None:
    now = datetime.now(timezone.utc)
    provider.provider_health = ProviderHealth.REAUTH_REQUIRED
    provider.provider_health_message = message[:1000]
    provider.provider_health_checked_at = now
    provider.cooldown_until = None
    provider.updated_at = now


def mark_cooldown(provider: OpenAIFallbackDb, seconds: int, message: str) -> None:
    now = datetime.now(timezone.utc)
    provider.provider_health = ProviderHealth.DEGRADED
    provider.provider_health_message = message[:1000]
    provider.provider_health_checked_at = now
    provider.status = AccountStatus.COOLDOWN
    provider.cooldown_until = now + timedelta(seconds=max(1, seconds))
    provider.updated_at = now


def to_api(db: Session, provider: OpenAIFallbackDb) -> OpenAIFallback:
    spent = monthly_spend(db, provider.id)
    limit = float(provider.monthly_spend_limit_usd) if provider.monthly_spend_limit_usd is not None else None
    known_models = model_ids(provider)
    return OpenAIFallback(
        id=provider.id,
        label=provider.label,
        base_url=provider.base_url,
        key_hint=provider.key_hint,
        status=provider.status,
        provider_health=provider.provider_health,
        provider_health_message=provider.provider_health_message,
        provider_health_checked_at=provider.provider_health_checked_at,
        cooldown_until=provider.cooldown_until,
        priority=provider.priority,
        monthly_spend_limit_usd=limit,
        monthly_spend_usd=round(spent, 6),
        monthly_spend_remaining_usd=None if limit is None else round(max(limit - spent, 0.0), 6),
        monthly_spend_reset_at=usage.month_reset_at(),
        model_count=len(known_models or ()),
        model_catalog_refreshed_at=provider.model_catalog_refreshed_at,
        last_used_at=provider.last_used_at,
        created_at=provider.created_at,
    )
