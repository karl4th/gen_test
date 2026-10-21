"""Enforce ledger owner references

Revision ID: 9dbaf315c674
Revises: 633749887d51
Create Date: 2026-10-21 20:59:56.234494
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '9dbaf315c674'
down_revision: str | None = '633749887d51'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f('fk_ledger_task_id_tasks'), 'ledger', type_='foreignkey')
    op.drop_constraint(op.f('fk_ledger_payment_id_payments'), 'ledger', type_='foreignkey')
    op.create_unique_constraint('uq_payments_user_id_payment_id', 'payments', ['user_id', 'payment_id'])
    op.create_foreign_key('fk_ledger_user_payment', 'ledger', 'payments', ['user_id', 'payment_id'], ['user_id', 'payment_id'], ondelete='RESTRICT')
    op.create_foreign_key('fk_ledger_user_task', 'ledger', 'tasks', ['user_id', 'task_id'], ['user_id', 'id'], ondelete='RESTRICT')


def downgrade() -> None:
    op.drop_constraint('fk_ledger_user_task', 'ledger', type_='foreignkey')
    op.drop_constraint('fk_ledger_user_payment', 'ledger', type_='foreignkey')
    op.drop_constraint('uq_payments_user_id_payment_id', 'payments', type_='unique')
    op.create_foreign_key(op.f('fk_ledger_payment_id_payments'), 'ledger', 'payments', ['payment_id'], ['payment_id'], ondelete='RESTRICT')
    op.create_foreign_key(op.f('fk_ledger_task_id_tasks'), 'ledger', 'tasks', ['task_id'], ['id'], ondelete='RESTRICT')
