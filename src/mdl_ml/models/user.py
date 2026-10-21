from sqlalchemy import BigInteger, CheckConstraint, Identity, text
from sqlalchemy.orm import Mapped, mapped_column

from mdl_ml.models.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("balance >= 0", name="balance_nonnegative"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
