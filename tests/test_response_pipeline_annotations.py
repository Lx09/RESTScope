"""Request-local typed annotation behavior for response Pipeline Modules."""


def test_annotation_owner_writes_once_and_later_modules_read_typed_value() -> None:
    """A namespaced fact has one writer while every later stage may read it."""

    import pytest

    from restscope.api_behavior_monitor.pipeline import (
        AnnotationKey,
        PipelineAnnotations,
    )

    key = AnnotationKey("observation.id", "observation", str)
    annotations = PipelineAnnotations()
    annotations.write("observation", key, "observation-1")

    assert annotations.read(key) == "observation-1"
    with pytest.raises(PermissionError):
        PipelineAnnotations().write("oracle", key, "wrong-owner")
    with pytest.raises(ValueError):
        annotations.write("observation", key, "second")
