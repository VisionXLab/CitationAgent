from __future__ import annotations

from pathlib import Path

from citationclaw.skills.base import SkillContext, SkillResult
from citationclaw.core.author_searcher import AuthorSearcher


class AuthorIntelSkill:
    name = "phase2_author_intel"

    async def run(self, ctx: SkillContext, **kwargs) -> SkillResult:
        config = ctx.config
        input_file = Path(kwargs["input_file"])
        output_file = Path(kwargs["output_file"])
        sleep_seconds = kwargs.get("sleep_seconds", config.sleep_between_authors)
        parallel_workers = kwargs.get("parallel_workers", config.parallel_author_search)
        citing_paper = kwargs.get("citing_paper")
        target_paper_authors = kwargs.get("target_paper_authors", "")
        author_cache = kwargs.get("author_cache")
        quota_event = kwargs.get("quota_event")

        searcher = AuthorSearcher(
            api_key=(config.gemini_api_key or config.openai_api_key),
            base_url=config.openai_base_url,
            model=config.openai_model,
            log_callback=ctx.log,
            progress_callback=ctx.progress or (lambda _c, _t: None),
            prompt1=config.author_search_prompt1,
            prompt2=config.author_search_prompt2,
            enable_renowned_scholar=config.enable_renowned_scholar_filter,
            renowned_scholar_model=config.renowned_scholar_model,
            renowned_scholar_prompt=config.renowned_scholar_prompt,
            enable_author_verification=config.enable_author_verification,
            author_verify_model=config.author_verify_model,
            author_verify_prompt=config.author_verify_prompt,
            debug_mode=config.debug_mode,
            target_paper_authors=target_paper_authors,
            author_cache=author_cache,
            cancel_event=quota_event,
        )

        await searcher.search(
            input_file=input_file,
            output_file=output_file,
            sleep_seconds=sleep_seconds,
            parallel_workers=parallel_workers,
            cancel_check=ctx.cancel_check,
            citing_paper=citing_paper,
        )
        return SkillResult(name=self.name, data={"output_file": str(output_file)})
