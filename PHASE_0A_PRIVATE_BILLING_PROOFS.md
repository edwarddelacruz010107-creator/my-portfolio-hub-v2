# Phase 0A — Private Billing-Proof Deployment Runbook

## What changed

Customer payment proofs no longer share the public `billing` media path used by payment QR codes.

- New local proofs are stored below `PRIVATE_UPLOAD_FOLDER/billing_proofs` with opaque names and owner-only POSIX permissions.
- New Cloudinary proofs are uploaded as authenticated raw assets. The database stores an opaque reference instead of a public URL.
- Proofs are rendered only through `GET /superadmin/billing/submissions/<id>/proof`, which requires an authenticated superadmin, is rate limited, revalidates the stored bytes, disables caching, and records a security event.
- Legacy `/uploads/billing/<filename>` and `/static/uploads/billing/<filename>` requests fail with 404 when the filename belongs to a `PaymentSubmission`.
- Public payment QR codes continue using the existing public `billing` storage and URLs.
- Proof deletion preserves the database reference if storage deletion fails.
- The container and repository ignore rules exclude billing uploads and private runtime storage.

No database schema change is required. `PaymentSubmission.payment_proof` remains backward compatible and now holds one of:

- `private-local:<opaque filename>`
- `cloudinary-auth:raw:<scoped object id>`
- a legacy filename/Cloudinary URL only until the migration command completes

## Required deployment order

### 1. Contain and preserve evidence

1. Take a database backup and an access-controlled storage backup.
2. Do not copy proof filenames or contents into tickets, chat, logs, or screenshots.
3. Deploy the route blockers before running storage migration.
4. Purge any CDN/proxy cache for old `/static/uploads/billing/*` and `/uploads/billing/*` paths.

### 2. Configure private storage

For Cloudinary deployments, keep `STORAGE_PROVIDER=cloudinary` and valid Cloudinary credentials. New proof uploads will use authenticated assets automatically.

For local or Supabase-public-media deployments, configure a mounted, non-public directory:

```bash
PRIVATE_UPLOAD_FOLDER=/var/data/private_uploads
```

The path must not be inside `UPLOAD_FOLDER`, `app/static`, or a directory served by Nginx/CDN. Production proof uploads fail closed when local private storage is not persistent.

### 3. Inventory without changing data

```bash
flask migrate-private-billing-proofs
```

The command reports counts only. It never prints proof references or filenames.

### 4. Migrate after backup verification

```bash
flask migrate-private-billing-proofs --apply
```

The command:

1. validates each legacy local or Cloudinary proof;
2. copies it to private storage;
3. updates all matching database rows;
4. commits the new references; and only then
5. removes the old public object unless the same file is still an intentional payment QR asset.

If any item is missing, invalid, or cannot be removed publicly, the command exits non-zero and keeps public proof routes blocked. Review server logs, resolve the item, and run the command again. It is safe to repeat.

### 5. Verify

- Anonymous, tenant-admin, and wrong-role requests cannot retrieve a proof.
- A superadmin can view each migrated image/PDF from Payment Submissions and Media.
- Response headers include `Cache-Control: private, no-store, max-age=0` and `X-Content-Type-Options: nosniff`.
- Payment QR codes still render on tenant billing pages.
- A new manual proof produces an opaque private reference and is absent from public upload roots.
- The dry-run reports every referenced proof as already private, with zero missing/invalid rows.
- Release archives and container layers contain no files under `app/static/uploads/billing/`.

## Tests

With the pinned project dependencies installed:

```bash
pytest tests/test_private_billing_proofs.py -q
pytest tests/test_security_patches.py tests/test_billing_v35.py -q
pytest -q
```

Also run a production-like browser test with the real CSP and a disposable Cloudinary account or mounted private disk.

## Rollback

The database column and billing routes remain compatible with legacy references. If the application must be rolled back, do not restore anonymous public proof serving. Keep the secure version of the proof route or place an authenticated proxy in front of the private objects. Never copy authenticated/private objects back into `app/static`.

## Residual operational work

Source changes cannot purge previously distributed archives, Git history, container layers, browser/CDN caches, or Cloudinary URLs. The deployment owner must remove those copies and complete the privacy/incident review described in `PROJECT_AUDIT.md`.
