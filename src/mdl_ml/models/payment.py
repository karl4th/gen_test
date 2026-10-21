from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from mdl_ml.domain.statuses import PaymentStatus
from mdl_ml.models.base import Base
from mdl_ml.models.status_type import StatusType, status_check


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("tokens > 0", name="tokens_positive"),
        status_check(PaymentStatus),
        UniqueConstraint(
            "user_id", "payment_id", name="uq_payments_user_id_payment_id"
        ),
    )

    payment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        StatusType(PaymentStatus), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
