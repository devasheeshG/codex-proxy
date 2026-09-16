"""Configured outbound network paths for account-pinned upstream requests."""

from __future__ import annotations

import ipaddress
import json
import re
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

import httpx

from app import config
from app.utils import account_limiter

_TARGET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LOCK_NAMESPACE = uuid.UUID("79120db4-bc52-4caa-8ab4-1df7e8e61f6f")


class EgressConfigurationError(ValueError):
    """The configured target list is malformed or ambiguous."""


class EgressUnavailable(RuntimeError):
    """The requested outbound path is missing, disabled, or fully occupied."""


@dataclass(frozen=True)
class EgressTarget:
    id: str
    label: str
    kind: str
    proxy_url: Optional[str] = None
    relay_auth: bool = False
    local_address: Optional[str] = None
    interface_name: Optional[str] = None
    private_ip: Optional[str] = None
    public_ip: Optional[str] = None
    max_concurrency: int = 32
    enabled: bool = True

    @property
    def lock_id(self) -> uuid.UUID:
        return uuid.uuid5(_LOCK_NAMESPACE, self.id)

    def public_dict(self) -> dict[str, Any]:
        """Return dashboard-safe metadata without proxy credentials."""
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "interface_name": self.interface_name,
            "private_ip": self.private_ip or self.local_address,
            "public_ip": self.public_ip,
            "max_concurrency": self.max_concurrency,
            "enabled": self.enabled,
        }


class EgressConnection:
    """One HTTPX client plus its cross-worker target-capacity lease."""

    def __init__(self, target: EgressTarget, lease: account_limiter.AccountLease, client: httpx.AsyncClient) -> None:
        self.target = target
        self.lease = lease
        self.client = client
        self.closed = False

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            await self.client.aclose()
        finally:
            self.lease.release()


def _optional_text(value: object) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EgressConfigurationError("Optional egress target values must be strings")
    normalized = value.strip()
    return normalized or None


def _validate_ip(value: Optional[str], field: str) -> Optional[str]:
    if value is None:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise EgressConfigurationError(f"Invalid {field} '{value}'") from exc


def _parse_target(raw: object, default_concurrency: int) -> EgressTarget:
    if not isinstance(raw, Mapping):
        raise EgressConfigurationError("Every egress target must be a JSON object")
    target_id = _optional_text(raw.get("id"))
    if not target_id or not _TARGET_ID_RE.fullmatch(target_id):
        raise EgressConfigurationError("Egress target id must use letters, numbers, '.', '_', ':', or '-'")
    kind = (_optional_text(raw.get("kind")) or "proxy").lower()
    if kind not in {"direct", "local", "proxy"}:
        raise EgressConfigurationError(f"Unsupported egress target kind '{kind}'")
    proxy_url = _optional_text(raw.get("proxy_url"))
    local_address = _validate_ip(_optional_text(raw.get("local_address")), "local_address")
    private_ip = _validate_ip(_optional_text(raw.get("private_ip")), "private_ip")
    public_ip = _validate_ip(_optional_text(raw.get("public_ip")), "public_ip")
    interface_name = _optional_text(raw.get("interface_name"))
    if kind == "proxy":
        if not proxy_url:
            raise EgressConfigurationError(f"Proxy target '{target_id}' requires proxy_url")
        parsed = urlsplit(proxy_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise EgressConfigurationError(f"Proxy target '{target_id}' has an invalid proxy_url")
    elif proxy_url:
        raise EgressConfigurationError(f"Non-proxy target '{target_id}' cannot define proxy_url")
    if kind == "local" and not local_address:
        local_address = private_ip
    if kind == "local" and not local_address:
        raise EgressConfigurationError(f"Local target '{target_id}' requires local_address or private_ip")
    try:
        max_concurrency = int(raw.get("max_concurrency", default_concurrency))
    except (TypeError, ValueError) as exc:
        raise EgressConfigurationError(f"Target '{target_id}' has invalid max_concurrency") from exc
    if not 1 <= max_concurrency <= 32:
        raise EgressConfigurationError(f"Target '{target_id}' max_concurrency must be between 1 and 32")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise EgressConfigurationError(f"Target '{target_id}' enabled must be a boolean")
    relay_auth = raw.get("relay_auth", False)
    if not isinstance(relay_auth, bool):
        raise EgressConfigurationError(f"Target '{target_id}' relay_auth must be a boolean")
    return EgressTarget(
        id=target_id,
        label=_optional_text(raw.get("label")) or target_id,
        kind=kind,
        proxy_url=proxy_url,
        relay_auth=relay_auth,
        local_address=local_address,
        interface_name=interface_name,
        private_ip=private_ip,
        public_ip=public_ip,
        max_concurrency=max_concurrency,
        enabled=enabled,
    )


def parse_targets(raw_json: str, default_concurrency: int = 32) -> tuple[EgressTarget, ...]:
    """Parse and validate the environment-backed target registry."""
    if not raw_json.strip():
        return (
            EgressTarget(
                id="direct",
                label="Default server network path",
                kind="direct",
                max_concurrency=default_concurrency,
            ),
        )
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise EgressConfigurationError("EGRESS_TARGETS_JSON must be valid JSON") from exc
    if not isinstance(payload, list) or not payload:
        raise EgressConfigurationError("EGRESS_TARGETS_JSON must be a non-empty JSON array")
    targets = tuple(_parse_target(item, default_concurrency) for item in payload)
    ids = [target.id for target in targets]
    if len(ids) != len(set(ids)):
        raise EgressConfigurationError("Egress target ids must be unique")
    if not any(target.enabled for target in targets):
        raise EgressConfigurationError("At least one egress target must be enabled")
    return targets


def _client_for(target: EgressTarget, timeout: httpx.Timeout) -> httpx.AsyncClient:
    if target.kind == "proxy":
        proxy: httpx.Proxy | str = target.proxy_url or ""
        if target.relay_auth:
            settings = config.get_settings()
            proxy = httpx.Proxy(
                target.proxy_url or "",
                auth=(settings.EGRESS_RELAY_USERNAME, settings.EGRESS_RELAY_TOKEN),
            )
        return httpx.AsyncClient(timeout=timeout, proxy=proxy, trust_env=False)
    if target.kind == "local":
        transport = httpx.AsyncHTTPTransport(local_address=target.local_address)
        return httpx.AsyncClient(timeout=timeout, transport=transport, trust_env=False)
    return httpx.AsyncClient(timeout=timeout)


def sync_client(target: EgressTarget, timeout: float | httpx.Timeout) -> httpx.Client:
    """Create a synchronous client that uses the same target as account traffic."""
    if target.kind == "proxy":
        proxy: httpx.Proxy | str = target.proxy_url or ""
        if target.relay_auth:
            settings = config.get_settings()
            proxy = httpx.Proxy(
                target.proxy_url or "",
                auth=(settings.EGRESS_RELAY_USERNAME, settings.EGRESS_RELAY_TOKEN),
            )
        return httpx.Client(timeout=timeout, proxy=proxy, trust_env=False)
    if target.kind == "local":
        transport = httpx.HTTPTransport(local_address=target.local_address)
        return httpx.Client(timeout=timeout, transport=transport, trust_env=False)
    return httpx.Client(timeout=timeout)


def provider_call_kwargs(target: EgressTarget | None) -> dict[str, EgressTarget]:
    """Only pass an override when the target changes the historical direct path."""
    if target is None or target.kind == "direct":
        return {}
    return {"egress_target": target}


class EgressPool:
    def __init__(self, targets: tuple[EgressTarget, ...]) -> None:
        self._targets = targets
        self._by_id = {target.id: target for target in targets}

    @classmethod
    def from_settings(cls) -> "EgressPool":
        settings = config.get_settings()
        targets = parse_targets(settings.EGRESS_TARGETS_JSON, settings.DEFAULT_EGRESS_MAX_CONCURRENCY)
        if any(target.relay_auth for target in targets) and len(settings.EGRESS_RELAY_TOKEN or "") < 24:
            raise EgressConfigurationError("EGRESS_RELAY_TOKEN must contain at least 24 characters when relay_auth is enabled")
        return cls(targets)

    def targets(self) -> tuple[EgressTarget, ...]:
        return self._targets

    def default_target(self) -> EgressTarget:
        """Return the first enabled target in configuration order.

        The first regional path is the deterministic fallback for accounts that
        do not yet have an explicit target assignment. It is deliberately not
        a rotating pool.
        """
        for target in self._targets:
            if target.enabled:
                return target
        raise EgressUnavailable("No enabled egress targets are configured")

    def resolve(self, target_id: Optional[str]) -> EgressTarget:
        if target_id is None:
            return self.default_target()
        target = self._by_id.get(target_id)
        if target is None:
            raise EgressUnavailable(f"Egress target '{target_id}' is not configured")
        if not target.enabled:
            raise EgressUnavailable(f"Egress target '{target_id}' is disabled")
        return target

    def has_target(self, target_id: str) -> bool:
        target = self._by_id.get(target_id)
        return bool(target and target.enabled)

    def acquire(self, target_id: Optional[str], *, priority: int = 1) -> EgressConnection:
        """Acquire the explicit target, or the first configured target by default."""
        target = self.resolve(target_id)
        try:
            lease = account_limiter.try_acquire(target.lock_id, target.max_concurrency, priority)
        except account_limiter.AccountBusy as exc:
            raise EgressUnavailable(f"Egress target '{target.id}' is at its concurrency limit") from exc
        try:
            client = _client_for(target, httpx.Timeout(600.0, connect=15.0))
        except Exception:
            lease.release()
            raise
        return EgressConnection(target, lease, client)


_pool: Optional[EgressPool] = None
_pool_lock = threading.Lock()


def get_pool() -> EgressPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = EgressPool.from_settings()
    return _pool


def reset_pool_for_tests() -> None:
    global _pool
    with _pool_lock:
        _pool = None
