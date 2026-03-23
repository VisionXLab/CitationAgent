from __future__ import annotations

from pathlib import Path

from citationclaw.skills.base import SkillContext, SkillResult
from citationclaw.core.phase1_cache import Phase1Cache
from citationclaw.core.scraper import GoogleScholarScraper


class CitationFetchSkill:
    name = "phase1_citation_fetch"

    async def run(self, ctx: SkillContext, **kwargs) -> SkillResult:
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

        # ── 完整缓存命中：直接从缓存重建 JSONL，跳过爬虫 ──────────────────
        if cache.is_complete(url):
            ctx.log(f"💾 [Phase1缓存] 命中完整缓存，跳过爬取: {url[:60]}...")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(cache.build_jsonl(url), encoding="utf-8")
            ctx.log(f"💾 Phase1缓存复用: {len(cache._data.get(url, {}).get('papers', {}))} 篇论文")
            return SkillResult(name=self.name, data={"output_file": str(out), "from_cache": True})

        # ── 定义 page_callback：每页写入缓存 ──────────────────────────────
        async def on_page(paper_dict: dict, year):
            await cache.add_papers(url, paper_dict, year=year)

        # ── 年份遍历模式：通过 year_complete_callback 标记完成 ───────────
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
                int(y) for y, v in cache._data.get(url, {}).get("years", {}).items()
                if v.get("complete")
            ) if enable_year_traverse else None,
        )

        # ── 标记完整完成（仅在未取消时）──────────────────────────────────
        if not (ctx.cancel_check and ctx.cancel_check()):
            await cache.mark_complete(url)
            ctx.log(f"💾 Phase1缓存已保存: {len(cache._data.get(url, {}).get('papers', {}))} 篇论文")

        return SkillResult(name=self.name, data={"output_file": str(out), "from_cache": False})
