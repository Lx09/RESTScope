"""FastMCP server registration for Schemathesis testing tools and resources."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from schemathesis_mcp.tools import ToolService


def create_server(service: ToolService | None = None) -> FastMCP:
    tools = service or ToolService.create()
    server = FastMCP(
        "schemathesis-mcp",
        instructions="Run OpenAPI or GraphQL API tests through the official Schemathesis CLI.",
    )

    @server.tool(annotations=_read_only("Get capabilities"))
    def get_capabilities() -> dict[str, Any]:
        """Describe supported tools, options, limits, and safe configuration state."""
        return tools.get_capabilities()

    @server.tool(
        annotations=ToolAnnotations(
            title="Start run",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        )
    )
    def start_run(
        schema: dict[str, Any],
        base_url: str | None = None,
        headers: dict[str, str] | None = None,
        phases: list[str] | None = None,
        checks: list[str] | None = None,
        generation_modes: list[str] | None = None,
        include: dict[str, Any] | None = None,
        exclude: dict[str, Any] | None = None,
        workers: int | str | None = None,
        max_examples: int | None = None,
        max_failures: int | None = None,
        max_time: float | None = None,
        seed: int | None = None,
        tls_verify: bool = True,
        reports: list[str] | None = None,
    ) -> dict[str, Any]:
        """Start an asynchronous Schemathesis test run."""
        return tools.start_run(
            schema=schema,
            base_url=base_url,
            headers=headers or {},
            phases=phases,
            checks=checks,
            generation_modes=generation_modes,
            include=include or {},
            exclude=exclude or {},
            workers=workers,
            max_examples=max_examples,
            max_failures=max_failures,
            max_time=max_time,
            seed=seed,
            tls_verify=tls_verify,
            reports=reports or [],
        )

    @server.tool(annotations=_read_only("Get run"))
    def get_run(run_id: str) -> dict[str, Any]:
        """Get status and progress for a test run."""
        return tools.get_run(run_id)

    @server.tool(annotations=_read_only("Get events"))
    def get_events(run_id: str, cursor: int = 0, limit: int = 100) -> dict[str, Any]:
        """Read a page of projected run events."""
        return tools.get_events(run_id, cursor, limit)

    @server.tool(annotations=_read_only("Get result"))
    def get_result(run_id: str) -> dict[str, Any]:
        """Get the completed result for a test run."""
        return tools.get_result(run_id)

    @server.tool(annotations=_read_only("Get failure"))
    def get_failure(run_id: str, failure_id: str) -> dict[str, Any]:
        """Get a detailed, sanitized API failure."""
        return tools.get_failure(run_id, failure_id)

    @server.tool(
        annotations=ToolAnnotations(
            title="Cancel run",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    )
    def cancel_run(run_id: str) -> dict[str, Any]:
        """Request cancellation of a running test."""
        return tools.cancel_run(run_id)

    @server.resource("schemathesis://runs/{run_id}/{name}")
    def run_artifact(run_id: str, name: str) -> str:
        """Read a run artifact such as events.ndjson or result.json."""
        return tools.read_resource(f"schemathesis://runs/{run_id}/{name}")

    @server.resource("schemathesis://runs/{run_id}/failures/{failure_id}.json")
    def failure_artifact(run_id: str, failure_id: str) -> str:
        """Read a detailed failure artifact."""
        return tools.read_resource(f"schemathesis://runs/{run_id}/failures/{failure_id}.json")

    return server


def _read_only(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
