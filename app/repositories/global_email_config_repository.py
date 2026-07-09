"""
app/repositories/global_email_config_repository.py — GlobalEmailConfig lookups
(Phase 4b — closes the one gap identified in PHASE4_AUDIT.md follow-up audit)

Scope: the singleton `global_email_config` row (id=1) used by superadmin's
MailerSend / SMTP / Resend settings panels.

`get_fresh_by_id` exists because the IDENTICAL chain —
`.query.execution_options(populate_existing=True).filter_by(id=...).first()`
— appears at 3 call sites in app/superadmin/__init__.py, all performing the
same "discard session, re-read from DB, bypass identity-map cache" check
immediately after a commit. This meets the project's own >1-call-site bar
for a named method (see base.py docstring). It is NOT a generic `get_by()`
substitute: `populate_existing=True` is load-bearing here — without it the
post-commit verification could silently return stale cached state — so it
is preserved verbatim as its own method rather than collapsed into
`BaseRepository.get_by()`.
"""
from __future__ import annotations

from typing import Optional

from app.models import GlobalEmailConfig
from app.repositories.base import BaseRepository


class GlobalEmailConfigRepository(BaseRepository[GlobalEmailConfig]):
    def __init__(self):
        super().__init__(GlobalEmailConfig)

    def get_fresh_by_id(self, pk: int) -> Optional[GlobalEmailConfig]:
        """1:1 wrapper around:
        GlobalEmailConfig.query.execution_options(populate_existing=True)
            .filter_by(id=pk).first()
        Bypasses the SQLAlchemy identity map — used for post-commit
        verification reads where a stale cached instance must not be
        returned.
        """
        return (
            self.model.query
            .execution_options(populate_existing=True)
            .filter_by(id=pk)
            .first()
        )


global_email_config_repository = GlobalEmailConfigRepository()
