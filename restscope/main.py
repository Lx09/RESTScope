"""Start one complete RESTScope process from an installed shell command.

The command reads App configuration, validates one local OpenAPI document and
target, starts the blocking Main Agent, and always closes the App. It returns
small process-facing exit codes while keeping target secrets and internal stack
traces out of terminal error messages.
"""

from __future__ import annotations

from pathlib import Path

import click

from restscope.app import RESTScopeApp
from restscope.target_api import TargetAPIError
from restscope.target_api.request import (
    validate_target_headers,
    validate_target_origin,
)


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument(
    "openapi_file",
    type=click.Path(
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        path_type=Path,
    ),
    metavar="OPENAPI_FILE",
)
@click.option(
    "--base-url",
    required=True,
    metavar="URL",
    help="HTTP or HTTPS origin of the target API.",
)
@click.option(
    "--env-file",
    type=click.Path(
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        path_type=Path,
    ),
    metavar="PATH",
    help="Optional dotenv file used for RESTScope configuration.",
)
@click.option(
    "--header",
    "header_pairs",
    type=(str, str),
    multiple=True,
    metavar="NAME VALUE",
    help=(
        "Target header; repeat for more values. Sensitive values may remain "
        "visible in shell history and process listings."
    ),
)
def main(
    openapi_file: Path,
    base_url: str,
    env_file: Path | None,
    header_pairs: tuple[tuple[str, str], ...],
) -> None:
    """Run RESTScope against one local OpenAPI file and target origin.

    ``openapi_file`` and ``base_url`` identify the target. ``env_file`` selects
    optional process configuration, while each ``header_pairs`` item becomes
    an App-lifetime target header. Successful completion returns through Click
    with code 0; an interrupt uses 130, a runtime failure uses 1, and Click
    reports malformed command input with code 2. Every constructed App is
    closed before the command returns.
    """
    headers = _validated_headers(header_pairs)
    try:
        validate_target_origin(base_url)
    except TargetAPIError as exc:
        raise click.BadParameter(
            "must be a safe HTTP or HTTPS target origin",
            param_hint="--base-url",
        ) from exc
    app: RESTScopeApp | None = None
    terminal_result_selected = False
    try:
        app = RESTScopeApp.from_environment(env_file=env_file)
        app.initialize(
            schema_source={"kind": "file", "path": str(openapi_file)},
            base_url=base_url,
            headers=headers,
        )
        if app.ui_url is not None:
            click.echo(f"Observer: {app.ui_url}")
        app.start()
    except KeyboardInterrupt:
        terminal_result_selected = True
        raise click.exceptions.Exit(130) from None
    except Exception:  # noqa: BLE001
        terminal_result_selected = True
        click.echo("RESTScope could not start or complete the run.", err=True)
        raise click.exceptions.Exit(1) from None
    finally:
        if app is not None:
            try:
                app.close()
            except BaseException:  # noqa: BLE001
                # Preserve an already-selected interrupt or failure. If the
                # work itself completed, cleanup failure is the runtime error.
                if not terminal_result_selected:
                    click.echo(
                        "RESTScope could not close the run cleanly.",
                        err=True,
                    )
                    raise click.exceptions.Exit(1) from None


def _validated_headers(
    pairs: tuple[tuple[str, str], ...],
) -> dict[str, str]:
    """Return validated unique target headers from Click's repeated pairs.

    Header names are compared without regard to case before the shared Target
    API validator checks HTTP token syntax and unsafe control characters.
    Invalid input becomes a Click parameter error and never reaches App
    construction.
    """
    headers: dict[str, str] = {}
    names: set[str] = set()
    for name, value in pairs:
        normalized = name.lower()
        if normalized in names:
            raise click.BadParameter(
                f"duplicate header name: {name}",
                param_hint="--header",
            )
        names.add(normalized)
        headers[name] = value
    try:
        validate_target_headers(headers)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--header") from None
    return headers


__all__ = ["main"]
