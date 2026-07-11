from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_duplicate_terminal_themes_are_retired_from_supported_catalog():
    engine = (ROOT / "app" / "theme_engine.py").read_text()
    assert "'developer_journal'" in engine
    assert "'console_blueprint'" not in engine
    assert "'terminal_green'" not in engine
    assert not (ROOT / "themes" / "console_blueprint").exists()
    assert not (ROOT / "themes" / "terminal_green").exists()
    assert not (ROOT / "app" / "static" / "themes" / "console_blueprint").exists()
    assert not (ROOT / "app" / "static" / "themes" / "terminal_green").exists()


def test_gmail_network_fallback_is_used_for_test_and_delivery():
    route = (ROOT / "app" / "superadmin" / "routes" / "email_settings.py").read_text()
    sender = (ROOT / "app" / "services" / "email" / "superadmin_email_service.py").read_text()
    assert "suggested_port': 465" in route
    assert "smtp.gmail.com" in route and "SMTP_SSL" in route
    assert "smtp.gmail.com" in sender and "_deliver(465, 'ssl')" in sender
