from dataclasses import dataclass

from schemathesis_mcp.projector import EventProjector


@dataclass
class FakePhase:
    name: str


@dataclass
class PhaseStarted:
    phase: FakePhase
    timestamp: float = 1.0


def test_projector_emits_stable_event_shape_without_internal_dataclass_dump() -> None:
    event = EventProjector().project(PhaseStarted(phase=FakePhase(name="fuzzing")))

    assert event == {
        "type": "phase_started",
        "timestamp": 1.0,
        "phase": "fuzzing",
    }
