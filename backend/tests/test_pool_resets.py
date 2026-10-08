"""Reset credits are a serialized last resort after usable pool capacity is gone."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import respx

from app.scripts import quota_refresher
from app.utils import rotation
from app.utils.models.api import AccountStatus
from app.utils.postgres import AccountDb
from app.utils.postgres.base import SessionFactory


def exhausted(seed_account, label, days, priority=1):
    account_id = seed_account(label, weekly_used_pct=1.0, priority=priority)
    with SessionFactory() as db:
        account = db.get(AccountDb, account_id)
        account.auto_limit_reset_enabled = True
        account.reset_credits_available = 1
        account.weekly_reset_at = datetime.now(timezone.utc) + timedelta(days=days) if days is not None else None
        db.commit()
    return account_id


def provider(monkeypatch):
    redeemed = set()
    claims = []

    def usage(_token, account_id, **_kwargs):
        return {
            "five_hour": {"utilization": 0.0},
            "weekly": {"utilization": 0.0 if account_id in redeemed else 1.0},
            "reset_credits_available": 0 if account_id in redeemed else 1,
            "limit_reached": account_id not in redeemed,
        }

    def consume(_token, account_id, **kwargs):
        claims.append(account_id)
        redeemed.add(account_id)
        return {"code": "reset"}

    monkeypatch.setattr(rotation.oauth, "fetch_usage", usage)
    monkeypatch.setattr(rotation.oauth, "consume_reset_credit", consume)
    monkeypatch.setattr(
        rotation.oauth,
        "list_reset_credits",
        lambda *_a, **_k: {"available_count": 1, "credits": [{"id": "credit", "status": "available", "is_supported_by_plan": True}]},
    )
    return claims


def test_healthy_pool_prevents_any_reset_probe(seed_account, monkeypatch):
    exhausted(seed_account, "exhausted", 6)
    seed_account("healthy", weekly_used_pct=0.3)
    claims = provider(monkeypatch)
    with SessionFactory() as db:
        assert rotation.recover_exhausted_pool(db) is None
    assert claims == []


def test_latest_weekly_reset_wins_over_priority_and_unknown_date(seed_account, monkeypatch):
    exhausted(seed_account, "near", 1, priority=1)
    far = exhausted(seed_account, "far", 6, priority=100)
    exhausted(seed_account, "unknown", None, priority=1)
    claims = provider(monkeypatch)
    with SessionFactory() as db:
        assert rotation.recover_exhausted_pool(db) == far
        assert rotation.recover_exhausted_pool(db) is None
    assert claims == ["chatgpt-far"]


def test_direct_claim_cannot_bypass_pool_or_farthest_selection(seed_account, monkeypatch):
    near = exhausted(seed_account, "near", 1)
    exhausted(seed_account, "far", 6)
    claims = provider(monkeypatch)
    with SessionFactory() as db:
        assert not rotation.auto_redeem_weekly_reset(db, db.get(AccountDb, near), "token")
    assert claims == []


def test_recovered_quota_account_is_removed_from_cooldown(seed_account, monkeypatch):
    account_id = exhausted(seed_account, "cooldown", 6)
    with SessionFactory() as db:
        a = db.get(AccountDb, account_id)
        a.status = AccountStatus.COOLDOWN
        a.cooldown_until = a.weekly_reset_at
        db.commit()
    provider(monkeypatch)
    with SessionFactory() as db:
        assert rotation.recover_exhausted_pool(db) == account_id
        assert rotation.is_available(db.get(AccountDb, account_id))


def test_refresher_updates_whole_pool_before_spending(seed_account, monkeypatch):
    exhausted(seed_account, "first", 6)
    seed_account("later-healthy", weekly_used_pct=1.0)
    claims = provider(monkeypatch)
    original = rotation.oauth.fetch_usage

    def usage(token, account_id, **kwargs):
        if account_id == "chatgpt-later-healthy":
            return {"five_hour": {"utilization": 0.0}, "weekly": {"utilization": 0.2}, "limit_reached": False}
        return original(token, account_id, **kwargs)

    monkeypatch.setattr(rotation.oauth, "fetch_usage", usage)
    monkeypatch.setattr(rotation.oauth, "fetch_model_catalog", lambda *_a, **_k: {"models": []})
    monkeypatch.setattr(quota_refresher.warmup, "warm_pool_if_needed", lambda: 0)
    quota_refresher.refresh_once()
    assert claims == []


def test_concurrent_recovery_spends_only_one_credit(seed_account, monkeypatch):
    far = exhausted(seed_account, "far", 6)
    exhausted(seed_account, "near", 1)
    claims = provider(monkeypatch)
    original = rotation.oauth.consume_reset_credit

    def consume(*args, **kwargs):
        time.sleep(0.1)
        return original(*args, **kwargs)

    monkeypatch.setattr(rotation.oauth, "consume_reset_credit", consume)

    def recover():
        with SessionFactory() as db:
            return rotation.recover_exhausted_pool(db)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: recover(), range(2)))
    assert far in results
    assert claims == ["chatgpt-far"]


def test_model_incompatible_capacity_does_not_block_recovery(seed_account, monkeypatch):
    far = exhausted(seed_account, "far", 6)
    other = seed_account("other")
    with SessionFactory() as db:
        db.get(AccountDb, other).model_catalog_json = json.dumps([{"slug": "gpt-6-luna"}])
        db.commit()
    claims = provider(monkeypatch)
    with SessionFactory() as db:
        assert rotation.recover_exhausted_pool(db, model="gpt-6-sol") == far
    assert claims == ["chatgpt-far"]


@respx.mock
def test_weekly_429_fails_over_without_spending_when_other_account_serves(client, seed_account, make_user, monkeypatch):
    first = seed_account("first", weekly_used_pct=0.99)
    seed_account("second", weekly_used_pct=0.2)
    with SessionFactory() as db:
        db.get(AccountDb, first).auto_limit_reset_enabled = True
        db.commit()
    claims = provider(monkeypatch)
    key = make_user("pool-user")
    respx.route(host="testserver").pass_through()
    upstream = respx.post("https://chatgpt.com/backend-api/codex/responses").mock(
        side_effect=[
            httpx.Response(
                429,
                headers={"x-codex-secondary-used-percent": "100", "x-codex-rate-limit-reset-credits-available": "1"},
                json={"error": {"message": "weekly limit"}},
            ),
            httpx.Response(200, json={"model": "gpt-6-sol", "usage": {"input_tokens": 1, "output_tokens": 1}}),
        ]
    )
    result = client.post("/api/v1/responses", headers={"Authorization": "Bearer " + key}, json={"model": "gpt-6-sol"})
    assert result.status_code == 200, result.text
    assert upstream.call_count == 2
    assert claims == []


@respx.mock
def test_last_serving_account_exhaustion_recovers_farthest_other_account(client, seed_account, make_user, monkeypatch):
    near = seed_account("near", weekly_used_pct=0.99)
    far = exhausted(seed_account, "far", 6, priority=100)
    with SessionFactory() as db:
        account = db.get(AccountDb, near)
        account.auto_limit_reset_enabled = True
        account.weekly_reset_at = datetime.now(timezone.utc) + timedelta(days=1)
        db.commit()
    claims = provider(monkeypatch)
    key = make_user("last-capacity")
    respx.route(host="testserver").pass_through()
    seen = []

    def inference(request):
        seen.append(request.headers["chatgpt-account-id"])
        if len(seen) == 1:
            return httpx.Response(
                429,
                headers={"x-codex-secondary-used-percent": "100", "x-codex-rate-limit-reset-credits-available": "1"},
                json={"error": {"message": "weekly limit"}},
            )
        return httpx.Response(200, json={"model": "gpt-6-sol", "usage": {"input_tokens": 1, "output_tokens": 1}})

    respx.post("https://chatgpt.com/backend-api/codex/responses").mock(side_effect=inference)
    result = client.post("/api/v1/responses", headers={"Authorization": "Bearer " + key}, json={"model": "gpt-6-sol"})
    assert result.status_code == 200, result.text
    assert seen == ["chatgpt-near", "chatgpt-far"]
    assert claims == ["chatgpt-far"]
    with SessionFactory() as db:
        assert db.get(AccountDb, near).weekly_used_pct == 1.0
        assert db.get(AccountDb, far).weekly_used_pct == 0.0
