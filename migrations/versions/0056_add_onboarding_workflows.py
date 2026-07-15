"""add durable tenant onboarding workflow state

Revision ID: 0056
Revises: 0055
"""
from alembic import op
import sqlalchemy as sa


revision = '0056'
down_revision = '0055'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('onboarding_workflows'):
        return

    op.create_table(
        'onboarding_workflows',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('tenant_slug', sa.String(length=120), nullable=False),
        sa.Column('state', sa.String(length=20), server_default='active', nullable=False),
        sa.Column('step_state', sa.JSON(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('dismissed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', name='uq_onboarding_workflows_tenant_id'),
    )
    op.create_index('ix_onboarding_workflows_tenant_id', 'onboarding_workflows', ['tenant_id'], unique=True)
    op.create_index('ix_onboarding_workflows_tenant_slug', 'onboarding_workflows', ['tenant_slug'], unique=False)
    op.create_index(
        'ix_onboarding_workflows_state_updated',
        'onboarding_workflows',
        ['state', 'updated_at'],
        unique=False,
    )


def downgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table('onboarding_workflows'):
        op.drop_table('onboarding_workflows')
