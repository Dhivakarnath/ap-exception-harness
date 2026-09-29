"""add extraction and policy columns to run_evaluations

Revision ID: c4e8b2f1a903
Revises: 5f3d9a8c1e20
Create Date: 2026-09-15 21:45:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8b2f1a903"
down_revision: str | Sequence[str] | None = "5f3d9a8c1e20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_evaluations",
        sa.Column("extraction_accuracy", sa.Float(), nullable=True),
    )
    op.add_column(
        "run_evaluations",
        sa.Column("policy_adherence", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("run_evaluations", "policy_adherence")
    op.drop_column("run_evaluations", "extraction_accuracy")
