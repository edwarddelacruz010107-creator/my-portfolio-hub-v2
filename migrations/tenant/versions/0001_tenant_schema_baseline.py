"""Establish and reconcile the tenant-data schema.

Revision ID: 0001_tenant_schema_baseline
Revises: None
Create Date: 2026-07-14

This baseline is deliberately additive.  On an empty database it creates the
current tenant tables.  On an existing database it adds missing columns and
reconciles indexes without dropping tenant rows.  It therefore replaces both
the historical ``ensure-tenant-schema`` create_all workaround and the startup
ALTER TABLE repair path.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_tenant_schema_baseline"
down_revision = None
branch_labels = None
depends_on = None


def _tables() -> sa.MetaData:
    metadata = sa.MetaData()

    sa.Table(
        "profile",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tenant_slug", sa.String(120), nullable=False, server_default="default"),
        sa.Column("name", sa.String(100), nullable=False, server_default=""),
        sa.Column("title", sa.String(150), server_default="Full Stack Developer"),
        sa.Column("subtitle", sa.String(200), server_default="Building beautiful digital experiences"),
        sa.Column("bio", sa.Text(), server_default=""),
        sa.Column("bio_short", sa.String(300), server_default=""),
        sa.Column("location", sa.String(100), server_default=""),
        sa.Column("email", sa.String(120), server_default=""),
        sa.Column("phone", sa.String(30), server_default=""),
        sa.Column("profile_image", sa.String(255), server_default=""),
        sa.Column("resume_url", sa.String(255), server_default=""),
        sa.Column("years_experience", sa.Integer(), server_default="0"),
        sa.Column("clients_count", sa.Integer(), server_default="0"),
        sa.Column("experience_start_year", sa.Integer()),
        sa.Column("hero_tagline", sa.String(200), server_default=""),
        sa.Column("availability_status", sa.String(100), server_default="Available for freelance"),
        sa.Column("is_available", sa.Boolean(), server_default=sa.true()),
        sa.Column("social_links", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("plan", sa.String(50), server_default="Basic"),
        sa.Column("monthly_rate", sa.Float(), server_default="0"),
        sa.Column("free_trial_days", sa.Integer(), server_default="0"),
        sa.Column("free_trial_ends", sa.DateTime(timezone=True)),
        sa.Column("internal_notes", sa.Text(), server_default=""),
        sa.Column("meta_title", sa.String(200), server_default=""),
        sa.Column("meta_description", sa.String(300), server_default=""),
        sa.Column("og_image", sa.String(500), server_default=""),
        sa.Column("profile_image_alt", sa.String(200), server_default=""),
        sa.Column("seo_keywords", sa.String(300), server_default=""),
        sa.Column("seo_indexable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("selected_theme", sa.String(64), nullable=False, server_default="default"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    sa.Table(
        "skills",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tenant_slug", sa.String(120), nullable=False, server_default="default"),
        sa.Column("name", sa.String(100), nullable=False, server_default=""),
        sa.Column("proficiency", sa.Integer(), server_default="80"),
        sa.Column("category", sa.String(50), server_default="Frontend"),
        sa.Column("icon", sa.String(100), server_default=""),
        sa.Column("color", sa.String(20), server_default=""),
        sa.Column("order", sa.Integer(), server_default="0"),
        sa.Column("is_visible", sa.Boolean(), server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    sa.Table(
        "projects",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tenant_slug", sa.String(120), nullable=False, server_default="default"),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("slug", sa.String(200)),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("description_short", sa.String(300), server_default=""),
        sa.Column("image", sa.String(500), server_default=""),
        sa.Column("image_alt", sa.String(200), server_default=""),
        sa.Column("before_image", sa.String(500), server_default=""),
        sa.Column("before_image_alt", sa.String(200), server_default=""),
        sa.Column("after_image", sa.String(500), server_default=""),
        sa.Column("after_image_alt", sa.String(200), server_default=""),
        sa.Column("live_url", sa.String(500), server_default=""),
        sa.Column("github_url", sa.String(500), server_default=""),
        sa.Column("prototype_url", sa.String(500), server_default=""),
        sa.Column("framework", sa.String(120), server_default=""),
        sa.Column("problem_statement", sa.Text(), server_default=""),
        sa.Column("solution_overview", sa.Text(), server_default=""),
        sa.Column("outcome_summary", sa.Text(), server_default=""),
        sa.Column("client_quote", sa.Text(), server_default=""),
        sa.Column("client_name", sa.String(120), server_default=""),
        sa.Column("client_role", sa.String(160), server_default=""),
        sa.Column("meta_title", sa.String(200), server_default=""),
        sa.Column("meta_description", sa.String(300), server_default=""),
        sa.Column("case_study_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("language", sa.String(120), server_default=""),
        sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("category", sa.String(100), server_default="Web App"),
        sa.Column("status", sa.String(50), server_default="published"),
        sa.Column("is_featured", sa.Boolean(), server_default=sa.false()),
        sa.Column("order", sa.Integer(), server_default="0"),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("date_completed", sa.Date()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    sa.Table(
        "project_reactions",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("reaction_type", sa.String(50), nullable=False, server_default="like"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    sa.Table(
        "testimonials",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tenant_slug", sa.String(120), nullable=False, server_default="default"),
        sa.Column("author_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("author_title", sa.String(150), server_default=""),
        sa.Column("author_company", sa.String(100), server_default=""),
        sa.Column("author_avatar", sa.String(255), server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("rating", sa.Integer(), server_default="5"),
        sa.Column("is_featured", sa.Boolean(), server_default=sa.false()),
        sa.Column("is_visible", sa.Boolean(), server_default=sa.true()),
        sa.Column("order", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    sa.Table(
        "services",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tenant_slug", sa.String(120), nullable=False, server_default="default"),
        sa.Column("title", sa.String(100), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("icon", sa.String(100), server_default="lucide:briefcase"),
        sa.Column("features", sa.Text(), server_default=""),
        sa.Column("display_order", sa.Integer(), server_default="0"),
        sa.Column("is_visible", sa.Boolean(), server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    sa.Table(
        "certificates",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tenant_slug", sa.String(120), nullable=False, server_default="default"),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("issuer", sa.String(255), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("credential_id", sa.String(255), server_default=""),
        sa.Column("verification_url", sa.String(500), server_default=""),
        sa.Column("image_path", sa.String(255), server_default=""),
        sa.Column("badge_path", sa.String(255), server_default=""),
        sa.Column("issue_date", sa.Date()),
        sa.Column("expiration_date", sa.Date()),
        sa.Column("skills", sa.Text(), server_default=""),
        sa.Column("is_featured", sa.Boolean(), server_default=sa.false()),
        sa.Column("is_visible", sa.Boolean(), server_default=sa.true()),
        sa.Column("display_order", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    sa.Table(
        "work_experiences",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tenant_slug", sa.String(120), nullable=False, server_default="default"),
        sa.Column("role", sa.String(160), nullable=False, server_default=""),
        sa.Column("company", sa.String(160), nullable=False, server_default=""),
        sa.Column("employment_type", sa.String(80), server_default="Full-time"),
        sa.Column("location", sa.String(160), server_default=""),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("is_current", sa.Boolean(), server_default=sa.false()),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("achievements", sa.Text(), server_default=""),
        sa.Column("technologies", sa.Text(), server_default=""),
        sa.Column("icon", sa.String(100), server_default="lucide:briefcase-business"),
        sa.Column("display_order", sa.Integer(), server_default="0"),
        sa.Column("is_visible", sa.Boolean(), server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    return metadata


INDEXES = (
    ("ix_profile_tenant_id", "profile", ("tenant_id",), False),
    ("ix_profile_tenant_slug", "profile", ("tenant_slug",), False),
    ("ix_profile_updated_at", "profile", ("updated_at",), False),
    ("ix_profile_is_available", "profile", ("is_available",), False),
    ("ix_skills_tenant_id", "skills", ("tenant_id",), False),
    ("ix_skills_tenant_slug", "skills", ("tenant_slug",), False),
    ("ix_skills_tenant_visible", "skills", ("tenant_id", "is_visible"), False),
    ("ix_projects_tenant_id", "projects", ("tenant_id",), False),
    ("ix_projects_tenant_slug", "projects", ("tenant_slug",), False),
    ("ix_projects_slug", "projects", ("slug",), False),
    ("ix_projects_status_featured", "projects", ("status", "is_featured"), False),
    ("ix_projects_tenant_status", "projects", ("tenant_id", "status"), False),
    ("ix_projects_tenant_category_status", "projects", ("tenant_id", "category", "status"), False),
    ("ix_project_reactions_project_id", "project_reactions", ("project_id",), False),
    ("ix_project_reactions_user_id", "project_reactions", ("user_id",), False),
    ("ix_project_reactions_tenant_id", "project_reactions", ("tenant_id",), False),
    ("ix_project_reactions_project_user", "project_reactions", ("project_id", "user_id"), True),
    ("ix_testimonials_tenant_id", "testimonials", ("tenant_id",), False),
    ("ix_testimonials_tenant_slug", "testimonials", ("tenant_slug",), False),
    ("ix_testimonials_tenant_visible", "testimonials", ("tenant_id", "is_visible"), False),
    ("ix_services_tenant_id", "services", ("tenant_id",), False),
    ("ix_services_tenant_slug", "services", ("tenant_slug",), False),
    ("ix_services_tenant_order", "services", ("tenant_id", "display_order"), False),
    ("ix_services_tenant_visible", "services", ("tenant_id", "is_visible"), False),
    ("ix_certificates_tenant_id", "certificates", ("tenant_id",), False),
    ("ix_certificates_tenant_slug", "certificates", ("tenant_slug",), False),
    ("ix_certificates_tenant_visible", "certificates", ("tenant_id", "is_visible"), False),
    ("ix_certificates_tenant_order", "certificates", ("tenant_id", "display_order"), False),
    ("ix_certificates_tenant_featured", "certificates", ("tenant_id", "is_featured"), False),
    ("ix_work_experiences_tenant_id", "work_experiences", ("tenant_id",), False),
    ("ix_work_experiences_tenant_visible", "work_experiences", ("tenant_id", "is_visible"), False),
    ("ix_work_experiences_tenant_order", "work_experiences", ("tenant_id", "display_order"), False),
    ("ix_work_experiences_tenant_current", "work_experiences", ("tenant_id", "is_current"), False),
)


def _clone_column(column: sa.Column) -> sa.Column:
    args: list[object] = [column.name, column.type.copy()]
    for foreign_key in column.foreign_keys:
        args.append(
            sa.ForeignKey(
                foreign_key.target_fullname,
                ondelete=foreign_key.ondelete,
                onupdate=foreign_key.onupdate,
            )
        )
    kwargs: dict[str, object] = {
        "nullable": column.nullable,
        "primary_key": column.primary_key,
    }
    if column.server_default is not None:
        kwargs["server_default"] = column.server_default.arg
    return sa.Column(*args, **kwargs)


def _reconcile_table(table: sa.Table) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table.name):
        table.create(bind=bind)
        return

    existing = {column["name"] for column in inspector.get_columns(table.name)}
    missing = [column for column in table.columns if column.name not in existing]
    if any(column.primary_key for column in missing):
        raise RuntimeError(
            f"Existing tenant table {table.name!r} has no primary key column; "
            "restore the database or write a reviewed repair migration."
        )
    if missing:
        with op.batch_alter_table(table.name) as batch:
            for column in missing:
                batch.add_column(_clone_column(column))


def _reconcile_indexes() -> None:
    bind = op.get_bind()
    for name, table_name, columns, unique in INDEXES:
        inspector = sa.inspect(bind)
        existing = {
            index.get("name"): (
                tuple(index.get("column_names") or ()),
                bool(index.get("unique")),
            )
            for index in inspector.get_indexes(table_name)
            if index.get("name")
        }
        expected = (columns, unique)
        actual = existing.get(name)
        if actual == expected:
            continue
        if actual is not None:
            op.drop_index(name, table_name=table_name)
        op.create_index(name, table_name, list(columns), unique=unique)


def _verify(metadata: sa.MetaData) -> None:
    inspector = sa.inspect(op.get_bind())
    failures: list[str] = []
    for table in metadata.sorted_tables:
        if not inspector.has_table(table.name):
            failures.append(f"missing table {table.name}")
            continue
        actual = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in actual:
                failures.append(f"missing column {table.name}.{column.name}")
    if failures:
        raise RuntimeError("Tenant baseline verification failed: " + "; ".join(failures))


def upgrade() -> None:
    metadata = _tables()
    for table in metadata.sorted_tables:
        _reconcile_table(table)
    _reconcile_indexes()
    _verify(metadata)


def downgrade() -> None:
    # This revision may adopt tables that predate the tenant Alembic history.
    # Dropping them on downgrade would destroy tenant content.  Roll back the
    # application release and forward-fix the schema instead.
    pass
