"""Provide one reproducible source for generated test values.

``SeededRandom`` receives the App-wide root seed and derives an independent
pseudo-random stream for each caller-supplied scope.  Call order therefore
cannot make one operation or input consume another input's values.  Runtime
identities such as report IDs remain outside this module and continue to use
UUIDs.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
import hashlib
import random
import secrets
from typing import TypeVar


_Value = TypeVar("_Value")


class SeededRandom:
    """Generate common deterministic values from one App-wide root seed."""

    def __init__(self, seed: int | None = None) -> None:
        """Use ``seed`` or generate one non-negative seed for this App."""
        resolved = secrets.randbits(63) if seed is None else seed
        if resolved < 0:
            raise ValueError("random seed must be non-negative")
        self.seed = resolved

    def integer(self, minimum: int, maximum: int, *, scope: str) -> int:
        """Return an inclusive integer for ``scope``."""
        return self._generator(scope).randint(minimum, maximum)

    def number(self, minimum: float, maximum: float, *, scope: str) -> float:
        """Return a floating-point number for ``scope``."""
        return self._generator(scope).uniform(minimum, maximum)

    def boolean(self, *, scope: str) -> bool:
        """Return a Boolean for ``scope``."""
        return bool(self._generator(scope).getrandbits(1))

    def choice(self, values: Sequence[_Value], *, scope: str) -> _Value:
        """Return one item from a non-empty ordered sequence."""
        if not values:
            raise ValueError("choice values must not be empty")
        return self._generator(scope).choice(values)

    def string(self, alphabet: str, length: int, *, scope: str) -> str:
        """Return a fixed-length string drawn from ``alphabet``."""
        if not alphabet:
            raise ValueError("string alphabet must not be empty")
        if length < 0:
            raise ValueError("string length must be non-negative")
        generator = self._generator(scope)
        return "".join(generator.choice(alphabet) for _ in range(length))

    def date(self, *, scope: str) -> date:
        """Return a reproducible date in the supported hundred-year window."""
        days = self.integer(0, 365 * 100 - 1, scope=f"{scope}:date")
        return date(2000, 1, 1) + timedelta(days=days)

    def date_time(self, *, scope: str) -> datetime:
        """Return a reproducible UTC timestamp in the supported window."""
        seconds = self.integer(
            0,
            365 * 100 * 24 * 60 * 60 - 1,
            scope=f"{scope}:date-time",
        )
        return datetime(2000, 1, 1, tzinfo=timezone.utc) + timedelta(
            seconds=seconds
        )

    def derived_seed(self, *, scope: str) -> int:
        """Return the stable integer seed used by an existing generator seam."""
        return int.from_bytes(
            hashlib.sha256(
                f"{self.seed}\0{scope}".encode("utf-8")
            ).digest()[:8],
            "big",
        )

    def _generator(self, scope: str) -> random.Random:
        """Create an independent stream so call order never leaks across scopes."""
        if not scope:
            raise ValueError("random scope must not be empty")
        return random.Random(self.derived_seed(scope=scope))
