"""Deterministic generators for configured OpenAPI input values."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import random
import re
from typing import Any
from uuid import UUID

from .models import (
    ArrayGenerator,
    BooleanGenerator,
    ChoiceGenerator,
    ConstantGenerator,
    FormatGenerator,
    GeneratedNodeValue,
    GeneratedTestCase,
    GeneratorStrategy,
    IntegerRangeGenerator,
    NumberRangeGenerator,
    ObjectGenerator,
    OperationGeneratorConfig,
    OperationTestSnapshot,
    ParameterSnapshot,
    RandomStringGenerator,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
    SchemaSnapshot,
)
from .models import InputNodeSnapshot
from .ports import ReferenceValueProvider


class GenerationError(ValueError):
    """A configured strategy cannot produce the requested value."""

    code = "generation_failed"


def generate_strategy_value(
    strategy: GeneratorStrategy,
    *,
    seed: int,
    reference_values: ReferenceValueProvider | None = None,
) -> Any:
    """Generate one deterministic scalar value from a configured strategy."""

    generator = random.Random(seed)
    if isinstance(strategy, ConstantGenerator):
        return deepcopy(strategy.value)
    if isinstance(strategy, ChoiceGenerator):
        return deepcopy(generator.choices(strategy.values, weights=strategy.weights, k=1)[0])
    if isinstance(strategy, IntegerRangeGenerator):
        return generator.randint(strategy.minimum, strategy.maximum)
    if isinstance(strategy, NumberRangeGenerator):
        return generator.uniform(strategy.minimum, strategy.maximum)
    if isinstance(strategy, RandomStringGenerator):
        length = generator.randint(strategy.min_length, strategy.max_length)
        return "".join(generator.choice(strategy.alphabet) for _ in range(length))
    if isinstance(strategy, BooleanGenerator):
        return generator.random() < strategy.true_probability
    if isinstance(strategy, FormatGenerator):
        return _format_value(strategy.format, generator)
    if isinstance(
        strategy,
        ResourceIdentifierGenerator | ResponseValueGenerator,
    ):
        values = (
            list(reference_values.values_for(strategy))
            if reference_values is not None
            else []
        )
        if not values:
            raise GenerationError(
                f"Reference value pool is empty for {strategy.type}"
            )
        return deepcopy(generator.choice(values))
    raise GenerationError(f"Strategy {strategy.type} does not generate a scalar value")


def generate_test_case(
    operation: OperationTestSnapshot,
    config: OperationGeneratorConfig,
    *,
    run_seed: int,
    case_index: int,
    reference_values: ReferenceValueProvider | None = None,
) -> GeneratedTestCase:
    """Generate one complete request from the persisted snapshot and configuration."""

    if operation.operation_key != config.operation_key:
        raise GenerationError("Generator configuration belongs to a different operation")
    if case_index < 0:
        raise GenerationError("case_index cannot be negative")

    generator = _TestCaseGenerator(
        operation=operation,
        config=config,
        run_seed=run_seed,
        case_index=case_index,
        reference_values=reference_values,
    )
    return generator.generate()


class _TestCaseGenerator:
    def __init__(
        self,
        *,
        operation: OperationTestSnapshot,
        config: OperationGeneratorConfig,
        run_seed: int,
        case_index: int,
        reference_values: ReferenceValueProvider | None,
    ) -> None:
        self.operation = operation
        self.config = config
        self.run_seed = run_seed
        self.case_index = case_index
        self.reference_values = reference_values
        self.nodes_by_path = {
            node.canonical_path: node for node in operation.input_nodes
        }
        self.nodes_by_id = {
            node.input_node_id: node for node in operation.input_nodes
        }
        self.configs = {item.input_node_id: item for item in config.configs}
        self.children: dict[str, list[InputNodeSnapshot]] = {}
        for node in operation.input_nodes:
            if node.parent_node_id is not None:
                self.children.setdefault(node.parent_node_id, []).append(node)
        for nodes in self.children.values():
            nodes.sort(key=lambda node: node.canonical_path)
        self.generated_values: list[GeneratedNodeValue] = []
        self.omitted: list[str] = []

    def generate(self) -> GeneratedTestCase:
        locations: dict[str, dict[str, Any]] = {
            "path": {},
            "query": {},
            "header": {},
            "cookie": {},
        }
        for parameter in self._parameters():
            node = self.nodes_by_id[parameter.input_node_id]
            included, value = self._build_value(
                node,
                instance_path=f"{parameter.location}.{parameter.name}",
            )
            if included:
                locations[parameter.location][parameter.name] = value

        body_value: Any | None = None
        body_present = False
        media_type = self.config.active_media_type
        body_node = (
            self.nodes_by_id.get(self.operation.request_body_node_id)
            if self.operation.request_body_node_id is not None
            else None
        )
        if body_node is not None:
            body_included = self._included(body_node, "body")
            if body_included:
                body_present = True
                if not media_type:
                    raise GenerationError("Request body configuration has no active media type")
                media_node_id = self.operation.media_type_node_ids.get(
                    media_type.strip().lower()
                )
                media_node = self.nodes_by_id.get(media_node_id) if media_node_id else None
                if media_node is None:
                    raise GenerationError(f"Active request media type is unavailable: {media_type}")
                included, body_value = self._build_value(media_node, instance_path="body")
                if not included:
                    body_value = None

        return GeneratedTestCase(
            operation_key=self.operation.operation_key,
            case_index=self.case_index,
            media_type=media_type if body_present else None,
            path_parameters=locations["path"],
            query_parameters=locations["query"],
            header_parameters=locations["header"],
            cookie_parameters=locations["cookie"],
            body=body_value,
            body_present=body_present,
            generated_values=self.generated_values,
            omitted_input_node_ids=list(dict.fromkeys(self.omitted)),
        )

    def _parameters(self) -> tuple[ParameterSnapshot, ...]:
        return tuple(self.operation.parameters)

    def _build_value(self, node: InputNodeSnapshot, *, instance_path: str) -> tuple[bool, Any]:
        if not self._included(node, instance_path):
            return False, None
        schema = node.schema_contract
        if schema is None:
            raise GenerationError(f"Input node has no schema: {node.canonical_path}")
        strategy = self._config(node).strategy

        if isinstance(
            strategy,
            ConstantGenerator
            | ChoiceGenerator
            | IntegerRangeGenerator
            | NumberRangeGenerator
            | RandomStringGenerator
            | BooleanGenerator
            | FormatGenerator
            | ResourceIdentifierGenerator
            | ResponseValueGenerator,
        ):
            value = generate_strategy_value(
                strategy,
                seed=self._seed(node, instance_path, "value"),
                reference_values=self.reference_values,
            )
            self.generated_values.append(
                GeneratedNodeValue(
                    input_node_id=node.input_node_id,
                    instance_path=instance_path,
                    value=value,
                )
            )
            return True, value

        from .models import VariantGenerator

        if isinstance(strategy, VariantGenerator):
            return True, self._build_variant(node, schema=schema, instance_path=instance_path)
        if isinstance(strategy, ObjectGenerator) and schema.all_of:
            return True, self._build_all_of(node, schema=schema, instance_path=instance_path)
        if isinstance(strategy, ObjectGenerator):
            result: dict[str, Any] = {}
            prefix = f"{node.canonical_path}/properties/"
            for child in self.children.get(node.input_node_id, []):
                if not child.canonical_path.startswith(prefix):
                    continue
                name = _unsegment(child.canonical_path.removeprefix(prefix).split("/", 1)[0])
                included, value = self._build_value(
                    child,
                    instance_path=f"{instance_path}.{name}",
                )
                if included:
                    result[name] = value
            return True, result
        if isinstance(strategy, ArrayGenerator):
            item_node = next(
                (
                    child
                    for child in self.children.get(node.input_node_id, [])
                    if child.canonical_path == f"{node.canonical_path}/items"
                ),
                None,
            )
            if item_node is None:
                raise GenerationError(f"Array node has no items node: {node.canonical_path}")
            length = random.Random(self._seed(node, instance_path, "length")).randint(
                strategy.min_items,
                strategy.max_items,
            )
            result = []
            for index in range(length):
                included, value = self._build_value(
                    item_node,
                    instance_path=f"{instance_path}[{index}]",
                )
                if included:
                    result.append(value)
            return True, result

        raise GenerationError(
            f"Strategy {strategy.type} cannot build input node: {node.canonical_path}"
        )

    def _build_variant(
        self,
        node: InputNodeSnapshot,
        *,
        schema: SchemaSnapshot,
        instance_path: str,
    ) -> Any:
        from .models import VariantGenerator

        strategy = self._config(node).strategy
        if not isinstance(strategy, VariantGenerator):
            raise GenerationError(f"Variant node requires variant strategy: {node.canonical_path}")
        name = "oneOf" if schema.one_of else "anyOf"
        branches = [
            child
            for child in self.children.get(node.input_node_id, [])
            if child.canonical_path.startswith(f"{node.canonical_path}/{name}/")
        ]
        if len(branches) != len(strategy.branch_weights):
            raise GenerationError(f"Variant branch weight count mismatch: {node.canonical_path}")
        branch = random.Random(self._seed(node, instance_path, "branch")).choices(
            branches,
            weights=strategy.branch_weights,
            k=1,
        )[0]
        included, value = self._build_value(branch, instance_path=instance_path)
        if not included:
            raise GenerationError(f"Selected variant branch was omitted: {branch.canonical_path}")
        return value

    def _build_all_of(
        self,
        node: InputNodeSnapshot,
        *,
        schema: SchemaSnapshot,
        instance_path: str,
    ) -> Any:
        branches = [
            child
            for child in self.children.get(node.input_node_id, [])
            if child.canonical_path.startswith(f"{node.canonical_path}/allOf/")
        ]
        result: dict[str, Any] = {}
        for branch in branches:
            included, value = self._build_value(branch, instance_path=instance_path)
            if not included or not isinstance(value, dict):
                raise GenerationError(f"allOf branches must generate objects: {node.canonical_path}")
            overlap = set(result).intersection(value)
            if any(result[key] != value[key] for key in overlap):
                raise GenerationError(f"allOf branches generated conflicting properties: {node.canonical_path}")
            result.update(value)
        return result

    def _included(self, node: InputNodeSnapshot, instance_path: str) -> bool:
        config = self._config(node)
        included = random.Random(self._seed(node, instance_path, "include")).random() < config.inclusion_probability
        if not included:
            self.omitted.append(node.input_node_id)
        return included

    def _config(self, node: InputNodeSnapshot):
        try:
            return self.configs[node.input_node_id]
        except KeyError as exc:
            raise GenerationError(f"Missing generator configuration: {node.input_node_id}") from exc

    def _seed(self, node: InputNodeSnapshot, instance_path: str, purpose: str) -> int:
        payload = f"{self.run_seed}\0{self.case_index}\0{node.input_node_id}\0{instance_path}\0{purpose}"
        return int.from_bytes(hashlib.sha256(payload.encode()).digest()[:8], "big")


def _format_value(format_name: str, generator: random.Random) -> str:
    if format_name == "uuid":
        return str(UUID(int=generator.getrandbits(128), version=4))
    if format_name == "date":
        return (date(2000, 1, 1) + timedelta(days=generator.randrange(365 * 100))).isoformat()
    if format_name == "date-time":
        instant = datetime(2000, 1, 1, tzinfo=timezone.utc) + timedelta(
            seconds=generator.randrange(365 * 100 * 24 * 60 * 60)
        )
        return instant.isoformat().replace("+00:00", "Z")
    if format_name == "email":
        return f"user{generator.randrange(10**12):012d}@example.test"
    raise GenerationError(f"Unsupported format generator: {format_name}")


def _validate_scalar(schema: SchemaSnapshot, value: Any, *, path: str) -> None:
    if value is None:
        if schema.nullable or _has_type(schema, "null"):
            return
        raise GenerationError(f"Generated null for non-nullable input: {path}")
    expected = schema.type
    if isinstance(expected, list):
        expected_types = set(expected)
    elif expected is None:
        expected_types = set()
    else:
        expected_types = {expected}
    valid_type = (
        not expected_types
        or ("string" in expected_types and isinstance(value, str))
        or ("integer" in expected_types and type(value) is int)
        or ("number" in expected_types and type(value) in {int, float})
        or ("boolean" in expected_types and type(value) is bool)
    )
    if not valid_type:
        raise GenerationError(f"Generated value has the wrong type for {path}")
    if schema.has_const and value != schema.const:
        raise GenerationError(f"Generated value violates const for {path}")
    if schema.enum is not None and value not in schema.enum:
        raise GenerationError(f"Generated value is not in enum for {path}")
    if type(value) in {int, float}:
        if schema.minimum is not None and value < schema.minimum:
            raise GenerationError(f"Generated value is below minimum for {path}")
        if schema.maximum is not None and value > schema.maximum:
            raise GenerationError(f"Generated value is above maximum for {path}")
        exclusive_minimum = schema.exclusive_minimum
        if isinstance(exclusive_minimum, bool):
            if exclusive_minimum and schema.minimum is not None and value <= schema.minimum:
                raise GenerationError(f"Generated value is not above exclusiveMinimum for {path}")
        elif exclusive_minimum is not None and value <= exclusive_minimum:
            raise GenerationError(f"Generated value is not above exclusiveMinimum for {path}")
        exclusive_maximum = schema.exclusive_maximum
        if isinstance(exclusive_maximum, bool):
            if exclusive_maximum and schema.maximum is not None and value >= schema.maximum:
                raise GenerationError(f"Generated value is not below exclusiveMaximum for {path}")
        elif exclusive_maximum is not None and value >= exclusive_maximum:
            raise GenerationError(f"Generated value is not below exclusiveMaximum for {path}")
        multiple_of = schema.multiple_of
        if isinstance(multiple_of, int | float) and multiple_of > 0:
            quotient = value / multiple_of
            if abs(quotient - round(quotient)) > 1e-9:
                raise GenerationError(f"Generated value violates multipleOf for {path}")
    if isinstance(value, str):
        if schema.min_length is not None and len(value) < schema.min_length:
            raise GenerationError(f"Generated string is shorter than minLength for {path}")
        if schema.max_length is not None and len(value) > schema.max_length:
            raise GenerationError(f"Generated string is longer than maxLength for {path}")
        if schema.pattern is not None and re.search(schema.pattern, value) is None:
            raise GenerationError(f"Generated string does not match pattern for {path}")
        if schema.format == "uuid":
            try:
                UUID(value)
            except (ValueError, AttributeError) as exc:
                raise GenerationError(f"Generated string is not a UUID for {path}") from exc
        elif schema.format == "date":
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise GenerationError(f"Generated string is not a date for {path}") from exc
        elif schema.format == "date-time":
            if "T" not in value:
                raise GenerationError(
                    f"Generated string is not a date-time for {path}"
                )
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise GenerationError(f"Generated string is not a date-time for {path}") from exc
        elif schema.format == "email" and re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            value,
        ) is None:
            raise GenerationError(f"Generated string is not an email for {path}")


def _validate_object(schema: SchemaSnapshot, value: dict[str, Any], *, path: str) -> None:
    read_only = sorted(set(schema.read_only_properties).intersection(value))
    if read_only:
        raise GenerationError(
            f"Generated object includes read-only properties for {path}: {read_only}"
        )
    missing = sorted(set(schema.required) - set(value))
    if missing:
        raise GenerationError(f"Generated object is missing required properties for {path}: {missing}")
    if schema.min_properties is not None and len(value) < schema.min_properties:
        raise GenerationError(f"Generated object has too few properties for {path}")
    if schema.max_properties is not None and len(value) > schema.max_properties:
        raise GenerationError(f"Generated object has too many properties for {path}")


def _validate_array(schema: SchemaSnapshot, value: list[Any], *, path: str) -> None:
    if schema.min_items is not None and len(value) < schema.min_items:
        raise GenerationError(f"Generated array is shorter than minItems for {path}")
    if schema.max_items is not None and len(value) > schema.max_items:
        raise GenerationError(f"Generated array is longer than maxItems for {path}")
    if schema.unique_items:
        canonical = [repr(item) for item in value]
        if len(canonical) != len(set(canonical)):
            raise GenerationError(f"Generated array violates uniqueItems for {path}")


def schema_matches(schema: SchemaSnapshot, value: Any) -> bool:
    """Return whether a concrete value satisfies the frozen supported constraints."""

    if schema.enum is not None:
        return value in schema.enum
    if value is None:
        return bool(schema.nullable or _has_type(schema, "null"))
    if schema.all_of and not all(schema_matches(branch, value) for branch in schema.all_of):
        return False
    if schema.any_of and not any(schema_matches(branch, value) for branch in schema.any_of):
        return False
    if schema.one_of and sum(schema_matches(branch, value) for branch in schema.one_of) != 1:
        return False
    if schema.has_const and value != schema.const:
        return False
    if schema.enum is not None and value not in schema.enum:
        return False
    expected = set(schema.type) if isinstance(schema.type, list) else ({schema.type} if schema.type else set())
    if "object" in expected or schema.properties:
        if not isinstance(value, dict):
            return False
        if set(schema.read_only_properties).intersection(value):
            return False
        if not set(schema.required).issubset(value):
            return False
        if schema.min_properties is not None and len(value) < schema.min_properties:
            return False
        if schema.max_properties is not None and len(value) > schema.max_properties:
            return False
        if schema.additional_properties is False and set(value) - set(schema.properties):
            return False
        return all(
            name not in value or schema_matches(child, value[name])
            for name, child in schema.properties.items()
        )
    if "array" in expected or schema.items is not None:
        if not isinstance(value, list):
            return False
        if schema.min_items is not None and len(value) < schema.min_items:
            return False
        if schema.max_items is not None and len(value) > schema.max_items:
            return False
        if schema.unique_items:
            canonical = [repr(item) for item in value]
            if len(canonical) != len(set(canonical)):
                return False
        return schema.items is None or all(
            schema_matches(schema.items, item) for item in value
        )
    try:
        _validate_scalar(schema, value, path="variant")
    except GenerationError:
        return False
    return True


def _segment(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _unsegment(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def _has_type(schema: SchemaSnapshot, expected: str) -> bool:
    return (
        expected in schema.type
        if isinstance(schema.type, list)
        else schema.type == expected
    )
