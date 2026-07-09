# MyPortfolioHub

**MyPortfolioHub** is a production-oriented, multi-tenant portfolio CMS built with Flask. It helps students, freelancers, developers, designers, and professionals create, manage, and publish portfolio websites from one platform.

The system includes tenant dashboards, public portfolio pages, theme previews, media management, contact forms, email provider configuration, authentication flows, billing support, and a Superadmin control panel for platform-level operations.

---

## Table of Contents

- [Overview](#overview)
- [Core Features](#core-features)
- [Recent Fixes in This Build](#recent-fixes-in-this-build)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Available Themes](#available-themes)
- [Local Development Setup](#local-development-setup)
- [Environment Configuration](#environment-configuration)
- [Database and Migrations](#database-and-migrations)
- [Important Routes](#important-routes)
- [Media and Upload Management](#media-and-upload-management)
- [Email and Contact Forms](#email-and-contact-forms)
- [Billing and Plan Controls](#billing-and-plan-controls)
- [Security Notes](#security-notes)
- [Testing and Validation](#testing-and-validation)
- [Production Deployment Notes](#production-deployment-notes)
- [Maintenance Checklist](#maintenance-checklist)

---

## Overview

MyPortfolioHub is designed as a SaaS-style portfolio platform with separate responsibilities for each user role:

| Role | Purpose |
|---|---|
| **Visitor** | Browses the landing page, explores portfolio showcases, opens public portfolios, and submits contact forms. |
| **Tenant/Admin** | Manages their own portfolio profile, projects, skills, services, testimonials, certificates, badges, theme selection, media uploads, billing, and account settings. |
| **Superadmin** | Manages platform settings, themes, tenant visibility, email providers, landing page content, uploads, billing controls, and operational tools. |

The platform supports both filesystem-based themes and Superadmin-managed theme catalog entries. Tenants can preview themes before applying them, while plan gating controls which themes are available for use.

---

## Core Features

### Portfolio Management

- Multi-tenant portfolio publishing
- Tenant-specific profile pages
- Project showcase with images, views, and reactions
- Skills, services, testimonials, work experience, certificates, and badges
- Public portfolio routes with clean URLs
- Default administrator portfolio support

### Theme System

- Filesystem theme registry under `/themes`
- Public theme preview route
- Tenant dashboard theme selection
- Superadmin theme catalog manager
- Theme metadata through `theme.json`
- Preview thumbnails and theme showcase assets
- Plan-based theme availability

### Admin Dashboard

- Portfolio profile management
- Project and image upload management
- Certificate and badge management
- Theme preview and apply workflow
- Tenant media library
- Email/contact settings
- Billing and subscription visibility

### Superadmin Dashboard

- Tenant and platform oversight
- Theme catalog synchronization
- Theme thumbnails, banners, and preview image uploads
- Landing page controls
- Email and form provider configuration
- Cross-tenant media/upload visibility
- Billing, plan, and discount management

### Authentication and Security

- Email/password authentication
- Signup email verification OTP
- Password reset flow
- Optional Google OAuth and GitHub OAuth support
- CSRF protection
- Rate limiting
- Secure upload handling
- Production-focused configuration defaults

### Integrations

- MailerSend, SMTP, and Resend-style email provider support
- PayMongo-ready billing configuration
- Optional Supabase media storage
- Redis support for rate limiting/cache storage
- Sentry-ready error monitoring

---

## Recent Fixes in This Build

This package includes the latest stabilization work for the theme and upload systems.

### Theme Preview Repair

- Fixed blank theme previews caused by hidden reveal sections when frontend JavaScript is blocked or delayed.
- Added safer theme rendering defaults.
- Repaired broken contact-form fallback behavior in theme templates.
- Removed hard-coded sample owner text from the Developer Pro theme.
- Added consistent hidden `subject` fields to theme contact forms.
- Improved preview behavior so locked themes can still be previewed while applying remains plan-gated.

### Admin Upload Navigation Repair

The Admin Dashboard upload library now includes tenant-owned images from:

- Profile photo
- Project cover/gallery images
- Testimonial avatars
- Certificate images
- Certificate badge images

The Superadmin media/uploads view was also updated to support broader cross-tenant visibility for certificate and badge-related uploads.

---

## Tech Stack

| Area | Technology |
|---|---|
| Backend | Python 3.12, Flask 3.x |
| ORM / Database | SQLAlchemy 2.x, Flask-Migrate, Alembic, PostgreSQL/SQLite |
| Authentication | Flask-Login, Flask-WTF, OTP, OAuth via Authlib |
| Email | MailerSend, SMTP, provider abstraction |
| Payments | PayMongo-ready service layer |
| Storage | Local uploads, optional Supabase storage |
| Security | CSRF, rate limiting, encrypted provider credentials, secure cookies |
| Deployment | Gunicorn, Docker, Render-ready config |
| Testing | Pytest-compatible test structure |

---

## Project Structure

```text
.
├── app/
│   ├── admin/                 # Tenant/admin dashboard routes and handlers
│   ├── auth/                  # Login, signup, OTP, password reset, OAuth
│   ├── main/                  # Main/default portfolio routes
│   ├── models/                # SQLAlchemy models and compatibility shims
│   ├── public/                # Landing page, explore, pricing, theme previews
│   ├── services/              # Business logic services
│   ├── superadmin/            # Platform-level management tools
│   ├── templates/             # Flask templates
│   ├── static/                # Static assets and uploaded media
│   ├── tenant/                # Public tenant portfolio routes
│   ├── theme_context.py       # Theme data context builder
│   └── theme_engine.py        # Filesystem theme registry and renderer
├── themes/                    # Portfolio themes and theme metadata
├── migrations/                # Alembic migration files and SQL helpers
├── tests/                     # Automated tests
├── tools/                     # Maintenance and repair scripts
├── run.py                     # Local development entry point
├── wsgi.py                    # Production WSGI entry point
├── requirements.txt           # Runtime dependencies
└── README.md                  # Project documentation
```

---

## Available Themes

| Theme ID | Display Name | Plan Requirement |
|---|---|---|
| `default` | Default Clean | Available by default |
| `developer_pro` | Developer Pro | Available by default |
| `futuristic_cyber` | Futuristic Cyber | Available by default |
| `aurora_dev_pro` | Aurora Dev Pro | Pro |
| `blockform_pro` | Blockform Pro | Pro |
| `drafting_table_pro` | Drafting Table Pro | Pro |
| `executive_pro` | Executive Pro | Pro |
| `glass_terminal_pro` | Glass Terminal Pro | Pro |
| `neon_stack_pro` | Neon Stack Pro | Pro |
| `schematic_grid_pro` | Schematic Grid Pro | Pro |
| `schematic_pro` | Schematic Pro | Pro |

Theme folders must contain:

```text
themes/<theme_id>/
├── theme.json
└── templates/
    └── index.html
```

Static preview assets are stored under:

```text
app/static/themes/
```

---

## Local Development Setup

### 1. Create and activate a virtual environment

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Create a local environment file

Copy the production template and adjust values for local development:

```bash
cp env.production.template .env
```

For Windows PowerShell:

```powershell
Copy-Item env.production.template .env
```

At minimum, configure:

```env
FLASK_ENV=development
FLASK_DEBUG=True
SECRET_KEY=replace-with-a-local-secret
FERNET_KEY=replace-with-a-valid-fernet-key
CORE_DATABASE_URL=sqlite:///portfolio_dev.db
```

Generate a valid Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 4. Run database migrations

```bash
flask --app run.py db upgrade
```

### 5. Start the development server

```bash
python run.py
```

Open the app at:

```text
http://localhost:5000
```

---

## Environment Configuration

Important environment variables include:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Flask session and CSRF signing key. Required in production. |
| `FERNET_KEY` | Encryption key for sensitive provider credentials. Required in production. |
| `CORE_DATABASE_URL` | Primary database connection string. |
| `TENANT_DATABASE_URL` | Optional separate tenant database connection string. |
| `REDIS_URL` | Optional Redis backend for rate limiting and caching. |
| `APP_BASE_URL` | Public application base URL. |
| `ADMIN_EMAIL` | Fallback admin/contact notification email. |
| `MAILERSEND_API_KEY` | Optional MailerSend fallback API key. |
| `PAYMONGO_ENABLED` | Enables PayMongo billing integration when set to `true`. |
| `PAYMONGO_PUBLIC_KEY` | PayMongo public key. |
| `PAYMONGO_SECRET_KEY` | PayMongo secret key. Required when PayMongo is enabled. |
| `PAYMONGO_WEBHOOK_SECRET` | PayMongo webhook secret. Required when PayMongo is enabled. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Enables Google OAuth when both are present. |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | Enables GitHub OAuth when both are present. |
| `USE_SUPABASE_STORAGE` | Enables Supabase-backed media storage when set to `true`. |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` / `SUPABASE_BUCKET` | Supabase storage configuration. |
| `CONVERT_UPLOADS_TO_WEBP` | Converts new profile, project, testimonial, certificate, badge, and other photo uploads to WebP. Defaults to `true`. |
| `UPLOAD_WEBP_QUALITY` | WebP quality for new uploads. Recommended: `82` for general images, higher only if needed. |
| `UPLOAD_IMAGE_MAX_DIMENSION` | Maximum width/height for large uploads before saving. Defaults to `2048`. |

Never commit `.env`, production secrets, API keys, database URLs, or webhook secrets.

---

## Database and Migrations

Run migrations with:

```bash
flask --app run.py db upgrade
```

Create a new migration after model changes:

```bash
flask --app run.py db migrate -m "describe change"
```

Apply the migration:

```bash
flask --app run.py db upgrade
```

Useful maintenance commands registered by the app include:

```bash
flask --app run.py ensure-default-tenant
flask --app run.py check-contact-config
flask --app run.py media audit-image-fields
flask --app run.py media clean-broken-image-fields --apply
```

For production, prefer Alembic migrations over runtime schema patching.

---

## Important Routes

| Route | Purpose |
|---|---|
| `/` | Public landing page |
| `/explore` | Portfolio discovery/explore page |
| `/projects` | Public project showcase |
| `/feed` | Public feed page |
| `/pricing` | Public pricing page |
| `/themes/<theme_id>/preview` | Public theme preview |
| `/u/<tenant_slug>` | Public tenant portfolio route |
| `/administrator` | Default administrator portfolio |
| `/auth/login` | Login page |
| `/auth/forgot-password` | Password reset request page |
| `/admin/appearance/themes` | Tenant theme selection page |
| `/superadmin/themes` | Superadmin theme catalog manager |

---

## Media and Upload Management

Admin uploads now collect tenant media from multiple portfolio sections, not only projects.

Supported image sources include:

- Profile image fields
- Project images
- Testimonial avatars
- Certificate images
- Certificate badge images
- Theme catalog thumbnails, banners, and previews

Recommended upload maintenance:

```bash
flask --app run.py media audit-image-fields
```

To clean invalid tuple-like image field values:

```bash
flask --app run.py media clean-broken-image-fields --apply
```

Use Superadmin uploads/media tools for platform-wide review and Admin uploads for tenant-owned media review.

---

## Email and Contact Forms

The system supports tenant and platform-level contact workflows.

General behavior:

- Public contact forms create inquiry records.
- Configured email providers send notifications when available.
- Tenant/admin contact settings can be managed from the dashboard.
- Superadmin email settings are used for platform-level delivery and fallback behavior.

Supported provider areas:

- MailerSend
- SMTP
- Resend-style provider configuration
- Environment variable fallback for production bootstrapping

After configuring email providers, verify delivery through the dashboard test tools and the CLI contact configuration check:

```bash
flask --app run.py check-contact-config
```

---

## Billing and Plan Controls

The project includes plan-aware service layers for trial, basic, pro, enterprise, and administrator-level access. Billing-related modules support PayMongo-style checkout and subscription flows.

Plan gating is used for features such as:

- Premium theme access
- Theme application controls
- Storage/media limits
- Advanced tenant features
- Administrator-only system access

PayMongo should only be enabled when all required keys and webhook secrets are configured.

---

## Security Notes

Before deploying to production:

- Generate strong values for `SECRET_KEY` and `FERNET_KEY`.
- Keep `.env` files out of version control.
- Use PostgreSQL for production deployments.
- Use Redis for production-grade rate limiting.
- Enable HTTPS and secure cookies.
- Configure trusted proxy headers correctly on Render, Nginx, or any reverse proxy.
- Verify CSRF behavior after changing forms or routes.
- Restrict upload types and keep image validation enabled.
- Rotate provider keys if they were ever exposed in logs, screenshots, or committed files.
- Confirm email DNS records for the active sending domain: SPF, DKIM, and DMARC.

---

## Testing and Validation

Run the available test suite:

```bash
pytest
```

Run Python compile validation:

```bash
python -m compileall app
```

Recommended manual checks before every release:

1. Signup with email OTP.
2. Login and logout.
3. Password reset flow.
4. Admin dashboard access.
5. Theme preview for every installed theme.
6. Apply an allowed theme from the Admin dashboard.
7. Upload profile, project, certificate, and badge images.
8. Confirm uploaded images appear in Admin uploads navigation.
9. Confirm Superadmin media/uploads can see expected platform media.
10. Submit a tenant contact form.
11. Submit the landing page contact form.
12. Verify billing plan gating for locked themes.

---

## Production Deployment Notes

Recommended production flow:

1. Provision PostgreSQL.
2. Configure production environment variables.
3. Configure Redis if available.
4. Configure email provider DNS and credentials.
5. Configure PayMongo only when billing is ready.
6. Run migrations.
7. Create or verify the Superadmin account.
8. Run startup diagnostics.
9. Test theme previews and contact forms.
10. Disable temporary bootstrap flags after first deployment.

Production server command:

```bash
gunicorn wsgi:app
```

Docker and Render support files are included:

```text
Dockerfile
docker-compose.yml
docker-compose.prod.yml
render.yaml
```

Review and adjust these files before deployment because environment names, database URLs, domain names, and startup flags depend on the target hosting setup.

---

## Maintenance Checklist

Use this checklist after major changes:

- [ ] Run migrations successfully.
- [ ] Run `python -m compileall app`.
- [ ] Run `pytest` where dependencies are available.
- [ ] Preview all installed themes.
- [ ] Verify Admin uploads shows profile, project, testimonial, certificate, and badge media.
- [ ] Verify Superadmin media/uploads visibility.
- [ ] Test tenant contact form delivery.
- [ ] Test landing page contact form delivery.
- [ ] Test signup OTP and password reset OTP.
- [ ] Confirm locked themes can be previewed but not applied by restricted plans.
- [ ] Confirm production secrets are not committed.
- [ ] Confirm email DNS records are valid for the selected provider.

---

## License

This project is prepared as a portfolio CMS/SaaS application. Update this section with the final license and ownership details before public release.
