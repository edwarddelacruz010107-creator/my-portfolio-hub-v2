"""
PATCH: app/__init__.py — Changes to apply (v5.3 → v5.3-FIXED)
═══════════════════════════════════════════════════════════════
Apply these 4 targeted edits to the existing app/__init__.py.
DO NOT replace the entire file — apply each change surgically.

CHANGE 1: Import auto_bootstrap_superadmin near the top of create_app()
CHANGE 2: Call auto_bootstrap_superadmin() in the app_context startup block (production + dev)
CHANGE 3: Register system_check routes on the superadmin blueprint
CHANGE 4: Update cli_create_superadmin() to read SUPERADMIN_USERNAME / SUPERADMIN_EMAIL

──────────────────────────────────────────────────────────────────────────────
CHANGE 1 — After: from config import config
──────────────────────────────────────────────────────────────────────────────
ADD this import anywhere near the top of the file (after config import):

    from app._superadmin_bootstrap import auto_bootstrap_superadmin

──────────────────────────────────────────────────────────────────────────────
CHANGE 2 — Inside create_app(), inside the `with app.app_context():` block,
           AFTER the existing `db.session.execute(db.text("SELECT 1"))` check.

Find this existing block (around line 300):
──────────────────────────────────────────────────────────────────────────────

    with app.app_context():
        _is_production = not app.config.get('TESTING') and not app.config.get('DEBUG')
        try:
            db.session.execute(db.text("SELECT 1"))
            db.session.remove()

            if app.debug:
                _ensure_profile_columns()
                _ensure_default_tenant()

            logger.info("Database connection verified at startup")
            ...

──────────────────────────────────────────────────────────────────────────────
REPLACE that block with:
──────────────────────────────────────────────────────────────────────────────

    with app.app_context():
        _is_production = not app.config.get('TESTING') and not app.config.get('DEBUG')
        try:
            db.session.execute(db.text("SELECT 1"))
            db.session.remove()

            if app.debug:
                _ensure_profile_columns()
                _ensure_default_tenant()

            logger.info("Database connection verified at startup")

            # ── Superadmin auto-bootstrap (CHANGE 2) ──────────────────────────
            # Runs in BOTH development and production.
            # Idempotent: safe to run on every restart.
            # In production: reads SUPERADMIN_USERNAME / SUPERADMIN_EMAIL /
            # SUPERADMIN_PASSWORD from environment. If SUPERADMIN_PASSWORD is
            # absent and no superadmin exists, generates one and prints it to
            # the deploy log (visible in Render's build/deploy log).
            #
            # This is why login fails on fresh production deployments — there
            # is no superadmin user. This block fixes that permanently.
            if not app.config.get('TESTING'):
                auto_bootstrap_superadmin(app, db)

            # (rest of existing startup block — tenant lookup, etc.)
            ...

──────────────────────────────────────────────────────────────────────────────
CHANGE 3 — After the superadmin blueprint is registered in create_app(),
           add the system-check routes.

Find this line (near the bottom of create_app()):
──────────────────────────────────────────────────────────────────────────────

    app.register_blueprint(superadmin_blueprint, url_prefix='/superadmin')

──────────────────────────────────────────────────────────────────────────────
ADD immediately after it:
──────────────────────────────────────────────────────────────────────────────

    # ── System check diagnostic routes (CHANGE 3) ─────────────────────────
    from app.superadmin.system_check import register_system_check
    register_system_check(superadmin_blueprint)

──────────────────────────────────────────────────────────────────────────────
CHANGE 4 — Update cli_create_superadmin() to use env-var driven username/email.

Find the existing cli_create_superadmin() command body (look for):
──────────────────────────────────────────────────────────────────────────────

        password = _os.environ.get('SUPERADMIN_PASSWORD', _secrets.token_urlsafe(16))
        existing = User.query.filter_by(username='superadmin').first()

        tenant = Tenant.query.filter_by(slug='default').first()
        ...

        if existing:
            existing.password      = password
            existing.is_superadmin = True
            existing.is_admin      = True
            existing.tenant        = tenant
            existing.tenant_slug   = tenant.slug
            db.session.commit()
            click.echo('✔  Superadmin already exists — password reset:')
            click.echo(f'   Username: superadmin')
            click.echo(f'   New password: {password}')
            ...

        superadmin = User(
            username='superadmin',
            email='superadmin@portfolio.local',
            ...

──────────────────────────────────────────────────────────────────────────────
REPLACE the entire cli_create_superadmin body with:
──────────────────────────────────────────────────────────────────────────────

        import secrets as _secrets
        import os as _os
        from app.models import User
        from app.models.portfolio import Tenant

        # FIX: Read all fields from env vars, not hardcoded strings.
        username = _os.environ.get('SUPERADMIN_USERNAME', 'superadmin').strip()
        email    = _os.environ.get('SUPERADMIN_EMAIL', 'superadmin@portfolio.local').strip()
        password = _os.environ.get('SUPERADMIN_PASSWORD', '').strip()
        pwd_generated = False
        if not password:
            password = _secrets.token_urlsafe(20)
            pwd_generated = True

        tenant = Tenant.query.filter_by(slug='default').first()
        if not tenant:
            tenant = Tenant(
                slug='default', company_name='Default Portfolio',
                email=email, status='active', plan='Basic',
            )
            db.session.add(tenant)
            db.session.flush()

        existing = (
            User.query.filter_by(is_superadmin=True).first()
            or User.query.filter_by(username=username).first()
        )

        if existing:
            existing.password      = password
            existing.is_superadmin = True
            existing.is_admin      = True
            existing.tenant        = tenant
            existing.tenant_slug   = tenant.slug
            db.session.commit()
            click.echo(f'✔  Superadmin exists — password reset.')
            click.echo(f'   Username: {existing.username}')
            if pwd_generated:
                click.echo(f'   New password: {password}')
                click.echo('⚠️  No SUPERADMIN_PASSWORD env var set — save this password now!')
            else:
                click.echo('   Password: (from SUPERADMIN_PASSWORD env var)')
            click.echo('   Login at: /superadmin/login')
            return

        superadmin = User(
            username=username,
            email=email,
            tenant=tenant,
            tenant_slug=tenant.slug,
            is_admin=True,
            is_superadmin=True,
        )
        superadmin.password = password
        db.session.add(superadmin)
        db.session.commit()

        click.echo('✔  Superadmin created:')
        click.echo(f'   Username: {username}')
        click.echo(f'   Email:    {email}')
        if pwd_generated:
            click.echo(f'   Password: {password}')
            click.echo('⚠️  Auto-generated! Set SUPERADMIN_PASSWORD env var for reproducible resets.')
        else:
            click.echo('   Password: (from SUPERADMIN_PASSWORD env var)')
        click.echo('   Login URL: /superadmin/login')
        click.echo('⚠️  Change this password immediately after first login!')
"""
