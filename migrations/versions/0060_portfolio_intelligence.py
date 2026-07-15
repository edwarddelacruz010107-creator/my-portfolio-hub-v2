"""add append-only portfolio intelligence snapshots

Revision ID: 0060
Revises: 0059

The migration deliberately creates no rows. Score history begins only when
the deployed Phase 6 service evaluates real tenant state.
"""
from alembic import op
import sqlalchemy as sa


revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "portfolio_intelligence_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("portfolio_hash", sa.String(length=64), nullable=False),
        sa.Column("rubric_version", sa.String(length=80), nullable=False),
        sa.Column("total_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("evaluated_weight", sa.SmallInteger(), nullable=False),
        sa.Column("dimension_scores", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("recommendations", sa.JSON(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "portfolio_hash", "rubric_version",
            name="uq_portfolio_intelligence_tenant_hash_version",
        ),
    )
    op.create_index(
        "ix_portfolio_intelligence_tenant_calculated",
        "portfolio_intelligence_snapshots",
        ["tenant_id", "calculated_at"],
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION reject_portfolio_intelligence_mutation() RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'portfolio intelligence snapshots are append-only';
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute(
            "CREATE TRIGGER trg_portfolio_intelligence_append_only "
            "BEFORE UPDATE OR DELETE ON portfolio_intelligence_snapshots "
            "FOR EACH ROW EXECUTE FUNCTION reject_portfolio_intelligence_mutation()"
        )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_portfolio_intelligence_append_only ON portfolio_intelligence_snapshots")
        op.execute("DROP FUNCTION IF EXISTS reject_portfolio_intelligence_mutation()")
    op.drop_index(
        "ix_portfolio_intelligence_tenant_calculated",
        table_name="portfolio_intelligence_snapshots",
    )
    op.drop_table("portfolio_intelligence_snapshots")
