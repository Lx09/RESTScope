"""LangGraph runtime for autonomous investigation of an App-bound OpenAPI IR."""

from __future__ import annotations

import json
import math
import time
from copy import deepcopy
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from restscope.llm import (
    LLMClient,
    LLMMessage,
    LLMModelConfig,
    LLMRequest,
    LLMResponse,
    OutputValidator,
    ToolCall,
)
from restscope.openapi_parser import OpenAPISpecIR

from .investigation import OpenAPIRetrievalQueryError, OpenAPIInvestigationTools, OpenAPIInvestigationWorkspace
from .schemas import (
    OpenAPIRetrievalDraft,
    OpenAPIRetrievalRequest,
    OpenAPIRetrievalResult,
    InvestigationAction,
    InvestigationSummary,
)
from .skills import ParameterValueProducerSkill


# Investigation and final summarization share one deadline. The reserve keeps a
# slow search loop from consuming the time needed for the required final JSON.
MAX_INTERNAL_TOOL_CALLS = 20
MAX_TOOL_RESULT_BYTES = 200 * 1024
MAX_INVESTIGATION_SECONDS = 120.0
FORCED_SUMMARY_RESERVE_SECONDS = 30.0


class OpenAPIRetrievalOutputError(RuntimeError):
    """Raised when the Subagent cannot produce a trustworthy result."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class OpenAPIRetrievalState(TypedDict, total=False):
    """Ephemeral LangGraph state for one retrieval request.

    ``messages``/``response`` are the model conversation, ``draft`` is the
    parsed but not-yet-trusted conclusion, and the counters/actions describe
    only resources consumed during this request. The App-level IR is captured
    by node closures and intentionally never enters graph state.
    """

    messages: list[dict[str, Any]]
    response: dict[str, Any]
    draft: dict[str, Any]
    validation_errors: list[str]
    tool_calls: int
    tool_result_bytes: int
    actions: list[dict[str, Any]]
    repair_count: int
    forced_summary: bool
    budget_limitation: str
    final_result: dict[str, Any]


class OpenAPIRetrievalAgent:
    """Investigate one already parsed OpenAPI IR through a bounded tool loop."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        validator: OutputValidator | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        self.client = client
        self.model = model
        self.validator = validator or OutputValidator()
        self.clock = clock

    def retrieve(
        self,
        request: OpenAPIRetrievalRequest,
        *,
        ir: OpenAPISpecIR,
    ) -> OpenAPIRetrievalResult:
        self._check_configured()
        # Workspace and tools are request-scoped views over the shared App IR.
        # They carry deterministic lookups and evidence, not another IR copy.
        workspace = OpenAPIInvestigationWorkspace(ir=ir)
        # Resolve caller-controlled identifiers before involving the model. This
        # guarantees that every investigation starts from a real consumer and a
        # parameter actually declared by that operation.
        consumer = workspace.find_operation(
            method=request.query.consumer_method,
            path=request.query.consumer_path,
        )
        target_parameter = workspace.find_target_parameter(consumer, request.query.parameter_name)
        tools = OpenAPIInvestigationTools(workspace)
        started = self.clock()
        graph = self._build_graph(
            request=request,
            workspace=workspace,
            tools=tools,
            consumer=consumer,
            target_parameter=target_parameter,
            started=started,
        )
        initial_messages = [
            LLMMessage(role="system", content=ParameterValueProducerSkill.instructions()),
            LLMMessage(
                role="user",
                content=json.dumps(
                    {
                        "objective": request.query.objective,
                        "consumer_operation": consumer.model_dump(mode="json"),
                        "target_parameter": target_parameter.model_dump(mode="json"),
                        "candidate_limit": request.query.limit,
                        "instruction": "Investigate autonomously and return a structured conclusion.",
                    },
                    ensure_ascii=False,
                ),
            ),
        ]
        final_state = graph.invoke(
            {
                "messages": [message.model_dump(mode="json") for message in initial_messages],
                "tool_calls": 0,
                "tool_result_bytes": 0,
                "actions": [],
                "repair_count": 0,
                "forced_summary": False,
            },
            config={"recursion_limit": 100},
        )
        return OpenAPIRetrievalResult.model_validate(final_state["final_result"])

    def _build_graph(
        self,
        *,
        request: OpenAPIRetrievalRequest,
        workspace: OpenAPIInvestigationWorkspace,
        tools: OpenAPIInvestigationTools,
        consumer,
        target_parameter,
        started: float,
    ):
        # ``workspace`` and ``tools`` are captured by these node closures rather
        # than serialized into OpenAPIRetrievalState.
        graph = StateGraph(OpenAPIRetrievalState)
        graph.add_node("ask_model", self._ask_model(tools=tools, started=started))
        graph.add_node("execute_tools", self._execute_tools(tools=tools, started=started))
        graph.add_node("force_summary", self._force_summary(started=started))
        graph.add_node("validate_output", self._validate_output(workspace=workspace, tools=tools, consumer=consumer, limit=request.query.limit))
        graph.add_node("repair_output", self._repair_output(started=started))
        graph.add_node(
            "finalize",
            self._finalize(
                request=request,
                workspace=workspace,
                tools=tools,
                consumer=consumer,
                target_parameter=target_parameter,
                started=started,
            ),
        )
        graph.add_node("fail", self._fail)

        graph.add_edge(START, "ask_model")
        graph.add_conditional_edges(
            "ask_model",
            self._route_after_model,
            {"tools": "execute_tools", "validate": "validate_output", "force": "force_summary"},
        )
        graph.add_conditional_edges(
            "execute_tools",
            self._route_after_tools,
            {"continue": "ask_model", "force": "force_summary"},
        )
        graph.add_edge("force_summary", "validate_output")
        graph.add_conditional_edges(
            "validate_output",
            self._route_after_validation,
            {"finalize": "finalize", "repair": "repair_output", "fail": "fail"},
        )
        graph.add_edge("repair_output", "validate_output")
        graph.add_edge("finalize", END)
        graph.add_edge("fail", END)
        return graph.compile()

    def _ask_model(self, *, tools: OpenAPIInvestigationTools, started: float):
        def node(state: OpenAPIRetrievalState) -> OpenAPIRetrievalState:
            remaining_seconds = (
                MAX_INVESTIGATION_SECONDS
                - FORCED_SUMMARY_RESERVE_SECONDS
                - (self.clock() - started)
            )
            if remaining_seconds < 1:
                response = LLMResponse(provider=self.model.provider, model=self.model.model)
                return {
                    "response": response.model_dump(mode="json"),
                    "budget_limitation": "Investigation time budget exhausted.",
                }
            response = self.client.invoke(
                self._request(
                    messages=state["messages"],
                    tools=tools.specs(),
                    tool_choice="auto",
                    timeout_seconds=math.floor(remaining_seconds),
                )
            )
            return {"response": response.model_dump(mode="json")}

        return node

    def _execute_tools(self, *, tools: OpenAPIInvestigationTools, started: float):
        def node(state: OpenAPIRetrievalState) -> OpenAPIRetrievalState:
            response = LLMResponse.model_validate(state["response"])
            remaining = MAX_INTERNAL_TOOL_CALLS - int(state.get("tool_calls", 0))
            # A provider may return several calls at once; execute only the
            # prefix that still fits the global request budget.
            selected_calls = response.tool_calls[: max(0, remaining)]
            messages = [LLMMessage.model_validate(item) for item in state["messages"]]
            messages.append(
                LLMMessage(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=selected_calls,
                )
            )
            calls = int(state.get("tool_calls", 0))
            byte_count = int(state.get("tool_result_bytes", 0))
            actions = list(state.get("actions", []))
            limitation = ""
            for call in selected_calls:
                if self.clock() - started >= (
                    MAX_INVESTIGATION_SECONDS - FORCED_SUMMARY_RESERVE_SECONDS
                ):
                    limitation = "Investigation time budget exhausted."
                if limitation:
                    messages.append(
                        LLMMessage(
                            role="tool",
                            name=call.name,
                            tool_call_id=call.id,
                            content=json.dumps({"error": "not_executed_due_to_budget"}),
                        )
                    )
                    continue
                calls += 1
                try:
                    payload = tools.execute(call.name, call.arguments)
                except Exception as exc:
                    payload = {
                        "error": {
                            "type": type(exc).__name__,
                            "code": getattr(exc, "code", "internal_tool_failed"),
                            "message": str(exc),
                        }
                    }
                content = json.dumps(payload, ensure_ascii=False, default=str)
                encoded_size = len(content.encode("utf-8"))
                if byte_count + encoded_size > MAX_TOOL_RESULT_BYTES:
                    limitation = "Investigation tool-result byte budget exhausted."
                    content = json.dumps({"error": "tool_result_byte_budget_exhausted"})
                    byte_count = MAX_TOOL_RESULT_BYTES
                else:
                    byte_count += encoded_size
                messages.append(
                    LLMMessage(
                        role="tool",
                        name=call.name,
                        tool_call_id=call.id,
                        content=content,
                    )
                )
                actions.append(
                    InvestigationAction(
                        tool=call.name,
                        summary=_action_summary(call),
                        result_count=_result_count(payload),
                    ).model_dump(mode="json")
                )
                if self.clock() - started >= MAX_INVESTIGATION_SECONDS:
                    limitation = "Investigation time budget exhausted."
            if not limitation and calls >= MAX_INTERNAL_TOOL_CALLS:
                limitation = "Investigation tool-call budget exhausted."
            return {
                "messages": [message.model_dump(mode="json") for message in messages],
                "tool_calls": calls,
                "tool_result_bytes": byte_count,
                "actions": actions,
                "budget_limitation": limitation,
            }

        return node

    def _force_summary(self, *, started: float):
        def node(state: OpenAPIRetrievalState) -> OpenAPIRetrievalState:
            remaining_seconds = MAX_INVESTIGATION_SECONDS - (self.clock() - started)
            if remaining_seconds < 1:
                raise OpenAPIRetrievalOutputError(
                    "openapi_retrieval_time_budget_exhausted",
                    "No time remained for the required tool-free summary.",
                )
            messages = [LLMMessage.model_validate(item) for item in state["messages"]]
            limitation = state.get("budget_limitation") or "Investigation budget exhausted."
            messages.append(
                LLMMessage(
                    role="user",
                    content=(
                        f"{limitation} Do not call more tools. Return the best supported structured conclusion now, "
                        "including this limitation."
                    ),
                )
            )
            response = self.client.invoke(
                self._request(
                    messages=[message.model_dump(mode="json") for message in messages],
                    tools=[],
                    tool_choice="none",
                    timeout_seconds=math.floor(remaining_seconds),
                )
            )
            return {
                "messages": [message.model_dump(mode="json") for message in messages],
                "response": response.model_dump(mode="json"),
                "forced_summary": True,
                "repair_count": 1,
            }

        return node

    def _validate_output(self, *, workspace, tools, consumer, limit: int):
        def node(state: OpenAPIRetrievalState) -> OpenAPIRetrievalState:
            response = LLMResponse.model_validate(state["response"])
            validation = self.validator.validate(response=response, output_model=OpenAPIRetrievalDraft)
            errors = [issue.message for issue in validation.errors]
            draft = validation.validated_object if validation.valid else None
            if draft is not None:
                # Schema validation checks shape. Semantic validation below
                # checks that operations and evidence actually came from this IR
                # and that evidence belongs to the candidate citing it.
                evidence_by_id = {item.id: item for item in tools.evidence()}
                errors.extend(
                    _semantic_errors(
                        draft=draft,
                        workspace=workspace,
                        evidence_by_id=evidence_by_id,
                        consumer=consumer,
                        limit=limit,
                    )
                )
            if errors:
                return {"draft": {}, "validation_errors": errors}
            return {"draft": draft.model_dump(mode="json"), "validation_errors": []}

        return node

    def _repair_output(self, *, started: float):
        def node(state: OpenAPIRetrievalState) -> OpenAPIRetrievalState:
            remaining_seconds = MAX_INVESTIGATION_SECONDS - (self.clock() - started)
            if remaining_seconds < 1:
                raise OpenAPIRetrievalOutputError(
                    "openapi_retrieval_time_budget_exhausted",
                    "No time remained for the output repair call.",
                )
            messages = [LLMMessage.model_validate(item) for item in state["messages"]]
            response = LLMResponse.model_validate(state["response"])
            if response.content:
                messages.append(LLMMessage(role="assistant", content=response.content))
            messages.append(
                LLMMessage(
                    role="user",
                    content=(
                        "The structured conclusion was invalid. Repair it once without calling tools. Errors: "
                        + json.dumps(state.get("validation_errors", []), ensure_ascii=False)
                    ),
                )
            )
            repaired = self.client.invoke(
                self._request(
                    messages=[message.model_dump(mode="json") for message in messages],
                    tools=[],
                    tool_choice="none",
                    timeout_seconds=math.floor(remaining_seconds),
                )
            )
            return {
                "messages": [message.model_dump(mode="json") for message in messages],
                "response": repaired.model_dump(mode="json"),
                "repair_count": int(state.get("repair_count", 0)) + 1,
            }

        return node

    def _finalize(self, *, request, workspace, tools, consumer, target_parameter, started: float):
        def node(state: OpenAPIRetrievalState) -> OpenAPIRetrievalState:
            draft = OpenAPIRetrievalDraft.model_validate(state["draft"])
            limitations = list(draft.limitations)
            budget_limitation = state.get("budget_limitation")
            if budget_limitation and budget_limitation not in limitations:
                limitations.append(budget_limitation)
            warnings = list(draft.warnings)
            if len(target_parameter.matches) > 1:
                warnings.append("Target parameter matched multiple request locations or fields.")
            # Trusted runtime values are attached here; the model never chooses
            # the consumer, target parameter, evidence collection, or counters.
            result = OpenAPIRetrievalResult(
                objective=request.query.objective,
                status=draft.status,
                consumer_operation=consumer,
                target_parameter=target_parameter,
                candidates=draft.candidates,
                evidence=tools.evidence(),
                conflicts=draft.conflicts,
                investigation_summary=InvestigationSummary(
                    tool_calls=int(state.get("tool_calls", 0)),
                    tool_result_bytes=int(state.get("tool_result_bytes", 0)),
                    elapsed_ms=max(0, int((self.clock() - started) * 1000)),
                    actions=[InvestigationAction.model_validate(item) for item in state.get("actions", [])],
                    evidence_sufficient=draft.evidence_sufficient,
                    limitations=limitations,
                ),
                warnings=warnings,
            )
            return {"final_result": result.model_dump(mode="json")}

        return node

    @staticmethod
    def _route_after_model(state: OpenAPIRetrievalState) -> str:
        if state.get("budget_limitation"):
            return "force"
        response = LLMResponse.model_validate(state["response"])
        if not response.tool_calls:
            return "validate"
        if int(state.get("tool_calls", 0)) >= MAX_INTERNAL_TOOL_CALLS:
            return "force"
        return "tools"

    @staticmethod
    def _route_after_tools(state: OpenAPIRetrievalState) -> str:
        return "force" if state.get("budget_limitation") else "continue"

    @staticmethod
    def _route_after_validation(state: OpenAPIRetrievalState) -> str:
        if not state.get("validation_errors"):
            return "finalize"
        return "repair" if int(state.get("repair_count", 0)) < 1 else "fail"

    @staticmethod
    def _fail(state: OpenAPIRetrievalState) -> OpenAPIRetrievalState:
        details = "; ".join(state.get("validation_errors", []))
        raise OpenAPIRetrievalOutputError(
            "openapi_retrieval_output_invalid",
            f"OpenAPI Retrieval Subagent output remained invalid: {details}",
        )

    def _request(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list,
        tool_choice: str,
        timeout_seconds: int | None = None,
    ) -> LLMRequest:
        return LLMRequest(
            provider=self.model.provider,
            model=self.model.model,
            messages=[LLMMessage.model_validate(message) for message in messages],
            temperature=0.0,
            max_tokens=self.model.max_tokens,
            response_format="json_schema",
            json_schema=_strict_output_schema(OpenAPIRetrievalDraft.model_json_schema()),
            json_schema_name="OpenAPIRetrievalDraft",
            tools=tools,
            tool_choice=tool_choice,
            timeout_seconds=min(
                self.model.timeout_seconds,
                timeout_seconds or int(MAX_INVESTIGATION_SECONDS),
            ),
            metadata={
                "role": "openapi_retrieval",
                "objective": ParameterValueProducerSkill.objective,
                "disable_retry": True,
            },
        )

    def _check_configured(self) -> None:
        if not self.model.enabled or not self.model.provider or not self.model.model:
            raise OpenAPIRetrievalOutputError("openapi_retrieval_not_configured", "Thinking model is not configured")
        registry = getattr(self.client, "registry", None)
        if registry is not None:
            try:
                registry.get(self.model.provider)
            except Exception as exc:
                raise OpenAPIRetrievalOutputError(
                    "openapi_retrieval_not_configured",
                    f"Thinking model provider is not configured: {self.model.provider}",
                ) from exc


def _semantic_errors(*, draft, workspace, evidence_by_id: dict, consumer, limit: int) -> list[str]:
    """Reject plausible-looking conclusions that are not grounded in this IR."""

    errors: list[str] = []
    if len(draft.candidates) > limit:
        errors.append(f"Candidate count exceeds requested limit {limit}")
    if draft.status == "found" and not draft.candidates:
        errors.append("status=found requires at least one candidate")
    if draft.status == "found" and not draft.evidence_sufficient:
        errors.append("status=found requires evidence_sufficient=true")
    if draft.status == "not_found" and draft.candidates:
        errors.append("status=not_found cannot include candidates")
    if draft.status == "not_found" and not draft.evidence_sufficient:
        errors.append("status=not_found requires evidence_sufficient=true")
    if draft.status == "insufficient_evidence" and draft.evidence_sufficient:
        errors.append("insufficient_evidence requires evidence_sufficient=false")
    if draft.status == "insufficient_evidence" and not draft.limitations:
        errors.append("insufficient_evidence requires at least one limitation")
    for candidate in draft.candidates:
        try:
            workspace.operation(candidate.operation)
        except OpenAPIRetrievalQueryError:
            errors.append(
                f"Candidate operation does not exist: {candidate.operation.method} {candidate.operation.path}"
            )
        if candidate.operation.identity() == consumer.identity():
            errors.append("Consumer operation cannot be its own producer candidate")
        if not candidate.evidence_refs:
            errors.append(
                f"Candidate {candidate.operation.method} {candidate.operation.path} has no evidence references"
            )
        candidate_evidence = []
        for evidence_ref in candidate.evidence_refs:
            evidence = evidence_by_id.get(evidence_ref)
            if evidence is None:
                errors.append(f"Unknown evidence reference: {evidence_ref}")
            elif (
                evidence.operation is not None
                and evidence.operation.identity() != candidate.operation.identity()
            ):
                errors.append(
                    f"Evidence {evidence_ref} does not belong to candidate operation "
                    f"{candidate.operation.method} {candidate.operation.path}"
                )
            else:
                candidate_evidence.append(evidence)
        if not any(evidence.operation is not None for evidence in candidate_evidence):
            errors.append(
                f"Candidate {candidate.operation.method} {candidate.operation.path} "
                "requires operation-bound evidence"
            )
        if not candidate.value_locations:
            errors.append(
                f"Candidate {candidate.operation.method} {candidate.operation.path} has no value locations"
            )
        supported_locations = {evidence.location for evidence in candidate_evidence}
        for value_location in candidate.value_locations:
            if value_location not in supported_locations:
                errors.append(f"Value location is not supported by cited evidence: {value_location}")
    for conflict in draft.conflicts:
        for evidence_ref in conflict.evidence_refs:
            if evidence_ref not in evidence_by_id:
                errors.append(f"Unknown conflict evidence reference: {evidence_ref}")
    return errors


def _action_summary(call: ToolCall) -> str:
    return json.dumps(call.arguments, ensure_ascii=False, sort_keys=True, default=str)[:500]


def _result_count(payload: dict[str, Any]) -> int:
    for key in ("results", "evidence"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return 1 if payload else 0


def _strict_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic schema valid for strict OpenAI structured output."""

    output = deepcopy(schema)

    def normalize(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties)
                node["additionalProperties"] = False
            for child in node.values():
                normalize(child)
        elif isinstance(node, list):
            for child in node:
                normalize(child)

    normalize(output)
    return output
