from __future__ import annotations

from pathlib import Path

from citationclaw.skills.base import SkillContext, SkillResult
from citationclaw.core.citing_description_cache import CitingDescriptionCache
from citationclaw.core.citing_description_searcher import CitingDescriptionSearcher


class CitationDescriptionSkill:
    name = "phase4_citation_desc"

    async def run(self, ctx: SkillContext, **kwargs) -> SkillResult:
        config = ctx.config
        input_excel = Path(kwargs["input_excel"])
        output_excel = Path(kwargs["output_excel"])
        parallel_workers = kwargs.get("parallel_workers", config.parallel_author_search)
        quota_event = kwargs.get("quota_event")

        desc_cache = kwargs.get("desc_cache") or CitingDescriptionCache()
        desc_searcher = CitingDescriptionSearcher(
            api_key=(config.gemini_api_key or config.openai_api_key),
            base_url=config.openai_base_url,
            model=config.openai_model,
            log_callback=ctx.log,
            progress_callback=ctx.progress or (lambda _c, _t: None),
            cache=desc_cache,
            cancel_event=quota_event,
        )
        await desc_searcher.search(
            input_excel=input_excel,
            output_excel=output_excel,
            parallel_workers=parallel_workers,
            cancel_check=lambda: (ctx.cancel_check() if ctx.cancel_check else False) or (quota_event is not None and quota_event.is_set()),
        )
        stats = desc_cache.stats()
        return SkillResult(
            name=self.name,
            data={
                "output_excel": str(output_excel),
                "cache_stats": stats,
            },
        )
