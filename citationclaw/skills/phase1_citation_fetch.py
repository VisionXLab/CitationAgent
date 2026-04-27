from __future__ import annotations

from pathlib import Path

from citationclaw.skills.base import SkillContext, SkillResult
from citationclaw.core.phase1_cache import Phase1Cache
from citationclaw.core.scraper import GoogleScholarScraper


class CitationFetchSkill:
    name = "phase1_citation_fetch"

    async def run(self, ctx: SkillContext, **kwargs) -> SkillResult:
        try:
            return await self._run_inner(ctx, **kwargs)
        except Exception as e:
            ctx.log(f"[Phase1] fatal error: {e}")
            raise

    async def _run_inner(self, ctx: SkillContext, **kwargs) -> SkillResult:
        config = ctx.config
        url: str = kwargs["url"]
        output_file = kwargs.get("output_file")
        probe_only: bool = kwargs.get("probe_only", False)
        start_page: int = kwargs.get("start_page", 0)
        sleep_seconds: int = kwargs.get("sleep_seconds", config.sleep_between_pages)
        enable_year_traverse: bool = kwargs.get("enable_year_traverse", config.enable_year_traverse)
        cost_tracker = kwargs.get("cost_tracker")

        scraper = GoogleScholarScraper(
            api_keys=config.scraper_api_keys,
            log_callback=ctx.log,
            progress_callback=ctx.progress or (lambda _c, _t: None),
            debug_mode=config.debug_mode,
            premium=config.scraper_premium,
            ultra_premium=config.scraper_ultra_premium,
            retry_max_attempts=config.retry_max_attempts,
            retry_intervals=config.retry_intervals,
            session=config.scraper_session,
            no_filter=config.scholar_no_filter,
            geo_rotate=config.scraper_geo_rotate,
            dc_retry_max_attempts=config.dc_retry_max_attempts,
            cost_tracker=cost_tracker,
        )

        if probe_only:
            citation_count, estimated_pages = await scraper.detect_citation_count(url)
            return SkillResult(
                name=self.name,
                data={
                    "citation_count": citation_count,
                    "estimated_pages": estimated_pages,
                },
            )

        if output_file is None:
            raise ValueError("phase1_citation_fetch requires output_file when probe_only=False")

        out = Path(output_file)
        cache = Phase1Cache()

        # -- full cache hit: rebuild JSONL from cache, skip scraping --
        if cache.is_complete(url):
            ctx.log(f"[Phase1 cache] full hit, skipping scrape: {url[:60]}...")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(cache.build_jsonl(url), encoding="utf-8")
            ctx.log(f"[Phase1 cache] reused {cache.paper_count(url)} papers")
            return SkillResult(name=self.name, data={"output_file": str(out), "from_cache": True})

        # -- page callback: write each page into cache --
        async def on_page(paper_dict: dict, year):
            await cache.add_papers(url, paper_dict, year=year)

        # -- year traverse: mark year complete --
        async def on_year_complete(year: int):
            await cache.mark_year_complete(url, year)

        await scraper.scrape(
            url=url,
            output_file=out,
            start_page=start_page,
            sleep_seconds=sleep_seconds,
            cancel_check=ctx.cancel_check,
            enable_year_traverse=enable_year_traverse,
            page_callback=on_page,
            year_complete_callback=on_year_complete,
            cached_years=set(
                int(y) for y, v in cache.cached_years(url).items()
                if v.get("complete")
            ) if enable_year_traverse else None,
        )

        # -- mark complete (only if not cancelled) --
        if not (ctx.cancel_check and ctx.cancel_check()):
            await cache.mark_complete(url)
            ctx.log(f"[Phase1 cache] saved {cache.paper_count(url)} papers")

        return SkillResult(name=self.name, data={"output_file": str(out), "from_cache": False})
