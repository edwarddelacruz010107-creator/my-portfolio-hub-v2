"""
Portfolio CMS v6.3 — Theme Engine Integration Guide
=====================================================

This file documents how to wire the theme engine into your
existing Flask CMS. No backend logic changes required.

─────────────────────────────────────────────────────
STEP 1 — Register the ThemeEngine at app startup
─────────────────────────────────────────────────────

In your app factory (create_app / __init__.py):

    from app.theme_engine import ThemeEngine

    theme_engine = ThemeEngine()   # module-level singleton

    def create_app(config=None):
        app = Flask(__name__)
        # ... your existing setup ...

        theme_engine.init_app(app)   # ← add this line

        # Register the themes blueprint
        from app.blueprints.themes import themes_bp
        app.register_blueprint(themes_bp)

        return app


─────────────────────────────────────────────────────
STEP 2 — Add selected_theme column to Tenant model
─────────────────────────────────────────────────────

In your Tenant (or User) model, add:

    selected_theme = db.Column(db.String(64), nullable=True, default='default')

Generate and run the migration:

    flask db migrate -m "add selected_theme to tenants"
    flask db upgrade

No other model changes needed.


─────────────────────────────────────────────────────
STEP 3 — Update the public portfolio route
─────────────────────────────────────────────────────

Find your existing public portfolio view. It likely looks like:

    @portfolio_bp.route('/<slug>')
    def portfolio(slug):
        tenant = Tenant.query.filter_by(slug=slug).first_or_404()
        portfolio = Portfolio.query.filter_by(tenant_id=tenant.id).first_or_404()
        return render_template('portfolio/index.html', portfolio=portfolio)

Change it to use the theme engine:

    from app.theme_engine import get_theme_engine

    @portfolio_bp.route('/<slug>')
    def portfolio(slug):
        tenant = Tenant.query.filter_by(slug=slug).first_or_404()
        portfolio = Portfolio.query.filter_by(tenant_id=tenant.id).first_or_404()

        engine = get_theme_engine()
        return engine.render(tenant, 'index.html', portfolio=portfolio_data(portfolio))

The engine automatically:
  - Resolves the correct theme for the tenant
  - Enforces subscription rules (FREE → default only, PRO → all)
  - Falls back to default if the theme is missing


─────────────────────────────────────────────────────
STEP 4 — Portfolio data contract
─────────────────────────────────────────────────────

The `futuristic_cyber` theme (and all future themes) expects a
`portfolio` object with these attributes.
You can pass your existing SQLAlchemy model directly, or build a
lightweight DTO.

REQUIRED:
  portfolio.name              str     "Jian Cody Q. Dela Cruz"
  portfolio.title             str     "Software Developer"
  portfolio.bio               str     HTML-safe bio text
  portfolio.slug              str     URL slug
  portfolio.email             str     contact email

OPTIONAL (gracefully hidden if absent):
  portfolio.bio_plain         str     plain-text bio (for JS)
  portfolio.bio_extended      str     second bio paragraph
  portfolio.about_highlight   str     security/highlight callout
  portfolio.avatar_url        str     profile photo URL
  portfolio.resume_url        str     PDF/download URL
  portfolio.location          str     "Philippines"
  portfolio.response_time     str     "Within 24 hours"
  portfolio.available_for_work bool
  portfolio.availability_text str     "Open to freelance"
  portfolio.footer_tagline    str     "Built with precision"
  portfolio.meta_description  str     SEO meta description
  portfolio.initials          str     "JC" (for nav logo)
  portfolio.contact_form_action str   override form action URL

  portfolio.typing_phrases    list[str]   rotating typewriter phrases
  portfolio.about_subtitle    str
  portfolio.skills_subtitle   str
  portfolio.projects_subtitle str
  portfolio.experience_subtitle str
  portfolio.contact_subtitle  str

  portfolio.stats             list[{value: int, label: str}]
  portfolio.skills            list[SkillCategory]
  portfolio.projects          list[Project]
  portfolio.experiences       list[Experience]
  portfolio.services          list[Service]

  portfolio.github_url        str
  portfolio.linkedin_url      str
  portfolio.twitter_url       str
  portfolio.facebook_url      str

DATA SHAPES:

  SkillCategory:
    { name, icon(optional), items: [{name, level}] }

  Project:
    { title, description, image_url, tech_stack: list[str],
      category, demo_url, github_url }

  Experience:
    { year, date_range, title, company, description, type }

  Service:
    { name, subtitle, description, icon }


─────────────────────────────────────────────────────
STEP 5 — Add navigation link in admin sidebar
─────────────────────────────────────────────────────

In your existing admin sidebar template, add:

    <li>
      <a href="{{ url_for('themes.themes_index') }}"
         class="{{ 'active' if request.endpoint.startswith('themes.') }}">
        <i class="fas fa-palette"></i>
        <span>Themes</span>
      </a>
    </li>


─────────────────────────────────────────────────────
STEP 6 — Copy theme assets to Flask static folder
─────────────────────────────────────────────────────

Theme preview images need to be accessible via HTTP.
Place them at:

    app/static/themes/<theme_id>/preview.png

Or serve them from the themes directory by adding a static route:

    @app.route('/static/themes/<theme_id>/<filename>')
    def theme_static(theme_id, filename):
        import os
        theme_dir = os.path.join(THEMES_DIR, theme_id, 'static')
        return send_from_directory(theme_dir, filename)


─────────────────────────────────────────────────────
ADDING NEW THEMES
─────────────────────────────────────────────────────

1. Create a folder under themes/<new_theme_id>/
2. Add theme.json (copy from existing, update fields)
3. Add templates/index.html (Jinja2, same portfolio contract)
4. Add a preview.png (1280×800 recommended)
5. Restart Flask — the engine auto-discovers it

No code changes needed to add themes.


─────────────────────────────────────────────────────
SUBSCRIPTION ENFORCEMENT SUMMARY
─────────────────────────────────────────────────────

  tenant.is_administrator = True   → ALL themes, no restrictions
  tenant.subscription_plan = 'pro' → ALL themes
  tenant.subscription_plan = 'free'→ Only themes where premium = false
  Missing/invalid theme            → Falls back to 'default'

This is enforced in:
  - ThemeEngine.resolve_theme()     (render time)
  - ThemeEngine.can_use_theme()     (UI time)
  - themes_bp.apply_theme()         (write time)
"""
