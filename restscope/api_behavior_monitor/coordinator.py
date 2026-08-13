"""Coordinate Contract checks, factual observations, and resource derivation.

Every matched target response is first stored as an Observation in its own
transaction, then reaches the current Contract and Resource Monitors.
Only a complete valid 2xx JSON body may then enter resource learning. Each
boundary fails independently so an advisory monitor cannot replace the
target's real HTTP or transport result.
"""

from __future__ import annotations

from typing import Literal

from restscope.data_types import JSONValue
from restscope.target_api.media_type import normalize_media_type
from restscope.openapi_parser import (
    OpenAPIOperationMatchError,
    OpenAPISpecIR,
    match_operation,
    build_openapi_document,
)
from restscope.openapi_parser.ir import OperationIR
from restscope.observability import TracingRuntime
from restscope.target_api import (
    TargetResponseObservation,
    TargetResponseOperationContext,
    TargetTransportObservation,
)

from .catalog import (
    ObservationWrite,
    OperationDefinition,
    ResourceDerivationResult,
    APIBehaviorCatalog,
    OracleAssessment,
)
from .contract_monitor import (
    ContractCheckResult,
    ResponseContractTracker,
)
from .contract_validation import (
    ContractValidator,
    ResponseEvidence,
    decode_response_evidence,
)
from .oracle import BugOracle, OraclePrimaryDecision
from .pipeline import (
    BASELINE_CONTRACT_VALIDATION,
    CURRENT_CONTRACT_VALIDATION,
    OBSERVATION_ID,
    RESPONSE_EVIDENCE,
    PipelineAnnotations,
)
from .results import APIBehaviorMonitorResult, APIBehaviorWarning
from .resource_monitor import ResourceResponseTracker


class APIBehaviorMonitorError(RuntimeError):
    """Report a response that cannot enter the OpenAPI-owned monitor flow."""

    def __init__(self, code: str, message: str) -> None:
        """Retain a stable processor warning code beside its readable message."""

        super().__init__(message)
        self.code = code


class APIBehaviorMonitorCoordinator:
    """Run the three response stages without sharing their transactions."""

    def __init__(
        self,
        *,
        contract_tracker: ResponseContractTracker,
        catalog: APIBehaviorCatalog,
        resource_tracker: ResourceResponseTracker | None = None,
        tracing_runtime: TracingRuntime | None = None,
        contract_validator: ContractValidator | None = None,
        bug_oracle: BugOracle | None = None,
    ) -> None:
        """Retain App-owned collaborators without opening persistent state."""

        self.contract_tracker = contract_tracker
        self.catalog = catalog
        self.resource_tracker = resource_tracker
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()
        self.contract_validator = contract_validator or ContractValidator()
        self.bug_oracle = bug_oracle

    def observe_response(
        self,
        observation: TargetResponseObservation,
        context: TargetResponseOperationContext,
    ) -> APIBehaviorMonitorResult:
        """Process one response and return a bounded stage-by-stage summary.

        Raises:
            APIBehaviorMonitorError: The supplied context is not an initialized
                OpenAPI IR or the request cannot identify one current operation.
                In either case no durable Monitor row is written.
        """

        if not isinstance(context.ir, OpenAPISpecIR):
            raise APIBehaviorMonitorError(
                "api_behavior_context_invalid",
                "API behavior monitoring requires an initialized OpenAPI IR",
            )
        try:
            operation_ir = _resolve_operation(
                observation,
                context=context,
                ir=context.ir,
            )
        except OpenAPIOperationMatchError as exc:
            raise APIBehaviorMonitorError(exc.code, str(exc)) from exc

        operation = OperationDefinition(
            operation_id=operation_ir.operation_key,
            method=operation_ir.method,
            path=operation_ir.path,
            description=operation_ir.description,
        )
        media_type = normalize_media_type(observation.headers.get("content-type"))
        warnings: list[APIBehaviorWarning] = []

        with self.tracing_runtime.span(
            "APIBehaviorMonitorCoordinator.observe_response",
            kind="CHAIN",
            input_value={
                "operation_id": operation.operation_id,
                "status_code": observation.status_code,
                "media_type": media_type,
            },
        ) as span:
            try:
                # The operation must exist before either observations or
                # OpenAPI change events can reference it.
                self.catalog.ensure_operation(operation)
            except Exception as exc:
                raise APIBehaviorMonitorError(
                    "operation_persistence_failed",
                    "Failed to persist matched operation metadata",
                ) from exc

            evidence = decode_response_evidence(
                status_code=observation.status_code,
                headers=dict(observation.headers),
                body=observation.body,
            )
            annotations = PipelineAnnotations()
            annotations.write("observation", RESPONSE_EVIDENCE, evidence)
            observation_id, parsed_body = self._record_observation(
                observation=observation,
                evidence=evidence,
                replay_of_observation_id=(
                    context.replay_directive.primary_observation_id
                    if context.replay_directive is not None
                    else None
                ),
                context=context,
                operation=operation,
                media_type=media_type,
                warnings=warnings,
            )
            if observation_id is not None:
                annotations.write("observation", OBSERVATION_ID, observation_id)
            current_validation = self.contract_validator.validate(
                document=build_openapi_document(context.ir, list(context.ir.operations)),
                operation_path=operation.path,
                operation_method=operation.method,
                evidence=evidence,
            )
            annotations.write(
                "contract_monitor",
                CURRENT_CONTRACT_VALIDATION,
                current_validation,
            )
            try:
                baseline_document = self.catalog.baseline_openapi()
            except RuntimeError:
                # Pure or legacy-focused tests may intentionally omit Catalog
                # initialization. Production initializes before any Tool call.
                baseline_document = build_openapi_document(
                    context.ir,
                    list(context.ir.operations),
                )
            baseline_validation = self.contract_validator.validate(
                document=baseline_document,
                operation_path=operation.path,
                operation_method=operation.method,
                evidence=evidence,
            )
            annotations.write(
                "oracle",
                BASELINE_CONTRACT_VALIDATION,
                baseline_validation,
            )
            contract = self._check_contract(
                observation=observation,
                evidence=evidence,
                operation=operation,
                media_type=media_type,
                ir=context.ir,
                warnings=warnings,
            )

            resources: ResourceDerivationResult | None = None
            if (
                observation_id is not None
                and parsed_body is not None
                and isinstance(parsed_body, (dict, list))
                and self.resource_tracker is not None
            ):
                try:
                    resources = self.resource_tracker.observe(
                        operation=operation,
                        body=parsed_body,
                    )
                except Exception as exc:
                    warnings.append(
                        APIBehaviorWarning(
                            code="resource_derivation_failed",
                            message="Resource derivation failed",
                            issues=(type(exc).__name__,),
                        )
                    )

            oracle_primary: OraclePrimaryDecision | None = None
            oracle_assessment: OracleAssessment | None = None
            if self.bug_oracle is not None:
                if context.replay_directive is None:
                    oracle_primary = self.bug_oracle.evaluate_primary(
                        primary_observation_id=annotations.read(OBSERVATION_ID),
                        operation_id=operation.operation_id,
                        status_code=observation.status_code,
                        input_validity=context.input_validity,
                        validity_provenance=context.validity_provenance,
                        baseline_validation=(
                            annotations.read(BASELINE_CONTRACT_VALIDATION)
                            or baseline_validation
                        ),
                    )
                    oracle_assessment = oracle_primary.assessment
                    persistence_failed = oracle_primary.persistence_failed
                elif isinstance(
                    context.replay_directive.state,
                    OraclePrimaryDecision,
                ):
                    finalization = self.bug_oracle.evaluate_replay(
                        primary=context.replay_directive.state,
                        replay_observation_id=observation_id,
                        status_code=observation.status_code,
                        baseline_validation=baseline_validation,
                        replay_persisted=observation_id is not None,
                    )
                    oracle_assessment = finalization.assessment
                    persistence_failed = finalization.persistence_failed
                else:
                    persistence_failed = False
                if persistence_failed:
                    warnings.append(
                        APIBehaviorWarning(
                            code="oracle_assessment_persistence_failed",
                            message="Bug Oracle Assessment could not be persisted",
                        )
                    )

            result = APIBehaviorMonitorResult(
                operation_id=operation.operation_id,
                contract=contract,
                observation_id=observation_id,
                resources=resources,
                warnings=tuple(warnings),
                current_validation=current_validation,
                baseline_validation=baseline_validation,
                oracle_primary=oracle_primary,
                oracle_assessment=oracle_assessment,
            )
            span.set_output(
                {
                    "operation_id": result.operation_id,
                    "observation_recorded": result.observation_id is not None,
                    "warning_count": len(result.warnings),
                }
            )
            return result

    def _check_contract(
        self,
        *,
        observation: TargetResponseObservation,
        evidence: ResponseEvidence,
        operation: OperationDefinition,
        media_type: str | None,
        ir: OpenAPISpecIR,
        warnings: list[APIBehaviorWarning],
    ) -> ContractCheckResult | None:
        """Check every response while isolating Contract Monitor failures."""

        try:
            result = self.contract_tracker.observe(
                ir=ir,
                operation_key=operation.operation_id,
                status_code=observation.status_code,
                media_type=media_type,
                body=observation.body,
                body_truncated=observation.body_truncated,
                evidence=evidence,
            )
        except Exception as exc:
            warnings.append(
                APIBehaviorWarning(
                    code="response_contract_check_failed",
                    message="Response contract checking failed",
                    issues=(type(exc).__name__,),
                )
            )
            return None
        if result.status == "pending_retry":
            warnings.append(
                APIBehaviorWarning(
                    code="response_contract_pending_retry",
                    message=(
                        "Response contract could not be checked because its "
                        "declared JSON body was invalid or incomplete"
                    ),
                )
            )
        return result

    def _record_observation(
        self,
        *,
        observation: TargetResponseObservation,
        evidence: ResponseEvidence,
        replay_of_observation_id: str | None,
        context: TargetResponseOperationContext,
        operation: OperationDefinition,
        media_type: str | None,
        warnings: list[APIBehaviorWarning],
    ) -> tuple[str | None, JSONValue | None]:
        """Store every HTTP response and return eligible 2xx JSON for learning."""

        body_format: Literal["json", "text", "base64"] = (
            "json" if evidence.body_kind == "json"
            else "base64" if evidence.body_kind == "binary"
            else "text"
        )
        parsed = evidence.json_value
        if context.replay_directive is not None and replay_of_observation_id is None:
            warnings.append(
                APIBehaviorWarning(
                    code="replay_observation_not_persisted",
                    message=(
                        "Replay observation cannot be related because its Primary "
                        "observation was not persisted"
                    ),
                )
            )
            return None, None
        try:
            record = self.catalog.record_observation(
                ObservationWrite(
                    operation_id=operation.operation_id,
                    timestamp=observation.received_at,
                    outcome_kind="http",
                    status_code=observation.status_code,
                    reason_phrase=observation.reason_phrase or None,
                    media_type=media_type,
                    request_json=observation.request_json,
                    response_headers=dict(observation.headers),
                    response_body=observation.body,
                    body_format=body_format,
                    abstract_test_case_id=context.abstract_test_case_id,
                    batch_id=context.batch_id,
                    batch_case_index=context.batch_case_index,
                    replay_of_observation_id=replay_of_observation_id,
                )
            )
        except Exception as exc:
            warnings.append(
                APIBehaviorWarning(
                    code="response_observation_persistence_failed",
                    message="HTTP observation could not be persisted",
                    issues=(type(exc).__name__,),
                )
            )
            return None, None
        eligible = (
            200 <= observation.status_code <= 299
            and not observation.body_truncated
            and evidence.body_kind == "json"
        )
        return record.observation_id, parsed if eligible else None

    def observe_transport(
        self,
        observation: TargetTransportObservation,
        context: TargetResponseOperationContext,
    ) -> APIBehaviorMonitorResult:
        """Persist one matched request attempt that received no HTTP response."""

        if not isinstance(context.ir, OpenAPISpecIR):
            raise APIBehaviorMonitorError(
                "api_behavior_context_invalid",
                "API behavior monitoring requires an initialized OpenAPI IR",
            )
        operation_ir = _resolve_transport_operation(
            observation,
            context=context,
            ir=context.ir,
        )
        operation = OperationDefinition(
            operation_id=operation_ir.operation_key,
            method=operation_ir.method,
            path=operation_ir.path,
            description=operation_ir.description,
        )
        self.catalog.ensure_operation(operation)
        if (
            context.replay_directive is not None
            and context.replay_directive.primary_observation_id is None
        ):
            if (
                self.bug_oracle is not None
                and isinstance(context.replay_directive.state, OraclePrimaryDecision)
            ):
                self.bug_oracle.replay_failed(
                    primary=context.replay_directive.state,
                    replay_observation_id=None,
                    error=observation.code,
                    replay_persisted=False,
                )
            return APIBehaviorMonitorResult(
                operation_id=operation.operation_id,
                contract=None,
                observation_id=None,
                warnings=(
                    APIBehaviorWarning(
                        code="replay_observation_not_persisted",
                        message=(
                            "Replay transport failure cannot be related because "
                            "its Primary observation was not persisted"
                        ),
                    ),
                ),
            )
        try:
            record = self.catalog.record_observation(
                ObservationWrite(
                operation_id=operation.operation_id,
                timestamp=observation.occurred_at,
                outcome_kind="transport",
                request_json=observation.request_json,
                transport_code=observation.code,
                transport_message=observation.message,
                abstract_test_case_id=context.abstract_test_case_id,
                batch_id=context.batch_id,
                batch_case_index=context.batch_case_index,
                replay_of_observation_id=(
                    context.replay_directive.primary_observation_id
                    if context.replay_directive is not None
                    else None
                ),
                )
            )
        except Exception as exc:
            if (
                self.bug_oracle is not None
                and context.replay_directive is not None
                and isinstance(context.replay_directive.state, OraclePrimaryDecision)
            ):
                self.bug_oracle.replay_failed(
                    primary=context.replay_directive.state,
                    replay_observation_id=None,
                    error=observation.code,
                    replay_persisted=False,
                )
            raise APIBehaviorMonitorError(
                "transport_observation_persistence_failed",
                "Transport observation could not be persisted",
            ) from exc
        if (
            self.bug_oracle is not None
            and context.replay_directive is not None
            and isinstance(context.replay_directive.state, OraclePrimaryDecision)
        ):
            finalization = self.bug_oracle.replay_failed(
                primary=context.replay_directive.state,
                replay_observation_id=record.observation_id,
                error=observation.code,
                replay_persisted=True,
            )
            warning = (
                APIBehaviorWarning(
                    code="oracle_assessment_persistence_failed",
                    message="Bug Oracle Assessment could not be persisted",
                )
                if finalization.persistence_failed
                else None
            )
        else:
            finalization = None
            warning = None
        return APIBehaviorMonitorResult(
            operation_id=operation.operation_id,
            contract=None,
            observation_id=record.observation_id,
            oracle_assessment=(
                finalization.assessment if finalization is not None else None
            ),
            warnings=(warning,) if warning is not None else (),
        )


def _resolve_operation(
    observation: TargetResponseObservation,
    *,
    context: TargetResponseOperationContext,
    ir: OpenAPISpecIR,
) -> OperationIR:
    """Resolve a generated operation key or match an ordinary concrete request."""

    if context.operation_key is None:
        return match_operation(ir, method=observation.method, path=observation.path)
    operation = ir.operations.get(context.operation_key)
    if operation is None:
        raise OpenAPIOperationMatchError(
            "operation_not_found",
            f"Operation {context.operation_key!r} is not present in the current IR",
        )
    return operation


def _resolve_transport_operation(
    observation: TargetTransportObservation,
    *,
    context: TargetResponseOperationContext,
    ir: OpenAPISpecIR,
) -> OperationIR:
    """Resolve one failed request using the same operation identity rules."""

    if context.operation_key is None:
        return match_operation(ir, method=observation.method, path=observation.path)
    operation = ir.operations.get(context.operation_key)
    if operation is None:
        raise OpenAPIOperationMatchError(
            "operation_not_found",
            f"Operation {context.operation_key!r} is not present in the current IR",
        )
    return operation
