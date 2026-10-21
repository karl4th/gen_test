from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from mdl_ml.domain.statuses import TaskStatus
from mdl_ml.models.base import Base
from mdl_ml.models.status_type import StatusType, status_check


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("price > 0", name="price_positive"),
        CheckConstraint("jsonb_typeof(ref_images) = 'array'", name="ref_images_array"),
        status_check(TaskStatus),
        UniqueConstraint("user_id", "id", name="uq_tasks_user_id_id"),
        ForeignKeyConstraint(
            ["id", "current_attempt_id"],
            ["attempts.task_id", "attempts.id"],
            name="fk_tasks_current_attempt_same_task",
            use_alter=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    model_slug: Mapped[str] = mapped_column(
        Text, ForeignKey("models.slug", ondelete="RESTRICT"), nullable=False
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    ref_images: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[TaskStatus] = mapped_column(StatusType(TaskStatus), nullable=False)
    current_attempt_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    result_url: Mapped[str | None] = mapped_column(Text)
    error_type: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
