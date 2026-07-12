"""merge phase 0/1-3/6/7 migration heads

Revision ID: ffc388e346a5
Revises: 77915d6e275a, 895f6c9924c8, 9a1c2b4e7f3d, b2c3d4e5f6a7
Create Date: 2026-07-12 14:13:47.878186

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ffc388e346a5'
down_revision: Union[str, None] = ('77915d6e275a', '895f6c9924c8', '9a1c2b4e7f3d', 'b2c3d4e5f6a7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
