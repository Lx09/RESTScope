"""Portable SQLAlchemy type helpers for RESTScope DB tables."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Numeric, String, Text
from sqlalchemy.types import TypeDecorator


JsonDict = dict[str, Any]
JsonList = list[Any]
DecimalValue = Decimal


class StringList(TypeDecorator[list[str]]):
    """Store string lists portably as JSON arrays."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value: list[str] | None, dialect) -> list[str]:
        del dialect
        return list(value or [])

    def process_result_value(self, value: object | None, dialect) -> list[str]:
        del dialect
        if value is None:
            return []
        return list(value)


JsonType = JSON
TextType = Text
ShortText = String
NumericType = Numeric
