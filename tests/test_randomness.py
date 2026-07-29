"""Contracts for the one App-wide source of generated test randomness."""

from __future__ import annotations

from uuid import uuid4


def test_random_config_loads_one_optional_environment_seed(tmp_path) -> None:
    """Scenario: one configured seed becomes the App-wide generation seed."""
    from restscope import RESTScopeConfig

    env_file = tmp_path / ".env"
    env_file.write_text("RANDOM_SEED=731\n", encoding="utf-8")

    config = RESTScopeConfig.from_environment(env_file)

    assert config.random.seed == 731


def test_seeded_random_repeats_common_values_without_shared_call_state() -> None:
    """Scenario: the same root seed and scope repeat regardless of call order."""
    from restscope.randomness import SeededRandom

    first = SeededRandom(731)
    second = SeededRandom(731)

    expected_integer = first.integer(3, 100, scope="age")
    first.string("abcdef", 8, scope="unrelated")

    assert second.integer(3, 100, scope="age") == expected_integer
    assert first.boolean(scope="enabled") == second.boolean(scope="enabled")
    assert first.choice(["a", "b", "c"], scope="choice") == second.choice(
        ["a", "b", "c"],
        scope="choice",
    )


def test_seeded_random_does_not_replace_unique_runtime_ids() -> None:
    """Scenario: replaying test values does not make UUID identities repeat."""
    from restscope.randomness import SeededRandom

    source = SeededRandom(731)
    first_value = source.integer(0, 10, scope="value")
    first_id = uuid4()
    second_value = source.integer(0, 10, scope="value")
    second_id = uuid4()

    assert first_value == second_value
    assert first_id != second_id
