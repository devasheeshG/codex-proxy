# Path: app/scripts/quota_refresher.py
# Description: Sidecar loop that refreshes every account's five-hour/weekly utilization, resets, and subscription tier
#              on a fixed interval, so the dashboard stays current even for idle accounts.
#              Run with: `python -m app.scripts.quota_refresher`.

import time
from datetime import datetime, timezone

from app import config
from app.logger import configure_logging, get_logger
from app.utils import notifications, oauth, provider_health, rotation
from app.utils.models.api import ProviderHealth as ProviderHealthEnum
from app.utils.postgres import AccountDb, get_db_cm

# Get the logger
logger = get_logger()

# Get the settings
settings = config.get_settings()


def refresh_once() -> None:
    """Refresh quota and reset-credit state for every account."""
    with get_db_cm() as db:
        accounts = db.query(AccountDb).all()
        for account in accounts:
            # Skip accounts that need re-authentication; they will fail
            # immediately on ensure_fresh_token and just create log noise.
            if account.provider_health == ProviderHealthEnum.REAUTH_REQUIRED:
                continue
            try:
                access_token = rotation.ensure_fresh_token(db, account)
                if not account.chatgpt_account_id:
                    raise provider_health.ProviderReauthenticationRequired("The account identity is incomplete. Re-authenticate this account.")
                limit_reached = rotation.apply_usage_probe(
                    account,
                    oauth.fetch_usage(access_token, account.chatgpt_account_id),
                )
                if (account.weekly_used_pct or 0) >= 1.0:
                    redeemed = rotation.auto_redeem_weekly_reset(db, account, access_token)
                    if redeemed:
                        # auto_redeem_weekly_reset refreshes the authoritative
                        # usage state after redemption. Avoid notifying from the
                        # stale pre-redemption probe result.
                        limit_reached = any(window.used_pct is not None and window.used_pct >= 1.0 for window in rotation.quota_windows(account))
                        logger.info("Automatically redeemed a weekly limit reset for account '%s'", account.label)
                provider_health.mark_success(account)
                account.updated_at = datetime.now(timezone.utc)
                if limit_reached:
                    notifications.enqueue_account_hard_limit(db, account, settings.FRONTEND_ORIGIN)
                else:
                    notifications.enqueue_account_threshold(db, account, settings.FRONTEND_ORIGIN)
                db.commit()
                logger.info(f"Refreshed quota for account '{account.label}'")
            except Exception as exc:  # noqa: BLE001
                health = provider_health.persist_failure(db, account.id, exc)
                failed = db.get(AccountDb, account.id)
                code = failed.provider_health_code if failed is not None else None
                logger.warning(
                    "Quota refresh failed for account '%s' (health=%s, code=%s, error_type=%s)",
                    account.label,
                    health.value,
                    code or "unknown",
                    type(exc).__name__,
                )


def main() -> None:
    configure_logging()
    interval = settings.QUOTA_REFRESH_INTERVAL_SECONDS
    logger.info(f"Quota refresher started; interval={interval}s")
    while True:
        try:
            refresh_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Quota refresh cycle error: {exc}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
