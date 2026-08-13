"""Load standard Skill directories into bounded immutable runtime definitions.

The loader is the only Module that reads packaged Skill files. It validates the
standard ``SKILL.md`` contract, RESTScope's optional runtime manifest, and every
directly linked Markdown Reference before the Harness builds an Agent. Its
outputs contain strings rather than filesystem handles, so later model Tool
calls cannot traverse the installation or observe files added after startup.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .manifest import SkillDefinition, SkillManifest, SkillReference

_STANDARD_FRONTMATTER_KEYS = frozenset({"name", "description"})
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_REFERENCE_PATH = re.compile(r"^references/[A-Za-z0-9][A-Za-z0-9._-]*\.md$")
_FILE_READ_TOOL_NAME = "file.read"
_MAX_TEXT_CHARS = 24_000


class _Traversable(Protocol):
    """Describe the pathlib/importlib.resources operations the loader needs."""

    @property
    def name(self) -> str: ...

    def iterdir(self) -> Iterable[_Traversable]: ...

    def is_dir(self) -> bool: ...

    def is_file(self) -> bool: ...

    def joinpath(self, *descendants: str) -> _Traversable: ...

    def read_bytes(self) -> bytes: ...


class _RuntimeManifest(BaseModel):
    """Validate the only RESTScope-specific fields allowed beside SKILL.md."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: str
    risk_level: Literal["low", "medium", "high"]
    required_tools: tuple[str, ...]
    required_context_sources: tuple[str, ...]

    @field_validator("required_tools", "required_context_sources", mode="before")
    @classmethod
    def accept_yaml_sequences(cls, value: object) -> object:
        """Convert ordinary YAML arrays while retaining strict element types."""
        if isinstance(value, list):
            return tuple(value)
        return value


@dataclass(frozen=True)
class _RuntimeSettings:
    """Hold validated RESTScope manifest fields and no-manifest defaults."""

    version: str | None
    risk_level: Literal["low", "medium", "high"]
    required_tools: tuple[str, ...]
    required_context_sources: tuple[str, ...]


def discover_skill_definitions(root: _Traversable) -> tuple[SkillDefinition, ...]:
    """Load every immediate standard Skill directory in stable name order.

    Args:
        root: Package or test directory whose immediate children are Skills.

    Returns:
        Fully validated definitions ordered by directory name.

    Raises:
        ValueError: If any discovered Skill is malformed or names collide.
    """
    definitions = tuple(
        load_skill_directory(entry)
        for entry in sorted(root.iterdir(), key=lambda item: item.name)
        if entry.is_dir() and entry.joinpath("SKILL.md").is_file()
    )
    names = [definition.name for definition in definitions]
    if len(names) != len(set(names)):
        raise ValueError("Built-in Skill names must be unique")
    return definitions


def load_skill_directory(directory: _Traversable) -> SkillDefinition:
    """Load one standard directory and close every content or access gap.

    Args:
        directory: Immediate Skill folder containing a required ``SKILL.md``.

    Returns:
        One immutable runtime definition with a separate Reference collection.
    """
    source = _read_utf8(directory.joinpath("SKILL.md"), label="SKILL.md")
    metadata, instructions = _parse_standard_skill(source, directory.name)
    runtime = _load_runtime_manifest(directory)
    references = _load_references(directory, instructions)
    if references and _FILE_READ_TOOL_NAME not in runtime.required_tools:
        raise ValueError(
            f"Skill {directory.name} links References but does not require file.read"
        )
    try:
        manifest = SkillManifest(
            name=metadata["name"],
            description=metadata["description"],
            version=runtime.version,
            required_tools=runtime.required_tools,
            required_context_sources=runtime.required_context_sources,
            risk_level=runtime.risk_level,
        )
        return SkillDefinition(
            manifest=manifest,
            instructions=instructions,
            references=references,
        )
    except ValidationError as exc:
        raise ValueError(f"Invalid Skill definition in {directory.name}") from exc


def _parse_standard_skill(source: str, directory_name: str) -> tuple[dict[str, str], str]:
    """Separate strict standard frontmatter from the bounded core body."""
    if not source.startswith("---\n"):
        raise ValueError(f"Skill {directory_name} SKILL.md needs YAML frontmatter")
    parts = source.split("---", 2)
    if len(parts) != 3:
        raise ValueError(f"Skill {directory_name} SKILL.md frontmatter is incomplete")
    try:
        raw_metadata = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        raise ValueError(f"Skill {directory_name} frontmatter is invalid YAML") from exc
    if not isinstance(raw_metadata, dict) or set(raw_metadata) != _STANDARD_FRONTMATTER_KEYS:
        raise ValueError(
            f"Skill {directory_name} frontmatter must contain only name and description"
        )
    name = raw_metadata.get("name")
    description = raw_metadata.get("description")
    if not isinstance(name, str) or not _SKILL_NAME.fullmatch(name) or len(name) > 64:
        raise ValueError(f"Skill {directory_name} frontmatter has an invalid name")
    if name != directory_name:
        raise ValueError(
            f"Skill name {name} does not match directory name {directory_name}"
        )
    if not isinstance(description, str) or not description.strip() or len(description) > 1_024:
        raise ValueError(f"Skill {directory_name} frontmatter has an invalid description")
    instructions = parts[2].strip()
    _validate_text(instructions, label=f"Skill {directory_name} body")
    return {"name": name, "description": description.strip()}, instructions


def _load_runtime_manifest(directory: _Traversable) -> _RuntimeSettings:
    """Load optional RESTScope runtime requirements without accepting extras."""
    path = directory.joinpath("restscope.yaml")
    if not path.is_file():
        return _RuntimeSettings(
            version=None,
            risk_level="low",
            required_tools=(),
            required_context_sources=(),
        )
    source = _read_utf8(path, label=f"{directory.name}/restscope.yaml")
    try:
        raw = yaml.safe_load(source)
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise TypeError("manifest must be a mapping")
        manifest = _RuntimeManifest.model_validate(raw)
    except (ValidationError, yaml.YAMLError, ValueError) as exc:
        raise ValueError(f"Invalid restscope.yaml for Skill {directory.name}") from exc
    for field_name, values in (
        ("required_tools", manifest.required_tools),
        ("required_context_sources", manifest.required_context_sources),
    ):
        if len(values) != len(set(values)):
            raise ValueError(
                f"Skill {directory.name} {field_name} entries must be unique"
            )
    return _RuntimeSettings(
        version=manifest.version,
        risk_level=manifest.risk_level,
        required_tools=manifest.required_tools,
        required_context_sources=manifest.required_context_sources,
    )


def _load_references(
    directory: _Traversable,
    instructions: str,
) -> tuple[SkillReference, ...]:
    """Register exactly the directly linked one-level Markdown References."""
    linked_paths: list[str] = []
    for target in _MARKDOWN_LINK.findall(instructions):
        if not target.startswith("references/"):
            continue
        if not _REFERENCE_PATH.fullmatch(target):
            raise ValueError(f"Invalid Reference link in Skill {directory.name}: {target}")
        if target in linked_paths:
            raise ValueError(
                f"Skill {directory.name} links Reference more than once: {target}"
            )
        linked_paths.append(target)

    references_directory = directory.joinpath("references")
    packaged_paths: set[str] = set()
    if references_directory.is_dir():
        for entry in references_directory.iterdir():
            if not entry.is_file() or not entry.name.endswith(".md"):
                raise ValueError(
                    f"Skill {directory.name} references must be one-level Markdown files"
                )
            packaged_paths.add(f"references/{entry.name}")

    linked_set = set(linked_paths)
    missing = linked_set - packaged_paths
    if missing:
        raise ValueError(
            f"Skill {directory.name} linked Reference is missing: {min(missing)}"
        )
    orphaned = packaged_paths - linked_set
    if orphaned:
        raise ValueError(
            f"Skill {directory.name} Reference is not linked: {min(orphaned)}"
        )

    loaded: list[SkillReference] = []
    for path in linked_paths:
        content = _read_utf8(directory.joinpath(path), label=f"{directory.name}/{path}")
        content = content.strip()
        _validate_text(content, label=f"Skill Reference {directory.name}/{path}")
        loaded.append(SkillReference(path=path, content=content))
    return tuple(loaded)


def _read_utf8(path: _Traversable, *, label: str) -> str:
    """Decode one package resource with a stable safe error on bad bytes."""
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be UTF-8") from exc


def _validate_text(value: str, *, label: str) -> None:
    """Reject blank or oversized model-facing instruction text."""
    if not value.strip():
        raise ValueError(f"{label} must not be blank")
    if len(value) > _MAX_TEXT_CHARS:
        raise ValueError(f"{label} exceeds 24000 characters")
