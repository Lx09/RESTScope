"""Database-layer exceptions."""

from __future__ import annotations


class DBError(RuntimeError):
    """Base class for DB module errors."""


class NotFoundError(DBError):
    """Raised when a requested record is not found."""


class ConflictError(DBError):
    """Raised when a write conflicts with existing data."""


class ConcurrencyError(ConflictError):
    """Raised when optimistic concurrency checks fail."""
