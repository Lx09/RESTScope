"""ORM mapping for schemas."""

from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin


class SchemaORM(CreatedAtMixin, Base):
    __tablename__ = "schemas"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str | None] = mapped_column(String)
    spec_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    raw_spec_uri: Mapped[str] = mapped_column(String, nullable=False)
    normalized_spec_uri: Mapped[str | None] = mapped_column(String)
    openapi_version: Mapped[str | None] = mapped_column(String)
    operation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
