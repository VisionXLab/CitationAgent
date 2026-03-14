from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Protocol

from citationclaw.app.config_manager import AppConfig


@dataclass
class SkillContext:
    """Runtime context shared by all skills."""

    config: AppConfig
    log: Callable[[str], None]
    progress: Optional[Callable[[int, int], None]] = None
    cancel_check: Optional[Callable[[], bool]] = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillResult:
    """Standardized skill result envelope."""

    name: str
    data: Dict[str, Any] = field(default_factory=dict)


class Skill(Protocol):
    """Skill protocol used by registry/runtime."""

    name: str

    async def run(self, ctx: SkillContext, **kwargs) -> SkillResult:
        ...
