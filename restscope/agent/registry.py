"""Index immutable Agent Profiles before the Harness can launch a session."""

from __future__ import annotations

from collections.abc import Iterable

from .profile import AgentProfile


class AgentProfileRegistry:
    """Reject duplicate Profiles and expose exact-name lookup."""

    def __init__(self, profiles: Iterable[AgentProfile] = ()) -> None:
        """Freeze Profiles in declaration order without accepting replacement."""
        indexed: dict[str, AgentProfile] = {}
        for profile in profiles:
            if profile.name in indexed:
                raise ValueError(f"Agent Profile is already registered: {profile.name}")
            indexed[profile.name] = profile
        self._profiles = indexed

    def get(self, name: str) -> AgentProfile:
        """Return one exact Profile or explain the missing launch configuration."""
        try:
            return self._profiles[name]
        except KeyError as exc:
            raise ValueError(f"Unknown Agent Profile: {name}") from exc

    def profiles(self) -> tuple[AgentProfile, ...]:
        """Return Profiles in stable declaration order."""
        return tuple(self._profiles.values())
