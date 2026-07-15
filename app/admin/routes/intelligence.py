"""Authenticated portfolio-intelligence workspace."""
from __future__ import annotations

from datetime import datetime
from flask import flash, redirect, render_template, url_for

from app.admin.blueprint import admin, admin_required, _load_tenant_profile
from app.services.custom_domain_service import tenant_portfolio_public_url
from app.services.intelligence.domain import ACTION_ROUTE_ALLOWLIST
from app.services.intelligence.intelligence_service import get_portfolio_intelligence


@admin.route("/intelligence")
@admin_required
def portfolio_intelligence():
    profile = _load_tenant_profile()
    if profile is None:
        flash("Create your profile to start portfolio intelligence.", "info")
        return redirect(url_for("admin.edit_profile"))

    result = get_portfolio_intelligence(profile.tenant_id, persist=True)
    for dimension in result["dimensions"]:
        endpoint = dimension.get("action_route")
        dimension["action_url"] = url_for(endpoint) if endpoint in ACTION_ROUTE_ALLOWLIST else None
    for recommendation in result["recommendations"]:
        endpoint = recommendation.get("action_route")
        recommendation["action_url"] = url_for(endpoint) if endpoint in ACTION_ROUTE_ALLOWLIST else None

    canonical_url = tenant_portfolio_public_url(profile.tenant_slug, external=True)
    seo_preview = {
        "title": (profile.meta_title or "").strip(),
        "description": (profile.meta_description or "").strip(),
        "canonical": canonical_url,
        "social_image": (profile.og_image or "").strip(),
        "indexable": bool(profile.seo_indexable),
    }
    calculated_at = datetime.fromisoformat(result["calculated_at"]) if result.get("calculated_at") else None
    return render_template(
        "admin/intelligence.html",
        profile=profile,
        intelligence=result,
        seo_preview=seo_preview,
        calculated_label=calculated_at.strftime("%b %d, %Y at %H:%M UTC") if calculated_at else "Unavailable",
    )
