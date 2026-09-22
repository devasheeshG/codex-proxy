"""Admin API for the append-only proxy event timeline."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.utils import security
from app.utils.models.api import ProxyEvent, ProxyEventPage
from app.utils.postgres import AccountDb, OpenAIFallbackDb, ProxyEventDb, UsageRecordDb, get_db

router = APIRouter(tags=["Events"], prefix="/events")


@router.get("", response_model=ProxyEventPage)
def list_events(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    event_type: Optional[str] = None,
    request_id: Optional[str] = None,
    user_id: Optional[uuid.UUID] = None,
    model: Optional[str] = None,
    operation: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    _: str = Depends(security.require_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ProxyEventPage:
    query = db.query(ProxyEventDb)
    # Admission reservations back client rate-limit enforcement but are not
    # user-facing proxy events; request.received remains the visible audit row.
    query = query.filter(ProxyEventDb.event_type != "request.reserved")
    if event_type:
        query = query.filter(ProxyEventDb.event_type == event_type)
    if request_id:
        query = query.filter(ProxyEventDb.request_id == request_id)
    if user_id:
        query = query.filter(ProxyEventDb.user_id == user_id)
    if model:
        normalized_model = model.strip()
        query = (
            query.outerjoin(UsageRecordDb, UsageRecordDb.request_id == ProxyEventDb.request_id)
            .filter(
                or_(
                    UsageRecordDb.model == normalized_model,
                    ProxyEventDb.metadata_json.contains(f'"model": "{normalized_model}"'),
                    ProxyEventDb.metadata_json.contains(f'"requested_model": "{normalized_model}"'),
                )
            )
            .distinct()
        )
    if operation:
        operation_marker = or_(
            ProxyEventDb.metadata_json.contains('"operation":"web_search"'),
            ProxyEventDb.metadata_json.contains('"operation": "web_search"'),
            ProxyEventDb.metadata_json.contains('"client_protocol":"codex_search"'),
            ProxyEventDb.metadata_json.contains('"client_protocol": "codex_search"'),
        )
        if operation == "web_search":
            query = query.filter(operation_marker)
        elif operation == "inference":
            query = query.filter(or_(ProxyEventDb.metadata_json.is_(None), ~operation_marker))
        else:
            query = query.filter(False)
    if start:
        query = query.filter(ProxyEventDb.created_at >= start)
    if end:
        query = query.filter(ProxyEventDb.created_at < end)
    total = query.count()
    rows = query.order_by(desc(ProxyEventDb.created_at), desc(ProxyEventDb.id)).offset(offset).limit(limit).all()
    request_ids = {row.request_id for row in rows}
    usage_rows = db.query(UsageRecordDb).filter(UsageRecordDb.request_id.in_(request_ids)).all() if request_ids else []
    usage_by_request = {row.request_id: row for row in usage_rows}
    account_ids = {row.account_id for row in rows if row.account_id}
    fallback_ids = {row.fallback_provider_id for row in rows if row.fallback_provider_id}
    account_names = {row.id: row.label for row in db.query(AccountDb).filter(AccountDb.id.in_(account_ids)).all()} if account_ids else {}
    fallback_names = (
        {row.id: row.label for row in db.query(OpenAIFallbackDb).filter(OpenAIFallbackDb.id.in_(fallback_ids)).all()} if fallback_ids else {}
    )
    events = []
    for row in rows:
        metadata = {}
        if row.metadata_json:
            try:
                parsed = json.loads(row.metadata_json)
                if isinstance(parsed, dict):
                    metadata = parsed
            except (TypeError, ValueError):
                metadata = {"raw": row.metadata_json}
        usage_row = usage_by_request.get(row.request_id)
        if usage_row is not None:
            # The event metadata records the model selected by the proxy.  A
            # provider may report a different model label in its response
            # usage object (for example, an alias such as gpt-5.6-luna for a
            # request routed as gpt-6-astra).  Do not overwrite the routing
            # model with that response label: doing so made the dashboard look
            # like the proxy had rewritten the request. Keep both values
            # explicit instead.
            upstream_response_model = usage_row.model
            metadata = {
                **metadata,
                "upstream_response_model": upstream_response_model,
                "input_tokens": usage_row.input_tokens,
                "output_tokens": usage_row.output_tokens,
                "cached_input_tokens": usage_row.cached_input_tokens,
                "cache_write_tokens": usage_row.cache_write_tokens,
                # UsageRecordDb stores the calculated API-equivalent cost under
                # billed_cost_usd; using the API response name here caused the
                # events list endpoint to raise AttributeError and return 500.
                "cost_usd": usage_row.billed_cost_usd,
                "reasoning_level": usage_row.reasoning_level,
            }
        if row.account_id:
            metadata["account_name"] = account_names.get(row.account_id, "Deleted account")
        elif row.fallback_provider_id:
            metadata["account_name"] = fallback_names.get(row.fallback_provider_id, "Deleted fallback provider")
        hot_cutoff = datetime.now(timezone.utc) - timedelta(days=get_settings().ARCHIVE_HOT_RETENTION_DAYS)
        events.append(ProxyEvent.model_validate({**row.__dict__, "metadata": metadata, "archive_hot": row.created_at >= hot_cutoff}))
    return ProxyEventPage(total=total, limit=limit, offset=offset, events=events)
