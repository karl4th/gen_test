from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from mdl_ml.domain.statuses import AttemptStatus
from mdl_ml.models.base import Base
from mdl_ml.models.status_type import StatusType, status_check


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("task_id", "leg_id", name="uq_attempts_task_id_leg_id"),
        UniqueConstraint(
            "provider", "external_id", name="uq_attempts_provider_external_id"
        ),
        UniqueConstraint("task_id", "id", name="uq_attempts_task_id_id"),
        status_check(AttemptStatus),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    leg_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("legs.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[AttemptStatus] = mapped_column(
        StatusType(AttemptStatus), nullable=False
    )
    error_type: Mapped[str | None] = mapped_column(Text)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    late_result_url: Mapped[str | None] = mapped_column(Text)
