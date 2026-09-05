# Path: tests/test_quota_refresher.py
# Description: Regression coverage for automatic weekly-reset redemption during background quota refreshes.

from datetime import datetime, timedelta, timezone

from app.scripts import quota_refresher
from app.utils.postgres import AccountDb
from app.utils.postgres.base import SessionFactory


def test_quota_refresher_redeems_an_exhausted_weekly_window(seed_account, monkeypatch):
    account_id = seed_account("refresher-exhausted", weekly_used_pct=0.5)
    exhausted = {
        "five_hour": {"utilization": 0.25},
        "weekly": {"utilization": 1.0},
        "monthly": None,
        "reset_credits_available": 1,
        "limit_reached": True,
    }
    recovered = {
        "five_hour": {"utilization": 0.0},
        "weekly": {"utilization": 0.0},
        "monthly": None,
        "reset_credits_available": 0,
        "limit_reached": False,
    }
    # Initial refresher probe, under-lock verification, and post-redemption
    # refresh respectively.
    usage_results = iter((exhausted, exhausted, recovered))
    consumed = []
    monkeypatch.setattr(quota_refresher.oauth, "fetch_usage", lambda *_args: next(usage_results))
    monkeypatch.setattr(
        quota_refresher.oauth,
        "list_reset_credits",
        lambda *_args: {
            "available_count": 1,
            "credits": [
                {
                    "id": "background-credit",
                    "status": "available",
                    "is_supported_by_plan": True,
                    "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
                }
            ],
        },
    )

    def consume(*_args, **kwargs):
        consumed.append(kwargs["credit_id"])
        return {"code": "reset", "windows_reset": 1, "idempotency_key": kwargs["idempotency_key"]}

    monkeypatch.setattr(quota_refresher.oauth, "consume_reset_credit", consume)

    quota_refresher.refresh_once()

    assert consumed == ["background-credit"]
    with SessionFactory() as db:
        account = db.get(AccountDb, account_id)
        assert account.weekly_used_pct == 0.0
        assert account.reset_credits_available == 0
