"""Opt-in asset acceptance test for IR-to-OpenAPI document generation.

This test sends the generated specifications to Swagger's public validator.
It is deliberately disabled unless the caller explicitly opts into that data
transfer with ``RUN_OPENAPI_IR_VALIDATOR_LIVE=1``.
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from restscope.openapi_parser import OpenAPIParser, build_openapi_document


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "openapi-ir-roundtrip"
ASSET_PATHS = (
    PROJECT_ROOT / "assets" / "openapi" / "gitlab-18.9.2-openapi-full.yaml",
    PROJECT_ROOT / "assets" / "openapi" / "gitlab-18.9.2-openapi.yaml",
    PROJECT_ROOT / "assets" / "openapi" / "petstore-v3.json",
    PROJECT_ROOT / "assets" / "openapi" / "project_swagger.yaml",
)
VALIDATOR_URL = "https://validator.swagger.io/validator/debug"
VALIDATOR_QUERY = {
    "validateInternalRefs": "true",
    "validateExternalRefs": "false",
    "jsonSchemaValidation": "true",
}

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_OPENAPI_IR_VALIDATOR_LIVE") != "1",
        reason=(
            "Set RUN_OPENAPI_IR_VALIDATOR_LIVE=1 to upload the generated "
            "asset specifications to validator.swagger.io."
        ),
    ),
]


def _diagnostic_counts(ir: Any) -> dict[str, int]:
    diagnostics = ir.diagnostics
    return {
        "spec_errors": len(diagnostics.spec_errors),
        "spec_warnings": len(diagnostics.spec_warnings),
        "path_errors": len(diagnostics.path_errors),
        "operation_errors": len(diagnostics.operation_errors),
    }


def _error_count(counts: Mapping[str, int]) -> int:
    return sum(value for name, value in counts.items() if name.endswith("_errors"))


def _json_compatible(value: Any) -> Any:
    """Make YAML-loaded values deterministic and JSON serializable."""
    if isinstance(value, Mapping):
        return {
            str(key): _json_compatible(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _canonical_json(document: Mapping[str, Any]) -> str:
    return json.dumps(
        _json_compatible(document),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _component_counts(document: Mapping[str, Any]) -> dict[str, int]:
    components = document.get("components")
    if isinstance(components, Mapping):
        return {
            str(kind): len(items) if isinstance(items, Mapping) else 0
            for kind, items in components.items()
        }

    swagger_sections = (
        "definitions",
        "parameters",
        "responses",
        "securityDefinitions",
    )
    return {
        section: len(document[section])
        for section in swagger_sections
        if isinstance(document.get(section), Mapping)
    }


def _validator_result(
    *,
    generated_yaml: str,
    artifact_dir: Path,
) -> tuple[dict[str, Any], bool]:
    url = f"{VALIDATOR_URL}?{urllib.parse.urlencode(VALIDATOR_QUERY)}"
    request = urllib.request.Request(
        url,
        data=generated_yaml.encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/yaml",
            "User-Agent": "RESTScope-openapi-ir-validator-test/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response_text = response.read().decode("utf-8")
            status_code = response.status
    except urllib.error.HTTPError as exc:
        error = {
            "error_type": type(exc).__name__,
            "http_status": exc.code,
            "message": str(exc),
            "response": exc.read().decode("utf-8", errors="replace"),
        }
        _write_json(artifact_dir / "swagger-validator-error.json", error)
        return error, False
    except (OSError, TimeoutError) as exc:
        error = {
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        _write_json(artifact_dir / "swagger-validator-error.json", error)
        return error, False

    (artifact_dir / "swagger-validator-response.json").write_text(
        response_text,
        encoding="utf-8",
    )
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        error = {
            "error_type": type(exc).__name__,
            "http_status": status_code,
            "message": "Swagger Validator returned a non-JSON response.",
        }
        _write_json(artifact_dir / "swagger-validator-error.json", error)
        return error, False

    if not isinstance(payload, Mapping):
        error = {
            "error_type": "InvalidValidatorResponse",
            "http_status": status_code,
            "message": "Swagger Validator response must be a JSON object.",
        }
        _write_json(artifact_dir / "swagger-validator-error.json", error)
        return error, False

    messages = payload.get("messages") or []
    schema_messages = payload.get("schemaValidationMessages") or []
    passed = status_code == 200 and not messages and not schema_messages
    result = {
        "http_status": status_code,
        "messages": messages,
        "schema_validation_messages": schema_messages,
        "passed": passed,
    }
    return result, passed


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _expected_transformations(input_format: str) -> list[str]:
    transformations = [
        "Emit OpenAPI 3.1.0 from typed IR.",
        "Inline ordinary schemas and retain only recursive schema closures.",
        "Omit callbacks and response links by document-builder contract.",
        "Filter non-schema raw fields to OpenAPI 3.1 node allowlists.",
    ]
    if input_format == "swagger2":
        transformations.insert(0, "Upgrade Swagger 2.0 structures to OpenAPI 3.1.")
    return transformations


def _process_asset(asset_path: Path) -> tuple[dict[str, Any], list[str]]:
    artifact_dir = OUTPUT_ROOT / asset_path.stem
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)
    artifact_dir.mkdir(parents=True)

    original_suffix = asset_path.suffix.lower()
    shutil.copyfile(asset_path, artifact_dir / f"source.original{original_suffix}")
    source_document = yaml.safe_load(asset_path.read_text(encoding="utf-8"))
    if not isinstance(source_document, Mapping):
        raise TypeError(f"{asset_path.name} did not load as an object")

    source_ir = OpenAPIParser.parse(asset_path)
    generated_document = build_openapi_document(
        source_ir,
        list(source_ir.operations),
    )
    generated_yaml = yaml.safe_dump(
        generated_document,
        allow_unicode=True,
        sort_keys=False,
    )
    generated_path = artifact_dir / "generated.openapi31.yaml"
    generated_path.write_text(generated_yaml, encoding="utf-8")

    source_json = _canonical_json(source_document)
    generated_json = _canonical_json(generated_document)
    (artifact_dir / "source.canonical.json").write_text(
        source_json,
        encoding="utf-8",
    )
    (artifact_dir / "generated.canonical.json").write_text(
        generated_json,
        encoding="utf-8",
    )
    diff = "".join(
        difflib.unified_diff(
            source_json.splitlines(keepends=True),
            generated_json.splitlines(keepends=True),
            fromfile=asset_path.name,
            tofile="generated.openapi31.yaml",
        )
    )
    (artifact_dir / "before-vs-after.diff").write_text(diff, encoding="utf-8")

    generated_ir = OpenAPIParser.parse(generated_path)
    source_operations = set(source_ir.operations)
    generated_operations = set(generated_ir.operations)
    source_diagnostics = _diagnostic_counts(source_ir)
    generated_diagnostics = _diagnostic_counts(generated_ir)
    validator, validator_passed = _validator_result(
        generated_yaml=generated_yaml,
        artifact_dir=artifact_dir,
    )

    missing_operations = sorted(source_operations - generated_operations)
    added_operations = sorted(generated_operations - source_operations)
    comparison = {
        "asset": asset_path.relative_to(PROJECT_ROOT).as_posix(),
        "input": {
            "format": source_ir.meta.spec_format,
            "version": source_ir.meta.spec_version,
            "path_count": len(source_ir.paths),
            "operation_count": len(source_operations),
            "component_counts": _component_counts(source_document),
            "diagnostics": source_diagnostics,
        },
        "output": {
            "format": generated_ir.meta.spec_format,
            "version": generated_document.get("openapi"),
            "path_count": len(generated_ir.paths),
            "operation_count": len(generated_operations),
            "component_counts": _component_counts(generated_document),
            "diagnostics": generated_diagnostics,
        },
        "operations": {
            "preserved_count": len(source_operations & generated_operations),
            "missing": missing_operations,
            "added": added_operations,
        },
        "expected_transformations": _expected_transformations(
            source_ir.meta.spec_format
        ),
        "swagger_validator": validator,
    }
    _write_json(artifact_dir / "comparison.json", comparison)

    failures: list[str] = []
    if generated_document.get("openapi") != "3.1.0":
        failures.append("generated document is not OpenAPI 3.1.0")
    if _error_count(generated_diagnostics):
        failures.append(
            f"generated document has parser errors: {generated_diagnostics}"
        )
    if missing_operations or added_operations:
        failures.append(
            "operation keys changed: "
            f"missing={missing_operations}, added={added_operations}"
        )
    if not validator_passed:
        failures.append(f"Swagger Validator did not pass: {validator}")

    summary = {
        "asset": asset_path.name,
        "artifact_dir": artifact_dir.relative_to(OUTPUT_ROOT).as_posix(),
        "input_version": f"{source_ir.meta.spec_format} {source_ir.meta.spec_version}",
        "output_version": str(generated_document.get("openapi")),
        "input_paths": len(source_ir.paths),
        "output_paths": len(generated_ir.paths),
        "input_operations": len(source_operations),
        "output_operations": len(generated_operations),
        "operation_set": "preserved" if not missing_operations and not added_operations else "changed",
        "reparse": "passed" if not _error_count(generated_diagnostics) else "failed",
        "swagger_validator": "passed" if validator_passed else "failed",
    }
    return summary, failures


def _write_summary(results: list[dict[str, Any]]) -> None:
    lines = [
        "# IR → OpenAPI 3.1 asset acceptance results",
        "",
        (
            "Each generated document contains every operation parsed from its "
            "source asset. The final column records whether Swagger Validator "
            "accepted the exact generated YAML."
        ),
        "",
        "| Asset | Input | Output | Paths | Operations | Operation set | Reparse | Swagger Validator |",
        "| --- | --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for result in results:
        artifact_dir = result["artifact_dir"]
        asset_link = (
            f"[{result['asset']}](./{artifact_dir}/generated.openapi31.yaml)"
        )
        lines.append(
            "| "
            f"{asset_link} | {result['input_version']} | {result['output_version']} | "
            f"{result['input_paths']} → {result['output_paths']} | "
            f"{result['input_operations']} → {result['output_operations']} | "
            f"{result['operation_set']} | {result['reparse']} | "
            f"{result['swagger_validator']} |"
        )
    lines.append("")
    (OUTPUT_ROOT / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def test_all_assets_round_trip_through_ir_and_pass_swagger_validator() -> None:
    """Generate every asset document, retain evidence, and validate them online."""
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    failures: list[str] = []

    for asset_path in ASSET_PATHS:
        try:
            summary, asset_failures = _process_asset(asset_path)
        except Exception as exc:  # Continue so every asset leaves evidence.
            artifact_dir = OUTPUT_ROOT / asset_path.stem
            artifact_dir.mkdir(parents=True, exist_ok=True)
            error = {
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            _write_json(artifact_dir / "processing-error.json", error)
            summary = {
                "asset": asset_path.name,
                "artifact_dir": artifact_dir.relative_to(OUTPUT_ROOT).as_posix(),
                "input_version": "unknown",
                "output_version": "unknown",
                "input_paths": "—",
                "output_paths": "—",
                "input_operations": "—",
                "output_operations": "—",
                "operation_set": "failed",
                "reparse": "failed",
                "swagger_validator": "not run",
            }
            asset_failures = [f"processing failed: {type(exc).__name__}: {exc}"]

        results.append(summary)
        failures.extend(
            f"{asset_path.name}: {failure}" for failure in asset_failures
        )

    _write_summary(results)
    assert not failures, "\n" + "\n".join(failures)
