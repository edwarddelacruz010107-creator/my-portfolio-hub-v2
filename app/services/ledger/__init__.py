"""Canonical append-only financial ledger API."""
from app.services.ledger.domain import LedgerPosting, aggregate_postings


def append_posting(*args, **kwargs):
    from app.services.ledger.posting_service import append_posting as implementation
    return implementation(*args, **kwargs)


def post_manual_submission(*args, **kwargs):
    from app.services.ledger.posting_service import post_manual_submission as implementation
    return implementation(*args, **kwargs)


def post_provider_event(*args, **kwargs):
    from app.services.ledger.posting_service import post_provider_event as implementation
    return implementation(*args, **kwargs)


def post_reversal(*args, **kwargs):
    from app.services.ledger.posting_service import post_reversal as implementation
    return implementation(*args, **kwargs)

__all__ = [
    "LedgerPosting", "aggregate_postings", "append_posting",
    "post_manual_submission", "post_provider_event", "post_reversal",
]
