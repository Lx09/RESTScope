from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from schemathesis_mcp.tools import ToolService


def create_server(service: ToolService | None = None) -> FastMCP:
    tools = service or ToolService.create()
    server = FastMCP(
        "schemathesis-mcp",
        instructions="Inspect and test OpenAPI or GraphQL APIs with Schemathesis.",
    )

    @server.tool()
    def inspect_api(
        schema: str,
        base_url: str | None = None,
        headers: dict[str, str] | None = None,
        include: dict[str, Any] | None = None,
        exclude: dict[str, Any] | None = None,
        tls_verify: bool = True,
    ) -> dict[str, Any]:
        """Inspect an API schema and list the selected operations."""
        return tools.inspect_api(
            schema=schema,
            base_url=base_url,
            headers=headers or {},
            include=include or {},
            exclude=exclude or {},
            tls_verify=tls_verify,
        )

    @server.tool()
    def start_run(
        schema: str,
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
        )

    @server.tool()
    def get_run(run_id: str) -> dict[str, Any]:
        """Get status and progress for a test run."""
        return tools.get_run(run_id)

    @server.tool()
    def get_events(run_id: str, cursor: int = 0, limit: int = 100) -> dict[str, Any]:
        """Read a page of projected run events."""
        return tools.get_events(run_id, cursor, limit)

    @server.tool()
    def get_result(run_id: str) -> dict[str, Any]:
        """Get the completed result for a test run."""
        return tools.get_result(run_id)

    @server.tool()
    def get_failure(run_id: str, failure_id: str) -> dict[str, Any]:
        """Get a detailed, sanitized API failure."""
        return tools.get_failure(run_id, failure_id)

    @server.tool()
    def cancel_run(run_id: str) -> dict[str, Any]:
        """Request cancellation of a running test."""
        return tools.cancel_run(run_id)

    @server.tool()
    def replay_failure(run_id: str, failure_id: str) -> dict[str, Any]:
        """Replay the original request associated with a failure."""
        return tools.replay_failure(run_id, failure_id)

    @server.resource("schemathesis://runs/{run_id}/{name}")
    def run_artifact(run_id: str, name: str) -> str:
        """Read a run artifact such as events.ndjson or result.json."""
        return tools.read_resource(f"schemathesis://runs/{run_id}/{name}")

    @server.resource("schemathesis://runs/{run_id}/failures/{failure_id}.json")
    def failure_artifact(run_id: str, failure_id: str) -> str:
        """Read a detailed failure artifact."""
        return tools.read_resource(f"schemathesis://runs/{run_id}/failures/{failure_id}.json")

    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
