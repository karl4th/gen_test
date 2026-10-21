from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from mdl_ml.domain.statuses import (
    AttemptStatus,
    PaymentStatus,
    ProviderErrorType,
    TaskStatus,
)

BIGINT_MAX = 2**63 - 1
# Identifiers are B-tree keys, and PostgreSQL rejects oversized index entries.
ID_MAX_LENGTH = 200
URL_MAX_LENGTH = 2048
PROMPT_MAX_LENGTH = 10_000
REF_IMAGES_MAX_ITEMS = 100

RefImage = Annotated[str, Field(max_length=URL_MAX_LENGTH)]


class GenerateRequest(BaseModel):
    user_id: int = Field(gt=0, le=BIGINT_MAX)
    model_slug: str = Field(min_length=1)
    prompt: str = Field(min_length=1, max_length=PROMPT_MAX_LENGTH)
    ref_images: list[RefImage] = Field(
        default_factory=list, max_length=REF_IMAGES_MAX_ITEMS
    )


class GenerateResponse(BaseModel):
    task_id: UUID


class PaymentWebhook(BaseModel):
    payment_id: str = Field(min_length=1, max_length=ID_MAX_LENGTH)
    user_id: int = Field(gt=0, le=BIGINT_MAX)
    tokens: int = Field(gt=0, le=BIGINT_MAX)
    status: PaymentStatus


class ProviderWebhook(BaseModel):
    external_id: str = Field(min_length=1, max_length=ID_MAX_LENGTH)
    status: Literal["completed", "failed"]
    result_url: str | None = Field(default=None, max_length=URL_MAX_LENGTH)
    error_type: ProviderErrorType | None = None

    @model_validator(mode="after")
    def check_result(self) -> "ProviderWebhook":
        if self.status == "completed" and not self.result_url:
            raise ValueError("Completed results require result_url")
        if self.status == "failed" and self.error_type is None:
            raise ValueError("Failed results require error_type")
        return self


class LedgerRow(BaseModel):
    id: int
    delta: int
    reason: str
    task_id: UUID | None
    payment_id: str | None
    created_at: datetime


class BalanceResponse(BaseModel):
    user_id: int
    balance: int
    ledger: list[LedgerRow]


class AttemptRow(BaseModel):
    id: UUID
    leg_id: int
    provider: str
    status: AttemptStatus
    error_type: str | None
    sent_at: datetime | None
    finished_at: datetime | None
    deadline_at: datetime | None
    late_result_url: str | None


class TaskResponse(BaseModel):
    id: UUID
    user_id: int
    model_slug: str
    price: int
    status: TaskStatus
    current_leg_id: int | None
    current_provider: str | None
    current_attempt_id: UUID | None
    result_url: str | None
    error_type: str | None
    created_at: datetime
    updated_at: datetime
    attempts: list[AttemptRow]
