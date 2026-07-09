"""
app/repositories/payment_submission_repository.py — PaymentSubmission lookups (Phase 4b, item 4)

Scope: escape-hatch `payment_submission_repository.query` for superadmin call sites.
Every PaymentSubmission.query occurrence in app/superadmin is a unique, multi-condition
chain — no named methods added per audit policy (no duplication to collapse).
"""
from __future__ import annotations

from app.models.portfolio import PaymentSubmission
from app.repositories.base import BaseRepository


class PaymentSubmissionRepository(BaseRepository[PaymentSubmission]):
    def __init__(self):
        super().__init__(PaymentSubmission)


payment_submission_repository = PaymentSubmissionRepository()
