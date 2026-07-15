"""org_settings: MailerSend + Mailtrap from-email columns

Revision ID: c3d4e5f6a7b8
Revises: ffc388e346a5
Create Date: 2026-07-15 00:00:00.000000

Adds two more nullable DB-backed from-email overrides to the org_settings
singleton row, following the same pattern as resend_from_email/
brevo_from_email/ses_from_email (see b2c3d4e5f6a7): NULL means "fall back to
the app.core.config.Settings env var default" (see
app.services.org_settings_cache), so this is a pure additive,
zero-behavior-change schema change for any deployment that never touches the
new /admin/settings fields. The API keys themselves are managed separately
via app.services.secrets_store (see app.web.secrets_web.MANAGED_KEYS), not as
columns here -- same split as every other provider already has.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'ffc388e346a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('org_settings', sa.Column('mailersend_from_email', sa.String(length=255), nullable=True))
    op.add_column('org_settings', sa.Column('mailtrap_from_email', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('org_settings', 'mailtrap_from_email')
    op.drop_column('org_settings', 'mailersend_from_email')
