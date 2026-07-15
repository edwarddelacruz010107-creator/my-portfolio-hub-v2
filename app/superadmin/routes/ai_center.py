"""Superadmin AI Center routes: authenticated transport over validated services."""
from __future__ import annotations

import secrets

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import db, limiter
from app.services.ai import get_ai_service
from app.services.ai.center_service import (
    activate_prompt_version,
    get_center_context,
    publish_prompt_version,
    save_feature_policy,
    save_model,
    save_provider,
)
from app.services.ai.domain import AIContractError, AIRequest, AIUnavailableError
from app.superadmin.blueprint import superadmin, superadmin_required


TABS = frozenset({
    "overview", "providers", "models", "features", "prompts",
    "knowledge", "usage", "logs", "test",
})


def _tab(value: str | None) -> str:
    candidate = str(value or "overview").strip().lower()
    return candidate if candidate in TABS else "overview"


def _redirect(tab: str):
    return redirect(url_for("superadmin.ai_center", tab=_tab(tab)))


def _handle_mutation(callback, success: str, tab: str):
    try:
        callback()
        flash(success, "success")
    except (AIContractError, AIUnavailableError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception:
        db.session.rollback()
        flash("The AI Center change could not be saved.", "danger")
    return _redirect(tab)


@superadmin.route("/ai", methods=["GET"])
@superadmin_required
def ai_center():
    selected_tab = _tab(request.args.get("tab"))
    context = get_center_context(selected_tab=selected_tab)
    return render_template(
        "superadmin/ai_center.html",
        page_title="AI Center",
        test_result=None,
        **context,
    )


@superadmin.route("/ai/providers", methods=["POST"])
@superadmin_required
def ai_provider_save():
    return _handle_mutation(
        lambda: save_provider(request.form, actor_user_id=int(current_user.id)),
        "Provider configuration saved.",
        "providers",
    )


@superadmin.route("/ai/models", methods=["POST"])
@superadmin_required
def ai_model_save():
    payload = request.form.to_dict(flat=True)
    payload["capabilities"] = request.form.getlist("capabilities")
    return _handle_mutation(
        lambda: save_model(payload, actor_user_id=int(current_user.id)),
        "Model configuration saved.",
        "models",
    )


@superadmin.route("/ai/features", methods=["POST"])
@superadmin_required
def ai_feature_save():
    return _handle_mutation(
        lambda: save_feature_policy(request.form, actor_user_id=int(current_user.id)),
        "Feature policy saved.",
        "features",
    )


@superadmin.route("/ai/prompts", methods=["POST"])
@superadmin_required
def ai_prompt_publish():
    return _handle_mutation(
        lambda: publish_prompt_version(request.form, actor_user_id=int(current_user.id)),
        "Immutable prompt version published.",
        "prompts",
    )


@superadmin.route("/ai/prompts/<prompt_id>/versions/<version_id>/activate", methods=["POST"])
@superadmin_required
def ai_prompt_activate(prompt_id: str, version_id: str):
    return _handle_mutation(
        lambda: activate_prompt_version(
            prompt_id, version_id, actor_user_id=int(current_user.id)
        ),
        "Prompt version activated.",
        "prompts",
    )


@superadmin.route("/ai/test", methods=["POST"])
@superadmin_required
@limiter.limit("5 per minute")
def ai_test_console():
    if request.form.get("billing_ack") != "yes":
        flash("Acknowledge that a live provider request may incur cost.", "danger")
        return _redirect("test")
    try:
        max_output = int(request.form.get("max_output_units") or 256)
        ai_request = AIRequest(
            operation="text",
            feature_key=request.form.get("feature_key") or "",
            input_text=request.form.get("input_text") or "",
            system_text=request.form.get("system_text") or "",
            max_output_units=max_output,
            temperature=0.2,
        )
        result = get_ai_service().execute(
            ai_request,
            tenant_id=None,
            user_id=int(current_user.id),
            idempotency_key=f"superadmin-test-{secrets.token_urlsafe(32)}",
        )
        context = get_center_context(selected_tab="test")
        return render_template(
            "superadmin/ai_center.html",
            page_title="AI Center",
            test_result=result,
            **context,
        )
    except (AIContractError, AIUnavailableError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception:
        db.session.rollback()
        flash("The live provider request failed. Review the redacted job log.", "danger")
    return _redirect("test")
