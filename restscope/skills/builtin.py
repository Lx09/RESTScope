"""Discover and cache RESTScope's installed standard Skill Catalog.

Built-in discovery grants no Agent access. The Harness still resolves only the
Skill names listed by each Profile and separately validates every Tool and
Context Source required by that selected definition.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files

from .loader import discover_skill_definitions
from .catalog import SkillCatalog


@lru_cache(maxsize=1)
def builtin_skill_catalog() -> SkillCatalog:
    """Return standard Skills packaged below ``restscope.builtin_skills``."""
    root = files("restscope.builtin_skills")
    return SkillCatalog(discover_skill_definitions(root))
