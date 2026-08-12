"""Keep every active Python-version declaration on the same supported baseline.

Developers select an interpreter through ``.python-version``. Package installers
read ``pyproject.toml``, while ``uv`` reads the generated lock file. These tests
keep those three entry points aligned so a local environment cannot silently use
an older Python than an installed RESTScope package supports.
"""

from pathlib import Path
import tomllib


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MINIMUM_PYTHON = "3.12"


def test_local_interpreter_matches_the_supported_python_baseline() -> None:
    """A fresh local checkout selects Python 3.12 by default."""

    selected_version = (REPOSITORY_ROOT / ".python-version").read_text(
        encoding="utf-8"
    )

    assert selected_version.strip() == MINIMUM_PYTHON


def test_package_and_lock_require_python_3_12_or_newer() -> None:
    """Installers and the lock resolver reject Python versions older than 3.12."""

    project_data = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    lock_data = tomllib.loads(
        (REPOSITORY_ROOT / "uv.lock").read_text(encoding="utf-8")
    )

    assert project_data["project"]["requires-python"] == ">=3.12"
    assert lock_data["requires-python"] == ">=3.12"
