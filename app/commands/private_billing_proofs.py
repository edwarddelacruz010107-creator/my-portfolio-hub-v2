"""Operational migration for legacy public manual-payment proofs."""

from __future__ import annotations

import logging

import click

logger = logging.getLogger(__name__)


def _public_qr_uses_reference(reference: str) -> bool:
    from app.models.portfolio import PaymentInstruction, PaymentMethod
    from app.services.media.upload_storage import normalize_upload_reference

    if reference.startswith(("http://", "https://")):
        candidates = (reference,)
    else:
        normalized = normalize_upload_reference(reference, "billing")
        if not normalized or normalized[0] != "billing":
            return False
        filename = normalized[1]
        candidates = (
            filename,
            f"billing/{filename}",
            f"uploads/billing/{filename}",
            f"/uploads/billing/{filename}",
            f"static/uploads/billing/{filename}",
            f"/static/uploads/billing/{filename}",
        )
    method_match = (
        PaymentMethod.query
        .filter(PaymentMethod.qr_image.in_(candidates))
        .with_entities(PaymentMethod.id)
        .first()
    )
    instruction_match = (
        PaymentInstruction.query
        .filter(PaymentInstruction.qr_image.in_(candidates))
        .with_entities(PaymentInstruction.id)
        .first()
    )
    return bool(method_match or instruction_match)


def register_private_billing_proof_commands(app) -> None:
    @app.cli.command("migrate-private-billing-proofs")
    @click.option(
        "--apply",
        "apply_changes",
        is_flag=True,
        help="Copy proofs to private storage, update references, then remove unshared public copies.",
    )
    def migrate_private_billing_proofs(apply_changes: bool) -> None:
        """Inventory or migrate PaymentSubmission proofs out of public storage."""
        from app import db
        from app.models.portfolio import PaymentSubmission
        from app.services.billing.private_proof_storage import (
            CLOUDINARY_REFERENCE_PREFIX,
            LOCAL_REFERENCE_PREFIX,
            import_legacy_billing_proof,
            private_proof_exists,
            resolve_private_proof_path,
        )
        from app.services.media.upload_storage import delete_upload_file
        from app.utils.cloudinary_storage import delete_image, is_cloudinary_url

        submissions = (
            PaymentSubmission.query
            .filter(PaymentSubmission.payment_proof.isnot(None))
            .filter(PaymentSubmission.payment_proof != "")
            .order_by(PaymentSubmission.id)
            .all()
        )
        counts = {
            "total": len(submissions),
            "already_private": 0,
            "migratable_local": 0,
            "migratable_cloudinary": 0,
            "missing_or_invalid": 0,
            "migrated": 0,
            "public_deleted": 0,
            "public_preserved_for_qr": 0,
            "cleanup_failed": 0,
        }

        legacy_references: dict[str, str] = {}
        rows_by_reference: dict[str, list[PaymentSubmission]] = {}

        for submission in submissions:
            reference = (submission.payment_proof or "").strip()
            if reference.startswith((LOCAL_REFERENCE_PREFIX, CLOUDINARY_REFERENCE_PREFIX)):
                if private_proof_exists(reference):
                    counts["already_private"] += 1
                else:
                    counts["missing_or_invalid"] += 1
                continue

            if resolve_private_proof_path(reference):
                counts["migratable_local"] += 1
            elif is_cloudinary_url(reference):
                counts["migratable_cloudinary"] += 1
            else:
                counts["missing_or_invalid"] += 1
                continue
            rows_by_reference.setdefault(reference, []).append(submission)

        mode = "APPLY" if apply_changes else "DRY RUN"
        click.echo(f"Private billing-proof migration: {mode}")
        click.echo(f"Referenced proof rows: {counts['total']}")
        click.echo(f"Already private: {counts['already_private']}")
        click.echo(f"Legacy local rows: {counts['migratable_local']}")
        click.echo(f"Legacy Cloudinary rows: {counts['migratable_cloudinary']}")
        click.echo(f"Missing or invalid rows: {counts['missing_or_invalid']}")

        if not apply_changes:
            click.echo("No files or database rows changed. Re-run with --apply after backup verification.")
            return

        try:
            for old_reference, rows in rows_by_reference.items():
                try:
                    new_reference = import_legacy_billing_proof(old_reference)
                except Exception:
                    logger.exception(
                        "Private proof migration failed for %d row(s)",
                        len(rows),
                    )
                    counts["missing_or_invalid"] += len(rows)
                    continue
                for row in rows:
                    row.payment_proof = new_reference
                    counts["migrated"] += 1
                legacy_references[old_reference] = new_reference
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise click.ClickException(
                "Database update failed. Public originals were preserved; private copies may be cleaned as orphans."
            ) from exc

        # Delete only after the new references are committed. A local file that
        # is also a public payment QR must remain available to checkout pages.
        for old_reference in legacy_references:
            if _public_qr_uses_reference(old_reference):
                counts["public_preserved_for_qr"] += 1
                continue
            if is_cloudinary_url(old_reference):
                if delete_image(old_reference):
                    counts["public_deleted"] += 1
                else:
                    counts["cleanup_failed"] += 1
                continue

            old_path = resolve_private_proof_path(old_reference)
            delete_upload_file(old_reference, "billing")
            if old_path is None or not old_path.exists():
                counts["public_deleted"] += 1
            else:
                counts["cleanup_failed"] += 1

        click.echo(f"Migrated rows: {counts['migrated']}")
        click.echo(f"Public objects deleted: {counts['public_deleted']}")
        click.echo(f"Public objects retained because they are QR assets: {counts['public_preserved_for_qr']}")
        click.echo(f"Public cleanup failures: {counts['cleanup_failed']}")

        if counts["missing_or_invalid"] or counts["cleanup_failed"]:
            raise click.ClickException(
                "Migration completed with unresolved items. Keep public proof routes blocked and review server logs."
            )
