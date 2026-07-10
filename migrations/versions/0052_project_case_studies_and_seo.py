"""Add professional project case-study and tenant SEO fields.

Revision ID: 0052_project_case_studies_and_seo
Revises: 0051_add_work_experience_timeline
Create Date: 2026-07-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = '0052_project_case_studies_and_seo'
down_revision = '0051_add_work_experience_timeline'
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    try:
        return {column['name'] for column in inspector.get_columns(table)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('profile'):
        existing = _columns(inspector, 'profile')
        additions = [
            sa.Column('profile_image_alt', sa.String(length=200), nullable=True, server_default=''),
            sa.Column('seo_keywords', sa.String(length=300), nullable=True, server_default=''),
            sa.Column('seo_indexable', sa.Boolean(), nullable=False, server_default=sa.true()),
        ]
        with op.batch_alter_table('profile') as batch:
            for column in additions:
                if column.name not in existing:
                    batch.add_column(column)

    if inspector.has_table('projects'):
        existing = _columns(inspector, 'projects')
        additions = [
            sa.Column('image_alt', sa.String(length=200), nullable=True, server_default=''),
            sa.Column('before_image', sa.String(length=500), nullable=True, server_default=''),
            sa.Column('before_image_alt', sa.String(length=200), nullable=True, server_default=''),
            sa.Column('after_image', sa.String(length=500), nullable=True, server_default=''),
            sa.Column('after_image_alt', sa.String(length=200), nullable=True, server_default=''),
            sa.Column('prototype_url', sa.String(length=500), nullable=True, server_default=''),
            sa.Column('problem_statement', sa.Text(), nullable=True, server_default=''),
            sa.Column('solution_overview', sa.Text(), nullable=True, server_default=''),
            sa.Column('outcome_summary', sa.Text(), nullable=True, server_default=''),
            sa.Column('client_quote', sa.Text(), nullable=True, server_default=''),
            sa.Column('client_name', sa.String(length=120), nullable=True, server_default=''),
            sa.Column('client_role', sa.String(length=160), nullable=True, server_default=''),
            sa.Column('meta_title', sa.String(length=200), nullable=True, server_default=''),
            sa.Column('meta_description', sa.String(length=300), nullable=True, server_default=''),
            sa.Column('case_study_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        ]
        with op.batch_alter_table('projects') as batch:
            for column in additions:
                if column.name not in existing:
                    batch.add_column(column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('projects'):
        existing = _columns(inspector, 'projects')
        names = [
            'case_study_enabled', 'meta_description', 'meta_title', 'client_role',
            'client_name', 'client_quote', 'outcome_summary', 'solution_overview',
            'problem_statement', 'prototype_url', 'after_image_alt', 'after_image',
            'before_image_alt', 'before_image', 'image_alt',
        ]
        with op.batch_alter_table('projects') as batch:
            for name in names:
                if name in existing:
                    batch.drop_column(name)
    if inspector.has_table('profile'):
        existing = _columns(inspector, 'profile')
        with op.batch_alter_table('profile') as batch:
            for name in ('seo_indexable', 'seo_keywords', 'profile_image_alt'):
                if name in existing:
                    batch.drop_column(name)
