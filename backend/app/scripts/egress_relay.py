"""Authenticated HTTPS CONNECT relay with one listener per AWS source address.

The relay runs with host networking so it can bind outbound sockets to EC2
secondary private addresses. The application stays on its normal Docker bridge
and selects a listener through HTTPX's proxy support.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import ipaddress
import json
import logging
import os
import signal
import socket
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

MAX_HEADER_BYTES = 16 * 1024
HEADER_TIMEOUT_SECONDS = 10
CONNECT_TIMEOUT_SECONDS = 15
BUFFER_BYTES = 64 * 1024

logger = logging.getLogger("egress-relay")


@dataclass(frozen=True)
class RelayTarget:
    id: str
    listen_port: int
    source_ip: str


def _target_host_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    normalized = host.rstrip(".").lower()
    for allowed in allowed_hosts:
        allowed = allowed.rstrip(".").lower()
        if allowed.startswith(".") and normalized.endswith(allowed) and normalized != allowed[1:]:
            return True
        if normalized == allowed:
            return True
    return False


def _parse_targets(raw_json: str) -> tuple[RelayTarget, ...]:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError("EGRESS_TARGETS_JSON must be valid JSON") from exc
    if not isinstance(payload, list):
        raise ValueError("EGRESS_TARGETS_JSON must be a JSON array")
    targets: list[RelayTarget] = []
    for raw in payload:
        if not isinstance(raw, Mapping) or raw.get("kind") != "proxy" or raw.get("enabled", True) is False:
            continue
        target_id = str(raw.get("id") or "").strip()
        source_ip = str(raw.get("private_ip") or raw.get("local_address") or "").strip()
        proxy_url = str(raw.get("proxy_url") or "").strip()
        parsed = urlsplit(proxy_url)
        if not target_id or not source_ip or parsed.port is None:
            raise ValueError("Every enabled proxy target needs id, private_ip, and a proxy_url port")
        try:
            source_ip = str(ipaddress.ip_address(source_ip))
        except ValueError as exc:
            raise ValueError(f"Relay target '{target_id}' has an invalid source IP") from exc
        targets.append(RelayTarget(target_id, parsed.port, source_ip))
    if not targets:
        raise ValueError("No enabled proxy targets were found in EGRESS_TARGETS_JSON")
    if len({target.listen_port for target in targets}) != len(targets):
        raise ValueError("Relay target proxy ports must be unique")
    return tuple(targets)


def _verify_local_address(source_ip: str) -> None:
    family = socket.AF_INET6 if ":" in source_ip else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.bind((source_ip, 0))
    except OSError as exc:
        raise RuntimeError(f"Source IP {source_ip} is not assigned to this host") from exc
    finally:
        sock.close()


async def _read_headers(reader: asyncio.StreamReader) -> bytes:
    try:
        return await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEADER_TIMEOUT_SECONDS)
    except asyncio.LimitOverrunError as exc:
        raise ValueError("Proxy request headers are too large") from exc
    except asyncio.IncompleteReadError as exc:
        raise ValueError("Incomplete proxy request") from exc


def _parse_connect_request(raw: bytes) -> tuple[str, int, dict[str, str]]:
    if len(raw) > MAX_HEADER_BYTES:
        raise ValueError("Proxy request headers are too large")
    try:
        lines = raw.decode("iso-8859-1").split("\r\n")
        method, authority, version = lines[0].split(" ", 2)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Malformed proxy request") from exc
    if method.upper() != "CONNECT" or not version.startswith("HTTP/1."):
        raise ValueError("Only HTTPS CONNECT requests are supported")
    parsed = urlsplit(f"//{authority}")
    if not parsed.hostname or parsed.port is None:
        raise ValueError("CONNECT requires host:port")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            break
        if ":" not in line:
            raise ValueError("Malformed proxy header")
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return parsed.hostname, parsed.port, headers


async def _respond(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
    writer.write(f"HTTP/1.1 {status} {reason}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode())
    await writer.drain()


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(BUFFER_BYTES):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.write_eof()
        except (AttributeError, OSError):
            pass


async def _handle_proxy(
    target: RelayTarget,
    expected_auth: str,
    allowed_hosts: tuple[str, ...],
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    peer = writer.get_extra_info("peername")
    upstream_writer: asyncio.StreamWriter | None = None
    try:
        raw = await _read_headers(reader)
        host, port, headers = _parse_connect_request(raw)
        if not hmac.compare_digest(headers.get("proxy-authorization", ""), expected_auth):
            await _respond(writer, 407, "Proxy Authentication Required")
            return
        if port != 443 or not _target_host_allowed(host, allowed_hosts):
            await _respond(writer, 403, "Forbidden")
            return
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, local_addr=(target.source_ip, 0)),
                CONNECT_TIMEOUT_SECONDS,
            )
        except (OSError, asyncio.TimeoutError):
            logger.exception("target=%s could not connect to %s:%s", target.id, host, port)
            await _respond(writer, 502, "Bad Gateway")
            return
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(
            _pump(reader, upstream_writer),
            _pump(upstream_reader, writer),
            return_exceptions=True,
        )
    except (ValueError, asyncio.TimeoutError) as exc:
        logger.warning("target=%s rejected peer=%s: %s", target.id, peer, exc)
        try:
            await _respond(writer, 400, "Bad Request")
        except ConnectionError:
            pass
    finally:
        if upstream_writer is not None:
            upstream_writer.close()
            await upstream_writer.wait_closed()
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


async def _handle_health(
    targets: tuple[RelayTarget, ...],
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEADER_TIMEOUT_SECONDS)
        body = json.dumps(
            {
                "status": "healthy",
                "targets": [{"id": target.id, "listen_port": target.listen_port, "source_ip": target.source_ip} for target in targets],
            },
            separators=(",", ":"),
        ).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n" + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
        )
        await writer.drain()
    except (ConnectionError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


async def run() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    token = os.getenv("EGRESS_RELAY_TOKEN", "")
    if len(token) < 24:
        raise RuntimeError("EGRESS_RELAY_TOKEN must contain at least 24 characters")
    targets = _parse_targets(os.getenv("EGRESS_TARGETS_JSON", ""))
    for target in targets:
        _verify_local_address(target.source_ip)
    username = os.getenv("EGRESS_RELAY_USERNAME", "proxy")
    encoded = base64.b64encode(f"{username}:{token}".encode()).decode()
    expected_auth = f"Basic {encoded}"
    allowed_hosts = tuple(
        item.strip().lower()
        for item in os.getenv(
            "EGRESS_RELAY_ALLOWED_HOSTS",
            "chatgpt.com,.chatgpt.com,openai.com,.openai.com",
        ).split(",")
        if item.strip()
    )
    listen_host = os.getenv("EGRESS_RELAY_LISTEN_HOST", "0.0.0.0")
    servers = []
    for target in targets:
        server = await asyncio.start_server(
            lambda reader, writer, selected=target: _handle_proxy(
                selected,
                expected_auth,
                allowed_hosts,
                reader,
                writer,
            ),
            listen_host,
            target.listen_port,
            limit=MAX_HEADER_BYTES,
        )
        servers.append(server)
        logger.info(
            "target=%s listening=%s:%s source_ip=%s",
            target.id,
            listen_host,
            target.listen_port,
            target.source_ip,
        )
    health_port = int(os.getenv("EGRESS_RELAY_HEALTH_PORT", "18080"))
    health_server = await asyncio.start_server(
        lambda reader, writer: _handle_health(targets, reader, writer),
        "127.0.0.1",
        health_port,
        limit=MAX_HEADER_BYTES,
    )
    servers.append(health_server)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    for server in servers:
        server.close()
    await asyncio.gather(*(server.wait_closed() for server in servers))


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
