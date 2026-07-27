"""Safety policy for skill metadata."""

from __future__ import annotations

from restscope.capabilities.skills.skill_manifest import SkillManifest


class SkillPolicy:
    """Permit low/medium-risk skills only for explicitly allowed roles."""

    def is_allowed(self, *, skill: SkillManifest, role: str, state: dict) -> bool:
        """
        Return whether allowed applies in the policy-controlled model tool boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        del state
        if role not in skill.allowed_roles:
            return False
        return skill.risk_level != "high"
