"""Enforce balance equals ledger sum at transaction commit.

Revision ID: 03e639a6b8e4
Revises: 7adf841e0321
"""

from alembic import op

revision = "03e639a6b8e4"
down_revision = "7adf841e0321"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM users u
                LEFT JOIN ledger l ON l.user_id = u.id
                GROUP BY u.id
                HAVING u.balance::numeric <> coalesce(sum(l.delta), 0)
            ) THEN
                RAISE EXCEPTION 'existing user balances differ from ledger sums';
            END IF;
        END;
        $$
    """)
    op.execute("""
        CREATE FUNCTION check_user_ledger_balance() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            checked_user_id bigint;
            actual_balance bigint;
            ledger_balance numeric;
        BEGIN
            IF TG_TABLE_NAME = 'users' THEN
                checked_user_id := NEW.id;
            ELSE
                checked_user_id := NEW.user_id;
            END IF;
            SELECT balance INTO actual_balance FROM users WHERE id = checked_user_id;
            SELECT coalesce(sum(delta), 0) INTO ledger_balance
              FROM ledger WHERE user_id = checked_user_id;
            IF actual_balance::numeric <> ledger_balance THEN
                RAISE EXCEPTION
                    'user balance differs from ledger sum for user %', checked_user_id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER user_balance_matches_ledger
        AFTER INSERT OR UPDATE OF balance ON users
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION check_user_ledger_balance()
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER ledger_matches_user_balance
        AFTER INSERT ON ledger
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION check_user_ledger_balance()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER ledger_matches_user_balance ON ledger")
    op.execute("DROP TRIGGER user_balance_matches_ledger ON users")
    op.execute("DROP FUNCTION check_user_ledger_balance()")
