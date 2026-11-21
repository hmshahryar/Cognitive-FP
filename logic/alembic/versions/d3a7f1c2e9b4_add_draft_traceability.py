"""add draft traceability fields

Revision ID: d3a7f1c2e9b4
Revises: b415bde10c94
Create Date: 2026-11-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3a7f1c2e9b4'
down_revision: Union[str, Sequence[str], None] = 'b415bde10c94'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('content_drafts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('provider', sa.String(length=64), server_default='google_genai', nullable=False))
        batch_op.add_column(sa.Column('prompt_version', sa.String(length=64), server_default='legacy', nullable=False))
        batch_op.add_column(sa.Column('claims', sa.JSON(), server_default='[]', nullable=False))
        batch_op.add_column(sa.Column('quality', sa.JSON(), server_default='{}', nullable=False))
        batch_op.add_column(sa.Column('research', sa.JSON(), server_default='{}', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('content_drafts', schema=None) as batch_op:
        batch_op.drop_column('research')
        batch_op.drop_column('quality')
        batch_op.drop_column('claims')
        batch_op.drop_column('prompt_version')
        batch_op.drop_column('provider')
