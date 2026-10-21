"""Index ledger rows by user and attempts by status and deadline.

Revision ID: 5f2a8c1d9e47
Revises: 03e639a6b8e4

The balance trigger sums a user's ledger on every money transaction, and the
watchdog polls attempts by status and deadline every interval. Without these
indexes both scan their whole table.
"""

from alembic import op

revision = "5f2a8c1d9e47"
down_revision = "03e639a6b8e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_ledger_user_id_id", "ledger", ["user_id", "id"])
    op.create_index(
        "ix_attempts_status_deadline_at", "attempts", ["status", "deadline_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_attempts_status_deadline_at", table_name="attempts")
    op.drop_index("ix_ledger_user_id_id", table_name="ledger")
