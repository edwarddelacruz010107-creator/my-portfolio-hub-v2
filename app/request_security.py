"""Trusted request metadata and correlation helpers.

``request.remote_addr`` is the only client address consumed by application
code.  A deployment may rewrite it through Werkzeug's ``ProxyFix`` only when
the exact trusted proxy hop count is explicitly configured at startup.
"""

from __future__ import annotations

import re
import secrets

from flask import g, request

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def get_client_ip() -> str:
    """Return the proxy-verified peer address, never a raw forwarding header."""
    return (request.remote_addr or "unknown")[:45]


def begin_request_context() -> None:
    """Install a privacy-safe correlation identifier for this request."""
    supplied = (request.headers.get("X-Request-ID") or "").strip()
    g.request_id = supplied if _REQUEST_ID_RE.fullmatch(supplied) else secrets.token_hex(16)
    g.query_count = 0
    g.query_ms = 0.0


def attach_request_context(response):
    """Return the correlation ID without reflecting unvalidated input."""
    response.headers["X-Request-ID"] = getattr(g, "request_id", secrets.token_hex(16))
    return response
