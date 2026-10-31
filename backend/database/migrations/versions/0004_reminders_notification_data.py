"""Reminders (tickets.reminded_at) and translated staff notifications (staff_notifications.data)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-25 18:10:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import backend.models.common


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reminded_at', backend.models.common.UTC(timezone=True), nullable=True))
    with op.batch_alter_table('staff_notifications', schema=None) as batch_op:
        # older notifications keep their English text: an empty object makes the dashboard show it as it is
        batch_op.add_column(sa.Column('data', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False,
                                      server_default='{}'))


def downgrade():
    with op.batch_alter_table('staff_notifications', schema=None) as batch_op:
        batch_op.drop_column('data')
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.drop_column('reminded_at')
