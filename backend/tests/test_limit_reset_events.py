"""Redemption audit events must not change credit consumption or quota refresh."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.routes import accounts
from app.utils import events, rotation
from app.utils.postgres import AccountDb, ProxyEventDb
from app.utils.postgres.base import SessionFactory


def reset_events():
    with SessionFactory() as db:
        return db.query(ProxyEventDb).filter_by(event_type="account.limit_reset").all()


@pytest.mark.parametrize("refresh_fails", [False, True])
def test_manual_redemption_is_logged_even_if_refresh_fails(client, admin_headers, seed_account, monkeypatch, refresh_fails):
    account_id = seed_account("reset-audit", weekly_used_pct=0.95)
    probes = []

    def probe(*args, **kwargs):
        probes.append(True)
        if refresh_fails and len(probes) == 2:
            raise RuntimeError("usage unavailable")

    monkeypatch.setattr(accounts, "_probe_account", probe)
    result = {"code": "reset", "windows_reset": 2, "idempotency_key": "reset-request"}
    monkeypatch.setattr(accounts.oauth, "consume_reset_credit", lambda *a, **kw: result)
    response = client.post(
        f"/api/v1/accounts/{account_id}/limit-resets/consume",
        headers=admin_headers,
        json={"credit_id": "credit-1", "idempotency_key": "reset-request"},
    )
    assert response.status_code == (502 if refresh_fails else 200)
    assert len(probes) == 2
    rows = reset_events()
    assert len(rows) == 1
    assert rows[0].account_id == account_id
    assert not rows[0].request_id.startswith("req_")  # No inference capture exists.
    metadata = json.loads(rows[0].metadata_json)
    assert metadata == {
        "source": "manual",
        "credit_id": "credit-1",
        "windows_reset": 2,
        "idempotency_key": "reset-request",
        "request_outcome": "succeeded",
    }
    page = client.get("/api/v1/events", headers=admin_headers, params={"event_type": "account.limit_reset", "group_by": "request"})
    assert page.status_code == 200
    assert page.json()["total"] == 1
    assert page.json()["events"][0]["metadata"]["request_outcome"] == "succeeded"


@pytest.mark.parametrize("code", ["already_redeemed", "nothing_to_reset", "no_credit"])
def test_noop_reset_does_not_log_another_consumption(client, admin_headers, seed_account, monkeypatch, code):
    account_id = seed_account("noop-reset", weekly_used_pct=0.95)
    monkeypatch.setattr(accounts, "_probe_account", lambda *a, **kw: None)
    monkeypatch.setattr(accounts.oauth, "consume_reset_credit", lambda *a, **kw: {"code": code, "windows_reset": 0, "idempotency_key": "replay"})
    response = client.post(f"/api/v1/accounts/{account_id}/limit-resets/consume", headers=admin_headers, json={})
    assert response.status_code == 200
    assert reset_events() == []


def test_logging_failure_does_not_break_redemption(client, admin_headers, seed_account, monkeypatch):
    account_id = seed_account("logging-unavailable", weekly_used_pct=0.95)
    monkeypatch.setattr(accounts, "_probe_account", lambda *a, **kw: None)
    monkeypatch.setattr(accounts.oauth, "consume_reset_credit", lambda *a, **kw: {"code": "reset", "windows_reset": 1, "idempotency_key": "reset"})

    def broken_db():
        raise RuntimeError("event database unavailable")

    monkeypatch.setattr(events, "get_db_cm", broken_db)
    response = client.post(f"/api/v1/accounts/{account_id}/limit-resets/consume", headers=admin_headers, json={})
    assert response.status_code == 200


@pytest.mark.parametrize("refresh_fails", [False, True])
def test_automatic_redemption_is_logged_before_refresh(seed_account, monkeypatch, refresh_fails):
    account_id = seed_account("automatic-audit", weekly_used_pct=1.0)
    with SessionFactory() as db:
        account = db.get(AccountDb, account_id)
        account.auto_limit_reset_enabled = True
        account.reset_credits_available = 1
        account.weekly_reset_at = datetime.now(timezone.utc) + timedelta(days=4)
        db.commit()
    consumed = []

    def usage(*a, **kw):
        if consumed and refresh_fails:
            raise RuntimeError("usage unavailable")
        return {"five_hour": {"utilization": 0.0}, "weekly": {"utilization": 0.0 if consumed else 1.0}, "limit_reached": not consumed}

    def consume(*a, **kw):
        consumed.append(kw)
        return {"code": "reset", "windows_reset": 1, "idempotency_key": kw["idempotency_key"]}

    monkeypatch.setattr(rotation.oauth, "fetch_usage", usage)
    monkeypatch.setattr(
        rotation.oauth, "list_reset_credits", lambda *a, **kw: {"available_count": 1, "credits": [{"id": "auto-credit", "status": "available"}]}
    )
    monkeypatch.setattr(rotation.oauth, "consume_reset_credit", consume)
    with SessionFactory() as db:
        if refresh_fails:
            with pytest.raises(RuntimeError, match="usage unavailable"):
                rotation.auto_redeem_weekly_reset(db, db.get(AccountDb, account_id), "token")
        else:
            assert rotation.auto_redeem_weekly_reset(db, db.get(AccountDb, account_id), "token")
            assert not rotation.auto_redeem_weekly_reset(db, db.get(AccountDb, account_id), "token")
    assert len(consumed) == 1
    rows = reset_events()
    assert len(rows) == 1 and rows[0].account_id == account_id
    metadata = json.loads(rows[0].metadata_json)
    assert metadata["source"] == "automatic" and metadata["credit_id"] == "auto-credit"
    assert metadata["windows_reset"] == 1
