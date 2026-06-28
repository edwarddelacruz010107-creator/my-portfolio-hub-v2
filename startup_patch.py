"""
startup_patch.py

Register the admin_credentials blueprint in your app factory / startup.py.

FIND in your startup.py (or wherever blueprints are registered):

    from routes.admin_portfolio import admin_portfolio_bp
    app.register_blueprint(admin_portfolio_bp)

ADD BELOW IT:

    from routes.admin_credentials import admin_credentials_bp
    app.register_blueprint(admin_credentials_bp)

Also add the navigation links to your admin sidebar template.
FIND templates/admin/partials/sidebar.html (or similar) and add:

    <a href="{{ url_for('admin_credentials.certificates_index') }}"
       class="sidebar-link {% if request.endpoint and 'certificate' in request.endpoint %}active{% endif %}">
      🏆 Certificates
    </a>
    <a href="{{ url_for('admin_credentials.badges_index') }}"
       class="sidebar-link {% if request.endpoint and 'badge' in request.endpoint %}active{% endif %}">
      🎖️ Badges
    </a>
"""
