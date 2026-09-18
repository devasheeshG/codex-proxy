import json
from datetime import datetime, timezone

from app.utils import rotation


def test_codex_429_body_persists_authoritative_reset(seed_account):
    account_id = seed_account("hard-limit")
    from app.utils.postgres import AccountDb
    from app.utils.postgres.base import SessionFactory

    with SessionFactory() as db:
        account = db.get(AccountDb, account_id)
        reached, reset_at = rotation.apply_rate_limit_body(
            account,
            json.dumps({"error": {"type": "usage_limit_reached", "resets_in_seconds": 3600}}).encode(),
        )
        assert reached == "usage_limit_reached"
        assert account.five_hour_used_pct == 1.0
        assert reset_at is not None
        assert 3590 <= (reset_at - datetime.now(timezone.utc)).total_seconds() <= 3600
