"""Production Profile registration after the deterministic Oracle simplification."""


def test_fast_runtime_registers_only_the_resource_monitor_system_agent(
    tmp_path,
    monkeypatch,
) -> None:
    """Bug Oracle status checks require no model Profile or result contract."""

    from restscope.app.profiles import _build_agent_runtime_definition
    from restscope.config import RESTScopeConfig
    from restscope.observability import TracingRuntime

    monkeypatch.setattr(
        "restscope.app.profiles.build_llm_client",
        lambda *_args, **_kwargs: object(),
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FAST_PROVIDER=test\n"
        "FAST_MODEL=fast-model\n"
        "FAST_MAX_TOKENS=512\n"
        "FAST_CONTEXT_WINDOW_TOKENS=8192\n",
        encoding="utf-8",
    )

    definition = _build_agent_runtime_definition(
        RESTScopeConfig.from_environment(env_file),
        tracing_runtime=TracingRuntime.disabled(),
    )

    assert definition is not None
    assert [profile.name for profile in definition.profiles] == [
        "resource-identifier-selector"
    ]
    assert [item.profile_name for item in definition.system_agents] == [
        "resource-identifier-selector"
    ]
