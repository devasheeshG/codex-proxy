"""Cross-worker in-flight request limits for pooled upstream accounts."""

from dataclasses import dataclass

from sqlalchemy import text

from app.utils.postgres.base import engine


class AccountBusy(Exception):
    """No request slot is available for this account right now."""


@dataclass
class AccountLease:
    connection: object
    lock_key: int
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        try:
            self.connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self.lock_key})
        finally:
            self.connection.close()


def try_acquire(account_id, max_requests: int, priority: int = 1) -> AccountLease:
    """Acquire an eligible priority lane, or raise ``AccountBusy``.

    Priority 1 can use every lane. Priority 2 leaves lane 0 reserved for
    priority 1, and priority 3+ leaves the first two lanes reserved. This
    preserves capacity for higher-priority users during bursts.
    """
    max_requests = max(1, min(int(max_requests), 32))
    base = (account_id.int % ((2**63 - 1) // max_requests)) * max_requests
    first_slot = min(max(1, int(priority)) - 1, max_requests - 1)
    for slot in range(first_slot, max_requests):
        connection = engine.connect()
        key = base + slot
        acquired = connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
        if acquired:
            return AccountLease(connection, key)
        connection.close()
    raise AccountBusy
