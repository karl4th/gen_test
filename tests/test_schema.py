import os
import unittest

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from mdl_ml.config import Settings
from mdl_ml.database import create_engine
from mdl_ml.domain.statuses import AttemptStatus, PaymentStatus, TaskStatus
from mdl_ml.models import Attempt, LedgerEntry, Leg, Model, Payment, Task, User


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "Set RUN_DB_TESTS=1 to run database tests")
class SchemaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_engine(Settings().database_url)
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()

    async def asyncTearDown(self) -> None:
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    async def assert_integrity_error(self, statement) -> None:
        savepoint = await self.connection.begin_nested()
        try:
            with self.assertRaises(IntegrityError):
                await self.connection.execute(statement)
        finally:
            await savepoint.rollback()

    async def assert_database_error(self, statement) -> None:
        savepoint = await self.connection.begin_nested()
        try:
            with self.assertRaises(DBAPIError):
                await self.connection.execute(statement)
        finally:
            await savepoint.rollback()

    async def test_seven_tables_and_database_invariants(self) -> None:
        table_names = await self.connection.run_sync(
            lambda connection: set(inspect(connection).get_table_names())
        )
        self.assertEqual(
            table_names - {"alembic_version"},
            {"users", "ledger", "payments", "models", "legs", "tasks", "attempts"},
        )

        user_id = (
            await self.connection.execute(User.__table__.insert().values(balance=0).returning(User.id))
        ).scalar_one()
        other_user_id = (
            await self.connection.execute(User.__table__.insert().values(balance=0).returning(User.id))
        ).scalar_one()
        await self.assert_integrity_error(User.__table__.insert().values(balance=-1))

        await self.connection.execute(Model.__table__.insert().values(slug="sample", price=10))
        await self.assert_integrity_error(Model.__table__.insert().values(slug="free", price=0))

        leg_id = (
            await self.connection.execute(
                Leg.__table__.insert()
                .values(model_slug="sample", provider="provider-a", priority=0, max_ref_images=2)
                .returning(Leg.id)
            )
        ).scalar_one()
        second_leg_id = (
            await self.connection.execute(
                Leg.__table__.insert()
                .values(model_slug="sample", provider="provider-b", priority=1, max_ref_images=0)
                .returning(Leg.id)
            )
        ).scalar_one()
        await self.assert_integrity_error(
            Leg.__table__.insert().values(
                model_slug="sample", provider="provider-c", priority=0, max_ref_images=0
            )
        )

        payment = Payment.__table__.insert().values(
            payment_id="payment-1",
            user_id=user_id,
            tokens=100,
            status=PaymentStatus.SUCCEEDED,
            received_at=text("now()"),
        )
        await self.connection.execute(payment)
        payment_status = (
            await self.connection.execute(
                select(Payment.status).where(Payment.payment_id == "payment-1")
            )
        ).scalar_one()
        self.assertIs(payment_status, PaymentStatus.SUCCEEDED)
        await self.assert_integrity_error(payment)
        await self.assert_integrity_error(
            text(
                "INSERT INTO payments (payment_id, user_id, tokens, status, received_at) "
                "VALUES ('payment-invalid', :user_id, 1, 'received', now())"
            ).bindparams(user_id=user_id)
        )
        await self.connection.execute(
            Payment.__table__.insert().values(
                payment_id="payment-2",
                user_id=user_id,
                tokens=50,
                status=PaymentStatus.FAILED,
                received_at=text("now()"),
            )
        )

        task_values = dict(
            user_id=user_id,
            model_slug="sample",
            prompt="example",
            price=10,
            status=TaskStatus.PENDING,
        )
        task_id = (
            await self.connection.execute(Task.__table__.insert().values(**task_values).returning(Task.id))
        ).scalar_one()
        second_task_id = (
            await self.connection.execute(Task.__table__.insert().values(**task_values).returning(Task.id))
        ).scalar_one()
        task_status = (
            await self.connection.execute(select(Task.status).where(Task.id == task_id))
        ).scalar_one()
        self.assertIs(task_status, TaskStatus.PENDING)
        await self.connection.execute(
            Task.__table__.update()
            .where(Task.id == second_task_id, Task.status == TaskStatus.PENDING)
            .values(status=TaskStatus.COMPLETED, updated_at=text("now()"))
        )
        completed_status = (
            await self.connection.execute(select(Task.status).where(Task.id == second_task_id))
        ).scalar_one()
        self.assertIs(completed_status, TaskStatus.COMPLETED)
        await self.assert_integrity_error(
            text(
                "INSERT INTO tasks (user_id, model_slug, prompt, price, status) "
                "VALUES (:user_id, 'sample', 'invalid status', 10, 'succeeded')"
            ).bindparams(user_id=user_id)
        )
        await self.assert_integrity_error(
            Task.__table__.insert().values(**task_values, ref_images={"invalid": True})
        )

        attempt_id = (
            await self.connection.execute(
                Attempt.__table__.insert()
                .values(
                    task_id=task_id,
                    leg_id=leg_id,
                    provider="provider-a",
                    external_id="external-1",
                    status=AttemptStatus.SENT,
                )
                .returning(Attempt.id)
            )
        ).scalar_one()
        attempt_status = (
            await self.connection.execute(select(Attempt.status).where(Attempt.id == attempt_id))
        ).scalar_one()
        self.assertIs(attempt_status, AttemptStatus.SENT)
        await self.assert_integrity_error(
            text(
                "INSERT INTO attempts (task_id, leg_id, provider, status) "
                "VALUES (CAST(:task_id AS uuid), :leg_id, 'provider-c', 'unknown')"
            ).bindparams(task_id=str(second_task_id), leg_id=leg_id)
        )
        await self.assert_integrity_error(
            Attempt.__table__.insert().values(
                task_id=task_id,
                leg_id=leg_id,
                provider="provider-a",
                status=AttemptStatus.SENT,
            )
        )
        await self.assert_integrity_error(
            Attempt.__table__.insert().values(
                task_id=task_id,
                leg_id=second_leg_id,
                provider="provider-a",
                external_id="external-1",
                status=AttemptStatus.SENT,
            )
        )
        await self.connection.execute(
            Task.__table__.update().where(Task.id == task_id).values(current_attempt_id=attempt_id)
        )
        await self.assert_integrity_error(
            Task.__table__.update().where(Task.id == second_task_id).values(current_attempt_id=attempt_id)
        )

        payment_entry = LedgerEntry.__table__.insert().values(
            user_id=user_id, delta=100, reason="payment", payment_id="payment-1"
        )
        await self.connection.execute(payment_entry)
        await self.assert_integrity_error(payment_entry)
        await self.assert_integrity_error(
            LedgerEntry.__table__.insert().values(
                user_id=other_user_id, delta=50, reason="payment", payment_id="payment-2"
            )
        )
        await self.assert_integrity_error(
            LedgerEntry.__table__.insert().values(user_id=user_id, delta=100, reason="payment")
        )

        charge_entry = LedgerEntry.__table__.insert().values(
            user_id=user_id, delta=-10, reason="charge", task_id=task_id
        )
        await self.connection.execute(charge_entry)
        await self.assert_integrity_error(charge_entry)
        await self.assert_integrity_error(
            LedgerEntry.__table__.insert().values(
                user_id=other_user_id, delta=-10, reason="charge", task_id=second_task_id
            )
        )
        await self.assert_integrity_error(
            LedgerEntry.__table__.insert().values(user_id=user_id, delta=-10, reason="charge")
        )

        refund_entry = LedgerEntry.__table__.insert().values(
            user_id=user_id, delta=10, reason="refund", task_id=task_id
        )
        await self.connection.execute(refund_entry)
        await self.assert_integrity_error(refund_entry)

        await self.assert_database_error(
            LedgerEntry.__table__.update().values(delta=1)
        )
        await self.assert_database_error(LedgerEntry.__table__.delete())
        await self.assert_database_error(text("TRUNCATE ledger"))
