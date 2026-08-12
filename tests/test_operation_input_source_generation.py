"""Reference-backed Generator behavior over exact operation input sources."""

from __future__ import annotations


class _CompositeResourceValues:
    """Supply two complete resource instances through the public value seam."""

    def values_for(self, strategy):
        del strategy
        return []

    def resource_key(self, strategy):
        del strategy
        return "memberships"

    def resource_records(self, strategy):
        del strategy
        return [
            {"organization_id": "acme", "user_id": 42},
            {"organization_id": "globex", "user_id": 77},
        ]


def test_composite_resource_generators_choose_components_from_one_instance() -> None:
    """Equal per-resource seeds never construct an unobserved composite identity."""
    from restscope.request_generation.models import (
        OperationInputSourceReference,
        ResourceIdentifierGenerator,
    )
    from restscope.request_generation.generation import generate_strategy_value

    common = {
        "producer_operation_id": "GET /memberships",
        "status_code": 200,
        "media_type": "application/json",
    }
    organization = ResourceIdentifierGenerator(
        type="resource_identifier",
        source=OperationInputSourceReference(
            **common,
            selector="$.items[].organization_id",
            field_name="organization_id",
        ),
    )
    user = ResourceIdentifierGenerator(
        type="resource_identifier",
        source=OperationInputSourceReference(
            **common,
            selector="$.items[].user_id",
            field_name="user_id",
        ),
    )
    values = _CompositeResourceValues()

    generated = (
        generate_strategy_value(organization, seed=9, reference_values=values),
        generate_strategy_value(user, seed=9, reference_values=values),
    )

    assert generated in {("acme", 42), ("globex", 77)}
