from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from mdl_ml.models.base import Base


class LedgerEntry(Base):
    __tablename__ = "ledger"
    __table_args__ = (
        CheckConstraint("reason IN ('payment', 'charge', 'refund')", name="reason_valid"),
        CheckConstraint(
            "(reason = 'payment' AND payment_id IS NOT NULL AND task_id IS NULL AND delta > 0) OR "
            "(reason = 'charge' AND task_id IS NOT NULL AND payment_id IS NULL AND delta < 0) OR "
            "(reason = 'refund' AND task_id IS NOT NULL AND payment_id IS NULL AND delta > 0)",
            name="reason_fields_valid",
        ),
        Index(
            "uq_ledger_payment_id_payment",
            "payment_id",
            unique=True,
            postgresql_where=text("reason = 'payment'"),
        ),
        Index(
            "uq_ledger_task_id_charge",
            "task_id",
            unique=True,
            postgresql_where=text("reason = 'charge'"),
        ),
        Index(
            "uq_ledger_task_id_refund",
            "task_id",
            unique=True,
            postgresql_where=text("reason = 'refund'"),
        ),
        ForeignKeyConstraint(
            ["user_id", "payment_id"],
            ["payments.user_id", "payments.payment_id"],
            name="fk_ledger_user_payment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id", "task_id"],
            ["tasks.user_id", "tasks.id"],
            name="fk_ledger_user_task",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    task_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    payment_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
