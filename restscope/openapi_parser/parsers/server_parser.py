"""Server parser module."""

from ..adapters.base import SpecificationAdapter
from ..ir import ServerIR, ServerVariableIR


def parse_server_variables(variables_raw: dict) -> dict[str, ServerVariableIR]:
    """
    Parse server variables.

    Args:
        variables_raw: The raw server variables dictionary.

    Returns:
        Dictionary of variable name to ServerVariableIR.
    """
    if not isinstance(variables_raw, dict):
        return {}

    result = {}
    for name, var_raw in variables_raw.items():
        if not isinstance(var_raw, dict):
            continue

        enum_list = var_raw.get("enum", [])
        if not isinstance(enum_list, list):
            enum_list = []

        result[str(name)] = ServerVariableIR(
            name=str(name),
            default=var_raw.get("default"),
            enum=[str(e) for e in enum_list],
            description=var_raw.get("description"),
        )

    return result


def parse_server(server_raw: dict) -> ServerIR:
    """
    Parse a server definition.

    Args:
        server_raw: The raw server dictionary.

    Returns:
        A ServerIR instance.
    """
    if not isinstance(server_raw, dict):
        return ServerIR(
            url="",
            description=None,
            variables={},
        )

    variables = parse_server_variables(server_raw.get("variables", {}))

    return ServerIR(
        url=server_raw.get("url", ""),
        description=server_raw.get("description"),
        variables=variables,
    )


def parse_servers(servers_raw: list) -> list[ServerIR]:
    """
    Parse a list of server definitions.

    Args:
        servers_raw: The raw servers list.

    Returns:
        List of ServerIR instances.
    """
    if not isinstance(servers_raw, list):
        return []

    result = []
    for server_raw in servers_raw:
        if isinstance(server_raw, dict):
            result.append(parse_server(server_raw))

    return result


def resolve_operation_servers(
    raw_schema: dict,
    operation_raw: dict,
    adapter: SpecificationAdapter,
    path_item_raw: dict | None = None,
) -> list[ServerIR]:
    """
    Resolve servers for an operation.

    Priority:
    1. Operation-level servers
    2. Path-item level servers
    3. Global servers
    4. Swagger 2.0 returns empty list

    Args:
        raw_schema: The raw schema dictionary.
        operation_raw: The raw operation dictionary.
        adapter: The specification adapter.
        path_item_raw: The optional path item dictionary.

    Returns:
        List of ServerIR instances.
    """
    # Check operation-level servers
    operation_servers = adapter.get_operation_servers(operation_raw)
    if operation_servers:
        return parse_servers(operation_servers)

    # Check path-item level servers
    if path_item_raw:
        path_item_servers = adapter.get_path_item_servers(path_item_raw)
        if path_item_servers:
            return parse_servers(path_item_servers)

    # Fall back to global servers
    global_servers = adapter.get_global_servers(raw_schema)
    return parse_servers(global_servers)
