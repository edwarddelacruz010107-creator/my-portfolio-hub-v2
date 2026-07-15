"""Operational commands for AI outbox execution and retention."""
import json

import click


def register_ai_commands(app) -> None:
    @app.cli.command("ai-run-jobs")
    @click.option("--limit", type=click.IntRange(1, 100), default=20, show_default=True)
    def ai_run_jobs(limit: int):
        """Run due AI jobs once; schedule this command in the worker tier."""
        from app.services.ai import get_ai_service

        result = get_ai_service().run_due_jobs(limit=limit)
        click.echo(json.dumps(result, sort_keys=True))

    @app.cli.command("ai-purge-payloads")
    @click.option("--limit", type=click.IntRange(1, 5000), default=500, show_default=True)
    def ai_purge_payloads(limit: int):
        """Purge encrypted AI response payloads whose policy retention expired."""
        from app.services.ai import get_ai_service

        count = get_ai_service().purge_expired_response_payloads(limit=limit)
        click.echo(json.dumps({"purged": count}, sort_keys=True))
