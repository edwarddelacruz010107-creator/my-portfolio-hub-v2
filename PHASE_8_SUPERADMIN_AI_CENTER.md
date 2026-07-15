# Phase 8 — Superadmin AI Center

Status: complete at the deterministic verification boundary on 2026-07-15.

## Delivered

- Added a versioned, provider-neutral request and pricing contract for OpenAI, Anthropic, Gemini, Groq, OpenRouter, Ollama, and Azure OpenAI.
- Added fixed-origin/approved-host endpoint validation, bounded requests, exact integer microunit pricing, recursive secret redaction, and strict prompt/schema validation.
- Added recorded-fixture adapters for OpenAI Responses, OpenAI-compatible chat completions, Anthropic Messages, and Gemini `generateContent`.
- Added migration `0062` with encrypted provider credentials and job payloads, provider/model registry, tenant/global feature policy, immutable prompt versions, idempotent outbox jobs, daily budget reservations, append-only terminal usage, and append-only audit history.
- Added one `AIService` boundary for policy, plan checks, capability selection, prompt activation, budget locks, execution, bounded rate-limit retry, idempotency, pricing snapshots, redacted diagnostics, and retention.
- Prevented automatic replay when a network error, non-429 provider error, or expired lease has an uncertain paid-call outcome.
- Added a Superadmin AI Center with provider/API-key configuration, observed health, model/pricing allowlist, feature/budget policy, prompt publish/activation, usage/cost/latency/error views, job/audit logs, and a rate-limited live test console with billing acknowledgement.
- Kept Knowledge Base explicitly unavailable until a real ingestion, isolation, provenance, deletion, and evaluation lifecycle exists.
- Added worker and retention commands without startup data mutation or provider seeding.

## Automated evidence

- 21 Phase 8 contracts: passed.
- Four recorded provider-protocol response fixtures: passed without network access.
- 100 `unittest` contracts across Phases 0C–5, 7, and 8: passed.
- 16 Phase 6 deterministic function contracts: passed.
- Seven JavaScript DOM/security contracts: passed.
- Eight registered CSS files passed design-token lint.
- Python compilation and full application AST scan: passed.
- Migration `0062` is the only child of `0061` in the active linear phase chain and contains no seeded AI data.

## Risk and limitations

- Database risk is moderate. `0062` is additive, but PostgreSQL triggers, advisory locks, uniqueness races, query plans, and downgrade behavior require staging evidence.
- Provider risk is moderate. Recorded fixtures verify known response contracts, not future provider changes or real authentication, quotas, safety responses, or billing.
- Secret risk is reduced but still operational. Production requires a stable `FERNET_KEY`, secret rotation procedures, restricted database/log access, and staging verification that keys never reach templates or error telemetry.
- Retry behavior is intentionally conservative. Uncertain outcomes fail visibly rather than risking an automatic duplicate paid call.
- The implementation workspace has no Flask, SQLAlchemy, Alembic, Jinja, PostgreSQL, Redis, or browser runtime. Runtime integration and visual verification remain deployment-owner gates.

## Deployment checklist

- [ ] Back up both database binds and apply `0062` to a PostgreSQL staging clone.
- [ ] Verify constraints, append-only triggers, advisory budget locks, prompt-version races, and downgrade on a disposable copy.
- [ ] Start the complete application with production-like Flask, Redis, database, and `FERNET_KEY` configuration.
- [ ] Confirm anonymous, tenant-admin, and non-superadmin requests cannot access or mutate `/superadmin/ai`.
- [ ] Attempt cross-tenant feature-policy and usage access; confirm denial and no metadata leakage.
- [ ] Configure each provider in a budget-capped non-production project and verify its current live response contract.
- [ ] Confirm blank credential saves retain the old key; clearing disables key-required providers; templates/logs never contain the raw value.
- [ ] Exercise client idempotency reuse, 429 retry, network timeout, provider 5xx, and expired-lease handling.
- [ ] Reconcile terminal usage rows, daily request counts, exact costs, provider request evidence, and provider invoices for a fixed sample.
- [ ] Verify request content clears on terminal state and response payloads purge at each retention boundary.
- [ ] Run keyboard, focus, screen-reader, 200% zoom, reduced-motion, mobile, CSP, and hostile-string browser checks.
- [ ] Confirm no normal regression, health check, startup hook, or GET request sends a provider call.

## Rollback

1. Disable every AI feature policy and stop `ai-run-jobs` scheduling.
2. Deploy the previous application while leaving additive tables in place.
3. Preserve append-only usage, prompt, and audit evidence according to policy.
4. Downgrade `0062` only after no deployed code imports the AI models and no worker can write them.
