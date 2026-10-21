"""Make ledger append only

Revision ID: 482bd3997556
Revises: 1e0544fcc874
Create Date: 2026-10-21 21:09:56.889697
"""
from collections.abc import Sequence

from alembic import op


revision: str = '482bd3997556'
down_revision: str | None = '1e0544fcc874'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_ledger_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'ledger is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER ledger_no_mutation
        BEFORE UPDATE OR DELETE OR TRUNCATE ON ledger
        FOR EACH STATEMENT
        EXECUTE FUNCTION prevent_ledger_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER ledger_no_mutation ON ledger")
    op.execute("DROP FUNCTION prevent_ledger_mutation()")
