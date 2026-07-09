"""Runtime safety checks for the platform-owner duplicate email policy.

These tests intentionally monkeypatch the repository lookup so they can verify
policy behavior without creating database rows or touching migrations.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.auth import email_policy

OWNER = email_policy.OWNER_SHARED_EMAIL


def _user(id: int, *, email: str = OWNER, superadmin: bool = False, slug: str = "tenant"):
    return SimpleNamespace(
        id=id,
        email=email,
        is_superadmin=superadmin,
        tenant_slug=slug,
        slug=slug,
    )


def test_public_signup_blocks_owner_shared_email(monkeypatch):
    monkeypatch.setattr(email_policy, "get_email_matches", lambda email, include_target=None: [])
    with pytest.raises(email_policy.EmailPolicyError):
        email_policy.assert_public_signup_email_allowed(OWNER)


def test_normal_tenant_cannot_use_owner_shared_email(monkeypatch):
    monkeypatch.setattr(email_policy, "get_email_matches", lambda email, include_target=None: [])
    with pytest.raises(email_policy.EmailPolicyError):
        email_policy.assert_email_allowed_for_user(OWNER, role="tenant_admin", slug="customer-site")


def test_owner_shared_email_allows_exactly_superadmin_and_default_admin(monkeypatch):
    existing = [_user(1, superadmin=True, slug=""), _user(2, superadmin=False, slug="default")]
    monkeypatch.setattr(email_policy, "get_email_matches", lambda email, include_target=None: existing)

    assert email_policy.assert_email_allowed_for_user(OWNER, user=existing[0], role="superadmin") == OWNER
    assert email_policy.assert_email_allowed_for_user(OWNER, user=existing[1], role="tenant_admin", slug="default") == OWNER


def test_owner_shared_email_blocks_third_default_or_normal_account(monkeypatch):
    existing = [_user(1, superadmin=True, slug=""), _user(2, superadmin=False, slug="default")]
    monkeypatch.setattr(email_policy, "get_email_matches", lambda email, include_target=None: existing)

    with pytest.raises(email_policy.EmailPolicyError):
        email_policy.assert_email_allowed_for_user(OWNER, role="tenant_admin", slug="administrator")

    with pytest.raises(email_policy.EmailPolicyError):
        email_policy.assert_email_allowed_for_user(OWNER, role="tenant_admin", slug="random")


def test_non_owner_duplicate_email_rejected(monkeypatch):
    existing = [_user(10, email="person@example.com", superadmin=False, slug="alpha")]
    monkeypatch.setattr(email_policy, "get_email_matches", lambda email, include_target=None: existing)

    with pytest.raises(email_policy.EmailPolicyError):
        email_policy.assert_public_signup_email_allowed("person@example.com")
