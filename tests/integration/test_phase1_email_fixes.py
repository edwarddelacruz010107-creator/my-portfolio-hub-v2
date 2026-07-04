"""
tests/integration/test_phase1_email_fixes.py
============================================

Integration tests for Phase 1 Email System Critical Fixes.

SCOPE:
  ✅ SMTP Retry Logic (transient errors with backoff)
  ✅ GlobalEmailConfig Race Condition (thread-safe singleton)
  ✅ Contact Form Atomicity (atomic email commits)
  ✅ Startup Email Validation (missing provider detection)

RUNNING TESTS:
  pytest tests/integration/test_phase1_email_fixes.py -v
  pytest tests/integration/test_phase1_email_fixes.py::TestSMTPRetry -v
  pytest tests/integration/test_phase1_email_fixes.py -v -s  (show print output)

PREREQUISITES:
  - App running in testing mode
  - SQLite database initialized (storage/portfolio_core_dev.db)
  - No external email providers required (mocked)
"""

import os
import sys
import time
import smtplib
import threading
import logging
from unittest.mock import Mock, patch, MagicMock
from contextlib import contextmanager

import pytest
from flask import Flask

# Configure logging for test output
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1: SMTP Retry Logic (Transient Error Handling)
# ─────────────────────────────────────────────────────────────────────────────

class TestSMTPRetry:
    """
    Test SMTP failover retry logic for transient errors (451, 452, 421, 450).
    
    Purpose: Verify that transient SMTP errors trigger retry mechanism instead
    of immediately failing email delivery.
    """

    def test_transient_error_451_triggers_retry(self):
        """
        Scenario: MailerSend fails, SMTP returns 451 (transient).
        Expected: Should retry SMTP (not fail immediately).
        """
        from app.services.email.email_service import EmailService
        
        svc = EmailService()
        
        # Mock MailerSend to fail
        with patch('app.services.email.email_service._send_via_mailersend') as mock_ms:
            mock_ms.return_value = (False, 'MailerSend quota exceeded')
            
            # Mock SMTP to fail on first attempt with 451, succeed on retry
            attempt_count = {'count': 0}
            
            def smtp_side_effect(*args, **kwargs):
                attempt_count['count'] += 1
                if attempt_count['count'] == 1:
                    # First attempt: transient error (451)
                    return (False, 'SMTP 451: Requested mail action not taken: too many connections from your IP')
                else:
                    # Retry: success
                    return (True, 'delivered')
            
            with patch('app.services.email.email_service._send_via_smtp') as mock_smtp:
                mock_smtp.side_effect = smtp_side_effect
                with patch.dict(os.environ, {
                    'SMTP_ENABLED': 'true',
                    'SMTP_HOST': 'smtp.test',
                    'SMTP_USERNAME': 'user',
                    'SMTP_PASSWORD': 'pass',
                    'SMTP_FROM_EMAIL': 'noreply@test',
                }, clear=False):
                    ok, msg = svc.send_email(
                        to='test@example.com',
                        subject='Test Email',
                        text='This is a test',
                        html='<p>This is a test</p>',
                        portal='tenant'
                    )
                
                # Should succeed on retry
                assert ok is True, f"Expected success after retry, got {msg}"
                # Should have attempted SMTP twice (initial + 1 retry)
                assert attempt_count['count'] == 2, f"Expected 2 SMTP attempts, got {attempt_count['count']}"
                logger.info(f"✅ Transient 451 error triggered retry: {attempt_count['count']} attempts")

    def test_transient_error_452_triggers_retry(self):
        """
        Scenario: SMTP returns 452 (transient service unavailable).
        Expected: Should retry up to MAX_RETRIES times.
        """
        from app.services.email.email_service import EmailService
        
        svc = EmailService()
        attempt_count = {'count': 0}
        
        def smtp_side_effect(*args, **kwargs):
            attempt_count['count'] += 1
            if attempt_count['count'] <= 2:
                # First 2 attempts: 452 (transient)
                return (False, 'SMTP 452: Too many concurrent connections')
            else:
                # Third attempt (last retry): success
                return (True, 'delivered')
        
        with patch('app.services.email.email_service._send_via_mailersend') as mock_ms:
            mock_ms.return_value = (False, 'MailerSend timeout')
            
            with patch('app.services.email.email_service._send_via_smtp') as mock_smtp:
                mock_smtp.side_effect = smtp_side_effect
                with patch.dict(os.environ, {
                    'SMTP_ENABLED': 'true',
                    'SMTP_HOST': 'smtp.test',
                    'SMTP_USERNAME': 'user',
                    'SMTP_PASSWORD': 'pass',
                    'SMTP_FROM_EMAIL': 'noreply@test',
                }, clear=False):
                    ok, msg = svc.send_email(
                        to='test@example.com',
                        subject='Test 452',
                        text='Test transient 452',
                        portal='tenant'
                    )
                
                # Should succeed after 2 retries
                assert ok is True
                assert attempt_count['count'] == 3  # 0, 1 (retry), 2 (retry)
                logger.info(f"✅ Transient 452 error retried 3 times total")

    def test_permanent_error_fails_fast(self):
        """
        Scenario: SMTP returns permanent error (550 auth failed).
        Expected: Should fail immediately (no retry).
        """
        from app.services.email.email_service import EmailService
        
        svc = EmailService()
        attempt_count = {'count': 0}
        
        def smtp_side_effect(*args, **kwargs):
            attempt_count['count'] += 1
            # Permanent auth failure (550)
            return (False, 'SMTP 550: Authentication failure')
        
        with patch('app.services.email.email_service._send_via_mailersend') as mock_ms:
            mock_ms.return_value = (False, 'MailerSend not configured')
            
            with patch('app.services.email.email_service._send_via_smtp') as mock_smtp:
                mock_smtp.side_effect = smtp_side_effect
                with patch.dict(os.environ, {
                    'SMTP_ENABLED': 'true',
                    'SMTP_HOST': 'smtp.test',
                    'SMTP_USERNAME': 'user',
                    'SMTP_PASSWORD': 'pass',
                    'SMTP_FROM_EMAIL': 'noreply@test',
                }, clear=False):
                    ok, msg = svc.send_email(
                        to='test@example.com',
                        subject='Test Permanent Error',
                        text='Test',
                        portal='tenant'
                    )
                
                # Should fail immediately
                assert ok is False
                # Should only try SMTP once (no retries for permanent errors)
                assert attempt_count['count'] == 1, f"Expected 1 attempt, got {attempt_count['count']}"
                logger.info(f"✅ Permanent error failed fast: {attempt_count['count']} attempt")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2: GlobalEmailConfig Race Condition (Thread-Safe Singleton)
# ─────────────────────────────────────────────────────────────────────────────

class TestGlobalEmailConfigRace:
    """
    Test GlobalEmailConfig singleton creation is thread-safe.
    
    Purpose: Verify that multiple concurrent calls to GlobalEmailConfig.get()
    don't cause IntegrityError crashes or return None.
    """

    def test_concurrent_singleton_creation(self, app):
        """
        Scenario: Two threads simultaneously call GlobalEmailConfig.get() when
        singleton doesn't exist.
        Expected: Both should succeed and get the same singleton row.
        """
        from app.models.core import GlobalEmailConfig
        from app import db
        
        with app.app_context():
            # Clear any existing singleton
            try:
                existing = GlobalEmailConfig.query.filter_by(id=1).first()
                if existing:
                    db.session.delete(existing)
                    db.session.commit()
            except Exception:
                db.session.rollback()
            
            results = {}
            errors = {}
            lock = threading.Lock()
            
            def get_singleton(thread_id):
                try:
                    # Each thread must push its own app context for DB access
                    with app.app_context():
                        # Call get() to ensure singleton exists, then re-query
                        GlobalEmailConfig.get()
                        fresh = db.session.get(GlobalEmailConfig, 1)
                        config_id = getattr(fresh, 'id', None)
                        with lock:
                            results[thread_id] = config_id
                        logger.info(f"Thread {thread_id}: Got singleton id={config_id}")
                except Exception as e:
                    with lock:
                        errors[thread_id] = str(e)
                    logger.error(f"Thread {thread_id}: Error - {e}")
            
            # Start two threads that race to create singleton
            t1 = threading.Thread(target=get_singleton, args=(1,))
            t2 = threading.Thread(target=get_singleton, args=(2,))
            
            t1.start()
            t2.start()
            t1.join()
            t2.join()
            
            # Both threads should succeed (no errors)
            assert not errors, f"Race condition errors: {errors}"
            
            # Both threads should get the same singleton id
            assert len(results) == 2
            config1 = results[1]
            config2 = results[2]
            assert config1 is not None, "Thread 1 got None"
            assert config2 is not None, "Thread 2 got None"
            assert config1 == config2 == 1
            
            logger.info(f"✅ Race condition test passed: both threads got singleton")

    def test_singleton_always_exists(self, app):
        """
        Scenario: Call GlobalEmailConfig.get() multiple times.
        Expected: Always returns valid singleton (never None).
        """
        from app.models.core import GlobalEmailConfig
        from app import db
        
        with app.app_context():
            # Get singleton 5 times
            configs = []
            for i in range(5):
                config = GlobalEmailConfig.get()
                assert config is not None, f"Attempt {i+1}: Got None"
                assert config.id == 1
                configs.append(config)
            
            # All should be the same object
            assert all(c.id == 1 for c in configs)
            logger.info(f"✅ Singleton never returns None after {len(configs)} calls")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3: Contact Form Atomicity (Per-Email Atomic Commits)
# ─────────────────────────────────────────────────────────────────────────────

class TestContactFormAtomicity:
    """
    Test contact form email delivery is atomic per email.
    
    Purpose: Verify that admin notification and visitor auto-reply are
    recorded atomically (one succeeds + other fails = partial success, not total failure).
    """

    def test_admin_notified_even_if_autoreply_fails(self, app):
        """
        Scenario: Admin notification succeeds, auto-reply fails.
        Expected: Inquiry.admin_notified = True, auto_reply_sent = False (partial success).
        """
        from app.services.communication.contact_service import process_contact_submission
        from app.models.portfolio import Inquiry
        from app import db
        
        with app.app_context():
            # Mock: admin email succeeds, auto-reply fails
            with patch('app.services.communication.contact_service.dispatch_email') as mock_dispatch:
                def dispatch_side_effect(**kwargs):
                    if 'auto_reply' in kwargs.get('category', ''):
                        # Auto-reply fails
                        return (False, 'SMTP down')
                    else:
                        # Admin notification succeeds
                        return (True, 'delivered')
                
                mock_dispatch.side_effect = dispatch_side_effect
                
                result = process_contact_submission(
                    tenant_slug='default',
                    name='John Doe',
                    email='john@example.com',
                    subject='Test Contact',
                    message='Hello, I have a question',
                    phone='',
                    company='',
                    source='contact_form',
                    ip_address='127.0.0.1',
                    user_agent='Test Browser'
                )
                
                # Should still report success (message saved)
                assert result.success is True
                
                # Check database state
                inquiry = Inquiry.query.filter_by(id=result.inquiry_id).first()
                assert inquiry is not None
                
                # Admin should be notified
                assert inquiry.admin_notified is True, "Admin should be notified"
                
                # Auto-reply should not be sent
                assert inquiry.auto_reply_sent is False, "Auto-reply should not be sent"
                
                logger.info(f"✅ Atomicity test: admin_notified={inquiry.admin_notified}, auto_reply_sent={inquiry.auto_reply_sent}")

    def test_autoreply_sent_even_if_admin_fails(self, app):
        """
        Scenario: Admin notification fails, auto-reply succeeds.
        Expected: Inquiry.admin_notified = False, auto_reply_sent = True (partial success).
        """
        from app.services.communication.contact_service import process_contact_submission
        from app.models.portfolio import Inquiry
        from app import db
        
        with app.app_context():
            # Mock: admin email fails, auto-reply succeeds
            with patch('app.services.communication.contact_service.dispatch_email') as mock_dispatch:
                def dispatch_side_effect(**kwargs):
                    if 'auto_reply' in kwargs.get('category', ''):
                        # Auto-reply succeeds
                        return (True, 'delivered')
                    else:
                        # Admin notification fails
                        return (False, 'MailerSend quota exceeded')
                
                mock_dispatch.side_effect = dispatch_side_effect
                
                result = process_contact_submission(
                    tenant_slug='default',
                    name='Jane Doe',
                    email='jane@example.com',
                    subject='Another Question',
                    message='This is a test',
                    phone='',
                    company='',
                    source='contact_form',
                    ip_address='127.0.0.1',
                    user_agent='Test Browser'
                )
                
                assert result.success is True
                
                inquiry = Inquiry.query.filter_by(id=result.inquiry_id).first()
                assert inquiry is not None
                
                # Admin should NOT be notified
                assert inquiry.admin_notified is False, "Admin should not be notified"
                
                # Auto-reply should be sent
                assert inquiry.auto_reply_sent is True, "Auto-reply should be sent"
                
                logger.info(f"✅ Atomicity test: admin_notified={inquiry.admin_notified}, auto_reply_sent={inquiry.auto_reply_sent}")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4: Startup Email Validation (Missing Provider Detection)
# ─────────────────────────────────────────────────────────────────────────────

class TestStartupEmailValidation:
    """
    Test startup validation detects missing email providers.
    
    Purpose: Verify that app startup validation warns/fails if no email
    provider is configured.
    """

    def test_production_fails_without_email_provider(self):
        """
        Scenario: Production environment with no MAILERSEND_API_KEY or SMTP config.
        Expected: validate_startup_env() adds error (app startup fails).
        """
        from app.startup_validation import validate_startup_env
        
        # Mock app
        app = Mock()
        app.debug = False
        app.config = {'SECRET_KEY': 'test-secret-key-' + 'x' * 50, 'FERNET_KEY': 'test-fernet'}
        
        # Mock environment (no email providers)
        with patch.dict(os.environ, {
            'SECRET_KEY': 'test-secret-key-' + 'x' * 50,
            'FERNET_KEY': 'test-fernet',
            'FLASK_ENV': 'production',
            'MAILERSEND_API_KEY': '',
            'SMTP_HOST': '',
            'SMTP_USERNAME': '',
            'SMTP_PASSWORD': '',
        }, clear=False):
            # Should not raise (logging handles it internally)
            # In production, SystemExit(1) is called
            with patch('sys.exit') as mock_exit:
                validate_startup_env(app)
                # Production should call sys.exit on errors
                assert mock_exit.called, "Production should call sys.exit on missing email provider"
                logger.info(f"✅ Production validation: sys.exit called for missing email provider")

    def test_development_warns_without_email_provider(self):
        """
        Scenario: Development environment with no email providers.
        Expected: validate_startup_env() adds warning (app boots, but warns).
        """
        from app.startup_validation import validate_startup_env
        
        # Mock app
        app = Mock()
        app.debug = True  # Development
        app.config = {'SECRET_KEY': 'test-secret-key-' + 'x' * 50, 'FERNET_KEY': 'test-fernet'}
        app.logger = Mock()
        
        # Mock environment (no email providers)
        with patch.dict(os.environ, {
            'SECRET_KEY': 'test-secret-key-' + 'x' * 50,
            'FERNET_KEY': 'test-fernet',
            'FLASK_ENV': 'development',
            'MAILERSEND_API_KEY': '',
            'SMTP_HOST': '',
            'SMTP_USERNAME': '',
            'SMTP_PASSWORD': '',
        }, clear=False):
            with patch('sys.exit') as mock_exit:
                validate_startup_env(app)
                # Development should NOT call sys.exit
                assert not mock_exit.called, "Development should not call sys.exit on missing email"
                logger.info(f"✅ Development validation: allows startup with warning")

    def test_mailersend_provider_passes_validation(self):
        """
        Scenario: Production with MAILERSEND_API_KEY configured.
        Expected: validate_startup_env() passes (no errors).
        """
        from app.startup_validation import validate_startup_env
        
        app = Mock()
        app.debug = False
        app.config = {'SECRET_KEY': 'test-secret-key-' + 'x' * 50, 'FERNET_KEY': 'test-fernet'}
        app.logger = Mock()
        
        with patch.dict(os.environ, {
            'SECRET_KEY': 'test-secret-key-' + 'x' * 50,
            'FERNET_KEY': 'test-fernet',
            'FLASK_ENV': 'production',
            'MAILERSEND_API_KEY': 'ms_live_xxx',
            'MAILERSEND_FROM_EMAIL': 'noreply@valid-domain.test',
            'SMTP_HOST': '',
        }, clear=False):
            with patch('sys.exit') as mock_exit:
                validate_startup_env(app)
                # Should NOT exit (MailerSend configured)
                assert not mock_exit.called, "Should pass with MailerSend configured"
                logger.info(f"✅ MailerSend provider passes validation")

    def test_smtp_provider_passes_validation(self):
        """
        Scenario: Production with SMTP configured (SMTP_HOST, username, password).
        Expected: validate_startup_env() passes (no errors).
        """
        from app.startup_validation import validate_startup_env
        
        app = Mock()
        app.debug = False
        app.config = {'SECRET_KEY': 'test-secret-key-' + 'x' * 50, 'FERNET_KEY': 'test-fernet'}
        app.logger = Mock()
        
        with patch.dict(os.environ, {
            'SECRET_KEY': 'test-secret-key-' + 'x' * 50,
            'FERNET_KEY': 'test-fernet',
            'FLASK_ENV': 'production',
            'MAILERSEND_API_KEY': '',
            'SMTP_HOST': 'smtp.gmail.com',
            'SMTP_USERNAME': 'user@gmail.com',
            'SMTP_PASSWORD': 'app-password',
            'SMTP_PORT': '587',
        }, clear=False):
            with patch('sys.exit') as mock_exit:
                validate_startup_env(app)
                # Should NOT exit (SMTP configured)
                assert not mock_exit.called, "Should pass with SMTP configured"
                logger.info(f"✅ SMTP provider passes validation")


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES & SETUP
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope='function')
def app():
    """Create Flask app for testing."""
    from app import create_app
    
    app = create_app('testing')
    
    with app.app_context():
        from app import db
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture(scope='function')
def client(app):
    """Create Flask test client."""
    return app.test_client()


# ─────────────────────────────────────────────────────────────────────────────
# TEST SUMMARY & EXECUTION
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
