"""Durable, body-exact inference archives in an S3-compatible object store.

The archive middleware records only the three inference endpoints. It never
copies request headers, so bearer tokens, cookies, and proxy credentials cannot
leak into the archive. Hot-tier bodies are stored as raw JSON; the reader keeps
temporary gzip compatibility while the existing archive is migrated.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Mapping, Optional, Tuple

import boto3
from botocore.config import Config
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings, get_settings
from app.logger import get_logger

logger = get_logger()

ARCHIVED_PATHS = {
    "/api/v1/responses",
    "/api/v1/responses/compact",
    "/api/v1/chat/completions",
}


class ArchiveWriteError(RuntimeError):
    """The required archive could not be written safely."""


@dataclass(frozen=True)
class ArchivedBody:
    key: str
    content_type: str
    raw_bytes: int
    stored_bytes: int
    sha256: str


def _header_value(headers: List[Tuple[bytes, bytes]], name: bytes) -> Optional[str]:
    lowered = name.lower()
    for key, value in headers:
        if key.lower() == lowered:
            return value.decode("latin-1")
    return None


class S3ArchiveStore:
    """Small async facade over boto3 for complete raw JSON object writes."""

    def __init__(self, settings: Settings, client=None) -> None:
        self.settings = settings
        self.bucket = settings.ARCHIVE_S3_BUCKET
        self.prefix = settings.ARCHIVE_S3_PREFIX
        if client is not None:
            self.client = client
            self.reader_client = client
            return

        client_kwargs: Dict[str, Any] = {
            "service_name": "s3",
            "region_name": settings.ARCHIVE_S3_REGION,
            "config": Config(
                retries={"max_attempts": settings.ARCHIVE_S3_MAX_ATTEMPTS, "mode": "standard"},
                s3={"addressing_style": "path" if settings.ARCHIVE_S3_FORCE_PATH_STYLE else "auto"},
            ),
        }
        if settings.ARCHIVE_S3_ENDPOINT_URL:
            client_kwargs["endpoint_url"] = settings.ARCHIVE_S3_ENDPOINT_URL
        if settings.ARCHIVE_S3_ACCESS_KEY_ID:
            client_kwargs["aws_access_key_id"] = settings.ARCHIVE_S3_ACCESS_KEY_ID
            client_kwargs["aws_secret_access_key"] = settings.ARCHIVE_S3_SECRET_ACCESS_KEY
        self.client = boto3.client(**client_kwargs)
        reader_kwargs = dict(client_kwargs)
        if settings.ARCHIVE_S3_READ_ACCESS_KEY_ID:
            reader_kwargs["aws_access_key_id"] = settings.ARCHIVE_S3_READ_ACCESS_KEY_ID
            reader_kwargs["aws_secret_access_key"] = settings.ARCHIVE_S3_READ_SECRET_ACCESS_KEY
        self.reader_client = boto3.client(**reader_kwargs)

    async def _call(self, operation: str, **kwargs):
        try:
            return await asyncio.to_thread(getattr(self.client, operation), **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise ArchiveWriteError(f"S3 archive operation '{operation}' failed") from exc

    async def _read_call(self, operation: str, **kwargs):
        try:
            return await asyncio.to_thread(getattr(self.reader_client, operation), **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise ArchiveWriteError(f"S3 archive read operation '{operation}' failed") from exc

    async def ensure_ready(self) -> None:
        await self._call("head_bucket", Bucket=self.bucket)

    async def put_body(self, key: str, body: bytes, content_type: str) -> "ArchivedBody":
        """Write one complete raw JSON object.

        The middleware deliberately buffers only its archive copy. The ASGI
        response is still forwarded chunk-by-chunk to the caller.
        """
        await self._call(
            "put_object",
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=content_type or "application/octet-stream",
        )
        return ArchivedBody(
            key=key,
            content_type=content_type or "application/octet-stream",
            raw_bytes=len(body),
            stored_bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
        )

    async def list_prefix(self, prefix: str) -> List[Dict[str, object]]:
        objects: List[Dict[str, object]] = []
        paginator = self.reader_client.get_paginator("list_objects_v2")
        try:
            pages = await asyncio.to_thread(lambda: list(paginator.paginate(Bucket=self.bucket, Prefix=prefix)))
        except Exception as exc:  # noqa: BLE001
            raise ArchiveWriteError("S3 archive prefix listing failed") from exc
        for page in pages:
            objects.extend(page.get("Contents", []))
        return objects

    async def find_body_key(self, event_id: str, created_at: datetime, filename: str) -> Optional[str]:
        """Find a deterministic request/response object by its event ID."""
        timestamp = created_at.astimezone(timezone.utc)
        suffix = f"event_id={event_id}/{filename}"
        for day_delta in (-1, 0, 1):
            day = timestamp + timedelta(days=day_delta)
            day_prefix = f"{self.prefix}/year={day:%Y}/month={day:%m}/day={day:%d}/" if self.prefix else f"year={day:%Y}/month={day:%m}/day={day:%d}/"
            for obj in await self.list_prefix(day_prefix):
                key = str(obj.get("Key", ""))
                if key.endswith(suffix):
                    return key
        return None

    async def get_object_bytes(self, key: str) -> bytes:
        result = await self._read_call("get_object", Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(result["Body"].read)

    async def get_decompressed_body(self, key: str) -> bytes:
        body = await self.get_object_bytes(key)
        if body[:2] == b"\x1f\x8b":
            try:
                return gzip.decompress(body)
            except OSError as exc:
                raise ArchiveWriteError("Archive body is not a valid gzip object") from exc
        return body

    async def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close is not None:
            await asyncio.to_thread(close)
        reader_close = getattr(self.reader_client, "close", None)
        if reader_close is not None and self.reader_client is not self.client:
            await asyncio.to_thread(reader_close)


class ArchiveSession:
    """All S3 objects and completion state for one client interaction."""

    def __init__(self, store: S3ArchiveStore, event_id: str, started_at: datetime, method: str, path: str) -> None:
        self.store = store
        self.event_id = event_id
        self.started_at = started_at
        self.method = method
        self.path = path
        prefix = f"{store.prefix}/" if store.prefix else ""
        self.base_key = f"{prefix}year={started_at:%Y}/month={started_at:%m}/day={started_at:%d}/hour={started_at:%H}/event_id={event_id}"
        self.request: Optional[ArchivedBody] = None
        self.response: Optional[ArchivedBody] = None
        self.response_body = bytearray()
        self.response_status: Optional[int] = None
        self.response_content_type = "application/octet-stream"
        self.finalized = False

    async def store_request(self, body: bytes, content_type: str) -> None:
        self.request = await self.store.put_body(f"{self.base_key}/request.json", body, content_type or "application/json")

    def start_response(self, status_code: int, content_type: str) -> None:
        self.response_status = status_code
        self.response_content_type = content_type or "application/octet-stream"
        # Keep one stable response name even when the client-facing response is
        # SSE. The stored bytes remain the exact original response body.

    async def write_response(self, body: bytes) -> None:
        if self.response_status is None:
            self.start_response(self.response_status or 500, self.response_content_type)
        self.response_body.extend(body)

    async def finish(self, *, complete: bool, termination_reason: Optional[str], metadata: Mapping[str, object]) -> None:
        if self.finalized:
            return
        if self.response_status is not None and self.response_body:
            self.response = await self.store.put_body(
                f"{self.base_key}/response.json",
                bytes(self.response_body),
                self.response_content_type,
            )
        self.finalized = True


class RawArchiveMiddleware:
    """ASGI tee that captures exact client-facing request and response bytes."""

    def __init__(self, app: ASGIApp, settings: Optional[Settings] = None, store: Optional[S3ArchiveStore] = None) -> None:
        self.app = app
        self.settings = settings or get_settings()
        self.store = store if store is not None else get_archive_store()
        self.required = self.settings.ARCHIVE_REQUIRED

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") not in ARCHIVED_PATHS or self.store is None:
            await self.app(scope, receive, send)
            return

        event_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)
        state = scope.setdefault("state", {})
        state["archive_event_id"] = event_id
        state["archive_metadata"] = {}
        session = ArchiveSession(self.store, event_id, started_at, str(scope.get("method")), str(scope.get("path")))
        request_body = bytearray()
        request_stored = False
        archive_disabled = False
        pending_start: Optional[Message] = None
        response_started = False

        request_headers = list(scope.get("headers") or [])
        request_content_type = _header_value(request_headers, b"content-type") or "application/json"

        async def archive_failed(exc: ArchiveWriteError) -> None:
            nonlocal archive_disabled
            logger.exception("Inference archive write failed: event_id=%s", event_id, exc_info=exc)
            if self.required:
                raise exc
            archive_disabled = True

        async def receive_wrapper() -> Message:
            nonlocal request_stored
            message = await receive()
            if message["type"] == "http.request" and not archive_disabled:
                request_body.extend(message.get("body", b""))
                if not message.get("more_body", False) and not request_stored:
                    try:
                        await session.store_request(bytes(request_body), request_content_type)
                        request_stored = True
                    except ArchiveWriteError as exc:
                        await archive_failed(exc)
            return message

        async def send_pending_start() -> None:
            nonlocal pending_start, response_started
            if pending_start is None or response_started:
                return
            await send(pending_start)
            pending_start = None
            response_started = True

        async def send_wrapper(message: Message) -> None:
            nonlocal pending_start
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                if not any(key.lower() == b"x-archive-event-id" for key, _ in headers):
                    headers.append((b"x-archive-event-id", event_id.encode("ascii")))
                pending_start = {**message, "headers": headers}
                session.start_response(
                    int(message["status"]),
                    _header_value(headers, b"content-type") or "application/octet-stream",
                )
                return

            if message["type"] != "http.response.body":
                await send_pending_start()
                await send(message)
                return

            if archive_disabled or not request_stored:
                await send_pending_start()
                await send(message)
                return

            try:
                await session.write_response(message.get("body", b""))
                if not message.get("more_body", False):
                    await session.finish(
                        complete=True,
                        termination_reason=None,
                        metadata=state.get("archive_metadata") or {},
                    )
            except ArchiveWriteError as exc:
                await archive_failed(exc)
            await send_pending_start()
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
            if request_stored and not archive_disabled and not session.finalized:
                await session.finish(
                    complete=False,
                    termination_reason="response_ended_without_final_body",
                    metadata=state.get("archive_metadata") or {},
                )
            await send_pending_start()
        except ArchiveWriteError:
            if response_started:
                raise
            body = json.dumps(
                {
                    "error": {
                        "message": "The required request archive is temporarily unavailable.",
                        "type": "archive_unavailable",
                    }
                },
                separators=(",", ":"),
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        (b"x-archive-event-id", event_id.encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body, "more_body": False})
        except BaseException as exc:
            if request_stored and not archive_disabled and not session.finalized:
                try:
                    await session.finish(
                        complete=False,
                        termination_reason=type(exc).__name__,
                        metadata=state.get("archive_metadata") or {},
                    )
                except ArchiveWriteError:
                    logger.exception("Failed to finalize interrupted inference archive: event_id=%s", event_id)
            raise


@lru_cache
def get_archive_store() -> Optional[S3ArchiveStore]:
    settings = get_settings()
    if not settings.ARCHIVE_ENABLED:
        return None
    return S3ArchiveStore(settings)


async def ensure_archive_ready() -> None:
    store = get_archive_store()
    if store is None:
        return
    try:
        await store.ensure_ready()
    except ArchiveWriteError:
        if get_settings().ARCHIVE_REQUIRED:
            raise
        logger.exception("Optional inference archive is unavailable at startup")
        return
    logger.info("Inference archive ready: bucket=%s prefix=%s", store.bucket, store.prefix or "<root>")


async def close_archive_store() -> None:
    store = get_archive_store()
    if store is not None:
        await store.close()
