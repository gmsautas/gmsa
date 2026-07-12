"""add email_send_failures table

Revision ID: 9a1c2b4e7f3d
Revises: 41076fd6fadf
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a1c2b4e7f3d'
down_revision: Union[str, None] = '41076fd6fadf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'email_send_failures',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('recipient', sa.String(length=255), nullable=False),
        sa.Column('purpose', sa.String(length=120), nullable=False),
        sa.Column('error', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('email_send_failures')
