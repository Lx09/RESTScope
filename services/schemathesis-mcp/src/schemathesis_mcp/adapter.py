from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests
from schemathesis import graphql, openapi
from schemathesis.config import SchemathesisConfig
from schemathesis.core.result import Ok
from schemathesis.engine import Status, events, from_schema
from schemathesis.reporting.har import HarWriter
from schemathesis.reporting.junitxml import JunitXmlWriter
from schemathesis.reporting.ndjson import serialize

from schemathesis_mcp.models import FailureDetail, RunRequest
from schemathesis_mcp.projector import EventProjector
from schemathesis_mcp.security import Sanitizer, TargetPolicy


class SchemathesisBackend:
    def __init__(
        self,
        sanitizer: Sanitizer | None = None,
        target_policy: TargetPolicy | None = None,
    ) -> None:
        self.sanitizer = sanitizer or Sanitizer()
        self.target_policy = target_policy or TargetPolicy.from_env()
        self.projector = EventProjector(self.sanitizer)
        self._replay_data: dict[tuple[str, str], dict[str, Any]] = {}
        self._artifact_dirs: dict[str, Path] = {}
        self._failure_counts: dict[tuple[str, str], int] = {}

    def configure_run(self, run_id: str, artifact_dir: Path) -> None:
        self._artifact_dirs[run_id] = artifact_dir

    def inspect(self, request: RunRequest) -> dict[str, Any]:
        schema = self._load_schema(request)
        operations = []
        warnings = []
        for result in schema.get_all_operations():
            if isinstance(result, Ok):
                operation = result.ok()
                raw = operation.definition.raw
                operations.append(
                    {
                        "label": operation.label,
                        "method": operation.method.upper(),
                        "path": operation.path,
                        "operation_id": raw.get("operationId") if isinstance(raw, Mapping) else None,
                        "tags": operation.tags or [],
                    }
                )
            else:
                warnings.append(str(result.err()))
        statistic = schema.statistic
        info = schema.raw_schema.get("info", {})
        specification = schema.specification
        kind = specification.kind.value
        specification_name = "Open API" if kind == "openapi" else "GraphQL"
        if specification.version:
            specification_name = f"{specification_name} {specification.version}"
        return self.sanitizer.sanitize(
            {
                "schema": request.schema_location,
                "title": info.get("title"),
                "specification": specification_name,
                "base_url": schema.get_base_url(),
                "operations": operations,
                "statistics": {
                    "operations": {
                        "total": statistic.operations.total,
                        "selected": statistic.operations.selected,
                    },
                    "transitions": {
                        "total": statistic.transitions.total,
                        "selected": statistic.transitions.selected,
                    },
                },
                "warnings": warnings,
                "supported_phases": ["probing", "schema_analysis", "examples", "coverage", "fuzzing", "stateful"],
            }
        )

    def execute(
        self,
        run_id: str,
        request: RunRequest | dict[str, Any],
        stop_event: threading.Event,
    ) -> Iterator[dict[str, Any]]:
        if not isinstance(request, RunRequest):
            request = RunRequest.model_validate(request)
        yield {"type": "loading_started", "timestamp": time.time()}
        schema = self._load_schema(request)
        yield {
            "type": "loading_finished",
            "timestamp": time.time(),
            "specification": schema.specification.kind.value,
            "operations": schema.statistic.operations.selected,
        }
        artifact_dir = self._artifact_dirs.get(run_id)
        junit = JunitXmlWriter(artifact_dir / "junit.xml", config=schema.config.output) if artifact_dir else None
        har = HarWriter(artifact_dir / "har.json", config=schema.config.output) if artifact_dir else None
        if har is not None:
            har.open(seed=schema.config.seed)
        stream = from_schema(schema).execute()
        had_failures = False
        had_errors = False
        try:
            for raw_event in stream:
                if stop_event.is_set():
                    stream.stop()
                projected = self.projector.project(raw_event)
                if isinstance(raw_event, events.ScenarioFinished):
                    if junit is not None:
                        junit.write(raw_event.recorder, raw_event.elapsed_time)
                    if har is not None:
                        har.write(raw_event.recorder)
                    details = self._extract_failures(run_id, raw_event)
                    if details:
                        had_failures = True
                        projected["_failures"] = [detail.model_dump(mode="json") for detail in details]
                elif isinstance(raw_event, (events.NonFatalError, events.FatalError)):
                    had_errors = True
                if isinstance(raw_event, events.EngineFinished):
                    if stop_event.is_set():
                        projected["outcome"] = "interrupted"
                    elif had_errors:
                        projected["outcome"] = "errored"
                    elif had_failures or raw_event.failures:
                        projected["outcome"] = "failed"
                    else:
                        projected["outcome"] = "passed"
                yield projected
        finally:
            if junit is not None:
                junit.close()
            if har is not None:
                har.close()

    def replay(self, run_id: str, failure_id: str) -> dict[str, Any]:
        replay = self._replay_data[(run_id, failure_id)]
        response = requests.request(
            replay["method"],
            replay["url"],
            headers=replay["headers"],
            data=replay["body"],
            verify=replay["verify"],
            timeout=30,
        )
        expected_status = replay.get("status_code")
        return self.sanitizer.sanitize(
            {
                "failure_id": failure_id,
                "reproduced": expected_status is None or response.status_code == expected_status,
                "expected_status_code": expected_status,
                "status_code": response.status_code,
                "response": {
                    "headers": dict(response.headers),
                    "body": response.text[:100_000],
                },
            }
        )

    def _load_schema(self, request: RunRequest):
        config = self._build_config(request)
        location = request.schema_location
        self.target_policy.validate(location)
        if request.base_url is not None:
            self.target_policy.validate(request.base_url)
        parsed = urlsplit(location)
        is_url = parsed.scheme in {"http", "https"}
        graphql_hint = location.lower().endswith((".graphql", ".gql", "/graphql", "/graphql/"))
        modules = (graphql, openapi) if graphql_hint else (openapi, graphql)
        error: Exception | None = None
        for module in modules:
            try:
                if is_url:
                    kwargs: dict[str, Any] = {"config": config, "verify": request.tls_verify}
                    if request.headers:
                        kwargs["headers"] = request.headers
                    schema = module.from_url(location, **kwargs)
                else:
                    schema = module.from_path(Path(location), config=config)
                if request.include:
                    schema = schema.include(**request.include)
                if request.exclude:
                    schema = schema.exclude(**request.exclude)
                return schema
            except Exception as exc:
                error = exc
        assert error is not None
        raise error

    @staticmethod
    def _build_config(request: RunRequest) -> SchemathesisConfig:
        data: dict[str, Any] = {}
        if request.base_url is not None:
            data["base-url"] = request.base_url
        if request.headers:
            data["headers"] = request.headers
        if request.workers is not None:
            data["workers"] = request.workers
        data["tls-verify"] = request.tls_verify
        if request.seed is not None:
            data["seed"] = request.seed
        if request.max_failures is not None:
            data["max-failures"] = request.max_failures
        if request.max_examples is not None or request.generation_modes:
            generation: dict[str, Any] = {}
            if request.max_examples is not None:
                generation["max-examples"] = request.max_examples
            if request.generation_modes:
                generation["mode"] = "all" if len(request.generation_modes) > 1 else request.generation_modes[0]
            data["generation"] = generation
        if request.phases is not None:
            phases: dict[str, Any] = {"enabled": False}
            for phase in request.phases:
                if phase in {"examples", "coverage", "fuzzing", "stateful"}:
                    phases[phase] = {"enabled": True}
            data["phases"] = phases
        if request.max_time is not None:
            data["fuzz"] = {"max-time": int(request.max_time)}
        if request.checks is not None:
            data["checks"] = {"enabled": False, **{name: {"enabled": True} for name in request.checks}}
        return SchemathesisConfig.from_dict(data)

    def _extract_failures(self, run_id: str, event: events.ScenarioFinished) -> list[FailureDetail]:
        recorder = event.recorder
        output: list[FailureDetail] = []
        for case_id, nodes in recorder.checks.items():
            for node in nodes:
                if node.status is not Status.FAILURE or node.failure_info is None:
                    continue
                failure = node.failure_info.failure
                case_node = recorder.cases.get(case_id)
                interaction = recorder.interactions.get(case_id)
                operation = case_node.value.operation.label if case_node is not None else recorder.label
                title = getattr(failure, "title", type(failure).__name__)
                message = str(failure)
                key = f"{operation}\0{node.name}\0{type(failure).__name__}\0{message}"
                failure_id = hashlib.sha256(key.encode()).hexdigest()[:16]
                count_key = (run_id, failure_id)
                count = self._failure_counts.get(count_key, 0) + 1
                self._failure_counts[count_key] = count
                request_payload = serialize(interaction.request) if interaction is not None else None
                response_payload = (
                    serialize(interaction.response)
                    if interaction is not None and interaction.response is not None
                    else None
                )
                case_payload = serialize(case_node.value) if case_node is not None else None
                related_case_ids = getattr(failure, "related_case_ids", ())
                if callable(related_case_ids):
                    related_case_ids = related_case_ids()
                detail = FailureDetail(
                    failure_id=failure_id,
                    operation=operation,
                    check=node.name,
                    title=title,
                    message=message,
                    count=count,
                    request=self.sanitizer.sanitize(request_payload),
                    response=self.sanitizer.sanitize(response_payload),
                    curl=self.sanitizer.sanitize(node.failure_info.code_sample),
                    case=self.sanitizer.sanitize(case_payload),
                    related_cases=[
                        self.sanitizer.sanitize(serialize(item))
                        for item in recorder.iter_chain_cases(
                            case_id=getattr(failure, "case_id", None) or case_id,
                            related_case_ids=tuple(related_case_ids or ()),
                        )
                    ],
                )
                output.append(detail)
                if interaction is not None:
                    raw_request = interaction.request
                    raw_response = interaction.response
                    self._replay_data[(run_id, failure_id)] = {
                        "method": raw_request.method,
                        "url": raw_request.uri,
                        "headers": {key: value[0] for key, value in raw_request.headers.items()},
                        "body": raw_request.body,
                        "verify": getattr(raw_response, "verify", True),
                        "status_code": getattr(raw_response, "status_code", None),
                    }
        return output
