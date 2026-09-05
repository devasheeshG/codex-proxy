# Path: tests/test_rotation.py
# Description: Unit tests for availability gating, priority selection, model capability, and exhaustion.

import uuid
from datetime import datetime, timedelta, timezone

from app import config
from app.utils import rotation
from app.utils.models.api import AccountStatus
from app.utils.postgres import AccountDb, UserDb
from app.utils.postgres.base import SessionFactory


def _new_user(db):
    user = UserDb(
        id=uuid.uuid4(),
        name="u",
        active=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    return user


def test_is_available_gates_on_status_and_threshold(seed_account):
    seed_account("active", five_hour_used_pct=0.10)
    seed_account("disabled", status=AccountStatus.DISABLED)
    seed_account("full", weekly_used_pct=1.0)
    with SessionFactory() as db:
        by_label = {a.label: a for a in db.query(AccountDb).all()}
        assert rotation.is_available(by_label["active"]) is True
        assert rotation.is_available(by_label["disabled"]) is False
        assert rotation.is_available(by_label["full"]) is False  # >= its rotation_threshold


def test_expired_cooldown_is_normalized(seed_account):
    account_id = seed_account("expired", status=AccountStatus.COOLDOWN)
    with SessionFactory() as db:
        account = db.get(AccountDb, account_id)
        account.cooldown_until = datetime(2020, 1, 1, tzinfo=timezone.utc)
        now = datetime(2020, 1, 2, tzinfo=timezone.utc)
        assert rotation.normalize_expired_cooldown(account, now) is True
        assert account.status == AccountStatus.ACTIVE
        assert account.cooldown_until is None
        assert rotation.normalize_expired_cooldown(account, now) is False


def test_new_account_gets_default_rotation_policy(seed_account):
    acct_id = seed_account("fresh")  # no rotation_threshold passed -> column defaults apply
    with SessionFactory() as db:
        acct = db.get(AccountDb, acct_id)
        assert config.DEFAULT_ROTATION_THRESHOLD == 1.0
        assert acct.five_hour_rotation_threshold == config.DEFAULT_FIVE_HOUR_ROTATION_THRESHOLD
        assert acct.weekly_rotation_threshold == config.DEFAULT_WEEKLY_ROTATION_THRESHOLD
        assert acct.cooldown_seconds == config.DEFAULT_COOLDOWN_SECONDS
        assert acct.max_failover_attempts == config.DEFAULT_MAX_FAILOVER_ATTEMPTS


def test_force_refresh_does_not_require_a_synthetic_expiry(seed_account, monkeypatch):
    """A provider 401 must refresh the current row without clobbering its expiry."""
    from app.utils import oauth
    from app.utils.postgres import AccountDb
    from app.utils.postgres.base import SessionFactory

    account_id = seed_account("force-refresh", expires_in_hours=1)
    refreshed_expiry = datetime.now(timezone.utc) + timedelta(hours=2)
    monkeypatch.setattr(
        oauth,
        "refresh_access_token",
        lambda _refresh: {"access_token": "rotated-access", "refresh_token": "rotated-refresh", "expires_at": refreshed_expiry},
    )

    with SessionFactory() as db:
        account = db.get(AccountDb, account_id)
        assert rotation.ensure_fresh_token(db, account, force_refresh=True) == "rotated-access"
        persisted = db.get(AccountDb, account_id)
        assert persisted.expires_at == refreshed_expiry


def test_is_available_uses_per_account_threshold(seed_account):
    # The same 0.50 utilization is benched under a 0.40 per-account threshold but available under the default 1.0.
    strict = seed_account("strict", weekly_used_pct=0.50, rotation_threshold=0.40)
    lenient = seed_account("lenient", weekly_used_pct=0.50)
    with SessionFactory() as db:
        by_id = {a.id: a for a in db.query(AccountDb).all()}
        assert rotation.is_available(by_id[strict]) is False
        assert rotation.is_available(by_id[lenient]) is True


def test_each_quota_window_uses_its_own_rotation_threshold(seed_account):
    seed_account(
        "five-hour-strict",
        five_hour_used_pct=0.80,
        weekly_used_pct=0.20,
        five_hour_rotation_threshold=0.75,
        weekly_rotation_threshold=1.0,
    )
    seed_account(
        "weekly-strict",
        five_hour_used_pct=0.20,
        weekly_used_pct=0.80,
        five_hour_rotation_threshold=1.0,
        weekly_rotation_threshold=0.75,
    )
    with SessionFactory() as db:
        accounts = {account.label: account for account in db.query(AccountDb).all()}
        assert rotation.is_available(accounts["five-hour-strict"]) is False
        assert rotation.is_available(accounts["weekly-strict"]) is False


def test_preview_account_uses_priority_not_load(seed_account):
    lower_priority_id = seed_account("later", five_hour_used_pct=0.10, priority=2)
    first_id = seed_account("first", weekly_used_pct=0.50, priority=1)
    with SessionFactory() as db:
        assert rotation.preview_account(db, _new_user(db)).id == first_id
        assert rotation.preview_account(db, exclude_ids={first_id}).id == lower_priority_id


def test_same_priority_prefers_earliest_weekly_reset_then_five_hour(seed_account):
    weekly_later = seed_account("weekly-later", priority=1)
    weekly_sooner = seed_account("weekly-sooner", priority=1)
    seed_account("five-hour-later", priority=2)
    five_hour_sooner = seed_account("five-hour-sooner", priority=2)
    now = datetime.now(timezone.utc)
    with SessionFactory() as db:
        accounts = {account.label: account for account in db.query(AccountDb).all()}
        accounts["weekly-later"].weekly_reset_at = now + timedelta(days=7)
        accounts["weekly-sooner"].weekly_reset_at = now + timedelta(days=2)
        accounts["weekly-later"].five_hour_reset_at = now + timedelta(hours=1)
        accounts["weekly-sooner"].five_hour_reset_at = now + timedelta(hours=4)
        accounts["five-hour-later"].five_hour_reset_at = now + timedelta(hours=4)
        accounts["five-hour-sooner"].five_hour_reset_at = now + timedelta(hours=1)
        db.commit()
        assert rotation.preview_account(db).id == weekly_sooner
        assert rotation.preview_account(db, exclude_ids={weekly_later, weekly_sooner}).id == five_hour_sooner


def test_select_account_then_exhausts(seed_account):
    only_id = seed_account("only", five_hour_used_pct=0.10)
    with SessionFactory() as db:
        user = _new_user(db)
        chosen = rotation.select_account(db, user)
        assert chosen.id == only_id

        # Excluding the only available account leaves nothing to serve.
        assert rotation.select_account(db, user, exclude_ids={only_id}) is None


def test_model_capability_skips_higher_priority_account(seed_account):
    go_id = seed_account("go", priority=1)
    team_id = seed_account("team", priority=2)
    with SessionFactory() as db:
        go = db.get(AccountDb, go_id)
        team = db.get(AccountDb, team_id)
        go.model_catalog_json = '[{"slug":"gpt-5.6-luna"}]'
        team.model_catalog_json = '[{"slug":"gpt-5.6-luna"},{"slug":"gpt-5.6-sol"}]'
        db.commit()
        assert rotation.preview_account(db, model="gpt-5.6-sol").id == team_id


def test_unknown_model_capability_keeps_account_priority(seed_account):
    new_account_id = seed_account("new", priority=1)
    confirmed_account_id = seed_account("confirmed", priority=2)
    with SessionFactory() as db:
        confirmed = db.get(AccountDb, confirmed_account_id)
        confirmed.model_catalog_json = '[{"slug":"gpt-5.6-sol"}]'
        db.commit()

        assert db.get(AccountDb, new_account_id).model_catalog_json is None
        assert rotation.preview_account(db, model="gpt-5.6-sol").id == new_account_id


def test_quota_headers_update_windows_and_reset_credits(seed_account):
    account_id = seed_account("headers")
    with SessionFactory() as db:
        account = db.get(AccountDb, account_id)
        reached = rotation.update_quota_from_headers(
            account,
            {
                "x-codex-primary-used-percent": "22",
                "x-codex-primary-reset-at": "1900000000",
                "x-codex-secondary-used-percent": "41",
                "x-codex-secondary-reset-at": "1900100000",
                "x-codex-rate-limit-reset-credits-available": "3",
                "x-codex-rate-limit-reached-type": "rate_limit_reached",
            },
        )
        assert account.five_hour_used_pct == 0.22
        assert account.weekly_used_pct == 0.41
        assert account.five_hour_reset_at.timestamp() == 1_900_000_000
        assert account.weekly_reset_at.timestamp() == 1_900_100_000
        assert account.reset_credits_available == 3
        assert reached == "rate_limit_reached"


def test_usage_probe_updates_both_windows_and_clears_an_absent_optional_window(seed_account):
    account_id = seed_account("probe", five_hour_used_pct=0.10, weekly_used_pct=0.80)
    with SessionFactory() as db:
        account = db.get(AccountDb, account_id)
        reached = rotation.apply_usage_probe(
            account,
            {
                "five_hour": {"utilization": 0.35, "reset_at": datetime(2030, 1, 1, tzinfo=timezone.utc)},
                "weekly": None,
                "limit_reached": False,
            },
        )

        assert reached is False
        assert account.five_hour_used_pct == 0.35
        assert account.five_hour_reset_at == datetime(2030, 1, 1, tzinfo=timezone.utc)
        assert account.weekly_used_pct is None
        assert account.weekly_reset_at is None


def test_usage_probe_clears_rolling_cold_five_hour_placeholder(seed_account):
    account_id = seed_account("cold", five_hour_used_pct=0.10)
    with SessionFactory() as db:
        account = db.get(AccountDb, account_id)
        account.five_hour_reset_at = datetime(2030, 1, 1, tzinfo=timezone.utc)

        rotation.apply_usage_probe(
            account,
            {
                "five_hour": {
                    "utilization": 0.0,
                    "reset_at": datetime(2030, 1, 2, tzinfo=timezone.utc),
                    "window_seconds": 18_000,
                    "reset_after_seconds": 18_000,
                    "is_cold": True,
                },
                "limit_reached": False,
            },
        )

        assert account.five_hour_used_pct == 0.0
        assert account.five_hour_reset_at is None
