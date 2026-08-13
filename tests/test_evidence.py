"""Regression scenarios for generic in-memory evidence confidence.

The tests exercise only the public :class:`restscope.evidence.Evidence`
interface. They treat the wrapped payload as caller-owned data and verify the
observable confidence score rather than private Beta counters.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest


class _CustomEvidenceData:
    """Represent an application-owned payload with no serialization contract."""


def test_new_evidence_keeps_its_payload_and_starts_neutral() -> None:
    """Scenario: arbitrary evidence data begins with the Beta(1,1) score."""
    from restscope.evidence import Evidence

    payload = {"conclusion": "The endpoint requires authentication"}

    evidence = Evidence(payload)

    assert evidence.data is payload
    assert evidence.confidence == 0.5


def test_support_and_opposition_update_the_same_evidence() -> None:
    """Scenario: equal-weight observations mutate and return the current score."""
    from restscope.evidence import Evidence

    evidence = Evidence("The response matches its documented contract")

    supported_confidence = evidence.update(supports=True)

    assert supported_confidence == 2 / 3
    assert evidence.confidence == 2 / 3

    opposed_confidence = evidence.update(supports=False)

    assert opposed_confidence == 0.5
    assert evidence.confidence == 0.5

    assert evidence.update(supports=True) == 0.6


@pytest.mark.parametrize("invalid_support", [1, 0, "support", None])
def test_non_boolean_update_is_rejected_without_changing_confidence(
    invalid_support: object,
) -> None:
    """Scenario: invalid observation input cannot partially update confidence."""
    from restscope.evidence import Evidence

    evidence = Evidence(None)

    with pytest.raises(TypeError, match="supports must be a bool"):
        evidence.update(supports=invalid_support)  # type: ignore[arg-type]

    assert evidence.confidence == 0.5


@pytest.mark.parametrize(
    "payload",
    ["text evidence", {"id": 7}, _CustomEvidenceData(), None],
)
def test_evidence_accepts_any_payload_without_copying(payload: object) -> None:
    """Scenario: common and application-defined payloads retain their identity."""
    from restscope.evidence import Evidence

    evidence = Evidence(payload)

    assert evidence.data is payload


def test_evidence_data_cannot_be_replaced() -> None:
    """Scenario: confidence cannot silently move to a different payload."""
    from restscope.evidence import Evidence

    evidence = Evidence("original")

    with pytest.raises(AttributeError):
        evidence.data = "replacement"

    assert evidence.data == "original"


def test_concurrent_updates_preserve_every_observation() -> None:
    """Scenario: threads sharing one evidence instance do not lose updates."""
    from restscope.evidence import Evidence

    evidence = Evidence("Shared conclusion")

    def record_many(*, supports: bool, count: int) -> None:
        """Submit one worker's observations through the public update seam."""
        # Each call is a distinct equal-weight observation. Splitting them
        # across workers exercises the Module's synchronization responsibility.
        for _ in range(count):
            evidence.update(supports=supports)

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [
            executor.submit(record_many, supports=True, count=1_000)
            for _ in range(4)
        ]
        futures.extend(
            executor.submit(record_many, supports=False, count=1_000)
            for _ in range(2)
        )
        for future in futures:
            future.result()

    # Beta(1,1) plus 4,000 supporting and 2,000 opposing observations.
    assert evidence.confidence == pytest.approx(0.6666111296234588)
