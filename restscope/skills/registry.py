"""Immutable registry for loaded Skill instructions and their requirements."""

from __future__ import annotations

from collections.abc import Iterable

from restscope.skills.manifest import SkillDefinition


class SkillRegistry:
    """Index loaded Skill definitions once before any Agent may start."""

    def __init__(self, skills: Iterable[SkillDefinition] = ()) -> None:
        """Freeze loaded instructions in declaration order without replacement."""
        indexed: dict[str, SkillDefinition] = {}
        for skill in skills:
            if skill.name in indexed:
                raise ValueError(f"Skill is duplicated: {skill.name}")
            indexed[skill.name] = skill
        self._skills = indexed

    def get(self, name: str) -> SkillDefinition:
        """Return one exact Skill or explain an unknown Profile selection."""
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"Unknown Skill: {name}") from exc

    def select(self, names: tuple[str, ...]) -> tuple[SkillDefinition, ...]:
        """Resolve only the ordered Skill names granted by one Agent Profile."""
        return tuple(self.get(name) for name in names)

    def definitions(self) -> tuple[SkillDefinition, ...]:
        """Return loaded definitions in stable discovery or declaration order."""
        return tuple(self._skills.values())
