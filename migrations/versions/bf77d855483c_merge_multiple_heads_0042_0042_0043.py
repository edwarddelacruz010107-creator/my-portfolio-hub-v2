"""merge multiple heads: 0042/0042/0043

Revision ID: bf77d855483c
Revises: 0042_add_inquiry_contact_metadata, 0042_payment_submission_expected_amount, 0043_add_user_email_verification_fields
Create Date: 2026-07-03 15:37:30.492130

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'bf77d855483c'
down_revision = ('0042_add_inquiry_contact_metadata', '0042_payment_submission_expected_amount', '0043_add_user_email_verification_fields')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
