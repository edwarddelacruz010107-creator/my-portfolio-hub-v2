"""Privacy-safe request and SQL telemetry for release budgets."""

from __future__ import annotations

import hashlib
import logging
import time

from flask import g, has_request_context

logger = logging.getLogger(__name__)
_installed = False


def install_query_observer(app) -> None:
    """Install one process-wide SQLAlchemy observer without logging values."""
    global _installed
    if _installed:
        return
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "before_cursor_execute")
    def _before(_conn, _cursor, _statement, _parameters, context, _many):
        context._portfolio_query_started = time.perf_counter()

    @event.listens_for(Engine, "after_cursor_execute")
    def _after(_conn, _cursor, statement, _parameters, context, _many):
        elapsed_ms = (time.perf_counter() - context._portfolio_query_started) * 1000
        if has_request_context():
            g.query_count = int(getattr(g, "query_count", 0)) + 1
            g.query_ms = float(getattr(g, "query_ms", 0.0)) + elapsed_ms
        threshold = float(app.config.get("SLOW_QUERY_THRESHOLD_MS", 500))
        if elapsed_ms >= threshold:
            fingerprint = hashlib.sha256(statement.encode("utf-8", "replace")).hexdigest()[:16]
            logger.warning(
                "slow_query request_id=%s fingerprint=%s duration_ms=%.1f",
                getattr(g, "request_id", "background") if has_request_context() else "background",
                fingerprint,
                elapsed_ms,
            )

    _installed = True


def attach_server_timing(response):
    """Expose aggregate timing only; SQL text and parameter values stay private."""
    count = int(getattr(g, "query_count", 0))
    query_ms = float(getattr(g, "query_ms", 0.0))
    response.headers["Server-Timing"] = f"db;dur={query_ms:.1f};desc=\"{count} queries\""
    return response

