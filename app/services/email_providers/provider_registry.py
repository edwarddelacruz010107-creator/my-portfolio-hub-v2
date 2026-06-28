"""
app/services/email_providers/provider_registry.py

Resolves the ordered, active provider list from GlobalEmailConfig.
Always reads fresh from DB (never uses stale SQLAlchemy identity map).

After every save/toggle the caller MUST call:
    db.session.commit()
    db.session.expire_all()

Then the registry re-reads config fresh via GlobalEmailConfig.get(fresh=True).
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from .base_provider import BaseEmailProvider
from .mailersend_provider import MailerSendProvider
from .resend_provider import ResendProvider
from .smtp_provider import SMTPProvider

logger = logging.getLogger("email_provider.registry")


def _env(key: str, fallback: str = "") -> str:
    return os.environ.get(key, "").strip() or fallback


def get_active_providers(portal: str = "superadmin") -> List[BaseEmailProvider]:
    """
    Return the list of configured, active providers in priority order.

    Reads GlobalEmailConfig fresh from DB to avoid stale state.
    Falls back gracefully if DB is unavailable.

    Args:
        portal: 'superadmin' | 'admin' | 'tenant'

    Returns:
        Ordered list of BaseEmailProvider instances ready to use.
    """
    cfg = _load_config_fresh()
    priority = _get_priority(cfg)
    providers: List[BaseEmailProvider] = []

    for name in priority:
        provider = _build_provider(name, cfg, portal)
        if provider is not None and provider.is_configured():
            providers.append(provider)
            logger.debug("registry: added provider '%s' for portal=%s", name, portal)
        else:
            logger.debug("registry: skipping provider '%s' (not configured or not active)", name)

    logger.info(
        "registry: resolved %d active provider(s) for portal=%s: %s",
        len(providers),
        portal,
        [p.provider_name for p in providers],
    )
    return providers


def _load_config_fresh():
    """Load GlobalEmailConfig fresh, bypassing SQLAlchemy identity map."""
    try:
        from app.models.core import GlobalEmailConfig
        cfg = GlobalEmailConfig.get(fresh=True)
        return cfg
    except Exception as exc:
        logger.error("registry: failed to load GlobalEmailConfig — %s", exc)
        return None


def _get_priority(cfg) -> List[str]:
    if cfg is not None:
        try:
            return cfg.get_sa_provider_priority()
        except Exception:
            pass
    return ["mailersend", "smtp", "resend"]


def _is_active(cfg, provider_name: str) -> bool:
    """Return True if the provider's active toggle is enabled (defaults shown per provider)."""
    if cfg is None:
        return False
    try:
        if provider_name == "mailersend":
            val = cfg.sa_mailersend_active
            return val if val is not None else True  # mailersend defaults ON
        if provider_name == "smtp":
            return bool(cfg.sa_smtp_active)
        if provider_name == "resend":
            return bool(cfg.sa_resend_active)
    except Exception:
        pass
    return False


def _build_provider(name: str, cfg, portal: str) -> Optional[BaseEmailProvider]:
    """Build a provider instance from DB config, with env fallbacks."""
    if not _is_active(cfg, name):
        return None

    if name == "mailersend":
        return _build_mailersend(cfg, portal)
    if name == "smtp":
        return _build_smtp(cfg)
    if name == "resend":
        return _build_resend(cfg)

    logger.warning("registry: unknown provider name '%s'", name)
    return None


def _build_mailersend(cfg, portal: str) -> Optional[MailerSendProvider]:
    api_key = ""
    sender_email = ""
    sender_name = "Portfolio CMS"

    if cfg is not None:
        try:
            api_key = cfg.get_portal_key(portal) or ""
            sender_email = cfg.get_portal_sender_email(portal) or ""
            sender_name = cfg.get_portal_sender_name(portal) or "Portfolio CMS"
        except Exception as exc:
            logger.error("registry.mailersend: error reading cfg — %s", exc)

    # Env fallbacks
    api_key = api_key or _env("MAILERSEND_API_KEY")
    sender_email = sender_email or _env("MAILERSEND_FROM_EMAIL")
    sender_name = sender_name or _env("MAILERSEND_FROM_NAME", "Portfolio CMS")

    if not api_key or not sender_email:
        logger.debug("registry.mailersend: missing api_key or sender_email, skipping")
        return None

    return MailerSendProvider(
        api_key=api_key,
        sender_email=sender_email,
        sender_name=sender_name,
    )


def _build_smtp(cfg) -> Optional[SMTPProvider]:
    host = ""
    port = 587
    username = ""
    password = ""
    sender_email = ""
    sender_name = "Portfolio CMS"
    encryption = "tls"

    if cfg is not None:
        try:
            host = cfg.sa_smtp_host or ""
            port = cfg.sa_smtp_port or 587
            username = cfg.sa_smtp_username or ""
            # Decrypt password — explicitly catch decryption errors
            try:
                password = cfg.sa_smtp_password or ""
            except Exception as exc:
                logger.error("registry.smtp: password decryption failed — %s", type(exc).__name__)
                password = ""
            sender_email = cfg.sa_smtp_sender_email or ""
            sender_name = cfg.sa_smtp_sender_name or "Portfolio CMS"
            encryption = cfg.sa_smtp_encryption or "tls"
        except Exception as exc:
            logger.error("registry.smtp: error reading cfg — %s", exc)

    # Env fallbacks
    host = host or _env("SMTP_HOST")
    username = username or _env("SMTP_USERNAME")
    password = password or _env("SMTP_PASSWORD")
    sender_email = sender_email or _env("SMTP_FROM_EMAIL")
    sender_name = sender_name or _env("SMTP_FROM_NAME", "Portfolio CMS")
    if not port:
        try:
            port = int(_env("SMTP_PORT", "587"))
        except ValueError:
            port = 587

    if not host or not username or not password or not sender_email:
        logger.debug("registry.smtp: incomplete config, skipping")
        return None

    return SMTPProvider(
        host=host,
        port=port,
        username=username,
        password=password,
        sender_email=sender_email,
        sender_name=sender_name,
        encryption=encryption,
    )


def _build_resend(cfg) -> Optional[ResendProvider]:
    api_key = ""
    sender_email = ""
    sender_name = "Portfolio CMS"

    if cfg is not None:
        try:
            # Explicitly decrypt — this was the root cause of "Not Configured":
            # the old code read the raw encrypted blob, saw a non-empty string,
            # and considered it "configured", but never actually decrypted.
            # ResendProvider.is_configured() receives the decrypted key here.
            try:
                api_key = cfg.sa_resend_api_key or ""
            except Exception as exc:
                logger.error("registry.resend: key decryption failed — %s", type(exc).__name__)
                api_key = ""
            sender_email = cfg.sa_resend_sender_email or ""
            sender_name = cfg.sa_resend_sender_name or "Portfolio CMS"
        except Exception as exc:
            logger.error("registry.resend: error reading cfg — %s", exc)

    # Env fallbacks
    api_key = api_key or _env("RESEND_API_KEY")
    sender_email = sender_email or _env("RESEND_FROM_EMAIL")
    sender_name = sender_name or _env("RESEND_FROM_NAME", "Portfolio CMS")

    if not api_key or not sender_email:
        logger.debug("registry.resend: missing api_key or sender_email, skipping")
        return None

    return ResendProvider(
        api_key=api_key,
        sender_email=sender_email,
        sender_name=sender_name,
    )


def get_provider_status(portal: str = "superadmin") -> dict:
    """
    Return the current configured/active status of all providers.
    Always fresh from DB. Used by UI badges and provider-status endpoint.
    """
    cfg = _load_config_fresh()
    priority = _get_priority(cfg)

    statuses = {}
    for name in ["mailersend", "smtp", "resend"]:
        provider = _build_provider(name, cfg, portal)
        active = _is_active(cfg, name)
        configured = (provider is not None and provider.is_configured()) if active else False
        # Even if not active, check if configured
        if not active:
            # Build without active check to detect configured state
            _temp = _build_unchecked(name, cfg, portal)
            configured_regardless = _temp is not None and _temp.is_configured()
        else:
            configured_regardless = configured

        statuses[name] = {
            "configured": configured_regardless,
            "active": active,
        }

    return {
        "providers": statuses,
        "priority": priority,
    }


def _build_unchecked(name: str, cfg, portal: str) -> Optional[BaseEmailProvider]:
    """Build provider without checking active flag — for status checks only."""
    if name == "mailersend":
        return _build_mailersend(cfg, portal)
    if name == "smtp":
        return _build_smtp(cfg)
    if name == "resend":
        return _build_resend(cfg)
    return None
