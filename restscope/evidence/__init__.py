"""Attach an updateable confidence score to arbitrary evidence data.

The :class:`Evidence` model receives one caller-owned Python value and starts
its confidence at the neutral Beta(1,1) prior. Later supporting or opposing
observations update private counters, and callers read the resulting numeric
confidence. This is a standalone, App-lifetime model: it does not persist the
payload, publish a Tool, or participate in the Agent or behavior-monitor flow.
"""

from __future__ import annotations

from threading import Lock


class Evidence[EvidenceData]:
    """Keep arbitrary evidence data together with its current confidence.

    Args:
        data: The caller-owned value described by this evidence. The model
            retains the exact object without interpreting, copying, or
            serializing it.

    The public ``data`` property cannot be reassigned. A mutable payload can
    still be changed through another reference owned by the caller. Confidence
    begins at ``0.5`` because both private Beta parameters start at one.
    """

    __slots__ = ("_alpha", "_beta", "_data", "_lock")

    def __init__(self, data: EvidenceData) -> None:
        self._data = data
        self._alpha = 1
        self._beta = 1
        self._lock = Lock()

    @property
    def data(self) -> EvidenceData:
        """Return the exact caller-owned payload without copying it."""
        return self._data

    @property
    def confidence(self) -> float:
        """Return alpha divided by alpha plus beta for the current state."""
        with self._lock:
            return self._confidence_while_locked()

    def update(self, *, supports: bool) -> float:
        """Record one equal-weight observation and return the new confidence.

        Args:
            supports: ``True`` means the observation supports the wrapped
                evidence data; ``False`` means it opposes that data.

        Returns:
            The confidence after incrementing exactly one Beta parameter.

        This method changes the current :class:`Evidence` instance. It does not
        retain the observation itself, so callers remain responsible for
        avoiding duplicate updates. A non-boolean value raises ``TypeError``
        before either private counter changes.
        """
        # Reject truthy and falsy substitutes such as 1, 0, and strings. Each
        # accepted call must describe one unambiguous Bernoulli observation.
        if not isinstance(supports, bool):
            raise TypeError("supports must be a bool")
        # Increment and calculate under the same lock. A concurrent caller can
        # therefore observe either the complete old state or complete new
        # state, never a mixture of the two private counters.
        with self._lock:
            if supports:
                self._alpha += 1
            else:
                self._beta += 1
            return self._confidence_while_locked()

    def _confidence_while_locked(self) -> float:
        """Calculate the score while the public caller holds ``_lock``.

        Keeping this formula in one private helper makes the read and update
        paths agree without acquiring the non-reentrant lock twice.
        """
        return self._alpha / (self._alpha + self._beta)


__all__ = ["Evidence"]
