from enum import StrEnum


class PaymentStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AttemptStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
