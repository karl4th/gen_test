"""Constrain status values

Revision ID: 1c9b2e72141a
Revises: 9dbaf315c674
Create Date: 2026-10-21 21:04:49.738887
"""
from collections.abc import Sequence

from alembic import op


revision: str = '1c9b2e72141a'
down_revision: str | None = '9dbaf315c674'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        op.f("ck_payments_status_valid"),
        "payments",
        "status IN ('received', 'credited', 'rejected')",
    )
    op.create_check_constraint(
        op.f("ck_tasks_status_valid"),
        "tasks",
        "status IN ('pending', 'running', 'succeeded', 'failed')",
    )
    op.create_check_constraint(
        op.f("ck_attempts_status_valid"),
        "attempts",
        "status IN ('pending', 'sent', 'succeeded', 'failed', 'timed_out')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_attempts_status_valid"), "attempts", type_="check")
    op.drop_constraint(op.f("ck_tasks_status_valid"), "tasks", type_="check")
    op.drop_constraint(op.f("ck_payments_status_valid"), "payments", type_="check")
