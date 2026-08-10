"""Define, store, compile, and solve deterministic request generation.

The package owns Generator strategies, the recursive Constraint language,
immutable OpenAPI input snapshots, Patch preparation, value generation, and
request serialization. Network execution consumes these outputs through
``restscope.harness.operation_testing`` and does not belong here.
"""

from .store import (
    RequestGenerationConfigStore,
    GeneratorConfigError,
    GeneratorConfigStateConflict,
    RequestGenerationState,
    expand_generator_patch_presence,
    preview_generator_patch,
    prepare_accepted_generator_patch,
    rebuild_generator_config,
    generation_state_digest,
)
from .constraints import (
    AndConstraint,
    ArithmeticValue,
    CardinalityConstraint,
    ConstraintValidationError,
    ComparePredicate,
    ConstraintSet,
    ImplicationConstraint,
    InputAssignment,
    InputNodeOverride,
    InputValue,
    LiteralValue,
    MatchesPredicate,
    NotConstraint,
    OrConstraint,
    OperationConstraintRecord,
    PresentPredicate,
    classify_constraint,
    evaluate_constraint_set,
    normalize_constraint_set,
    referenced_input_node_ids,
    validate_constraint_set,
)
from .constraint_solver import (
    ConstraintSolveError,
    assignments_from_generated_case,
    solve_input_overrides,
)
from .models import (
    GeneratorDisabledReason,
    GeneratedNodeValue,
    GeneratedTestCase,
    InputGeneratorConfig,
    InputGeneratorPatch,
    InputNodeSnapshot,
    OperationGeneratorConfig,
    OperationTestSnapshot,
    ParameterSnapshot,
    SchemaSnapshot,
    PreparedTestRequest,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)
from .generation import generate_strategy_value, project_generated_input_value
from .ports import ReferenceValueProvider
from .reference_values import BehaviorMonitorReferenceValues
from .parameter_patch.models import (
    CompiledConstraintPatch,
    CompiledParameterPatch,
    SelectedReferenceProvenance,
    SemanticParameterPatch,
)
from .parameter_patch.runtime import (
    RequestGenerationPatchRuntime,
    ParameterPatchValidationError,
    ValidatedPatch,
)
from .parameter_patch.projection import (
    constraint_closure,
    semantic_state_payload,
    validation_payload,
)
from .randomness import SeededRandom
from .semantics import (
    build_semantic_input_map,
)

__all__ = [
    "AndConstraint",
    "ArithmeticValue",
    "CardinalityConstraint",
    "ComparePredicate",
    "ConstraintSet",
    "ConstraintSolveError",
    "ConstraintValidationError",
    "RequestGenerationConfigStore",
    "GeneratorConfigError",
    "GeneratorConfigStateConflict",
    "RequestGenerationState",
    "GeneratorDisabledReason",
    "GeneratedNodeValue",
    "GeneratedTestCase",
    "InputGeneratorConfig",
    "InputGeneratorPatch",
    "InputAssignment",
    "InputNodeOverride",
    "InputValue",
    "InputNodeSnapshot",
    "ImplicationConstraint",
    "LiteralValue",
    "MatchesPredicate",
    "NotConstraint",
    "OperationGeneratorConfig",
    "OperationConstraintRecord",
    "OperationTestSnapshot",
    "ParameterSnapshot",
    "SchemaSnapshot",
    "PreparedTestRequest",
    "ReferenceValueProvider",
    "BehaviorMonitorReferenceValues",
    "CompiledConstraintPatch",
    "CompiledParameterPatch",
    "SelectedReferenceProvenance",
    "SemanticParameterPatch",
    "RequestGenerationPatchRuntime",
    "ParameterPatchValidationError",
    "ValidatedPatch",
    "ResourceIdentifierGenerator",
    "ResponseValueGenerator",
    "OrConstraint",
    "PresentPredicate",
    "SeededRandom",
    "classify_constraint",
    "evaluate_constraint_set",
    "expand_generator_patch_presence",
    "build_semantic_input_map",
    "assignments_from_generated_case",
    "generate_strategy_value",
    "normalize_constraint_set",
    "preview_generator_patch",
    "prepare_accepted_generator_patch",
    "rebuild_generator_config",
    "generation_state_digest",
    "constraint_closure",
    "semantic_state_payload",
    "validation_payload",
    "project_generated_input_value",
    "referenced_input_node_ids",
    "solve_input_overrides",
    "validate_constraint_set",
]
