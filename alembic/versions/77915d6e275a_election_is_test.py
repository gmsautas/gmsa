"""election is_test flag

Revision ID: 77915d6e275a
Revises: 41076fd6fadf
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '77915d6e275a'
down_revision: Union[str, None] = '41076fd6fadf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Sandbox/test election flag -- see app.services.elections.assert_year_available
    # / assert_election_deletable. server_default keeps existing rows valid
    # (all treated as real, non-test elections) without a manual backfill.
    op.add_column(
        'elections',
        sa.Column('is_test', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('elections', 'is_test')
