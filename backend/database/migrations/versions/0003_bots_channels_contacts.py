"""Bot_id and channel (several bots, other channels); «My contacts» menu item

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25 16:40:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

BOT_TABLES = ('users', 'tickets', 'campaigns')
CHANNEL_TABLES = ('tickets', 'messages')


def upgrade():
    for table in BOT_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('bot_id', sa.String(length=32), nullable=False, server_default='main'))
    for table in CHANNEL_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('channel', sa.String(length=16), nullable=False, server_default='telegram'))
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.create_index('ix_tickets_bot', ['bot_id', 'status'], unique=False)

    # Installations made before this version get «My contacts» in the menu list too — switched off, as on new ones.
    json = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
    menu = sa.table('menu_items', sa.column('action', sa.String), sa.column('label', json), sa.column('value', sa.String),
                    sa.column('content', json), sa.column('enabled', sa.Boolean), sa.column('position', sa.Integer))
    bind = op.get_bind()
    has_items = bind.execute(sa.text("SELECT COUNT(*) FROM menu_items")).scalar()
    has_contacts = bind.execute(sa.text("SELECT COUNT(*) FROM menu_items WHERE action = 'contacts'")).scalar()
    if has_items and not has_contacts:
        last = bind.execute(sa.text("SELECT COALESCE(MAX(position), 0) FROM menu_items")).scalar()
        op.bulk_insert(menu, [{'action': 'contacts', 'label': {}, 'value': '', 'content': {}, 'enabled': False, 'position': last + 1}])


def downgrade():
    op.execute("DELETE FROM menu_items WHERE action = 'contacts'")
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.drop_index('ix_tickets_bot')
    for table in CHANNEL_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('channel')
    for table in BOT_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('bot_id')
