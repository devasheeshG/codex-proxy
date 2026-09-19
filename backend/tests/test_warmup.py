from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app.utils import warmup
from app.utils.models.api import AccountStatus, ProviderHealth
from app.utils.postgres import AccountDb
from app.utils.postgres.base import SessionFactory


def _make_healthy(*account_ids):
    with SessionFactory() as db:
        for account_id in account_ids:
            db.get(AccountDb, account_id).provider_health = ProviderHealth.HEALTHY
        db.commit()


def test_default_warmup_trigger_is_ten_percent():
    assert warmup.settings.WARMUP_ENABLED is True
    assert warmup.settings.WARMUP_TRIGGER_POOL_USAGE_PCT == 0.10


def test_pool_usage_fraction_is_aggregate_pool_capacity(seed_account):
    first = seed_account("first", five_hour_used_pct=0.60)
    second = seed_account("second", five_hour_used_pct=0.0)
    third = seed_account("third", five_hour_used_pct=0.0)
    with SessionFactory() as db:
        accounts = [db.get(AccountDb, account_id) for account_id in (first, second, third)]
        assert warmup.pool_usage_fraction(accounts) == 0.20


def test_peak_usage_can_trigger_warmup_when_average_is_diluted(seed_account):
    busy = seed_account("busy", five_hour_used_pct=0.20)
    idle_one = seed_account("idle-one", five_hour_used_pct=0.0)
    idle_two = seed_account("idle-two", five_hour_used_pct=0.0)
    idle_three = seed_account("idle-three", five_hour_used_pct=0.0)
    _make_healthy(busy, idle_one, idle_two)
    with SessionFactory() as db:
        _make_healthy(idle_three)
        accounts = [db.get(AccountDb, account_id) for account_id in (busy, idle_one, idle_two, idle_three)]
        assert warmup.pool_usage_fraction(accounts) < warmup.settings.WARMUP_TRIGGER_POOL_USAGE_PCT
        assert warmup.demand_trigger_reached(accounts) is True


def test_warmup_skips_disabled_reauthentication_and_weekly_reserve(seed_account):
    disabled_id = seed_account("disabled", status=AccountStatus.DISABLED)
    reauth_id = seed_account("reauth")
    reserve_id = seed_account("reserve", weekly_used_pct=0.95)
    monthly_only_id = seed_account("monthly-only")
    _make_healthy(disabled_id, reserve_id, monthly_only_id)

    with SessionFactory() as db:
        db.get(AccountDb, reauth_id).provider_health = ProviderHealth.REAUTH_REQUIRED
        db.get(AccountDb, monthly_only_id).five_hour_used_pct = None
        db.commit()
        assert warmup.is_eligible(db.get(AccountDb, disabled_id)) is False
        assert warmup.is_eligible(db.get(AccountDb, reauth_id)) is False
        assert warmup.is_eligible(db.get(AccountDb, reserve_id)) is False
        assert warmup.is_eligible(db.get(AccountDb, monthly_only_id)) is False


def test_recent_traffic_or_warmup_means_account_is_already_warm(seed_account):
    account_id = seed_account("warm")
    now = datetime.now(timezone.utc)
    with SessionFactory() as db:
        account = db.get(AccountDb, account_id)
        account.five_hour_reset_at = now + timedelta(hours=4)
        # A provider reset timestamp alone is not evidence of warm-up.
        assert warmup.is_cold(account, now) is True
        account.last_used_at = now - timedelta(minutes=1)
        assert warmup.is_cold(account, now) is False


def test_crossing_trigger_warms_every_other_cold_account(seed_account, monkeypatch):
    active_id = seed_account("active", five_hour_used_pct=0.60)
    cold_one_id = seed_account("cold-one", five_hour_used_pct=0.0)
    cold_two_id = seed_account("cold-two", five_hour_used_pct=0.0)
    _make_healthy(active_id, cold_one_id, cold_two_id)
    with SessionFactory() as db:
        active = db.get(AccountDb, active_id)
        active.five_hour_reset_at = datetime.now(timezone.utc) + timedelta(hours=4)
        active.last_used_at = datetime.now(timezone.utc)
        db.commit()

    warmed = []

    @contextmanager
    def test_db():
        with SessionFactory() as db:
            yield db

    monkeypatch.setattr(warmup, "get_db_cm", test_db)
    monkeypatch.setattr(warmup, "_try_lock", lambda _db: True)
    monkeypatch.setattr(warmup, "_unlock", lambda _db: None)
    monkeypatch.setattr(warmup, "_warm_one", lambda _db, account, _now: warmed.append(account.id) is None)

    assert warmup.warm_pool_if_needed() == 2
    assert set(warmed) == {cold_one_id, cold_two_id}


def test_manual_warmup_uses_the_same_eligibility_and_lock(seed_account, monkeypatch):
    account_id = seed_account("manual")
    _make_healthy(account_id)
    calls = []
    monkeypatch.setattr(warmup, "_try_lock", lambda _db: True)
    monkeypatch.setattr(warmup, "_unlock", lambda _db: calls.append("unlock"))
    monkeypatch.setattr(warmup, "_warm_one", lambda _db, account, _now: calls.append(account.id) or True)

    with SessionFactory() as db:
        warmup.warm_account(db, db.get(AccountDb, account_id))

    assert calls == [account_id, "unlock"]
