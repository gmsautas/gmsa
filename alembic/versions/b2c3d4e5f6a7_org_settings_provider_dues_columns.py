"""org_settings: email/SMS provider + dues amount columns

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-11 00:00:01.000000

Adds nullable DB-backed overrides to the org_settings singleton row for
operational config that previously required an env var + redeploy to change:
which email provider is active and its from-addresses/region, the Arkesel
SMS sender ID, and the four dues amounts. Every new column is nullable with
no server default -- NULL means "fall back to the app.core.config.Settings
env var default" (see app.services.org_settings_cache), so this migration is
a pure additive, zero-behavior-change schema change for any deployment that
never touches the new /admin/settings sections.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('org_settings', sa.Column('email_provider', sa.String(length=20), nullable=True))
    op.add_column('org_settings', sa.Column('resend_from_email', sa.String(length=255), nullable=True))
    op.add_column('org_settings', sa.Column('brevo_from_email', sa.String(length=255), nullable=True))
    op.add_column('org_settings', sa.Column('ses_from_email', sa.String(length=255), nullable=True))
    op.add_column('org_settings', sa.Column('ses_region', sa.String(length=40), nullable=True))
    op.add_column('org_settings', sa.Column('arkesel_sender_id', sa.String(length=20), nullable=True))
    op.add_column('org_settings', sa.Column('dues_amount_ghs', sa.Integer(), nullable=True))
    op.add_column('org_settings', sa.Column('dues_amount_level_100', sa.Integer(), nullable=True))
    op.add_column('org_settings', sa.Column('dues_amount_continuing', sa.Integer(), nullable=True))
    op.add_column('org_settings', sa.Column('dues_amount_final_year', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('org_settings', 'dues_amount_final_year')
    op.drop_column('org_settings', 'dues_amount_continuing')
    op.drop_column('org_settings', 'dues_amount_level_100')
    op.drop_column('org_settings', 'dues_amount_ghs')
    op.drop_column('org_settings', 'arkesel_sender_id')
    op.drop_column('org_settings', 'ses_region')
    op.drop_column('org_settings', 'ses_from_email')
    op.drop_column('org_settings', 'brevo_from_email')
    op.drop_column('org_settings', 'resend_from_email')
    op.drop_column('org_settings', 'email_provider')
