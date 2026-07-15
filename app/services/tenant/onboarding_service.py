"""Real-data-only tenant onboarding workflow.

New accounts receive an empty portfolio and a durable setup checklist. This
module intentionally never creates projects, skills, services, testimonials,
certificates, or attributed claims on a tenant's behalf.
"""
from __future__ import annotations

import logging
from typing import Any

from app import db
from app.models.core import OnboardingWorkflow

logger = logging.getLogger(__name__)

_INITIAL_STEPS = {
    'profile': False,
    'project': False,
    'theme': False,
    'publish': False,
}


def ensure_onboarding_workspace(
    user,
    *,
    display_name: str | None = None,
    commit: bool = True,
) -> OnboardingWorkflow:
    """Ensure only the real profile shell and workflow metadata exist."""
    tenant_id = getattr(user, 'tenant_id', None)
    tenant_slug = (getattr(user, 'tenant_slug', None) or '').strip().lower()
    if not tenant_id or not tenant_slug:
        raise ValueError('User missing tenant information')

    from app.models.tenant_data import Profile

    profile = Profile.query.filter_by(tenant_id=tenant_id).first()
    if profile is None:
        profile = Profile(
            tenant_id=tenant_id,
            tenant_slug=tenant_slug,
            name=(display_name or getattr(user, 'username', None) or tenant_slug).strip(),
            email=(getattr(user, 'email', None) or '').strip().lower(),
            title='',
            subtitle='',
            bio='',
            is_available=False,
        )
        db.session.add(profile)

    workflow = OnboardingWorkflow.query.filter_by(tenant_id=tenant_id).first()
    if workflow is None:
        workflow = OnboardingWorkflow(
            tenant_id=tenant_id,
            tenant_slug=tenant_slug,
            state='active',
            step_state=dict(_INITIAL_STEPS),
        )
        db.session.add(workflow)

    if commit:
        db.session.commit()

    logger.info('Onboarding workspace ready with empty portfolio tenant=%s', tenant_slug)
    return workflow


def build_onboarding_checklist(*, tenant_id: int, tenant_slug: str, profile=None) -> dict[str, Any]:
    """Build an honest checklist from persisted workflow plus real content."""
    from app.models.tenant_data import Project

    workflow = OnboardingWorkflow.query.filter_by(tenant_id=tenant_id).first()
    if workflow is None:
        return {'state': 'unavailable', 'completed': 0, 'total': 4, 'steps': []}

    has_profile = bool(
        profile
        and (getattr(profile, 'name', '') or '').strip()
        and (getattr(profile, 'title', '') or '').strip()
        and (getattr(profile, 'bio', '') or '').strip()
    )
    has_project = bool(Project.query.filter_by(tenant_id=tenant_id).first())
    has_published = bool(Project.query.filter_by(tenant_id=tenant_id, status='published').first())
    has_theme_choice = bool(profile and (getattr(profile, 'selected_theme', '') or 'default') != 'default')

    step_values = {
        'profile': has_profile,
        'project': has_project,
        'theme': has_theme_choice,
        'publish': has_published,
    }
    steps = [
        {'key': 'profile', 'label': 'Complete your profile', 'done': has_profile, 'endpoint': 'admin.edit_profile'},
        {'key': 'project', 'label': 'Add a real project', 'done': has_project, 'endpoint': 'admin.new_project'},
        {'key': 'theme', 'label': 'Choose a theme', 'done': has_theme_choice, 'endpoint': 'admin.appearance'},
        {'key': 'publish', 'label': 'Publish your first project', 'done': has_published, 'endpoint': 'admin.projects'},
    ]
    completed = sum(1 for value in step_values.values() if value)
    return {
        'state': 'completed' if completed == len(steps) else workflow.state,
        'completed': completed,
        'total': len(steps),
        'steps': steps,
        'tenant_slug': tenant_slug,
    }


def create_default_portfolio_for(user, commit: bool = True) -> bool:
    """Backward-compatible adapter; no sample portfolio content is created."""
    ensure_onboarding_workspace(user, commit=commit)
    return True
