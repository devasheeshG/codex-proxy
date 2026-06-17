# Path: app/utils/request_context.py
# Description: Request-scoped correlation fields (request_id, endpoint, method) for structured logging, via contextvars.

from contextvars import ContextVar
from typing import Dict, Optional
from uuid import uuid4

_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
_endpoint: ContextVar[Optional[str]] = ContextVar("endpoint", default=None)
_method: ContextVar[Optional[str]] = ContextVar("method", default=None)


def set_request_context(request_id: Optional[str] = None, endpoint: Optional[str] = None, method: Optional[str] = None) -> str:
    """Bind request-scoped correlation data, generating a request id when one isn't supplied. Returns the request id."""
    # Keep the complete UUID so high-volume archives and logs have an
    # effectively collision-free correlation key.
    request_id = request_id or f"req_{uuid4().hex}"
    _request_id.set(request_id)
    if endpoint:
        _endpoint.set(endpoint)
    if method:
        _method.set(method)
    return request_id


def get_request_context() -> Dict[str, str]:
    """Return the currently-bound correlation fields (only those that are set)."""
    pairs = (("request_id", _request_id.get()), ("endpoint", _endpoint.get()), ("method", _method.get()))
    return {key: value for key, value in pairs if value}


def clear_request_context() -> None:
    """Reset all request-scoped correlation data."""
    _request_id.set(None)
    _endpoint.set(None)
    _method.set(None)
