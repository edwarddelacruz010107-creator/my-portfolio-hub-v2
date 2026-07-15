# AI Center Architecture

## Scope and ownership

Phase 8 adds one provider-neutral AI control plane. Product features call `AIService`; they do not instantiate provider adapters, choose credentials, calculate cost, reserve budgets, retry jobs, or write usage directly. The Superadmin routes are transport-only and delegate validated changes to `center_service`.

The installed provider registry supports OpenAI, Anthropic, Gemini, Groq, OpenRouter, Ollama, and Azure OpenAI. Provider metadata, protocol selection, endpoint allowlists, and implemented capabilities live in `app/services/ai/domain.py`. This is a capability registry, not a claim that every provider/model supports every operation.

## Request path

1. Resolve the tenant feature policy, with a tenant override before the global policy.
2. Verify tenant state and plan, provider/model enablement, credential presence, output limit, and operation capability.
3. Resolve an optional published prompt version and render its exact variable allowlist.
4. Calculate a conservative reservation using integer microunits and lock the daily tenant/feature budget stream.
5. Create an encrypted outbox job with a globally unique idempotency key and request digest.
6. Execute through the protocol adapter with a bounded timeout.
7. Append one terminal usage row with the versioned pricing snapshot and hashed provider request evidence.
8. Settle the original reservation date, clear terminal request content, and retain encrypted output only for the configured retention window.

The job keeps its reservation amount and reservation date. This prevents a queued-worker takeover, retry, or midnight boundary from double-reserving or settling the wrong daily row.

## Idempotency and retry safety

The database uniqueness constraint and request digest prevent a client idempotency key from issuing a second job or being reused for different content, users, or tenants. A queued job that has already reserved budget reuses that reservation when a worker takes over.

Automatic retries are deliberately narrow. Only an explicit HTTP 429 rejection is treated as safe to replay across every implemented protocol. Network timeouts, connection failures, other HTTP errors, and expired worker leases have an uncertain paid-call outcome, so they terminate without automatically issuing another provider call. Rate-limit retries are bounded to three attempts with increasing backoff. Operators may inspect the redacted job and provider console before deciding on any new request.

## Provider adapters

Adapters translate only HTTP request and response shapes:

- OpenAI Responses protocol: OpenAI, Azure OpenAI, and Ollama compatibility.
- OpenAI chat-completions protocol: Groq and OpenRouter.
- Anthropic Messages protocol.
- Gemini `generateContent` protocol.

Text and structured operations are implemented only where the protocol adapter declares them. Embeddings and moderation remain typed operation names but cannot be selected until a real adapter contract exists. Structured output accepts a bounded object schema without external references. Tools and arbitrary provider parameters are not exposed.

Provider endpoints are allowlisted. Official cloud providers must match their fixed HTTPS API origin and path. Azure must use an approved resource host ending in `/openai/v1`. Ollama permits loopback HTTP or `https://ollama.com`; private-network and arbitrary URLs are rejected.

## Secrets, content, and diagnostics

- Credentials and job request/response payloads use the application's canonical Fernet encryption helper.
- Templates receive provider dictionaries containing only a masked suffix and configuration state.
- API keys use headers, never query strings, and are never included in audit metadata.
- Audit metadata is recursively redacted. Provider errors are line-bounded, length-bounded, and secret-redacted.
- Request content is cleared after every terminal outcome. Successful response content is encrypted until policy retention expires; `ai-purge-payloads` removes expired payloads.
- Usage and audit rows contain no prompt or generated content.
- Provider request IDs are represented by a SHA-256 hash and an eight-character diagnostic suffix.

## Pricing and unavailable evidence

Prices are stored as integer currency microunits per one million provider units. Cost calculations use integer arithmetic with upward rounding; binary floats are not accepted. Every usage row snapshots the control-plane version, pricing schema, model pricing version, currency, and input/output prices.

Input units, output units, and cost stay `NULL` when the provider did not return complete evidence. The dashboard says “Unavailable”; it never substitutes zero. The daily request count increments once per terminal job, so request totals reconcile to append-only usage rows rather than retry attempts.

## Prompt lifecycle

Prompt definitions have a mutable active-version pointer. Publishing derives and stores the exact simple-variable allowlist, appends an immutable version with a required change note, and moves the pointer. Activating an older version changes only the pointer and appends an audit event. PostgreSQL and ORM guards reject updates and deletes on prompt versions.

## Superadmin surface

`/superadmin/ai` contains:

- provider configuration, encrypted API-key replacement/clearing, and evidence-based health;
- model allowlisting, protocol capability checks, exact pricing, and pricing versions;
- global or tenant feature policy, minimum plan, model, daily budget, output cap, and retention;
- immutable prompt history and activation;
- usage, cost, latency, failures, jobs, and configuration audit;
- a rate-limited live test console requiring an explicit billing acknowledgement;
- an honest Knowledge Base unavailable state.

Provider “health” is derived only from the latest retained live request result. A configured provider with no request evidence is labeled “no live evidence,” not healthy. The Knowledge Base remains unavailable because no real source-ingestion, tenant-isolation, embedding deletion, retrieval-provenance, or evaluation lifecycle exists.

## Operations

- Apply migration `0062` before deploying code that imports the AI models.
- Run `flask ai-run-jobs --limit 20` from a scheduled worker for queued and rate-limit retry jobs.
- Run `flask ai-purge-payloads --limit 500` on a retention schedule.
- Store a stable production `FERNET_KEY`; rotating it requires an explicit credential/payload re-encryption procedure.
- Configure a provider, enable a compatible model, then enable a feature policy. The application performs no startup seeding or provider mutation.

## Deployment verification

The source suite uses recorded response fixtures and makes no real provider calls. Staging must still verify migration upgrade/downgrade, PostgreSQL advisory-lock races, cross-tenant denial, Fernet key behavior, worker crash boundaries, 429 retries, actual provider response changes, pricing reconciliation, response purge, full Flask/Jinja rendering, CSRF, accessibility, and browser behavior. Live tests require a budget-capped non-production provider project and must never run in the normal regression suite.

## Rollback

Disable all feature policies before application rollback. Older code can ignore the additive `0062` tables. Stop AI workers before downgrading. Export append-only usage, prompt, and audit records if policy requires preservation, deploy code that no longer imports the models, and only then downgrade `0062`.
