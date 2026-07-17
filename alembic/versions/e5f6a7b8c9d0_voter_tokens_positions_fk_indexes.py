"""voter_tokens/positions FK indexes

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-16 00:00:00.000000

Adds two more missing FK indexes that sit directly on the vote-casting hot
path: VoterToken.voter_id (queried on every vote cast, nullify-and-reissue,
and token resend -- see app.services.elections._resolve_voter_and_token,
nullify_vote, resend_token) and Position.election_id (queried on every vote
cast and every results-page load -- see app.services.elections.cast_vote,
compute_results). Every sibling FK in app/models/models.py already carries
an index (see phase 1's a1b2c3d4e5f6); these two were missed. Pure additive
DDL -- no column/type changes, safe at the current row counts without
CONCURRENTLY.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(op.f('ix_voter_tokens_voter_id'), 'voter_tokens', ['voter_id'], unique=False)
    op.create_index(op.f('ix_positions_election_id'), 'positions', ['election_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_positions_election_id'), table_name='positions')
    op.drop_index(op.f('ix_voter_tokens_voter_id'), table_name='voter_tokens')
