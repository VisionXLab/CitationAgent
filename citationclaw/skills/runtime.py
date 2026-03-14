from __future__ import annotations

from citationclaw.app.config_manager import AppConfig
from citationclaw.skills.base import SkillContext
from citationclaw.skills.registry import build_default_registry


class SkillsRuntime:
    """Skills execution runtime for phase-based pipelines."""

    def __init__(self):
        self.registry = build_default_registry()

    async def run(
        self,
        skill_name: str,
        *,
        config: AppConfig,
        log,
        progress=None,
        cancel_check=None,
        extras=None,
        **kwargs,
    ):
        ctx = SkillContext(
            config=config,
            log=log,
            progress=progress,
            cancel_check=cancel_check,
            extras=extras or {},
        )
        skill = self.registry.get(skill_name)
        result = await skill.run(ctx, **kwargs)
        return result.data
