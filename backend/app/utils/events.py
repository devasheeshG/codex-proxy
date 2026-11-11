"""Best-effort persistence for the proxy's operational event timeline."""

from __future__ import annotations

import json
import uuid
from typing import Any, Mapping

from app.logger import get_logger
from app.utils.postgres import ProxyEventDb, UserDb, get_db_cm

logger = get_logger()


def record_limit_reset(account_id: uuid.UUID, result: Mapping[str, Any], *, source: str, credit_id: str | None) -> None:
    """Audit a confirmed credit consumption before the subsequent usage probe.

    No-op and idempotent replay outcomes did not consume a new credit.
    Use a separate timeline ID, since this operation has no inference archive.
    """
    if result.get("code") != "reset":
        return
    record_event(
        "account.limit_reset",
        f"limit_reset_{uuid.uuid4().hex}",
        account_id=account_id,
        message=f"Limit reset redeemed ({source}).",
        metadata={
            "source": source,
            "credit_id": credit_id,
            "windows_reset": result.get("windows_reset"),
            "idempotency_key": result.get("idempotency_key"),
            "request_outcome": "succeeded",
        },
    )


def record_event(
    event_type: str,
    request_id: str,
    *,
    codex_session_id: str | None = None,
    codex_thread_id: str | None = None,
    codex_turn_id: str | None = None,
    codex_root_turn_id: str | None = None,
    user_id: uuid.UUID | None = None,
    api_key_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None,
    fallback_provider_id: uuid.UUID | None = None,
    status_code: int | None = None,
    message: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """Write an event in its own short transaction; logging must never break proxy traffic."""
    try:
        with get_db_cm() as db:
            event_metadata = dict(metadata or {})
            if user_id is not None:
                user = db.get(UserDb, user_id)
                if user is not None:
                    event_metadata["user_name"] = user.name
            db.add(
                ProxyEventDb(
                    id=uuid.uuid4(),
                    request_id=request_id,
                    codex_session_id=codex_session_id,
                    codex_thread_id=codex_thread_id,
                    codex_turn_id=codex_turn_id,
                    codex_root_turn_id=codex_root_turn_id,
                    user_id=user_id,
                    api_key_id=api_key_id,
                    account_id=account_id,
                    fallback_provider_id=fallback_provider_id,
                    event_type=event_type,
                    status_code=status_code,
                    message=message,
                    metadata_json=json.dumps(event_metadata, default=str, separators=(",", ":")) if event_metadata else None,
                )
            )
    except Exception:  # noqa: BLE001
        logger.exception("Unable to persist proxy event %s", event_type)
