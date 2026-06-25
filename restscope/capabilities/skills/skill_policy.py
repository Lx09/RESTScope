"""Safety policy for skill metadata."""

from __future__ import annotations

from restscope.capabilities.skills.skill_manifest import SkillManifest


class SkillPolicy:
    """Permit low/medium-risk skills only for explicitly allowed roles."""

    def is_allowed(self, *, skill: SkillManifest, role: str, state: dict) -> bool:
        del state
        if role not in skill.allowed_roles:
            return False
        return skill.risk_level != "high"
