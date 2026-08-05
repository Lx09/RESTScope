"""Protect ignored model configuration lookup for GitLab live worktrees.

Git does not copy the main checkout's ignored ``.env`` into a feature
worktree. These tests keep both GitLab live harnesses able to find that file
through Git's common directory without invoking a model or target API.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import test_gitlab_post_projects_smoke_live as focused_live
from tests import test_gitlab_projects_operations_live as five_operation_live


@pytest.mark.parametrize("live_module", [focused_live, five_operation_live])
def test_live_env_lookup_uses_the_git_common_checkout(
    live_module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A sibling feature worktree resolves the main checkout's ignored file."""
    main_checkout = tmp_path / "RESTScope"
    worktree = tmp_path / "RESTScope-worktrees" / "feature"
    main_checkout.mkdir()
    worktree.mkdir(parents=True)
    main_env = main_checkout / ".env"
    main_env.write_text("THINK_MODEL=test\n", encoding="utf-8")
    monkeypatch.setattr(live_module, "PROJECT_ROOT", worktree)
    monkeypatch.setattr(
        live_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=str(main_checkout / ".git") + "\n",
        ),
    )

    assert live_module._default_env_file() == main_env
