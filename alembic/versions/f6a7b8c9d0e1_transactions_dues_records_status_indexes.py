"""transactions/dues_records status indexes

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-17 00:00:00.000000

Adds missing indexes on Transaction.status and DuesRecord.status. Both
columns are filtered by value repeatedly -- the admin dashboard's count
queries, finance totals/summary, dues generation, and SMS/email audience
resolution (app.services.audience) all filter on one or the other -- but
unlike their sibling user_id/project_id FKs on the same models (indexed in
phase 1's a1b2c3d4e5f6), neither status column was indexed, forcing a
sequential scan on every dashboard load. Pure additive DDL -- no
column/type changes, safe at the current row counts without CONCURRENTLY.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(op.f('ix_transactions_status'), 'transactions', ['status'], unique=False)
    op.create_index(op.f('ix_dues_records_status'), 'dues_records', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_dues_records_status'), table_name='dues_records')
    op.drop_index(op.f('ix_transactions_status'), table_name='transactions')
