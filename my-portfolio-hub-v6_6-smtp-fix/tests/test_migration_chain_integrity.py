"""
tests/test_migration_chain_integrity.py — Alembic migration chain guard

WHY THIS TEST EXISTS
─────────────────────
The Admin Portal and Superadmin Portal "Forgot Password" OTP emails silently
failed in production because two migration files declared `down_revision =
None`, each creating a second/third/fourth competing Alembic head:

  - 0029_merge_heads.py (a merge revision that should have used a tuple of
    both parent heads, but used None instead)
  - v5_6_per_portal_email_config.py (should have chained onto the new
    single head after the merge, but used None instead)

With multiple heads, `flask db upgrade` (the first step of Render's
preDeployCommand) hard-fails with "Multiple head revisions are present."
That means the migration adding GlobalEmailConfig.admin_mailersend_api_key
/ superadmin_mailersend_api_key / sender columns never runs in production.
Since GlobalEmailConfig.get() does a full-row ORM load, every portal's
email flow (OTP reset, contact form email_only provider, test-email,
notifications) breaks the moment it touches that table.

This test does NOT require a live database. It only inspects migration
file topology (revision / down_revision wiring) via Alembic's own
ScriptDirectory, the same mechanism `flask db upgrade` uses to resolve
"head". This is intentionally cheap to run and run often.
"""
import os

import pytest

MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'migrations'
)


def _get_script_directory():
    from alembic.script import ScriptDirectory
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option('script_location', MIGRATIONS_DIR)
    return ScriptDirectory.from_config(cfg)


def test_migration_chain_has_exactly_one_head():
    """
    `flask db upgrade` resolves an implicit target of 'head'. If more than
    one head exists, Alembic refuses to run at all -- and EVERY migration
    in the chain is skipped, not just the most recent one.
    """
    script = _get_script_directory()
    heads = script.get_heads()
    assert len(heads) == 1, (
        f"Expected exactly 1 Alembic head, found {len(heads)}: {heads}. "
        "Multiple heads make `flask db upgrade` hard-fail in production "
        "(Render's preDeployCommand), silently skipping all pending "
        "migrations -- including ones email/OTP code depends on."
    )


def test_migration_chain_has_exactly_one_base():
    """
    More than one base (a revision with down_revision=None) means a
    migration was added as a disconnected second root instead of being
    chained onto the existing history.
    """
    script = _get_script_directory()
    bases = script.get_bases()
    assert len(bases) == 1, (
        f"Expected exactly 1 Alembic base, found {len(bases)}: {bases}. "
        "A new root usually means down_revision was left as None instead "
        "of pointing at the actual prior revision/head."
    )


def test_full_revision_path_is_walkable():
    """
    Confirms every revision file is reachable from base to head in a single
    connected path -- this is what `flask db upgrade head` actually walks.
    Raises (via Alembic) if there's a cycle, a dangling down_revision
    reference, or a disconnected branch.
    """
    script = _get_script_directory()
    revisions = list(script.walk_revisions(base='base', head='heads'))
    assert len(revisions) > 0, "No revisions found in migrations/versions/"

    # Sanity check: the two previously-broken revisions must be present
    # and reachable, not just present-on-disk.
    rev_ids = {r.revision for r in revisions}
    assert 'v5_6_portal_email' in rev_ids, (
        "v5_6_per_portal_email_config.py is not reachable from the main "
        "chain -- it will not run on `flask db upgrade`."
    )
    assert '0029_merge_heads' in rev_ids, (
        "0029_merge_heads.py is not reachable from the main chain -- it "
        "will not run on `flask db upgrade`."
    )


def test_merge_revision_has_tuple_down_revision():
    """
    0029_merge_heads is a merge revision (it has two parents). Alembic
    merge revisions MUST declare down_revision as a tuple of both parent
    revision IDs, never a single string and never None.
    """
    script = _get_script_directory()
    rev = script.get_revision('0029_merge_heads')
    assert isinstance(rev.down_revision, tuple), (
        f"0029_merge_heads.down_revision should be a tuple of both parent "
        f"heads, got {type(rev.down_revision).__name__}: {rev.down_revision!r}"
    )
    assert set(rev.down_revision) == {
        '0011_add_paymongo_subscription',
        '0028_add_email_only_provider',
    }


def test_v5_6_portal_email_chains_onto_merge_head():
    """
    The v5.6 per-portal email config migration must chain onto the actual
    current head (the merge revision), not declare itself a new root.
    """
    script = _get_script_directory()
    rev = script.get_revision('v5_6_portal_email')
    assert rev.down_revision == '0029_merge_heads'


if __name__ == '__main__':
    import sys
    sys.exit(pytest.main([__file__, '-v']))
