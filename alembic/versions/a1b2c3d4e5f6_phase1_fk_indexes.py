"""phase 1 - FK indexes

Revision ID: a1b2c3d4e5f6
Revises: 41076fd6fadf
Create Date: 2026-07-11 00:00:00.000000

Adds missing indexes on foreign-key columns that were queried/joined on
regularly but never indexed (admin voter lists, transaction lookups by
member, vote nullification/audit lookups, etc.). Pure additive DDL -- no
column/type changes, safe at the current row counts (~1500) without
CONCURRENTLY. `User.transactions`' cascade="all, delete-orphan" fix (also
part of this phase) is an ORM-relationship-only change and needs no DDL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '41076fd6fadf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(op.f('ix_transactions_user_id'), 'transactions', ['user_id'], unique=False)
    op.create_index(op.f('ix_transactions_project_id'), 'transactions', ['project_id'], unique=False)
    op.create_index(op.f('ix_dues_records_user_id'), 'dues_records', ['user_id'], unique=False)
    op.create_index(op.f('ix_rsvps_user_id'), 'rsvps', ['user_id'], unique=False)
    op.create_index(op.f('ix_rsvps_event_id'), 'rsvps', ['event_id'], unique=False)
    op.create_index(op.f('ix_candidates_position_id'), 'candidates', ['position_id'], unique=False)
    op.create_index(op.f('ix_candidates_user_id'), 'candidates', ['user_id'], unique=False)
    op.create_index(op.f('ix_voters_user_id'), 'voters', ['user_id'], unique=False)
    op.create_index(op.f('ix_votes_election_id'), 'votes', ['election_id'], unique=False)
    op.create_index(op.f('ix_votes_position_id'), 'votes', ['position_id'], unique=False)
    op.create_index(op.f('ix_votes_candidate_id'), 'votes', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_votes_voter_token_id'), 'votes', ['voter_token_id'], unique=False)
    op.create_index(op.f('ix_committee_members_committee_id'), 'committee_members', ['committee_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_committee_members_committee_id'), table_name='committee_members')
    op.drop_index(op.f('ix_votes_voter_token_id'), table_name='votes')
    op.drop_index(op.f('ix_votes_candidate_id'), table_name='votes')
    op.drop_index(op.f('ix_votes_position_id'), table_name='votes')
    op.drop_index(op.f('ix_votes_election_id'), table_name='votes')
    op.drop_index(op.f('ix_voters_user_id'), table_name='voters')
    op.drop_index(op.f('ix_candidates_user_id'), table_name='candidates')
    op.drop_index(op.f('ix_candidates_position_id'), table_name='candidates')
    op.drop_index(op.f('ix_rsvps_event_id'), table_name='rsvps')
    op.drop_index(op.f('ix_rsvps_user_id'), table_name='rsvps')
    op.drop_index(op.f('ix_dues_records_user_id'), table_name='dues_records')
    op.drop_index(op.f('ix_transactions_project_id'), table_name='transactions')
    op.drop_index(op.f('ix_transactions_user_id'), table_name='transactions')
