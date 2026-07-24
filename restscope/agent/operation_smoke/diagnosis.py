"""Two bounded FAST calls that locate inputs before proposing generator patches."""

from __future__ import annotations

from collections import OrderedDict
import json
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from restscope.llm import (
    LLMClient,
    LLMMessage,
    LLMModelConfig,
    LLMRequest,
    LLMResponse,
    OutputValidator,
)
from restscope.testing import (
    InputGeneratorConfig,
    OperationExecutionReport,
    OperationGeneratorConfig,
)

from .schemas import (
    GeneratorPatchDraft,
    ParameterDiagnosis,
    TwoRoundDiagnosisResult,
)


MAX_FIRST_ROUND_USER_BYTES = 64 * 1024
_FIRST_ROUND_SECTION_BYTES = 32 * 1024
_MAX_REPAIR_ERRORS = 10
_OutputT = TypeVar("_OutputT", bound=BaseModel)


class OperationSmokeOutputError(RuntimeError):
    """The FAST model did not return a safe diagnosis or generator patch."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class OperationSmokeDiagnoser:
    """Separate parameter localization from generator mutation."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        validator: OutputValidator | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.validator = validator or OutputValidator()

    def diagnose(
        self,
        *,
        report: OperationExecutionReport,
        config: OperationGeneratorConfig,
    ) -> TwoRoundDiagnosisResult:
        if not self.model.enabled:
            raise OperationSmokeOutputError(
                "operation_smoke_model_not_configured",
                "The Operation Smoke FAST model is not configured",
            )
        if report.operation_key != config.operation_key:
            raise OperationSmokeOutputError(
                "operation_smoke_report_mismatch",
                "Execution report and generator config identify different operations",
            )
        first_context = build_parameter_diagnosis_context(report)
        known_nodes = {
            node.input_node_id for node in config.snapshot.input_nodes
        }
        allowed_evidence = {
            item["failure_id"] for item in first_context["failure_messages"]
        } | {
            item["case_id"] for item in first_context["test_inputs"]
        }
        diagnosis = self._call_with_repair(
            messages=[
                LLMMessage(
                    role="system",
                    content=_parameter_diagnosis_instructions(),
                ),
                LLMMessage(
                    role="user",
                    content=_json(first_context),
                ),
            ],
            output_model=ParameterDiagnosis,
            schema_name="OperationSmokeParameterDiagnosis",
            role="operation_smoke_parameter_diagnosis",
            semantic_validate=lambda draft: _diagnosis_errors(
                draft,
                known_nodes=known_nodes,
                allowed_evidence=allowed_evidence,
            ),
        )
        if diagnosis.no_parameter_issue:
            return TwoRoundDiagnosisResult(diagnosis=diagnosis)

        suspect_ids = {item.input_node_id for item in diagnosis.suspects}
        selected_generators = [
            item for item in config.configs if item.input_node_id in suspect_ids
        ]
        second_context = {
            "diagnosis": diagnosis.model_dump(mode="json"),
            "current_generators": [
                item.model_dump(mode="json") for item in selected_generators
            ],
        }
        patch = self._call_with_repair(
            messages=[
                LLMMessage(
                    role="system",
                    content=_generator_patch_instructions(),
                ),
                LLMMessage(role="user", content=_json(second_context)),
            ],
            output_model=GeneratorPatchDraft,
            schema_name="OperationSmokeGeneratorPatch",
            role="operation_smoke_generator_patch",
            semantic_validate=lambda draft: _generator_patch_errors(
                draft,
                suspect_ids=suspect_ids,
                current_generators=selected_generators,
            ),
        )
        return TwoRoundDiagnosisResult(
            diagnosis=diagnosis,
            updates=patch.updates,
        )

    def _call_with_repair(
        self,
        *,
        messages: list[LLMMessage],
        output_model: type[_OutputT],
        schema_name: str,
        role: str,
        semantic_validate: Callable[[_OutputT], list[str]],
    ) -> _OutputT:
        response = self.client.invoke(
            self._request(
                messages=messages,
                output_model=output_model,
                schema_name=schema_name,
                role=role,
            )
        )
        parsed, errors = self._validate(
            response,
            output_model=output_model,
            semantic_validate=semantic_validate,
        )
        if not errors:
            assert parsed is not None
            return parsed
        repair_messages = [
            *messages,
            LLMMessage(
                role="assistant",
                content=_json(
                    response.parsed_json
                    if response.parsed_json is not None
                    else response.content
                ),
            ),
            LLMMessage(
                role="user",
                content=_json(
                    {
                        "instruction": (
                            "Repair the output using only the supplied IDs and "
                            "return the complete result."
                        ),
                        "validation_errors": errors[:_MAX_REPAIR_ERRORS],
                    }
                ),
            ),
        ]
        repaired_response = self.client.invoke(
            self._request(
                messages=repair_messages,
                output_model=output_model,
                schema_name=schema_name,
                role=role,
            )
        )
        parsed, errors = self._validate(
            repaired_response,
            output_model=output_model,
            semantic_validate=semantic_validate,
        )
        if errors or parsed is None:
            raise OperationSmokeOutputError(
                "operation_smoke_output_invalid",
                "Operation Smoke output remained invalid: "
                + "; ".join(errors[:5]),
            )
        return parsed

    def _request(
        self,
        *,
        messages: list[LLMMessage],
        output_model: type[BaseModel],
        schema_name: str,
        role: str,
    ) -> LLMRequest:
        return LLMRequest(
            provider=self.model.provider,
            model=self.model.model,
            messages=messages,
            temperature=self.model.temperature,
            max_tokens=self.model.max_tokens,
            response_format="json_schema",
            json_schema=output_model.model_json_schema(),
            json_schema_name=schema_name,
            tool_choice="none",
            timeout_seconds=self.model.timeout_seconds,
            reasoning=self.model.reasoning,
            metadata={"role": role},
        )

    def _validate(
        self,
        response: LLMResponse,
        *,
        output_model: type[_OutputT],
        semantic_validate: Callable[[_OutputT], list[str]],
    ) -> tuple[_OutputT | None, list[str]]:
        validation = self.validator.validate(
            response=response,
            output_model=output_model,
        )
        if not validation.valid:
            return None, [
                ": ".join(
                    part
                    for part in (error.location, error.message)
                    if part
                )
                for error in validation.errors
            ]
        parsed = output_model.model_validate(validation.validated_object)
        return parsed, semantic_validate(parsed)


def build_parameter_diagnosis_context(
    report: OperationExecutionReport,
) -> dict[str, Any]:
    """Project one batch into a deterministic, bounded first-round prompt."""

    source_messages = report.failure_report.unique_failure_messages
    included_messages: list[dict[str, str]] = []
    messages_truncated = report.failure_report.truncated
    for item in source_messages:
        candidate = {
            "failure_id": item.failure_id,
            "message": item.message,
        }
        if _json_size([*included_messages, candidate]) > _FIRST_ROUND_SECTION_BYTES:
            messages_truncated = True
            continue
        included_messages.append(candidate)

    included_message_ids = {
        item["failure_id"] for item in included_messages
    }
    refs_by_case: OrderedDict[str, list[str]] = OrderedDict()
    for failure in source_messages:
        if failure.failure_id not in included_message_ids:
            continue
        for case_id in failure.case_ids:
            refs_by_case.setdefault(case_id, []).append(failure.failure_id)

    cases_by_id = {case.case_id: case for case in report.cases}
    test_inputs: list[dict[str, Any]] = []
    inputs_truncated = False
    for case_id, failure_ids in refs_by_case.items():
        case = cases_by_id.get(case_id)
        if case is None:
            continue
        projected, projection_truncated = _project_case_values(
            case_id=case_id,
            failure_ids=failure_ids,
            generated_values=case.generated_test_case.generated_values,
            omitted_input_node_ids=case.generated_test_case.omitted_input_node_ids,
            existing=test_inputs,
        )
        if projected is None:
            inputs_truncated = True
            continue
        test_inputs.append(projected)
        inputs_truncated = inputs_truncated or projection_truncated

    context = {
        "failure_messages": included_messages,
        "test_inputs": test_inputs,
        "context_truncated": messages_truncated or inputs_truncated,
        "failure_message_count": len(source_messages),
        "included_failure_message_count": len(included_messages),
        "failed_case_count": len(refs_by_case),
        "included_failed_case_count": len(test_inputs),
    }
    while _json_size(context) > MAX_FIRST_ROUND_USER_BYTES:
        context["context_truncated"] = True
        if context["test_inputs"]:
            context["test_inputs"].pop()
            context["included_failed_case_count"] -= 1
        elif context["failure_messages"]:
            removed = context["failure_messages"].pop()
            context["included_failure_message_count"] -= 1
            removed_id = removed["failure_id"]
            for item in context["test_inputs"]:
                item["failure_message_ids"] = [
                    ref
                    for ref in item["failure_message_ids"]
                    if ref != removed_id
                ]
        else:
            break
    return context


def _project_case_values(
    *,
    case_id: str,
    failure_ids: list[str],
    generated_values,
    omitted_input_node_ids: list[str],
    existing: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, bool]:
    base: dict[str, Any] = {
        "case_id": case_id,
        "failure_message_ids": failure_ids,
        "values": {},
        "omitted_input_node_ids": [],
    }
    if _json_size([*existing, base]) > _FIRST_ROUND_SECTION_BYTES:
        return None, True
    truncated = False
    grouped: OrderedDict[str, list[Any]] = OrderedDict()
    for item in generated_values:
        grouped.setdefault(item.input_node_id, []).append(item.value)
    for node_id, values in grouped.items():
        value: Any = values[0] if len(values) == 1 else values
        candidate = {
            **base,
            "values": {**base["values"], node_id: value},
        }
        if _json_size([*existing, candidate]) > _FIRST_ROUND_SECTION_BYTES:
            truncated = True
            continue
        base = candidate
    for node_id in omitted_input_node_ids:
        candidate = {
            **base,
            "omitted_input_node_ids": [
                *base["omitted_input_node_ids"],
                node_id,
            ],
        }
        if _json_size([*existing, candidate]) > _FIRST_ROUND_SECTION_BYTES:
            truncated = True
            continue
        base = candidate
    return base, truncated


def _diagnosis_errors(
    draft: ParameterDiagnosis,
    *,
    known_nodes: set[str],
    allowed_evidence: set[str],
) -> list[str]:
    errors: list[str] = []
    ids = [item.input_node_id for item in draft.suspects]
    duplicates = sorted(
        node_id for node_id in set(ids) if ids.count(node_id) > 1
    )
    if duplicates:
        errors.append(f"suspect input_node_ids cannot repeat: {duplicates}")
    for suspect in draft.suspects:
        if suspect.input_node_id not in known_nodes:
            errors.append(f"unknown input_node_id: {suspect.input_node_id}")
        unknown_refs = sorted(set(suspect.evidence_refs) - allowed_evidence)
        if unknown_refs:
            errors.append(
                f"{suspect.input_node_id}: unknown evidence refs {unknown_refs}"
            )
    return errors


def _generator_patch_errors(
    draft: GeneratorPatchDraft,
    *,
    suspect_ids: set[str],
    current_generators: list[InputGeneratorConfig],
) -> list[str]:
    del current_generators
    ids = [item.input_node_id for item in draft.updates]
    errors: list[str] = []
    unknown = sorted(set(ids) - suspect_ids)
    if unknown:
        errors.append(f"updates must target suspect input_node_ids only: {unknown}")
    duplicates = sorted(
        node_id for node_id in set(ids) if ids.count(node_id) > 1
    )
    if duplicates:
        errors.append(f"generator updates cannot repeat nodes: {duplicates}")
    return errors


def _parameter_diagnosis_instructions() -> str:
    return (
        "Identify which supplied input_node_ids may explain the supplied failure "
        "messages. Use only concrete test values, omitted IDs, and the temporary "
        "failure/case references. Do not propose generators, schemas, requests, "
        "or tools. Return no_parameter_issue=true only when no supplied input is "
        "a plausible cause. Keep reasons concise and do not include chain of thought."
    )


def _generator_patch_instructions() -> str:
    return (
        "Convert the supplied diagnosis into InputGeneratorPatch updates. Use "
        "only supplied current_generators and only their input_node_ids. Do not "
        "request failure messages, test values, schemas, operations, or tools. "
        "Use resource_identifier with a canonical resource name when the input "
        "must reuse a resource ID, or response_value with a stable value_name "
        "when the input should reuse a monitored response value. These reference "
        "generators are resolved from persistent pools before a batch is sent. "
        "Return complete structured updates without explanations or chain of thought."
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_size(value: Any) -> int:
    return len(_json(value).encode("utf-8"))
