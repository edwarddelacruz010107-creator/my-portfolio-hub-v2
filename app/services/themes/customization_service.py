"""Typed theme customization draft/publish/rollback workflow."""
from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import text

from app import db
from app.models.theme_customization import ThemeCustomizationDraft, ThemeCustomizationVersion
from app.services.themes.contract import ThemeContractError, render_customization_css, sanitize_tokens
from app.theme_engine import get_theme_engine, is_supported_theme_id


def _schema(theme_id: str) -> Mapping[str, Any]:
    if not is_supported_theme_id(theme_id):
        raise ThemeContractError("unsupported theme")
    manifest = get_theme_engine().get_theme_meta(theme_id)
    if not manifest:
        raise ThemeContractError("theme is not installed")
    schema = manifest.get("configurable_tokens")
    if not isinstance(schema, Mapping):
        raise ThemeContractError("theme does not expose configurable tokens")
    return schema


def get_draft(tenant_id: int, theme_id: str) -> ThemeCustomizationDraft | None:
    return ThemeCustomizationDraft.query.filter_by(tenant_id=int(tenant_id), theme_id=theme_id).first()


def get_versions(tenant_id: int, theme_id: str, *, limit: int = 20) -> list[ThemeCustomizationVersion]:
    return (
        ThemeCustomizationVersion.query
        .filter_by(tenant_id=int(tenant_id), theme_id=theme_id)
        .order_by(ThemeCustomizationVersion.version_number.desc())
        .limit(max(1, min(int(limit), 100)))
        .all()
    )


def get_published(tenant_id: int, theme_id: str) -> ThemeCustomizationVersion | None:
    return (
        ThemeCustomizationVersion.query
        .filter_by(tenant_id=int(tenant_id), theme_id=theme_id)
        .order_by(ThemeCustomizationVersion.version_number.desc())
        .first()
    )


def save_draft(tenant_id: int, theme_id: str, values: Mapping[str, Any], *, user_id: int | None) -> ThemeCustomizationDraft:
    schema = _schema(theme_id)
    clean = sanitize_tokens(values, schema)
    draft = (
        ThemeCustomizationDraft.query
        .filter_by(tenant_id=int(tenant_id), theme_id=theme_id)
        .with_for_update()
        .first()
    )
    if draft is None:
        published = get_published(int(tenant_id), theme_id)
        draft = ThemeCustomizationDraft(
            tenant_id=int(tenant_id),
            theme_id=theme_id,
            base_version_id=published.id if published else None,
        )
        db.session.add(draft)
    draft.tokens = clean
    draft.updated_by_id = user_id
    db.session.commit()
    return draft


def _next_version_number(tenant_id: int, theme_id: str) -> int:
    bind = db.session.get_bind(mapper=ThemeCustomizationVersion)
    if bind.dialect.name == "postgresql":
        db.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:stream_key, 0))"),
            {"stream_key": f"theme-customization:{int(tenant_id)}:{theme_id}"},
        )
    latest = (
        ThemeCustomizationVersion.query
        .filter_by(tenant_id=int(tenant_id), theme_id=theme_id)
        .order_by(ThemeCustomizationVersion.version_number.desc())
        .with_for_update()
        .first()
    )
    return (latest.version_number if latest else 0) + 1


def publish_draft(tenant_id: int, theme_id: str, *, user_id: int | None) -> ThemeCustomizationVersion:
    _schema(theme_id)
    draft = (
        ThemeCustomizationDraft.query
        .filter_by(tenant_id=int(tenant_id), theme_id=theme_id)
        .with_for_update()
        .first()
    )
    if draft is None:
        raise ThemeContractError("save a draft before publishing")
    version = ThemeCustomizationVersion(
        tenant_id=int(tenant_id),
        theme_id=theme_id,
        version_number=_next_version_number(int(tenant_id), theme_id),
        tokens=dict(draft.tokens or {}),
        source="publish",
        created_by_id=user_id,
    )
    db.session.add(version)
    db.session.flush()
    draft.base_version_id = version.id
    db.session.commit()
    return version


def rollback_to_version(
    tenant_id: int,
    theme_id: str,
    version_id: str,
    *,
    user_id: int | None,
) -> ThemeCustomizationVersion:
    _schema(theme_id)
    target = ThemeCustomizationVersion.query.filter_by(
        id=str(version_id), tenant_id=int(tenant_id), theme_id=theme_id
    ).first()
    if target is None:
        raise ThemeContractError("customization version was not found")
    draft = (
        ThemeCustomizationDraft.query
        .filter_by(tenant_id=int(tenant_id), theme_id=theme_id)
        .with_for_update()
        .first()
    )
    if draft is None:
        draft = ThemeCustomizationDraft(tenant_id=int(tenant_id), theme_id=theme_id)
        db.session.add(draft)
    version = ThemeCustomizationVersion(
        tenant_id=int(tenant_id),
        theme_id=theme_id,
        version_number=_next_version_number(int(tenant_id), theme_id),
        tokens=dict(target.tokens or {}),
        source="rollback",
        restored_from_id=target.id,
        created_by_id=user_id,
    )
    db.session.add(version)
    db.session.flush()
    draft.tokens = dict(target.tokens or {})
    draft.base_version_id = version.id
    draft.updated_by_id = user_id
    db.session.commit()
    return version


def customization_css(tenant_id: int, theme_id: str, *, draft: bool = False) -> str:
    schema = _schema(theme_id)
    record = get_draft(int(tenant_id), theme_id) if draft else get_published(int(tenant_id), theme_id)
    return render_customization_css(record.tokens if record else {}, schema)
