"""Run one Profile-authorized model conversation through a fixed Interface.

The Harness is the only production constructor. It supplies an already-resolved
model, toolbox, and private Prompt Session. This Module owns the one-Tool-or-
final model loop, local output validation, and lifecycle results; prompt roles,
Context projection, fixed protocols, and compaction assembly remain cohesive in
the Prompt Session. A Main Agent may start one taskless App-lifetime loop;
task-scoped callers and Subagents continue to use the bounded task protocol.
"""

from __future__ import annotations

from collections.abc import Callable
from threading import Event
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from restscope.llm import LLMClient, OutputValidator

from .contracts import (
    AgentCompletion,
    AgentError,
    AgentResult,
    AgentTask,
    AgentUsage,
    SystemAgentResult,
    SystemAgentTask,
)
from .ports import AgentToolExecutor, AgentTreeControlPort
from .profile import AgentProfile
from .prompt import AgentPromptSession, PromptSessionError

_HARNESS_CONSTRUCTION_TOKEN = object()
AgentLifecycle = Literal["main", "subagent", "system"]
AgentLoopResult = AgentResult | SystemAgentResult


class Agent:
    """Execute one authorized Profile without assembling model prompts."""

    def __init__(
        self,
        *,
        profile: AgentProfile,
        client: LLMClient,
        toolbox: AgentToolExecutor,
        prompt_session: AgentPromptSession | None = None,
        session_id: str | None = None,
        tree_control: AgentTreeControlPort | None = None,
        cancel_event: Event | None = None,
        lifecycle: AgentLifecycle = "main",
        output_model: type[BaseModel] = AgentCompletion,
        validate_output: Callable[[BaseModel], tuple[str, ...]] | None = None,
        depth: int = 0,
        parent_session_id: str | None = None,
        _construction_token: object | None = None,
    ) -> None:
        """Store resolved dependencies without allowing later permission changes."""
        if _construction_token is not _HARNESS_CONSTRUCTION_TOKEN:
            raise RuntimeError("Agent must be constructed by HarnessRuntime")
        self.profile = profile
        self.client = client
        self.toolbox = toolbox
        if prompt_session is None:
            raise RuntimeError("HarnessRuntime must provide an Agent Prompt Session")
        self.prompt_session = prompt_session
        self.session_id = session_id or f"agent_{uuid4().hex}"
        self.tree_control = tree_control
        self.cancel_event = cancel_event or Event()
        self.lifecycle = lifecycle
        self.output_model = output_model
        self.validate_output = validate_output or (lambda _output: ())
        self.depth = depth
        self.parent_session_id = parent_session_id
        self._closed = False
        self._has_run = False
        self._has_started = False

    @classmethod
    def _from_harness(cls, **dependencies) -> Agent:
        """Let the Harness create an Agent after resolving every authorization."""
        return cls(
            **dependencies,
            _construction_token=_HARNESS_CONSTRUCTION_TOKEN,
        )

    def run(self, task: AgentTask | SystemAgentTask) -> AgentLoopResult:
        """Trace one independent session task and return only its bounded result."""
        with self.client.tracing_runtime.span(
            "Agent.run",
            kind="CHAIN",
            input_value={"objective": task.objective},
            attributes={
                "restscope.agent.session_id": self.session_id,
                "restscope.agent.profile": self.profile.name,
                "restscope.agent.depth": self.depth,
                "restscope.agent.lifecycle": self.lifecycle,
                **(
                    {"restscope.agent.parent_session_id": self.parent_session_id}
                    if self.parent_session_id is not None
                    else {}
                ),
            },
        ) as span:
            result = self._run_task(task)
            span.set_output(result)
            span.set_attribute("restscope.agent.status", result.status)
            if result.status != "completed":
                span.mark_error(result.error.message if result.error else result.status)
            return result

    def start(self) -> None:
        """Run the Main Profile's App-lifetime loop and block until it ends.

        The Main Agent receives no ``AgentTask`` because its stable Profile
        instructions define its continuing mission. A normal completion is an
        internal model-loop boundary and therefore returns nothing to the App.
        Safe terminal runtime failures are raised so a blocking caller cannot
        mistake cancellation or exhausted capacity for successful shutdown.
        """
        with self.client.tracing_runtime.span(
            "Agent.start",
            kind="CHAIN",
            input_value={"profile_name": self.profile.name},
            attributes={
                "restscope.agent.session_id": self.session_id,
                "restscope.agent.profile": self.profile.name,
                "restscope.agent.depth": self.depth,
                "restscope.agent.lifecycle": self.lifecycle,
            },
        ) as span:
            result = self._start_main_loop()
            span.set_output(result)
            span.set_attribute("restscope.agent.status", result.status)
            if result.status != "completed":
                message = result.error.message if result.error else result.status
                span.mark_error(message)
                code = result.error.code if result.error else result.status
                raise RuntimeError(f"{code}: {message}")

    def _start_main_loop(self) -> AgentLoopResult:
        """Prepare the taskless Main prompt before entering the shared loop."""
        if self._closed:
            raise RuntimeError("Agent is closed")
        if self.lifecycle != "main":
            raise RuntimeError("Taskless start is available only to the Main Agent")
        if self._has_started or self._has_run:
            raise RuntimeError("Main Agent loop is already started")
        self._has_started = True
        if self.cancel_event.is_set():
            return self._cancelled_result()
        try:
            self.prompt_session.prepare_start()
        except PromptSessionError as exc:
            return self._prompt_error_result(exc)
        return self._execute_loop()

    def _run_task(self, task: AgentTask | SystemAgentTask) -> AgentLoopResult:
        """Execute the correction loop while retaining bounded Main history."""
        if self._closed:
            raise RuntimeError("Agent is closed")
        if self._has_started:
            raise RuntimeError("Main Agent loop is already started")
        if self.lifecycle in {"subagent", "system"} and self._has_run:
            raise RuntimeError(f"{self.lifecycle.title()} Agent accepts only one task")
        self._has_run = True
        if self.cancel_event.is_set():
            return self._cancelled_result()
        try:
            self.prompt_session.prepare_task(task)
        except PromptSessionError as exc:
            return self._prompt_error_result(exc)

        return self._execute_loop()

    def _execute_loop(self) -> AgentLoopResult:
        """Execute the shared model, Tool, correction, and compaction loop."""

        prompt_tokens = cached_tokens = output_tokens = model_outputs = tool_calls = 0
        subagents_started = 0
        while True:
            if self.prompt_session.needs_compaction():
                compacted = False
                pending_reminders: list[int] = []
                for _attempt in range(2):
                    compact_response = (
                        self.tree_control.invoke_model(
                            self.client.invoke,
                            self.prompt_session.compaction_request(),
                        )
                        if self.tree_control is not None
                        else self.client.invoke(
                            self.prompt_session.compaction_request()
                        )
                    )
                    prompt_tokens += compact_response.prompt_tokens or 0
                    cached_tokens += compact_response.cached_input_tokens
                    output_tokens += compact_response.completion_tokens or 0
                    model_outputs += 1
                    compact_charge = (
                        self.tree_control.charge_response(compact_response)
                        if self.tree_control is not None
                        else None
                    )
                    if compact_charge is not None:
                        pending_reminders.extend(compact_charge.reminder_percentages)
                        if compact_charge.exceeded:
                            return self._budget_exceeded_result(
                                prompt_tokens=prompt_tokens,
                                cached_input_tokens=cached_tokens,
                                output_tokens=output_tokens,
                                model_outputs=model_outputs,
                                tool_calls=tool_calls,
                                subagents_started=subagents_started,
                            )
                    if self.cancel_event.is_set():
                        return self._cancelled_result(
                            prompt_tokens=prompt_tokens,
                            cached_input_tokens=cached_tokens,
                            output_tokens=output_tokens,
                            model_outputs=model_outputs,
                            tool_calls=tool_calls,
                            subagents_started=subagents_started,
                        )
                    summary = (compact_response.content or "").strip()
                    if (
                        not compact_response.tool_calls
                        and compact_response.parsed_json is None
                        and 0 < len(summary) <= 24_000
                    ):
                        try:
                            self.prompt_session.replace_compacted_history(summary)
                        except PromptSessionError as exc:
                            return self._prompt_error_result(
                                exc,
                                prompt_tokens=prompt_tokens,
                                cached_input_tokens=cached_tokens,
                                output_tokens=output_tokens,
                                model_outputs=model_outputs,
                                tool_calls=tool_calls,
                                subagents_started=subagents_started,
                            )
                        self._append_budget_reminders(tuple(pending_reminders))
                        compacted = True
                        break
                if not compacted:
                    return self._failure_result(
                        status="context_compaction_failed",
                        code="context_compaction_failed",
                        message="The Agent context could not be compacted safely.",
                        usage=self._usage(
                            prompt_tokens=prompt_tokens,
                            cached_input_tokens=cached_tokens,
                            output_tokens=output_tokens,
                            model_outputs=model_outputs,
                            tool_calls=tool_calls,
                            subagents_started=subagents_started,
                        ),
                    )

            try:
                request = self.prompt_session.request()
            except PromptSessionError as exc:
                return self._prompt_error_result(
                    exc,
                    prompt_tokens=prompt_tokens,
                    cached_input_tokens=cached_tokens,
                    output_tokens=output_tokens,
                    model_outputs=model_outputs,
                    tool_calls=tool_calls,
                    subagents_started=subagents_started,
                )
            response = (
                self.tree_control.invoke_model(self.client.invoke, request)
                if self.tree_control is not None
                else self.client.invoke(request)
            )
            prompt_tokens += response.prompt_tokens or 0
            cached_tokens += response.cached_input_tokens
            output_tokens += response.completion_tokens or 0
            model_outputs += 1
            charge = (
                self.tree_control.charge_response(response)
                if self.tree_control is not None
                else None
            )
            if charge is not None and charge.exceeded:
                return self._budget_exceeded_result(
                    prompt_tokens=prompt_tokens,
                    cached_input_tokens=cached_tokens,
                    output_tokens=output_tokens,
                    model_outputs=model_outputs,
                    tool_calls=tool_calls,
                    subagents_started=subagents_started,
                )
            reminders = charge.reminder_percentages if charge is not None else ()
            if self.cancel_event.is_set():
                return self._cancelled_result(
                    prompt_tokens=prompt_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_tokens,
                    model_outputs=model_outputs,
                    tool_calls=tool_calls,
                    subagents_started=subagents_started,
                )

            # A mixed or multi-call response has no valid provider-protocol
            # continuation. Do not append or execute any part of that turn.
            has_final = response.parsed_json is not None or bool(
                (response.content or "").strip()
            )
            if len(response.tool_calls) > 1 or (response.tool_calls and has_final):
                self.prompt_session.append_feedback(
                    "CORRECTION: Return exactly one Tool Call or one final result, "
                    "never both and never multiple Tool Calls. No Tool was executed."
                )
                self._append_budget_reminders(reminders)
                continue

            if response.tool_calls:
                call = response.tool_calls[0]
                self.prompt_session.append_assistant(response)
                result = (
                    self.tree_control.execute_tool(
                        call.name,
                        self.toolbox.execute,
                        call,
                    )
                    if self.tree_control is not None
                    else self.toolbox.execute(call)
                )
                self.prompt_session.append_tool_result(
                    call.name,
                    call.id,
                    result.model_dump_json(exclude_none=True),
                )
                tool_calls += 1
                if call.name == "subagent.start" and result.status == "succeeded":
                    subagents_started += 1
                if call.name == "skill.read" and result.status == "succeeded":
                    self.prompt_session.append_skill_instructions(
                        str(call.arguments["name"])
                    )
                self._append_budget_reminders(reminders)
                if self.cancel_event.is_set():
                    return self._cancelled_result(
                        prompt_tokens=prompt_tokens,
                        output_tokens=output_tokens,
                        cached_input_tokens=cached_tokens,
                        model_outputs=model_outputs,
                        tool_calls=tool_calls,
                        subagents_started=subagents_started,
                    )
                continue

            self.prompt_session.append_assistant(response)
            validated = OutputValidator().validate(
                response=response,
                output_model=self.output_model,
            )
            if not validated.valid:
                self.prompt_session.append_feedback(
                    self._validation_feedback(
                        tuple(
                            f"{issue.location or 'result'}: {issue.message}"
                            for issue in validated.errors
                        )
                    )
                )
                self._append_budget_reminders(reminders)
                continue
            output = self.output_model.model_validate(validated.validated_object)
            output_errors = self.validate_output(output)
            if output_errors:
                self.prompt_session.append_feedback(
                    self._validation_feedback(output_errors)
                )
                self._append_budget_reminders(reminders)
                continue
            usage = self._usage(
                prompt_tokens=prompt_tokens,
                cached_input_tokens=cached_tokens,
                output_tokens=output_tokens,
                model_outputs=model_outputs,
                tool_calls=tool_calls,
                subagents_started=subagents_started,
            )
            if self.lifecycle == "system":
                return SystemAgentResult(
                    session_id=self.session_id,
                    profile_name=self.profile.name,
                    status="completed",
                    output=output.model_dump(mode="json"),
                    usage=usage,
                )
            return AgentResult(
                session_id=self.session_id,
                profile_name=self.profile.name,
                status="completed",
                completion=AgentCompletion.model_validate(output),
                usage=usage,
            )

    def close(self) -> None:
        """Prevent more tasks and release the in-memory conversation."""
        self.cancel_event.set()
        if self.tree_control is not None:
            if self.lifecycle == "subagent":
                self.tree_control.close_descendants(self.session_id)
            else:
                self.tree_control.close()
        self._closed = True
        self.prompt_session.close()

    @property
    def closed(self) -> bool:
        """Return whether this session rejects every later task."""
        return self._closed

    def _cancelled_result(
        self,
        *,
        prompt_tokens: int = 0,
        cached_input_tokens: int = 0,
        output_tokens: int = 0,
        model_outputs: int = 0,
        tool_calls: int = 0,
        subagents_started: int = 0,
    ) -> AgentLoopResult:
        """Return stable cooperative cancellation without model-authored state."""
        return self._failure_result(
            status="cancelled",
            code="agent_cancelled",
            message="The Agent was cancelled by its parent or Harness.",
            usage=self._usage(
                prompt_tokens=prompt_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                model_outputs=model_outputs,
                tool_calls=tool_calls,
                subagents_started=subagents_started,
            ),
        )

    def _budget_exceeded_result(
        self,
        *,
        prompt_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        model_outputs: int,
        tool_calls: int,
        subagents_started: int,
    ) -> AgentLoopResult:
        """Stop the tree after charging, without accepting its overage action."""
        return self._failure_result(
            status="rollout_budget_exceeded",
            code="rollout_budget_exceeded",
            message="The shared Agent-tree model budget was exhausted.",
            usage=self._usage(
                prompt_tokens=prompt_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                model_outputs=model_outputs,
                tool_calls=tool_calls,
                subagents_started=subagents_started,
            ),
        )

    def _prompt_error_result(
        self,
        error: PromptSessionError,
        *,
        prompt_tokens: int = 0,
        cached_input_tokens: int = 0,
        output_tokens: int = 0,
        model_outputs: int = 0,
        tool_calls: int = 0,
        subagents_started: int = 0,
    ) -> AgentLoopResult:
        """Return a stable pre-action failure from private prompt assembly."""
        return self._failure_result(
            status=error.code,
            code=error.code,
            message=error.safe_message,
            usage=self._usage(
                prompt_tokens=prompt_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                model_outputs=model_outputs,
                tool_calls=tool_calls,
                subagents_started=subagents_started,
            ),
        )

    def _failure_result(
        self,
        *,
        status: str,
        code: str,
        message: str,
        usage: AgentUsage,
    ) -> AgentLoopResult:
        """Build the lifecycle-specific terminal result outside model control."""
        error = AgentError(code=code, message=message)
        if self.lifecycle == "system":
            return SystemAgentResult(
                session_id=self.session_id,
                profile_name=self.profile.name,
                status=status,
                error=error,
                usage=usage,
            )
        return AgentResult(
            session_id=self.session_id,
            profile_name=self.profile.name,
            status=status,
            error=error,
            usage=usage,
        )

    @staticmethod
    def _validation_feedback(errors: tuple[str, ...]) -> str:
        """Return bounded actionable feedback without imposing a retry limit."""
        details = "; ".join(errors[:10])[:1_800]
        if not details:
            details = "The result did not match the registered contract."
        return (
            "CORRECTION: The previous final result was rejected by the Harness. "
            f"Problems: {details} Return one complete corrected result matching "
            "the supplied JSON Schema."
        )

    @staticmethod
    def _usage(
        *,
        prompt_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        model_outputs: int,
        tool_calls: int,
        subagents_started: int,
    ) -> AgentUsage:
        """Project one Agent's local contribution to the shared tree usage."""
        return AgentUsage(
            prompt_tokens=prompt_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            weighted_tokens=(
                output_tokens
                + max(0, prompt_tokens - cached_input_tokens) * 0.1
            ),
            model_outputs=model_outputs,
            tool_calls=tool_calls,
            subagents_started=subagents_started,
        )

    def _append_budget_reminders(self, percentages: tuple[int, ...]) -> None:
        """Add each newly crossed tree threshold once before the next turn."""
        for percentage in percentages:
            self.prompt_session.append_feedback(
                "SHARED ROLLOUT BUDGET: the Agent tree has at most "
                f"{percentage}% of its weighted-token budget remaining. "
                "Finish or delegate only "
                "work essential to the objective."
            )
