from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from mdl_ml.models.base import Base


class Leg(Base):
    __tablename__ = "legs"
    __table_args__ = (
        CheckConstraint("priority >= 0", name="priority_nonnegative"),
        CheckConstraint("max_ref_images >= 0", name="max_ref_images_nonnegative"),
        UniqueConstraint("model_slug", "priority", name="uq_legs_model_slug_priority"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    model_slug: Mapped[str] = mapped_column(
        Text, ForeignKey("models.slug", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    max_ref_images: Mapped[int] = mapped_column(Integer, nullable=False)
    skip_on_moderation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
