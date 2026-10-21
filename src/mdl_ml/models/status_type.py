from enum import StrEnum
from typing import TypeVar

from sqlalchemy import CheckConstraint, Text, column
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


StatusEnum = TypeVar("StatusEnum", bound=StrEnum)


class StatusType(TypeDecorator[StatusEnum]):
    impl = Text
    cache_ok = True

    def __init__(self, enum_class: type[StatusEnum]) -> None:
        self.enum_class = enum_class
        super().__init__()

    def process_bind_param(
        self, value: StatusEnum | str | None, dialect: Dialect
    ) -> str | None:
        if isinstance(value, StrEnum):
            return value.value
        return value

    def process_result_value(
        self, value: str | None, dialect: Dialect
    ) -> StatusEnum | None:
        if value is None:
            return None
        return self.enum_class(value)


def status_check(enum_class: type[StrEnum]) -> CheckConstraint:
    return CheckConstraint(
        column("status").in_([status.value for status in enum_class]),
        name="status_valid",
    )
