"""Production Profile registration after the deterministic Oracle simplification."""


def test_runtime_registers_all_profiles_with_profile_owned_effort(
    tmp_path,
    monkeypatch,
) -> None:
    """Resource selectors disable thinking while planning Profiles keep it."""

    from restscope.app.profiles import _build_agent_runtime_definition
    from restscope.config import RESTScopeConfig
    from restscope.harness import ContextSourceBinding
    from restscope.observability import TracingRuntime

    monkeypatch.setattr(
        "restscope.app.profiles.build_llm_client",
        lambda *_args, **_kwargs: object(),
    )
    models_file = tmp_path / "models.toml"
    models_file.write_text(
        "[providers.openai_compatible]\n"
        'api_key_env = "TEST_MODEL_API_KEY"\n'
        "\n"
        "[models.default]\n"
        'provider = "openai_compatible"\n'
        'model = "test-model"\n'
        "max_tokens = 512\n"
        "context_tokens = 8192\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"MODELS_FILE={models_file}\nTEST_MODEL_API_KEY=test-key\n",
        encoding="utf-8",
    )

    definition = _build_agent_runtime_definition(
        RESTScopeConfig.from_environment(env_file),
        tracing_runtime=TracingRuntime.disabled(),
        test_progress_context=ContextSourceBinding(
            name="test-progress",
            read=lambda: "current progress",
        ),
    )

    assert definition is not None
    assert [profile.name for profile in definition.profiles] == [
        "orchestrator",
        "task-executor",
        "parameter-patch",
        "resource-identifier-selector",
        "resource-state-selector",
    ]
    assert [item.profile_name for item in definition.system_agents] == [
        "orchestrator",
        "task-executor",
        "resource-identifier-selector",
        "resource-state-selector",
    ]
    assert all(
        profile.model_config_name == "default" for profile in definition.profiles
    )
    assert [profile.reasoning_effort for profile in definition.profiles] == [
        "high",
        "high",
        "low",
        "none",
        "none",
    ]


def test_production_profiles_require_the_exact_default_model(
    tmp_path,
) -> None:
    """A valid catalog still fails when production references an absent model."""
    import pytest

    from restscope.app.profiles import _build_agent_runtime_definition
    from restscope.config import RESTScopeConfig
    from restscope.observability import TracingRuntime

    models_file = tmp_path / "models.toml"
    models_file.write_text(
        "[providers.deepseek]\n"
        'api_key_env = "MODEL_KEY"\n'
        "[models.custom]\n"
        'provider = "deepseek"\n'
        'model = "custom-model"\n',
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"MODELS_FILE={models_file}\nMODEL_KEY=test-key\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="require model configuration: default"):
        _build_agent_runtime_definition(
            RESTScopeConfig.from_environment(env_file),
            tracing_runtime=TracingRuntime.disabled(),
            test_progress_context=None,
        )
