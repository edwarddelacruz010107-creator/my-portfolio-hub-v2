"""
app/repositories/user_repository.py — User lookups (Phase 4)

Covers the unconditionally-safe, single-tenant-unaware lookup patterns
observed across app/scripts/*.py and other non-request-path code:
username-only and email-only existence checks. Multi-condition,
tenant-scoped, or auth-flow lookups (login, password reset, superadmin
verification) are explicitly OUT of scope for this pass — several of
those call sites are marked "DO NOT TOUCH" in-code (e.g.
app/services/auth/password_reset_service.py) and are deferred to a
dedicated, individually-reviewed Phase 4b change. See PHASE4_AUDIT.md.
"""
from __future__ import annotations

from typing import Optional

from app.models import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self):
        super().__init__(User)

    def get_by_username(self, username: str) -> Optional[User]:
        return self.model.query.filter_by(username=username).first()

    def get_by_email(self, email: str) -> Optional[User]:
        # Email is unique for normal accounts, but the protected owner email can
        # exist in two contexts. Return a user only when the lookup is
        # deterministic; auth flows use tenant/portal-scoped helpers instead.
        from app.services.auth.email_policy import get_email_matches

        matches = get_email_matches(email)
        return matches[0] if len(matches) == 1 else None

    def username_exists(self, username: str) -> bool:
        return self.get_by_username(username) is not None

    def email_exists(self, email: str) -> bool:
        from app.services.auth.email_policy import get_email_matches

        return bool(get_email_matches(email))

user_repository = UserRepository()
