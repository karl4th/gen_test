"""Align statuses with task contract

Revision ID: 1e0544fcc874
Revises: 1c9b2e72141a
Create Date: 2026-10-21 21:08:58.810000
"""
from collections.abc import Sequence

from alembic import op


revision: str = '1e0544fcc874'
down_revision: str | None = '1c9b2e72141a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_payments_status_valid"), "payments", type_="check")
    op.create_check_constraint(
        op.f("ck_payments_status_valid"),
        "payments",
        "status IN ('succeeded', 'failed')",
    )
    op.drop_constraint(op.f("ck_tasks_status_valid"), "tasks", type_="check")
    op.create_check_constraint(
        op.f("ck_tasks_status_valid"),
        "tasks",
        "status IN ('pending', 'running', 'completed', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_tasks_status_valid"), "tasks", type_="check")
    op.create_check_constraint(
        op.f("ck_tasks_status_valid"),
        "tasks",
        "status IN ('pending', 'running', 'succeeded', 'failed')",
    )
    op.drop_constraint(op.f("ck_payments_status_valid"), "payments", type_="check")
    op.create_check_constraint(
        op.f("ck_payments_status_valid"),
        "payments",
        "status IN ('received', 'credited', 'rejected')",
    )
