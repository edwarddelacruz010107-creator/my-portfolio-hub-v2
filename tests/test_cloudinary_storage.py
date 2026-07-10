from flask import Flask

from app.utils.cloudinary_storage import _public_id_from_url, is_cloudinary_url


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
