"""org_settings: drop MailerSend + Mailtrap from-email columns

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-15 02:00:00.000000

Reverses c3d4e5f6a7b8: the app is being consolidated onto Brevo (primary,
via app.services.resend_client) and Gmail SMTP (local bulk-import path only)
-- MailerSend and Mailtrap support is being removed from the codebase, so
these two now-unused columns are dropped too rather than left as dead
columns nothing reads. This migration is written as a proper forward step
(not a file deletion) because c3d4e5f6a7b8 has already shipped to
production -- `start.sh` runs `alembic upgrade head` on every deploy, so the
live DB's alembic_version is very likely already stamped past it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('org_settings', 'mailtrap_from_email')
    op.drop_column('org_settings', 'mailersend_from_email')


def downgrade() -> None:
    op.add_column('org_settings', sa.Column('mailersend_from_email', sa.String(length=255), nullable=True))
    op.add_column('org_settings', sa.Column('mailtrap_from_email', sa.String(length=255), nullable=True))
