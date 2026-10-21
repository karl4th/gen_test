from enum import StrEnum


class PaymentStatus(StrEnum):
    RECEIVED = "received"
    CREDITED = "credited"
    REJECTED = "rejected"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AttemptStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
