"""Financial-ledger operational commands."""
import json

import click


def register_ledger_commands(app) -> None:
    @app.cli.command("ledger-backfill")
    @click.option("--apply", "apply_changes", is_flag=True, help="Write eligible postings and durable dispositions.")
    def ledger_backfill(apply_changes: bool):
        """Dry-run or apply the provenance-first legacy reconciliation."""
        from app.services.ledger.backfill_service import run_legacy_backfill

        report = run_legacy_backfill(apply_changes=apply_changes)
        click.echo(json.dumps(report.to_dict(), sort_keys=True))
        if not apply_changes:
            click.echo("Dry run only. No ledger or disposition rows were written.")
