"""Registry for skill metadata."""

from __future__ import annotations

from restscope.capabilities.skills.skill_manifest import SkillManifest


class SkillRegistry:
    """Register and select skill manifests without executing them."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillManifest] = {}

    def register(self, skill: SkillManifest) -> None:
        """
        Handle register as part of the policy-controlled model tool boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        self._skills[skill.name] = skill

    def get(self, name: str) -> SkillManifest:
        """
        Handle get as part of the policy-controlled model tool boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        return self._skills[name]

    def select_for_role(self, role: str) -> list[SkillManifest]:
        """
        Select for role for the policy-controlled model tool boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        return [skill for skill in self._skills.values() if role in skill.allowed_roles]
