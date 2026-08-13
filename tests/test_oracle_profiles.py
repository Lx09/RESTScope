"""Production registration contracts for the three Bug Oracle System Agents."""


def test_fast_runtime_registers_three_capability_free_oracle_profiles(tmp_path, monkeypatch) -> None:
    """Each category has one no-Tool Profile and strict confirmation result contract."""

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
    names = {
        "valid-input-server-error-oracle",
        "invalid-input-success-oracle",
        "response-schema-mismatch-oracle",
    }
    profiles = {profile.name: profile for profile in definition.profiles}
    assert names <= set(profiles)
    for name in names:
        profile = profiles[name]
        assert profile.model_config_name == "fast"
        assert profile.tool_names == ()
        assert profile.skill_names == ()
        assert profile.context_sources == ()
        assert profile.subagent_profile_names == ()
    definitions = {item.profile_name: item for item in definition.system_agents}
    assert names <= set(definitions)
    assert all(
        definitions[name].output_schema_name == "OracleConfirmationDecision"
        for name in names
    )
