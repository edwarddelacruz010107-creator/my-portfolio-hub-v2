# Production Support Matrix

Effective: 2026-07-14

| Layer | Supported contract | Verification gate |
|---|---|---|
| Python | CPython 3.12.x | Image build, compileall, pinned test suite |
| Flask stack | Flask 3.1.3, Werkzeug 3.1.8, Flask-SQLAlchemy 3.1.1, SQLAlchemy 2.0.51, Alembic 1.18.5 | Exact pins in `requirements.txt` and `requirements.lock.txt` |
| PostgreSQL | PostgreSQL 16.x production target; UTF-8; SSL required | Core and tenant migration rehearsal against a disposable PostgreSQL 16 database |
| Redis | Redis 7.x production target; TLS supported | `/readyz` PING with two-second client timeouts; production is not ready without Redis |
| Chromium browsers | Current stable and previous two major versions | Keyboard, responsive, reduced-motion, CSP, and smoke suite |
| Firefox | Current stable and previous two major versions | Keyboard, responsive, reduced-motion, CSP, and smoke suite |
| Safari | Current stable and previous major version on supported macOS/iOS | Keyboard, responsive, reduced-motion, CSP, and smoke suite |
| Edge | Current stable and previous two major versions | Chromium smoke suite plus sign-in/payment redirects |
| PayMongo | REST API v1 (`https://api.paymongo.com/v1`) | Checkout/webhook contract tests; verified signatures and replay tests |
| Dodo Payments | Hosted Checkouts contract used by `/checkouts`; upstream API is unversioned in the configured base URL | Contract test in test and live modes before release; fail closed on response drift |
| Google sign-in | OIDC discovery document and OAuth 2.0 | Provider sandbox sign-in/link/recovery smoke |
| GitHub sign-in | OAuth 2.0 and current GitHub user/email API | Provider sandbox sign-in/link/recovery smoke |
| MailerSend | Python SDK 2.0.3 | Provider sandbox send, error, and fallback tests |
| Resend | HTTPS `/emails` contract | Provider sandbox send, error, and fallback tests |
| Basin / Web3Forms | Configured HTTPS form endpoint contract | Per-tenant provider test, timeout, and internal-inbox fallback |

Unlisted runtime or provider versions are unsupported until their contract
tests pass. An unavailable provider or dependency is reported explicitly; it
must never be presented as a zero result or successful delivery.
