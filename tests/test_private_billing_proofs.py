"""Regression tests for Phase 0A private payment-proof containment."""

from __future__ import annotations

import io
from pathlib import Path

from flask import Flask
from werkzeug.datastructures import FileStorage


PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"


def _storage_app(tmp_path: Path) -> Flask:
    app = Flask(__name__, instance_path=str(tmp_path / "instance"))
    app.config.update(
        TESTING=True,
        STORAGE_PROVIDER="local",
        UPLOAD_FOLDER=str(tmp_path / "public_uploads"),
        PRIVATE_UPLOAD_FOLDER=str(tmp_path / "private_uploads"),
        PRIVATE_UPLOAD_PERSISTENT=False,
        MAX_PRIVATE_PROOF_DOWNLOAD_BYTES=10 * 1024 * 1024,
    )
    return app


def test_local_proof_is_opaque_private_and_round_trips(tmp_path):
    from app.services.billing.private_proof_storage import (
        LOCAL_REFERENCE_PREFIX,
        load_private_billing_proof,
        resolve_private_proof_path,
        save_private_billing_proof,
    )

    app = _storage_app(tmp_path)
    with app.app_context():
        upload = FileStorage(
            stream=io.BytesIO(PDF_BYTES),
            filename="bank-receipt.pdf",
            content_type="application/pdf",
        )
        reference = save_private_billing_proof(upload)

        assert reference.startswith(LOCAL_REFERENCE_PREFIX)
        assert "bank-receipt" not in reference
        path = resolve_private_proof_path(reference)
        assert path is not None and path.is_file()
        assert not str(path).startswith(str(tmp_path / "public_uploads"))

        content = load_private_billing_proof(reference)
        assert content.data == PDF_BYTES
        assert content.mimetype == "application/pdf"
        assert content.download_name == "payment-proof.pdf"


def test_private_root_cannot_be_nested_below_public_storage(tmp_path):
    from app.services.billing.private_proof_storage import (
        PrivateProofStorageError,
        private_proof_root,
    )

    app = _storage_app(tmp_path)
    app.config["PRIVATE_UPLOAD_FOLDER"] = str(tmp_path / "public_uploads" / "private")
    with app.app_context():
        try:
            private_proof_root()
        except PrivateProofStorageError:
            pass
        else:
            raise AssertionError("Private proof root inside public uploads must be rejected")


def test_public_url_builder_never_emits_private_reference(tmp_path):
    from app.services.media.upload_storage import build_upload_url

    app = _storage_app(tmp_path)
    with app.test_request_context("/"):
        assert build_upload_url("private-local:opaque-proof.pdf", "billing") == ""


def test_private_proof_implementation_contract_is_wired():
    root = Path(__file__).resolve().parents[1]
    app_factory = (root / "app" / "__init__.py").read_text(encoding="utf-8")
    billing_routes = (root / "app" / "superadmin" / "routes" / "billing.py").read_text(encoding="utf-8")
    template = (root / "app" / "templates" / "superadmin" / "billing_submissions.html").read_text(encoding="utf-8")
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")

    assert "is_legacy_public_billing_proof" in app_factory
    assert "billing_submission_proof" in billing_routes
    assert "@superadmin_required" in billing_routes
    assert "Cache-Control'] = 'private, no-store" in billing_routes
    assert "url_for('superadmin.billing_submission_proof'" in template
    assert "proof|upload_url('billing')" not in template
    assert "app/static/uploads/billing/" in dockerignore


def test_authorized_route_and_public_legacy_block(app, tmp_path):
    """Superadmin can view; anonymous/public legacy paths cannot."""
    from app import db
    from app.models.core import PaymentSubmission, Tenant, User
    from app.services.billing.private_proof_storage import save_private_billing_proof

    app.config.update(
        WTF_CSRF_ENABLED=False,
        PRIVATE_UPLOAD_FOLDER=str(tmp_path / "private"),
        PRIVATE_UPLOAD_PERSISTENT=True,
        STORAGE_PROVIDER="local",
        MAX_PRIVATE_PROOF_DOWNLOAD_BYTES=10 * 1024 * 1024,
    )
    client = app.test_client()

    with app.app_context():
        tenant = Tenant.query.filter_by(slug="default").first()
        assert tenant is not None
        superadmin = User.query.filter_by(username="proof_security_superadmin").first()
        if superadmin is None:
            superadmin = User(
                username="proof_security_superadmin",
                email="proof-security-superadmin@example.invalid",
                is_admin=True,
                is_superadmin=True,
            )
            superadmin.password = "Test-only-Password-123!"
            db.session.add(superadmin)
            db.session.flush()

        private_reference = save_private_billing_proof(FileStorage(
            stream=io.BytesIO(PDF_BYTES),
            filename="proof.pdf",
            content_type="application/pdf",
        ))
        private_row = PaymentSubmission(
            tenant_id=tenant.id,
            plan="Basic",
            payment_proof=private_reference,
            status="pending",
        )
        db.session.add(private_row)
        db.session.commit()
        private_id = private_row.id
        superadmin_id = superadmin.id
        tenant_admin_id = User.query.filter_by(is_superadmin=False).first().id

    anonymous = client.get(f"/superadmin/billing/submissions/{private_id}/proof")
    assert anonymous.status_code in (302, 401, 403)

    with client.session_transaction() as session:
        session["_user_id"] = str(tenant_admin_id)
        session["_fresh"] = True
        session["tenant_slug"] = "default"
    wrong_role = client.get(f"/superadmin/billing/submissions/{private_id}/proof")
    assert wrong_role.status_code in (302, 403)

    with client.session_transaction() as session:
        session.clear()
        session["_user_id"] = str(superadmin_id)
        session["_fresh"] = True
    authorized = client.get(f"/superadmin/billing/submissions/{private_id}/proof")
    assert authorized.status_code == 200
    assert authorized.data == PDF_BYTES
    assert authorized.headers["Cache-Control"] == "private, no-store, max-age=0"
    assert authorized.headers["X-Content-Type-Options"] == "nosniff"

    legacy_name = "legacy-public-proof.pdf"
    legacy_dir = Path(app.config["UPLOAD_FOLDER"]) / "billing"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / legacy_name).write_bytes(PDF_BYTES)
    with app.app_context():
        tenant = Tenant.query.filter_by(slug="default").first()
        legacy_row = PaymentSubmission(
            tenant_id=tenant.id,
            plan="Basic",
            payment_proof=legacy_name,
            status="pending",
        )
        db.session.add(legacy_row)
        db.session.commit()
        legacy_id = legacy_row.id

    with client.session_transaction() as session:
        session.clear()
    blocked = client.get(f"/uploads/billing/{legacy_name}")
    assert blocked.status_code == 404

    with app.app_context():
        PaymentSubmission.query.filter(
            PaymentSubmission.id.in_([private_id, legacy_id])
        ).delete(synchronize_session=False)
        User.query.filter_by(username="proof_security_superadmin").delete(
            synchronize_session=False
        )
        db.session.commit()
