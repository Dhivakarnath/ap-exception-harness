"""add live run evaluations

Revision ID: 5f3d9a8c1e20
Revises: ab21f4687390
Create Date: 2026-09-15 13:50:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5f3d9a8c1e20"
down_revision: str | Sequence[str] | None = "ab21f4687390"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_evaluations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("task_completion", sa.Float(), nullable=True),
        sa.Column("tool_use", sa.Float(), nullable=True),
        sa.Column("rag_grounding", sa.Float(), nullable=True),
        sa.Column("judge_model", sa.String(length=128), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="ck_run_evaluations_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index(
        op.f("ix_run_evaluations_invoice_id"),
        "run_evaluations",
        ["invoice_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_run_evaluations_status"),
        "run_evaluations",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_run_evaluations_tenant_id"),
        "run_evaluations",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_run_evaluations_tenant_id"), table_name="run_evaluations"
    )
    op.drop_index(op.f("ix_run_evaluations_status"), table_name="run_evaluations")
    op.drop_index(
        op.f("ix_run_evaluations_invoice_id"), table_name="run_evaluations"
    )
    op.drop_table("run_evaluations")
