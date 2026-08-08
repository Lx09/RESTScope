"""Safety policy for skill metadata."""

from __future__ import annotations

from restscope.skills.manifest import SkillDefinition


class SkillPolicy:
    """Reject high-risk Skills after explicit Profile selection."""

    def is_allowed(self, *, skill: SkillDefinition) -> bool:
        """Return whether one selected Skill may run in the supplied state."""
        return skill.manifest.risk_level != "high"
