from flask import Flask

from app.utils.cloudinary_storage import (
    _parse_private_billing_reference,
    _public_id_from_url,
    _resource_type_from_url,
    is_cloudinary_url,
)


def test_cloudinary_url_detection_and_public_id():
    app = Flask(__name__)
    app.config["CLOUDINARY_FOLDER_ROOT"] = "myportfoliohub"
    with app.app_context():
        url = "https://res.cloudinary.com/demo/image/upload/v1720000000/myportfoliohub/projects/abc123.webp"
        assert is_cloudinary_url(url)
        assert _public_id_from_url(url) == "myportfoliohub/projects/abc123"


def test_cloudinary_delete_scope_guard():
    app = Flask(__name__)
    app.config["CLOUDINARY_FOLDER_ROOT"] = "myportfoliohub"
    with app.app_context():
        url = "https://res.cloudinary.com/demo/image/upload/v1720000000/other-app/projects/abc123.webp"
        assert _public_id_from_url(url) is None


def test_private_billing_reference_is_scoped_to_authenticated_folder():
    app = Flask(__name__)
    app.config["CLOUDINARY_FOLDER_ROOT"] = "myportfoliohub"
    with app.app_context():
        valid = "cloudinary-auth:raw:myportfoliohub/billing-proofs/opaque.pdf"
        assert _parse_private_billing_reference(valid) == (
            "raw",
            "myportfoliohub/billing-proofs/opaque.pdf",
        )
        assert _parse_private_billing_reference(
            "cloudinary-auth:raw:other-app/billing-proofs/opaque.pdf"
        ) is None
        assert _parse_private_billing_reference(
            "cloudinary-auth:image:myportfoliohub/billing-proofs/opaque.pdf"
        ) is None


def test_raw_cloudinary_public_id_keeps_file_extension_for_deletion():
    app = Flask(__name__)
    app.config["CLOUDINARY_FOLDER_ROOT"] = "myportfoliohub"
    with app.app_context():
        url = "https://res.cloudinary.com/demo/raw/upload/v1720000000/myportfoliohub/billing/opaque.pdf"
        assert _resource_type_from_url(url) == "raw"
        assert _public_id_from_url(url) == "myportfoliohub/billing/opaque.pdf"
