"""
app/limiter_config.py — Flask-Limiter proxy-aware key function

Handles Cloudflare / Render / any reverse-proxy that sets X-Forwarded-For.

Problem:
  - get_remote_address() always returns the proxy IP (e.g. 10.0.0.1) in
    multi-worker Gunicorn behind Render/Cloudflare — every worker sees the
    same "client IP" → false positive 429s for real users.
  - memory:// storage does not share state across workers → bots can bypass
    limits by hitting different workers.

Solution:
  1. Proxy-aware IP extraction: trust CF-Connecting-IP first, then the first
     non-RFC-1918 address in X-Forwarded-For, then REMOTE_ADDR as last resort.
  2. Redis-backed storage (configured via RATELIMIT_STORAGE_URL in config.py).
  3. RATELIMIT_STORAGE_URL is set in ProductionConfig from REDIS_URL env var.
"""
from __future__ import annotations

import ipaddress
import logging

from flask import request

logger = logging.getLogger(__name__)

# RFC-1918 private ranges + loopback — never trust as a real client IP
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _is_private(ip: str) -> bool:
    """Return True if IP is a private/loopback address."""
    try:
        addr = ipaddress.ip_address(ip.strip())
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        return False


def _extract_real_ip() -> str:
    """
    Extract the real client IP from request headers.

    Priority:
      1. CF-Connecting-IP  (Cloudflare — single, authoritative)
      2. X-Forwarded-For   (first non-private IP in the chain)
      3. REMOTE_ADDR       (direct connection fallback)
    """
    # Cloudflare sets this to the real client IP — most authoritative
    cf_ip = request.headers.get("CF-Connecting-IP", "").strip()
    if cf_ip and not _is_private(cf_ip):
        return cf_ip

    # X-Forwarded-For can be a comma-separated list; pick leftmost non-private
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        for candidate in xff.split(","):
            candidate = candidate.strip()
            if candidate and not _is_private(candidate):
                return candidate

    # Direct connection — trust REMOTE_ADDR
    return request.remote_addr or "127.0.0.1"


def create_limiter_key_func():
    """
    Key function for Flask-Limiter.

    Returns the real client IP, correctly extracted behind Cloudflare/Render.
    This is the function passed as `key_func` to the Limiter() constructor.
    """
    ip = _extract_real_ip()
    logger.debug("Rate limiter key: %s", ip)
    return ip
