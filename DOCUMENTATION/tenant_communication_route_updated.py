"""
UPDATED ROUTE: app/superadmin/__init__.py

Replace the tenant_communication route (lines 2374-2454) with this updated version.
Handles MailerSend configuration instead of SMTP.

This replaces:
    POST field handling for:
        - mail_username, mail_password, smtp_host, smtp_port, smtp_tls
    With:
        - mailersend_api_key, mailersend_from_email, mailersend_from_name

Template context variables:
    has_mailersend: bool (instead of has_smtp)
"""

@superadmin.route('/tenants/<int:tenant_id>/communication', methods=['GET', 'POST'])
@superadmin_required
def tenant_communication(tenant_id):
    """View/edit per-tenant contact form (Basin/internal) and MailerSend settings.
    
    v5.0 migration: SMTP fields deprecated, replaced with per-tenant MailerSend.
    SMTP columns retained in DB for rollback safety but not used for email dispatch.
    """
    from app.models.core import TenantCommunicationSettings
    from app.models.core import Tenant
    from app.models.tenant_data import Profile
    from app.services.basin_service import validate_basin_endpoint

    tenant  = Tenant.query.get_or_404(tenant_id)
    profile = Profile.query.filter_by(tenant_id=tenant_id).first_or_404()
    comm    = TenantCommunicationSettings.get_or_create(tenant_id, profile.tenant_slug)

    if request.method == 'POST':
        # ── Contact form provider ─────────────────────────────────────────
        form_provider  = request.form.get('form_provider', 'internal').strip()
        basin_endpoint = request.form.get('basin_endpoint', '').strip()

        if form_provider not in ('internal', 'basin'):
            form_provider = 'internal'

        if form_provider == 'basin' and basin_endpoint:
            valid, err = validate_basin_endpoint(basin_endpoint)
            if not valid:
                flash(f'Invalid Basin endpoint: {err}', 'danger')
                return redirect(url_for('superadmin.tenant_communication', tenant_id=tenant_id))
            tenant.basin_endpoint = basin_endpoint
        elif form_provider == 'internal':
            # Don't wipe basin_endpoint so they can re-enable without retyping
            pass

        tenant.form_provider = form_provider

        # ── MailerSend Configuration (NEW) ────────────────────────────────
        # API key: password field behavior — empty = keep existing
        api_key = request.form.get('mailersend_api_key', '').strip()
        if api_key and api_key != '●' * 8:
            # User provided a new API key
            comm.mailersend_api_key = api_key
        # else: leave existing value unchanged

        # From email and name can be cleared
        comm.mailersend_from_email = request.form.get('mailersend_from_email', '').strip()
        comm.mailersend_from_name  = request.form.get('mailersend_from_name', '').strip()

        # Validate: if any MailerSend field is set, all must be set
        has_email_config = (
            api_key or comm._mailersend_api_key or
            comm.mailersend_from_email or
            comm.mailersend_from_name
        )
        if has_email_config:
            if not (comm._mailersend_api_key and comm.mailersend_from_email and comm.mailersend_from_name):
                flash(
                    'MailerSend configuration incomplete. '
                    'Provide API Key, Sender Email, and Sender Name together, or leave all blank.',
                    'warning'
                )
                # Don't save incomplete config — keep old values
                db.session.rollback()
                comm = TenantCommunicationSettings.get_or_create(tenant_id, profile.tenant_slug)
                return redirect(url_for('superadmin.tenant_communication', tenant_id=tenant_id))

        # ── Reset Option ──────────────────────────────────────────────────
        if request.form.get('reset_to_defaults'):
            tenant.form_provider  = 'internal'
            tenant.basin_endpoint = None
            comm.mailersend_api_key    = ''
            comm.mailersend_from_email = ''
            comm.mailersend_from_name  = ''
            # Legacy SMTP fields cleared too
            comm.mail_username       = ''
            comm.mail_password       = ''
            comm.mail_default_sender = ''
            comm.admin_email         = ''
            comm.smtp_host           = ''
            comm.smtp_port           = 587
            comm.smtp_tls            = True
            flash('Communication settings reset to global defaults.', 'success')
        else:
            flash('Communication settings saved.', 'success')

        db.session.commit()
        log_security_event(
            'comm_settings_updated', current_user,
            f'Superadmin updated comm settings for tenant {profile.tenant_slug!r}',
        )
        return redirect(url_for('superadmin.tenant_communication', tenant_id=tenant_id))

    return render_template(
        'superadmin/tenant_communication.html',
        profile=profile,
        tenant=tenant,
        comm=comm,
        has_mailersend=comm.has_mailersend,  # ← Changed from has_smtp
        page_title=f'Communication — {profile.tenant_slug}',
    )
