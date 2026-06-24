"""initial_schema

Revision ID: 7d0f3492b2b3
Revises: 
Create Date: 2026-06-19

This is the TRUE initial schema migration that creates all core tables.
The previous version was empty (just pass statements), which caused
all subsequent migrations to fail because tables didn't exist.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from datetime import datetime, timezone

# revision identifiers, used by Alembic.
revision = '7d0f3492b2b3'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('username', sa.String(120), nullable=False, unique=True, index=True),
        sa.Column('email', sa.String(120), nullable=False, unique=True, index=True),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('is_admin', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('is_superadmin', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('tenant_slug', sa.String(120), nullable=True, index=True, server_default='default'),
        sa.Column('tenant_id', sa.Integer, nullable=True, index=True),
        sa.Column('last_login', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_login_ip', sa.String(45), nullable=True),
        sa.Column('totp_secret', sa.String(64), nullable=True),
        sa.Column('totp_enabled', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('totp_backup_codes', sa.Text, nullable=True),
        sa.Column('failed_login_attempts', sa.Integer, nullable=False, server_default='0'),
        sa.Column('last_failed_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('require_password_reset', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('last_password_changed', sa.DateTime(timezone=True), nullable=True),
        sa.Column('session_token', sa.String(255), nullable=True),
        sa.Column('last_totp_verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_totp_code_hash', sa.String(64), nullable=True),
        sa.Column('password_reset_token', sa.String(100), nullable=True),
        sa.Column('password_reset_expires', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
    )
    op.create_index('ix_users_session_token', 'users', ['session_token'], unique=True, postgresql_where=sa.text("session_token IS NOT NULL"))
    op.create_index('ix_users_password_reset_token', 'users', ['password_reset_token'], unique=True, postgresql_where=sa.text("password_reset_token IS NOT NULL"))

    # Create tenants table
    op.create_table(
        'tenants',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('slug', sa.String(120), nullable=False, unique=True, index=True),
        sa.Column('company_name', sa.String(200), nullable=False, server_default=''),
        sa.Column('email', sa.String(120), nullable=False, server_default=''),
        sa.Column('contact_email', sa.String(120), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='active'),
        sa.Column('plan', sa.String(50), nullable=False, server_default='Basic'),
        sa.Column('form_provider', sa.String(50), nullable=True, server_default='internal'),
        sa.Column('basin_endpoint', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
    )
    op.create_index('ix_tenants_slug', 'tenants', ['slug'], unique=True)
    op.create_index('ix_tenants_status', 'tenants', ['status'])
    op.create_index('ix_tenants_contact_email', 'tenants', ['contact_email'])

    # Create subscriptions table
    op.create_table(
        'subscriptions',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('tenant_id', sa.Integer, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('plan', sa.String(50), nullable=False, server_default='Basic'),
        sa.Column('status', sa.String(30), nullable=False, server_default='pending'),
        sa.Column('billing_cycle', sa.String(20), nullable=True, server_default='monthly'),
        sa.Column('amount_paid', sa.Float, nullable=False, server_default='0'),
        sa.Column('payment_method', sa.String(100), nullable=True),
        sa.Column('payment_reference', sa.String(255), nullable=True),
        sa.Column('payment_proof', sa.String(255), nullable=True),
        sa.Column('payment_status', sa.String(20), nullable=False, server_default='unpaid'),
        sa.Column('license_key', sa.String(64), nullable=True),
        sa.Column('paymongo_subscription_id', sa.String(255), nullable=True),
        sa.Column('paymongo_customer_id', sa.String(255), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_subscriptions_tenant_status', 'subscriptions', ['tenant_id', 'status'])
    op.create_index('ix_subscriptions_expires_at', 'subscriptions', ['expires_at'])
    op.create_index('ix_subscriptions_paymongo_subscription_id', 'subscriptions', ['paymongo_subscription_id'], unique=True)
    op.create_unique_constraint('uq_subscriptions_license_key', 'subscriptions', ['license_key'])

    # Create payment_instructions table
    op.create_table(
        'payment_instructions',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('tenant_id', sa.Integer, sa.ForeignKey('tenants.id'), nullable=True, index=True),
        sa.Column('method', sa.String(50), nullable=False, server_default=''),
        sa.Column('title', sa.String(120), nullable=False, server_default=''),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('account_name', sa.String(120), nullable=False, server_default=''),
        sa.Column('account_number', sa.String(120), nullable=False, server_default=''),
        sa.Column('bank_name', sa.String(120), nullable=False, server_default=''),
        sa.Column('qr_image', sa.String(255), nullable=False, server_default=''),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
    )

    # Create payment_submissions table
    op.create_table(
        'payment_submissions',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('tenant_id', sa.Integer, sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('subscription_id', sa.Integer, sa.ForeignKey('subscriptions.id'), nullable=True),
        sa.Column('plan', sa.String(50), nullable=False, server_default='Basic'),
        sa.Column('amount_paid', sa.Float, nullable=False, server_default='0'),
        sa.Column('payment_method', sa.String(100), nullable=False, server_default=''),
        sa.Column('payment_reference', sa.String(255), nullable=False, server_default=''),
        sa.Column('payment_proof', sa.String(255), nullable=False, server_default=''),
        sa.Column('note', sa.Text, nullable=True),
        sa.Column('status', sa.String(30), nullable=False, server_default='pending'),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reviewed_by', sa.String(120), nullable=False, server_default=''),
        sa.Column('review_notes', sa.Text, nullable=True),
    )
    op.create_index('ix_payment_subscriptions_subscription_id', 'payment_submissions', ['subscription_id'])
    op.create_index('ix_payment_subscriptions_tenant_status', 'payment_submissions', ['tenant_id', 'status'])

    # Create payment_methods table
    op.create_table(
        'payment_methods',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('tenant_id', sa.Integer, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('method', sa.String(50), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('qr_image', sa.String(255), nullable=True),
        sa.Column('instructions', sa.Text, nullable=True),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
    )

    # Create platform_settings table
    op.create_table(
        'platform_settings',
        sa.Column('key', sa.String(100), nullable=False),
        sa.Column('value', sa.String(500), nullable=False, server_default=''),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('key'),
    )

    # Create password_reset_otps table
    op.create_table(
        'password_reset_otps',
        sa.Column('id', sa.Integer, nullable=False),
        sa.Column('user_type', sa.String(20), nullable=False),
        sa.Column('user_id', sa.Integer, nullable=False),
        sa.Column('tenant_id', sa.Integer, nullable=True),
        sa.Column('email', sa.String(120), nullable=False),
        sa.Column('otp_hash', sa.String(64), nullable=False),
        sa.Column('attempts', sa.Integer, default=0, nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used', sa.Boolean, default=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.String(300), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_otp_user_type_user_id', 'password_reset_otps', ['user_type', 'user_id'])
    op.create_index('ix_otp_tenant_id', 'password_reset_otps', ['tenant_id'])
    op.create_index('ix_otp_expires_at', 'password_reset_otps', ['expires_at'])

    # Create global_email_config table
    op.create_table(
        'global_email_config',
        sa.Column('id', sa.Integer, nullable=False),
        sa.Column('web3forms_key', sa.Text, default='', nullable=True),
        sa.Column('sender_name', sa.String(200), default='Portfolio CMS', nullable=True),
        sa.Column('sender_email', sa.String(200), default='', nullable=True),
        sa.Column('otp_expiry_minutes', sa.Integer, default=10, nullable=False),
        sa.Column('recovery_enabled', sa.Boolean, default=True, nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sa.String(120), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.execute("INSERT INTO global_email_config (id, sender_name, otp_expiry_minutes, recovery_enabled) VALUES (1, 'Portfolio CMS', 10, 1)")

    # Create tenant_communication_settings table
    op.create_table(
        'tenant_communication_settings',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('tenant_id', sa.Integer, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('tenant_slug', sa.String(120), nullable=False),
        sa.Column('web3forms_key', sa.Text, nullable=False, server_default=''),
        sa.Column('mail_username', sa.String(200), nullable=False, server_default=''),
        sa.Column('mail_password', sa.Text, nullable=False, server_default=''),
        sa.Column('mail_default_sender', sa.String(200), nullable=False, server_default=''),
        sa.Column('admin_email', sa.String(200), nullable=False, server_default=''),
        sa.Column('smtp_host', sa.String(200), nullable=False, server_default=''),
        sa.Column('smtp_port', sa.Integer, nullable=False, server_default='587'),
        sa.Column('smtp_tls', sa.Boolean, nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint('uq_tenant_comm_settings', 'tenant_communication_settings', ['tenant_id'])
    op.create_index('ix_tenant_comm_slug', 'tenant_communication_settings', ['tenant_slug'])

    # Create tenant_form_settings table
    op.create_table(
        'tenant_form_settings',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('tenant_id', sa.Integer, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('provider', sa.String(50), nullable=False, server_default='disabled'),
        sa.Column('api_key_encrypted', sa.Text, nullable=False, server_default=''),
        sa.Column('form_endpoint', sa.Text, nullable=True),
        sa.Column('receiver_email', sa.String(200), nullable=True),
        sa.Column('sender_name', sa.String(200), nullable=True),
        sa.Column('is_enabled', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
        sa.UniqueConstraint('tenant_id', name='uq_tenant_form_settings'),
    )
    op.create_index('ix_tfs_provider', 'tenant_form_settings', ['provider'])
    op.create_index('ix_tfs_is_enabled', 'tenant_form_settings', ['is_enabled'])

    # Create webhook_events table
    op.create_table(
        'webhook_events',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('tenant_id', sa.Integer, sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('payload', sa.Text, nullable=True),
        sa.Column('processed', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
    )

    # Create inquiries table
    op.create_table(
        'inquiries',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('tenant_id', sa.Integer, nullable=True),
        sa.Column('tenant_slug', sa.String(120), nullable=False, index=True, default='default'),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('email', sa.String(120), nullable=False),
        sa.Column('subject', sa.String(200), nullable=True),
        sa.Column('message', sa.Text, nullable=False),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.String(500), nullable=True),
        sa.Column('submission_id', sa.String(80), nullable=True),
        sa.Column('provider_used', sa.String(30), nullable=True),
        sa.Column('delivery_status', sa.String(20), nullable=True),
        sa.Column('delivery_error', sa.String(500), nullable=True),
        sa.Column('sender', sa.String(20), nullable=False, server_default='visitor'),
        sa.Column('is_read', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
    )
    op.create_index('ix_inquiries_tenant_slug', 'inquiries', ['tenant_slug'])
    op.create_index('ix_inquiries_provider_delivery', 'inquiries', ['provider_used', 'delivery_status'])
    op.create_index('ix_inquiries_tenant_submission_id', 'inquiries', ['tenant_slug', 'submission_id'])

    # Create inquiry_replies table
    op.create_table(
        'inquiry_replies',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('inquiry_id', sa.Integer, sa.ForeignKey('inquiries.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tenant_slug', sa.String(120), nullable=False, index=True),
        sa.Column('direction', sa.String(20), nullable=False),
        sa.Column('sender_name', sa.String(120), nullable=False),
        sa.Column('message', sa.Text, nullable=False),
        sa.Column('is_read', sa.Boolean, default=False, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), default=datetime.now(timezone.utc)),
    )
    op.create_index('ix_reply_inquiry_id', 'inquiry_replies', ['inquiry_id'])
    op.create_index('ix_reply_tenant_slug', 'inquiry_replies', ['tenant_slug'])
    op.create_index('ix_reply_direction_read', 'inquiry_replies', ['direction', 'is_read'])

    # Create subscription_notifications table
    op.create_table(
        'subscription_notifications',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('tenant_id', sa.Integer, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('subscription_id', sa.Integer, sa.ForeignKey('subscriptions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('notification_type', sa.String(50), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('message', sa.Text, nullable=False),
        sa.Column('is_read', sa.Boolean, default=False, nullable=False),
        sa.Column('sent_via_email', sa.Boolean, default=False, nullable=False),
        sa.Column('sent_via_dashboard', sa.Boolean, default=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), default=datetime.now(timezone.utc)),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_sub_notif_tenant_read', 'subscription_notifications', ['tenant_id', 'is_read'])
    op.create_index('ix_sub_notif_type', 'subscription_notifications', ['notification_type'])

    # Create activity_log table
    op.create_table(
        'activity_log',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('tenant_id', sa.Integer, sa.ForeignKey('tenants.id', ondelete='SET NULL'), nullable=True, index=True),
        sa.Column('tenant_slug', sa.String(120), nullable=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('username', sa.String(120), nullable=True),
        sa.Column('action', sa.String(50), nullable=False),
        sa.Column('entity_type', sa.String(50), nullable=True),
        sa.Column('entity_name', sa.String(200), nullable=True),
        sa.Column('description', sa.String(500), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), default=datetime.now(timezone.utc)),
    )
    op.create_index('ix_activitylog_created_at', 'activity_log', ['created_at'])
    op.create_index('ix_activitylog_tenant_action', 'activity_log', ['tenant_slug', 'action'])
    op.create_index('ix_activitylog_user_tenant', 'activity_log', ['user_id', 'tenant_slug'])


def downgrade():
    # Drop all tables in reverse order of creation
    op.drop_table('activity_log')
    op.drop_table('subscription_notifications')
    op.drop_table('inquiry_replies')
    op.drop_table('inquiries')
    op.drop_table('webhook_events')
    op.drop_table('tenant_form_settings')
    op.drop_table('tenant_communication_settings')
    op.drop_table('global_email_config')
    op.drop_table('password_reset_otps')
    op.drop_table('platform_settings')
    op.drop_table('payment_methods')
    op.drop_table('payment_submissions')
    op.drop_table('payment_instructions')
    op.drop_table('subscriptions')
    op.drop_table('tenants')
    op.drop_table('users')