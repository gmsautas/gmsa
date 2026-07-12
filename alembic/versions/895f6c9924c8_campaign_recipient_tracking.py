"""campaign recipient tracking

Revision ID: 895f6c9924c8
Revises: 41076fd6fadf
Create Date: 2026-07-11 00:00:00.000000

Adds per-recipient delivery tracking for SmsCampaign/EmailCampaign (Phase 7
of the remediation plan) -- previously each campaign had one aggregate
`status` column for the whole blast, so a partial failure (e.g. 40 of 100
recipients bouncing) was invisible. app.services.campaign_sender writes one
row per recipient (email) / per recipient per batch (SMS) into these new
tables as the paced/chunked send loop runs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '895f6c9924c8'
down_revision: Union[str, None] = '41076fd6fadf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'email_campaign_recipients',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('campaign_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['campaign_id'], ['email_campaigns.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_email_campaign_recipients_campaign_id'),
        'email_campaign_recipients',
        ['campaign_id'],
        unique=False,
    )

    op.create_table(
        'sms_campaign_recipients',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('campaign_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('phone', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['campaign_id'], ['sms_campaigns.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_sms_campaign_recipients_campaign_id'),
        'sms_campaign_recipients',
        ['campaign_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_sms_campaign_recipients_campaign_id'), table_name='sms_campaign_recipients'
    )
    op.drop_table('sms_campaign_recipients')
    op.drop_index(
        op.f('ix_email_campaign_recipients_campaign_id'), table_name='email_campaign_recipients'
    )
    op.drop_table('email_campaign_recipients')
