from __future__ import annotations

from pathlib import Path

from citationclaw.skills.base import SkillContext, SkillResult
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
        await scraper.scrape(
            url=url,
            output_file=out,
            start_page=start_page,
            sleep_seconds=sleep_seconds,
            cancel_check=ctx.cancel_check,
            enable_year_traverse=enable_year_traverse,
        )
        return SkillResult(name=self.name, data={"output_file": str(out)})
