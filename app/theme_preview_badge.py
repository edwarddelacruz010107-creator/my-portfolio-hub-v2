"""Utilities for adding a non-invasive label to read-only theme previews."""

from __future__ import annotations

from markupsafe import escape
from flask import url_for


def inject_theme_preview_badge(html: str, theme_meta: dict | None, label: str = "Theme preview") -> str:
    """Inject a small fixed badge into generated theme preview HTML.

    This keeps each theme template clean while making public/admin previews
    obvious and branded. It is intentionally defensive: if a theme returns a
    partial document without <head> or <body>, the original HTML is still
    returned with the badge appended.
    """
    if not html:
        return html

    meta = theme_meta or {}
    icon = escape(meta.get("icon") or "✨")
    name = escape(meta.get("name") or meta.get("id") or "Theme")
    label_text = escape(label)
    css_href = escape(url_for("static", filename="css/theme-preview-badge.css"))

    css_link = f'<link rel="stylesheet" href="{css_href}">'
    badge = (
        '<div class="mph-theme-preview-badge" role="note" aria-label="Theme preview label">'
        f'<span class="mph-theme-preview-badge__icon" aria-hidden="true">{icon}</span>'
        '<span class="mph-theme-preview-badge__text">'
        f'<strong>{name}</strong><small>{label_text}</small>'
        '</span>'
        '</div>'
    )

    out = str(html)
    lower = out.lower()
    if "theme-preview-badge.css" not in lower:
        head_idx = lower.rfind("</head>")
        if head_idx != -1:
            out = out[:head_idx] + css_link + out[head_idx:]
        else:
            out = css_link + out

    lower = out.lower()
    body_idx = lower.rfind("</body>")
    if body_idx != -1:
        out = out[:body_idx] + badge + out[body_idx:]
    else:
        out += badge
    return out
