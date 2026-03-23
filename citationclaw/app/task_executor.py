import asyncio
import json as _json
from pathlib import Path
from typing import Optional, List, Tuple
from datetime import datetime
from citationclaw.core.author_cache import AuthorInfoCache
from citationclaw.core.citing_description_cache import CitingDescriptionCache
from citationclaw.core.pipeline_adapter import PipelineAdapter
from citationclaw.core.metadata_collector import MetadataCollector
from citationclaw.core.metadata_cache import MetadataCache
from citationclaw.core.self_citation import SelfCitationDetector
from citationclaw.core.scholar_prefilter import ScholarPreFilter
from citationclaw.core.scholar_search_agent import ScholarSearchAgent
from citationclaw.core.pdf_downloader import PDFDownloader
from citationclaw.core.pdf_mineru_parser import MinerUParser
from citationclaw.core.pdf_parse_cache import PDFParseCache
from citationclaw.core.pdf_author_extractor import PDFAuthorExtractor
from citationclaw.core.affiliation_validator import AffiliationValidator
from citationclaw.app.log_manager import LogManager
from citationclaw.app.config_manager import AppConfig, ConfigManager, DATA_DIR
from citationclaw.app.cost_tracker import CostTracker
from citationclaw.skills.runtime import SkillsRuntime


def _mask_token(token: str) -> str:
    """Fully mask a token for safe logging."""
    if not token:
        return "''"
    return f"***({len(token)} chars)"


class TaskExecutor:
    def __init__(self, log_manager: LogManager, config_manager: ConfigManager):
        """
        任务执行器,负责协调整个工作流

        Args:
            log_manager: 日志管理器
            config_manager: 配置管理器（用于获取最新配置）
        """
        self.log_manager = log_manager
        self.config_manager = config_manager
        self.current_task: Optional[asyncio.Task] = None
        self.is_running = False
        self.should_cancel = False

        # 保存阶段1的结果，供阶段2使用
        self.stage1_result: Optional[dict] = None

        # 年份遍历用户确认状态
        self._year_traverse_event: Optional[asyncio.Event] = None
        self._year_traverse_choice: bool = False   # True = 用户同意开启
        self._year_traverse_prompted: bool = False  # 本次运行已提示过，不再重复
        self.skills_runtime = SkillsRuntime()

    async def _run_skill(self, skill_name: str, config: AppConfig, **kwargs):
        """Execute one pipeline skill with shared runtime context."""
        result = await self.skills_runtime.run(
            skill_name,
            config=config,
            log=self.log_manager.info,
            progress=self.log_manager.update_progress,
            cancel_check=lambda: self.should_cancel,
            **kwargs,
        )
        # Validate output files exist if returned in result
        for key in ("output_file", "excel_output", "json_output", "output_excel", "output_html"):
            if key in result:
                p = Path(result[key])
                if not p.exists():
                    self.log_manager.warning(f"Skill {skill_name} reported {key}={p} but file does not exist")
        return result

    async def _run_new_phase2_and_3(
        self,
        citing_files: List[Tuple[Path, str]],
        result_dir: Path,
        output_prefix: str,
        config: AppConfig,
        canonical_titles: List[str] = None,
    ) -> Optional[Tuple[Path, Path, Path]]:
        """New Phase 2 (structured API metadata) + Phase 3 (scholar assess) pipeline.

        Returns: (merged_jsonl, excel_file, json_file) or None on failure.
        """
        adapter = PipelineAdapter()
        metadata_cache = MetadataCache()
        collector = MetadataCollector(
            s2_api_key=getattr(config, 's2_api_key', None),
        )
        self_cite_detector = SelfCitationDetector()
        prefilter = ScholarPreFilter()

        # ── Phase 2a: 查询目标论文作者（用于自引检测）──
        target_authors_map: dict = {}  # canonical → [{name, affiliation}]
        for ct in (canonical_titles or []):
            self.log_manager.info(f"[自引检测] 查询目标论文作者: {ct[:50]}...")
            try:
                target_meta = await collector.collect(ct)
                if target_meta:
                    target_authors_map[ct] = target_meta.get("authors", [])
                    names = [a.get("name", "") for a in target_authors_map[ct]]
                    self.log_manager.info(f"  → 找到 {len(names)} 位作者: {', '.join(names[:5])}")
                else:
                    target_authors_map[ct] = []
            except Exception:
                target_authors_map[ct] = []

        # ── Phase 2b: 作者信息采集 (structured APIs) ──
        self.log_manager.info("=" * 50)
        self.log_manager.info("Phase 2 · 作者信息采集: 通过 OpenAlex / S2 / arXiv 查询结构化数据")
        self.log_manager.info("=" * 50)

        # Flatten all Phase 1 files into papers
        all_papers: List[Tuple[dict, str]] = []  # (paper_dict, canonical_title)
        for citing_file, canonical in citing_files:
            if not citing_file.exists():
                continue
            flat = adapter.flatten_phase1_file(citing_file)
            for p in flat:
                all_papers.append((p, canonical))

        total = len(all_papers)
        if total == 0:
            self.log_manager.warning("Phase 1 未找到任何施引论文")
            return None
        self.log_manager.info(f"共 {total} 篇施引论文待查询")

        # Query metadata for each paper (parallel, 10 workers)
        all_author_dicts: List[dict] = []
        s2_author_ids: dict = {}
        oa_author_ids: dict = {}
        api_hits = 0
        api_queries = 0

        # Dedup first
        seen_dedup: set = set()
        deduped_papers: List[Tuple[int, dict, str]] = []  # (original_index, paper, canonical)
        for i, (paper, canonical) in enumerate(all_papers):
            title = paper["paper_title"]
            link = paper["paper_link"]
            dedup_key = f"{link or title.lower()}::{canonical}"
            if dedup_key in seen_dedup:
                continue
            seen_dedup.add(dedup_key)
            deduped_papers.append((i, paper, canonical))

        total = len(deduped_papers)
        self.log_manager.info(f"去重后 {total} 篇，开始并行查询 (10 workers)...")

        # Parallel fetch with semaphore
        sem = asyncio.Semaphore(10)
        results_slots: List[Optional[dict]] = [None] * total  # ordered results

        async def _fetch_one(idx: int, paper: dict, canonical: str):
            nonlocal api_hits, api_queries
            async with sem:
                if self.should_cancel:
                    return
                title = paper["paper_title"]
                cached = await metadata_cache.get(title=title)
                if cached:
                    metadata = cached
                    api_hits += 1
                else:
                    metadata = await collector.collect(title)
                    if metadata:
                        await metadata_cache.update(metadata.get("doi", ""), title, metadata)
                    api_queries += 1
                results_slots[idx] = metadata

        try:
            tasks = [
                _fetch_one(idx, paper, canonical)
                for idx, (_, paper, canonical) in enumerate(deduped_papers)
            ]
            await asyncio.gather(*tasks)
        finally:
            await metadata_cache.flush()

        # Build records_data in order and log sequentially
        records_data: List[Tuple[dict, Optional[dict], str]] = []
        for idx, (orig_i, paper, canonical) in enumerate(deduped_papers):
            metadata = results_slots[idx]
            src_tag = ",".join((metadata or {}).get("sources", [])) or "Scholar"
            if metadata and any(k in src_tag for k in ["openalex", "s2", "arxiv"]):
                pass  # API result
            elif metadata:
                src_tag = "缓存" if api_hits > 0 else src_tag
            n_authors = len((metadata or {}).get("authors", []))
            self.log_manager.info(
                f"  [{idx+1}/{total}] [{src_tag}] {paper['paper_title'][:55]}... ({n_authors} 位作者)"
            )

            # Collect authors + IDs for Phase 3 enrichment
            for a in (metadata or {}).get("authors", []):
                all_author_dicts.append(a)
                name_lower = a.get("name", "").lower()
                s2_id = a.get("s2_id", "")
                oa_id = a.get("openalex_id", "")
                if s2_id:
                    s2_author_ids[name_lower] = s2_id
                if oa_id:
                    oa_author_ids[name_lower] = oa_id

            records_data.append((paper, metadata, canonical))
            self.log_manager.update_progress(idx + 1, total)

        self.log_manager.success(
            f"Phase 2 完成: 缓存命中 {api_hits} / 新查询 {api_queries} / "
            f"共 {len(records_data)} 篇"
        )

        # ── Phase 2c: 查询作者 h-index (S2 author API) ──
        # Deduplicate authors for enrichment
        seen_authors: dict = {}
        for a in all_author_dicts:
            name = a.get("name", "").strip()
            if name and name.lower() not in seen_authors:
                seen_authors[name.lower()] = a
        unique_authors = list(seen_authors.values())

        # Batch lookup author details via OpenAlex Author API (parallel)
        # Gets h-index AND fills in missing affiliation/country/citation_count
        author_details: dict = {}  # name_lower → {h_index, affiliation, country, citation_count}
        oa_lookups = [(name, oid) for name, oid in oa_author_ids.items() if oid]

        if oa_lookups:
            self.log_manager.info(f"[作者详情] 并行查询 {len(oa_lookups)} 位作者 (OpenAlex Author API)...")
            detail_sem = asyncio.Semaphore(10)

            async def _fetch_author_detail(name_lower: str, oid: str):
                async with detail_sem:
                    try:
                        data = await collector.openalex.get_author(oid)
                        if data:
                            author_details[name_lower] = {
                                "h_index": data.get("h_index", 0),
                                "affiliation": data.get("affiliation", ""),
                                "citation_count": data.get("citation_count", 0),
                            }
                    except Exception:
                        pass

            await asyncio.gather(*[_fetch_author_detail(n, o) for n, o in oa_lookups])

            enriched_h = 0
            enriched_affil = 0
            # Enrich all author dicts: h-index, affiliation, citation_count
            for a in all_author_dicts:
                name_lower = a.get("name", "").strip().lower()
                detail = author_details.get(name_lower)
                if not detail:
                    continue
                if detail.get("h_index") and not a.get("h_index"):
                    a["h_index"] = detail["h_index"]
                    enriched_h += 1
                if detail.get("affiliation") and not a.get("affiliation"):
                    a["affiliation"] = detail["affiliation"]
                    enriched_affil += 1
                if detail.get("citation_count") and not a.get("citation_count"):
                    a["citation_count"] = detail["citation_count"]

            # Also enrich in records_data (for adapter output)
            for _, metadata, _ in records_data:
                if not metadata:
                    continue
                for a in metadata.get("authors", []):
                    name_lower = a.get("name", "").strip().lower()
                    detail = author_details.get(name_lower)
                    if not detail:
                        continue
                    if detail.get("h_index") and not a.get("h_index"):
                        a["h_index"] = detail["h_index"]
                    if detail.get("affiliation") and not a.get("affiliation"):
                        a["affiliation"] = detail["affiliation"]
                    if detail.get("citation_count") and not a.get("citation_count"):
                        a["citation_count"] = detail["citation_count"]

            # Enrich unique_authors for prefilter
            for a in unique_authors:
                name_lower = a.get("name", "").strip().lower()
                detail = author_details.get(name_lower)
                if detail and detail.get("h_index") and not a.get("h_index"):
                    a["h_index"] = detail["h_index"]
                if detail and detail.get("affiliation") and not a.get("affiliation"):
                    a["affiliation"] = detail["affiliation"]

            self.log_manager.info(
                f"  → h-index: {enriched_h} 位补充 / 机构: {enriched_affil} 位补充 / "
                f"共 {len(author_details)} 位查到详情"
            )

        # S2 Author API fallback for authors still missing affiliation
        s2_fallback = []
        for a in all_author_dicts:
            if not a.get("affiliation") and a.get("s2_id"):
                name_lower = a.get("name", "").strip().lower()
                if name_lower not in author_details:  # Not already enriched
                    s2_fallback.append((name_lower, a.get("s2_id"), a))

        if s2_fallback:
            self.log_manager.info(f"[S2补充] 查询 {len(s2_fallback)} 位仍缺机构的作者 (Semantic Scholar)...")
            s2_sem = asyncio.Semaphore(5)  # S2 rate limit lower
            s2_enriched = 0

            async def _fetch_s2_author(name_lower: str, s2_id: str, author_dict: dict):
                nonlocal s2_enriched
                async with s2_sem:
                    try:
                        data = await collector.s2.get_author(s2_id)
                        if data:
                            if data.get("affiliation") and not author_dict.get("affiliation"):
                                author_dict["affiliation"] = data["affiliation"]
                                s2_enriched += 1
                            if data.get("h_index") and not author_dict.get("h_index"):
                                author_dict["h_index"] = data["h_index"]
                            if data.get("citation_count") and not author_dict.get("citation_count"):
                                author_dict["citation_count"] = data["citation_count"]
                    except Exception:
                        pass

            await asyncio.gather(*[_fetch_s2_author(n, s, a) for n, s, a in s2_fallback])
            if s2_enriched:
                self.log_manager.info(f"  → S2 补充了 {s2_enriched} 位作者的机构信息")

        await collector.close()

        # ── Phase 2 · PDF 下载 + 解析 + 交叉验证 ──
        self.log_manager.info("=" * 50)
        self.log_manager.info("Phase 2 · PDF 并行下载 + MinerU 解析 + 作者交叉验证")
        self.log_manager.info("=" * 50)

        downloader = PDFDownloader()
        parser = MinerUParser()
        parse_cache = PDFParseCache()
        author_extractor = PDFAuthorExtractor(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url,
            model=config.dashboard_model or config.openai_model,  # Use lightweight model
        )
        validator = AffiliationValidator()

        # Build download-friendly dicts with all URL sources (including GS paper_link)
        dl_papers = []
        for paper, metadata, canonical in records_data:
            dl_papers.append({
                "doi": (metadata or {}).get("doi", ""),
                "pdf_url": (metadata or {}).get("pdf_url", ""),
                "oa_pdf_url": (metadata or {}).get("oa_pdf_url", ""),
                "paper_link": paper.get("paper_link", ""),  # GS link to publisher
                "Paper_Title": paper.get("paper_title", ""),
                "title": paper.get("paper_title", ""),
            })

        # Parallel PDF download (10 workers)
        self.log_manager.info(f"[PDF下载] 并行下载 {len(dl_papers)} 篇 (10 workers)...")
        pdf_paths = await downloader.batch_download(
            dl_papers, concurrency=10, log=self.log_manager.info
        )
        downloaded = sum(1 for p in pdf_paths if p)
        self.log_manager.success(f"PDF 下载: {downloaded}/{len(dl_papers)} 篇成功")

        # Parse + extract authors + cross-validate (parallel, 5 workers for MinerU)
        if downloaded > 0:
            self.log_manager.info(f"[MinerU] 解析 {downloaded} 篇 PDF (txt模式) + LLM 提取作者...")
            parse_sem = asyncio.Semaphore(3)  # MinerU is CPU-heavy
            validated_count = 0
            parse_done = 0

            async def _parse_and_validate(idx: int):
                nonlocal validated_count, parse_done
                pdf_path = pdf_paths[idx]
                if not pdf_path:
                    return
                paper, metadata, canonical = records_data[idx]
                title = paper.get("paper_title", "")
                pkey = parser.paper_key({"doi": (metadata or {}).get("doi", ""), "title": title})

                async with parse_sem:
                    # Check cache
                    if parse_cache.has(pkey):
                        cached_authors = parse_cache.get_authors(pkey)
                        if cached_authors:
                            api_authors = (metadata or {}).get("authors", [])
                            merged = validator.validate(api_authors, cached_authors)
                            if metadata:
                                metadata["authors"] = merged
                            validated_count += 1
                            return

                    # Parse PDF
                    parsed = parser.parse(pdf_path, pkey)
                    if not parsed:
                        return

                    # Store parse cache
                    parse_cache.store(pkey, {
                        "title": title,
                        "source": parsed.get("source", ""),
                        "has_content_list": bool(parsed.get("content_list")),
                    })

                    parse_done += 1
                    self.log_manager.info(
                        f"  [解析 {parse_done}/{downloaded}] {title[:50]}... "
                        f"({parsed.get('source','?')})"
                    )

                    # Extract authors from first page via LLM
                    pdf_authors = await author_extractor.extract(
                        parsed.get("first_page_blocks", [])
                    )
                    if pdf_authors:
                        parse_cache.store_authors(pkey, pdf_authors)

                        # Cross-validate: PDF affiliation vs API affiliation
                        api_authors = (metadata or {}).get("authors", [])
                        merged = validator.validate(api_authors, pdf_authors)
                        if metadata:
                            metadata["authors"] = merged
                        validated_count += 1

            await asyncio.gather(*[_parse_and_validate(i) for i in range(len(records_data))])
            self.log_manager.success(
                f"交叉验证: {validated_count} 篇作者机构已更新 (PDF优先)"
            )

        await downloader.close()

        # ── Self-citation detection (before Phase 3, affects all downstream) ──
        self.log_manager.info("[自引检测] 标记自引论文...")
        self_cite_map: dict = {}  # index → bool
        non_self_cite_records = []
        self_cite_count = 0
        for i, (paper, metadata, canonical) in enumerate(records_data):
            target_authors = target_authors_map.get(canonical, [])
            paper_authors = (metadata or {}).get("authors", [])
            result = self_cite_detector.check(target_authors, paper_authors)
            is_self = result.get("is_self_citation", False)
            self_cite_map[i] = is_self
            if is_self:
                self_cite_count += 1
            else:
                non_self_cite_records.append((paper, metadata, canonical))

        self.log_manager.info(
            f"[自引检测] {self_cite_count} 篇自引 / {len(records_data)} 篇总计"
            f"（自引论文将跳过学者搜索和引文语境提取）"
        )

        # ── Phase 3: 学者影响力评估（仅非自引论文）──
        self.log_manager.info("=" * 50)
        self.log_manager.info("Phase 3 · 学者影响力评估: 预过滤 + 搜索候选学者")
        self.log_manager.info("=" * 50)

        # Collect authors from NON-self-cite papers only
        non_self_authors: dict = {}
        for paper, metadata, canonical in non_self_cite_records:
            for a in (metadata or {}).get("authors", []):
                name = a.get("name", "").strip()
                if name and name.lower() not in non_self_authors:
                    non_self_authors[name.lower()] = a
        unique_non_self_authors = list(non_self_authors.values())

        # Pre-filter with enriched h-index data
        candidates, non_candidates = prefilter.filter_candidates(unique_non_self_authors)
        self.log_manager.info(
            f"[预过滤] {len(unique_non_self_authors)} 位作者（非自引）→ "
            f"{len(candidates)} 位候选, {len(non_candidates)} 位普通学者"
        )

        # Search candidates per-paper using search LLM (skip self-cite papers)
        scholar_results: dict = {}  # name → {tier, honors, ...}
        search_agent = ScholarSearchAgent(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url,
            model=config.openai_model,
            log_callback=self.log_manager.info,
        )

        candidate_names = {c.get("name", "").strip().lower() for c in candidates}
        papers_with_candidates = []
        for paper, metadata, canonical in non_self_cite_records:
            if not metadata:
                continue
            paper_authors = metadata.get("authors", [])
            if any(a.get("name", "").strip().lower() in candidate_names for a in paper_authors):
                papers_with_candidates.append((paper, metadata, canonical))

        n_search = len(papers_with_candidates)
        self.log_manager.info(f"[搜索LLM] {n_search} 篇非自引论文含候选学者，并行搜索 (10 workers)...")

        # Parallel search LLM calls
        search_sem = asyncio.Semaphore(10)

        async def _search_one(idx: int, paper: dict, metadata: dict):
            async with search_sem:
                if self.should_cancel:
                    return []
                try:
                    return await search_agent.search_paper_authors(
                        paper_title=paper.get("paper_title", ""),
                        authors=metadata.get("authors", []),
                    )
                except Exception:
                    return []

        try:
            tasks = [
                _search_one(idx, paper, metadata)
                for idx, (paper, metadata, canonical) in enumerate(papers_with_candidates)
            ]
            search_results_list = await asyncio.gather(*tasks)
        finally:
            await search_agent.close()

        # Build per-paper scholar map (keyed by paper_title, not scholar name)
        paper_scholar_map: dict = {}  # paper_title_lower → list of scholar dicts
        global_seen_keys: set = set()  # All name variant keys seen across all papers
        global_unique_count = 0

        for idx, (paper, metadata, canonical) in enumerate(papers_with_candidates):
            title = paper.get("paper_title", "")
            raw = search_results_list[idx]
            if not raw:
                self.log_manager.info(f"  [{idx+1}/{n_search}] {title[:50]}... → 无知名学者")
                continue
            found = []
            for r in raw:
                if r.name and r.tier:
                    scholar_dict = {
                        "name": r.name,
                        "tier": r.tier,
                        "honors": [r.honors] if r.honors else [],
                        "affiliation": r.affiliation,
                        "country": r.country,
                        "position": r.position,
                    }
                    found.append(scholar_dict)
                    # Global dedup tracking
                    name_keys = ScholarSearchAgent._extract_name_keys(r.name)
                    if not (name_keys & global_seen_keys):
                        global_unique_count += 1
                    global_seen_keys.update(name_keys)
            if found:
                paper_scholar_map[title.lower().strip()] = found
                labels = "; ".join(f"{s['tier']}: {s['name']}" for s in found)
                self.log_manager.info(f"  [{idx+1}/{n_search}] {title[:50]}... → {labels}")
            else:
                self.log_manager.info(f"  [{idx+1}/{n_search}] {title[:50]}... → 无知名学者")

        self.log_manager.success(
            f"Phase 3 完成: {global_unique_count} 位知名学者（去重）/ {n_search} 篇论文"
        )

        # ── Build merged JSONL with scholar data ──
        merged_file = result_dir / "merged_authors.jsonl"
        record_idx = 0
        with open(merged_file, "w", encoding="utf-8") as f:
            for i, (paper, metadata, canonical) in enumerate(records_data):
                is_self = self_cite_map.get(i, False)

                # Look up scholars by paper title (not by author name matching)
                paper_title_key = paper.get("paper_title", "").lower().strip()
                paper_scholars = [] if is_self else paper_scholar_map.get(paper_title_key, [])

                self_cite_result = {"is_self_citation": is_self, "method": "pre-checked"}

                record_idx += 1
                record = adapter.to_legacy_record(
                    paper=paper,
                    metadata=metadata,
                    self_citation=self_cite_result,
                    renowned_scholars=paper_scholars,
                    citing_paper=canonical,
                    record_index=record_idx,
                )
                f.write(_json.dumps(record, ensure_ascii=False) + "\n")

        # ── Export ──
        self.log_manager.info("Phase 3 · 导出结果")
        excel_file = result_dir / f"{output_prefix}_results.xlsx"
        json_file = result_dir / f"{output_prefix}_results.json"
        await self._run_skill(
            "phase3_export",
            config,
            input_file=merged_file,
            excel_output=excel_file,
            json_output=json_file,
        )

        return merged_file, excel_file, json_file

    async def execute_full_pipeline(
        self,
        url: str,
        config: AppConfig,
        output_prefix: str,
        resume_page: int = 0
    ):
        """
        执行完整流程:抓取 -> 搜索作者 -> 导出
        Supports all 5 phases with the same parameters as execute_for_titles.

        Args:
            url: Google Scholar引用列表URL
            config: 应用配置
            output_prefix: 输出文件前缀
            resume_page: 断点续爬起始页
        """
        # Note: is_running is set synchronously by the caller before creating the task.
        self.should_cancel = False

        # Initialize caches
        author_cache = AuthorInfoCache()
        desc_cache = CitingDescriptionCache()
        cost_tracker = CostTracker()
        self.quota_exceeded_event = asyncio.Event()

        try:
            # 生成带时间戳的文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            result_dir = DATA_DIR / f"result-{timestamp}"
            result_dir.mkdir(parents=True, exist_ok=True)
            file_prefix = f"{output_prefix}-{timestamp}"

            self.log_manager.info(f"文件前缀: {file_prefix}")
            self.log_manager.info(f"结果目录: {result_dir}")

            # ==================== 阶段1: 抓取引用列表 ====================
            self.log_manager.info("=" * 50)
            self.log_manager.info("Phase 1 · 施引文献检索: 开始抓取引用列表")
            self.log_manager.info("=" * 50)

            citing_papers_file = result_dir / f"{file_prefix}_citing_papers.jsonl"
            await self._run_skill(
                "phase1_citation_fetch",
                config,
                url=url,
                output_file=citing_papers_file,
                start_page=resume_page,
                sleep_seconds=config.sleep_between_pages,
                enable_year_traverse=config.enable_year_traverse,
                cost_tracker=cost_tracker,
            )

            if self.should_cancel:
                self.log_manager.warning("任务已被用户取消")
                return

            self.log_manager.success("Phase 1 · 施引文献检索 完成")

            # ==================== 阶段2: 搜索作者信息 ====================
            self.log_manager.info("=" * 50)
            self.log_manager.info("Phase 2 · 作者信息采集: 开始搜索作者学术信息")
            self.log_manager.info("=" * 50)

            author_info_file = result_dir / f"{file_prefix}_author_information.jsonl"
            await self._run_skill(
                "phase2_author_intel",
                config,
                input_file=citing_papers_file,
                output_file=author_info_file,
                sleep_seconds=config.sleep_between_authors,
                parallel_workers=config.parallel_author_search,
                author_cache=author_cache,
                quota_event=self.quota_exceeded_event,
            )

            if self.should_cancel:
                self.log_manager.warning("任务已被用户取消")
                return
            if self.quota_exceeded_event.is_set():
                self._handle_quota_exceeded()
                return

            self.log_manager.success("Phase 2 · 作者信息采集 完成")

            # ==================== 阶段3: 导出结果 ====================
            self.log_manager.info("=" * 50)
            self.log_manager.info("Phase 3 · 导出结果")
            self.log_manager.info("=" * 50)

            excel_file = result_dir / f"{file_prefix}_author_information.xlsx"
            json_file = result_dir / f"{file_prefix}_author_information.json"

            await self._run_skill(
                "phase3_export",
                config,
                input_file=author_info_file,
                excel_output=excel_file,
                json_output=json_file,
            )

            self.log_manager.success("Phase 3 · 导出 完成")

            # ==================== 阶段4: 引用描述（可选）====================
            citing_desc_excel = excel_file
            if config.enable_citing_description:
                self.log_manager.info("=" * 50)
                self.log_manager.info("Phase 4 · 引文语境提取: 搜索引用描述")
                self.log_manager.info("=" * 50)

                citing_desc_excel = result_dir / f"{file_prefix}_results_with_citing_desc.xlsx"
                await self._run_skill(
                    "phase4_citation_desc",
                    config,
                    input_excel=excel_file,
                    output_excel=citing_desc_excel,
                    parallel_workers=config.parallel_author_search,
                    quota_event=self.quota_exceeded_event,
                    desc_cache=desc_cache,
                )
                if self.quota_exceeded_event.is_set():
                    self._handle_quota_exceeded()
                    return

            # ==================== 阶段5: HTML报告（可选）====================
            html_file = None
            if config.enable_dashboard:
                self.log_manager.info("=" * 50)
                self.log_manager.info("Phase 5 · 报告生成与导出: 生成 HTML 画像报告")
                self.log_manager.info("=" * 50)

                html_file = result_dir / f"{file_prefix}_dashboard.html"
                all_renowned = excel_file.with_stem(excel_file.stem + "_all_renowned_scholar")
                top_renowned = excel_file.with_stem(excel_file.stem + "_top-tier_scholar")

                def _fwd(p: Path) -> str:
                    return str(p).replace("\\", "/")

                await self._run_skill(
                    "phase5_report_generate",
                    config,
                    citing_desc_excel=citing_desc_excel,
                    renowned_all_xlsx=all_renowned,
                    renowned_top_xlsx=top_renowned,
                    output_html=html_file,
                    canonical_titles=[output_prefix],
                    download_filenames={
                        "excel": _fwd(citing_desc_excel),
                        "all_renowned": _fwd(all_renowned),
                        "top_renowned": _fwd(top_renowned),
                    },
                    skip_citing_analysis=config.dashboard_skip_citing_analysis,
                )

            self.log_manager.success("=" * 50)
            self.log_manager.success("全部任务完成!")
            self.log_manager.success(f"Excel文件: {excel_file}")
            self.log_manager.success(f"JSON文件: {json_file}")
            if html_file:
                self.log_manager.success(f"Dashboard: {html_file}")
            self.log_manager.success("=" * 50)

            await self.log_manager._broadcast({"type": "all_done", "data": {
                "excel": str(excel_file),
                "json": str(json_file),
                "dashboard": str(html_file) if html_file else None,
                "cost_summary": cost_tracker.get_summary(),
            }})

        except Exception as e:
            self.log_manager.error(f"任务执行错误: {str(e)}")
            import traceback
            self.log_manager.error(traceback.format_exc())
            raise
        finally:
            self.is_running = False
            if author_cache is not None:
                try:
                    await author_cache.flush()
                except Exception:
                    pass
            if desc_cache is not None:
                try:
                    await desc_cache.flush()
                except Exception:
                    pass

    async def execute_stage1_scraping(
        self,
        url: str,
        config: AppConfig,
        output_prefix: str,
        resume_page: int = 0
    ):
        """
        执行阶段1: 抓取引用列表

        Args:
            url: Google Scholar引用列表URL
            config: 应用配置
            output_prefix: 输出文件前缀
            resume_page: 断点续爬起始页
        """
        # Note: is_running is set synchronously by the caller before creating the task.
        self.should_cancel = False

        try:
            # 生成带时间戳的文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_prefix = f"{output_prefix}-{timestamp}"

            self.log_manager.info(f"文件前缀: {file_prefix}")

            # ==================== 阶段1: 抓取引用列表 ====================
            self.log_manager.info("=" * 50)
            self.log_manager.info("Phase 1 · 施引文献检索: 开始抓取引用列表")
            self.log_manager.info("=" * 50)

            citing_papers_file = DATA_DIR / "jsonl" / f"{file_prefix}_citing_papers.jsonl"
            await self._run_skill(
                "phase1_citation_fetch",
                config,
                url=url,
                output_file=citing_papers_file,
                start_page=resume_page,
                sleep_seconds=config.sleep_between_pages,
                enable_year_traverse=config.enable_year_traverse,
            )

            if self.should_cancel:
                self.log_manager.warning("任务已被用户取消")
                return

            self.log_manager.success("=" * 50)
            self.log_manager.success("Phase 1 · 施引文献检索 完成")
            self.log_manager.success(f"输出文件: {citing_papers_file}")
            self.log_manager.success("=" * 50)

            # 保存阶段1结果供阶段2使用
            self.stage1_result = {
                "file_prefix": file_prefix,
                "citing_papers_file": str(citing_papers_file),
            }

            # 发送阶段1完成通知
            await self.log_manager._broadcast({
                "type": "stage1_complete",
                "data": {
                    "file_prefix": file_prefix,
                    "citing_papers_file": str(citing_papers_file)
                }
            })

        except Exception as e:
            self.log_manager.error(f"阶段1执行错误: {str(e)}")
            import traceback
            self.log_manager.error(traceback.format_exc())
            raise
        finally:
            self.is_running = False

    async def import_history(self, file_path: Path, config: AppConfig) -> dict:
        """
        导入历史抓取记录

        Args:
            file_path: 导入的jsonl文件路径
            config: 应用配置

        Returns:
            导入结果信息
        """
        try:
            import json

            if not file_path.exists():
                return {"success": False, "message": "文件不存在"}

            # 读取文件并统计论文数
            paper_count = 0
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    data = json.loads(line)
                    for page_id, page_content in data.items():
                        paper_dict = page_content.get('paper_dict', {})
                        paper_count += len(paper_dict)

            # 复制文件到data/jsonl目录
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_prefix = f"imported-{timestamp}"
            target_path = DATA_DIR / "jsonl" / f"{file_prefix}_citing_papers.jsonl"
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # 复制文件
            import shutil
            shutil.copy2(file_path, target_path)

            # 保存阶段1结果供阶段2使用 (no stale config reference)
            self.stage1_result = {
                "file_prefix": file_prefix,
                "citing_papers_file": str(target_path),
            }

            self.log_manager.success(f"成功导入历史记录: {file_path.name}")
            self.log_manager.info(f"论文数量: {paper_count}")
            self.log_manager.info(f"保存位置: {target_path}")

            return {
                "success": True,
                "file_name": file_path.name,
                "paper_count": paper_count,
                "file_prefix": file_prefix
            }

        except Exception as e:
            self.log_manager.error(f"导入失败: {str(e)}")
            return {"success": False, "message": str(e)}

    async def execute_stage2_and_3(self):
        """
        执行阶段2和3: 搜索作者信息 + 导出结果
        需要先执行阶段1，或者有保存的阶段1结果
        """
        if not self.stage1_result:
            self.log_manager.error("错误: 未找到阶段1的结果，请先执行阶段1或导入历史记录")
            return

        # Note: is_running is set synchronously by the caller before creating the task.
        self.should_cancel = False

        try:
            file_prefix = self.stage1_result["file_prefix"]
            citing_papers_file = Path(self.stage1_result["citing_papers_file"])
            # Re-read fresh config instead of using stale stored reference
            config = self.config_manager.get()

            # ==================== 阶段2: 搜索作者信息 ====================
            self.log_manager.info("=" * 50)
            self.log_manager.info("Phase 2 · 作者信息采集: 开始搜索作者学术信息")
            self.log_manager.info("=" * 50)

            author_info_file = DATA_DIR / "jsonl" / f"{file_prefix}_author_information.jsonl"
            await self._run_skill(
                "phase2_author_intel",
                config,
                input_file=citing_papers_file,
                output_file=author_info_file,
                sleep_seconds=config.sleep_between_authors,
                parallel_workers=config.parallel_author_search,
            )

            if self.should_cancel:
                self.log_manager.warning("任务已被用户取消")
                return

            self.log_manager.success("Phase 2 · 作者信息采集 完成")

            # ==================== 阶段3: 导出结果 ====================
            self.log_manager.info("=" * 50)
            self.log_manager.info("Phase 3 · 导出结果")
            self.log_manager.info("=" * 50)

            excel_file = DATA_DIR / "excel" / f"{file_prefix}_author_information.xlsx"
            json_file = DATA_DIR / "json" / f"{file_prefix}_author_information.json"

            await self._run_skill(
                "phase3_export",
                config,
                input_file=author_info_file,
                excel_output=excel_file,
                json_output=json_file,
            )

            self.log_manager.success("=" * 50)
            self.log_manager.success("全部任务完成!")
            self.log_manager.success(f"Excel文件: {excel_file}")
            self.log_manager.success(f"JSON文件: {json_file}")
            self.log_manager.success("=" * 50)

            # 清空阶段1结果
            self.stage1_result = None

        except Exception as e:
            self.log_manager.error(f"阶段2/3执行错误: {str(e)}")
            import traceback
            self.log_manager.error(traceback.format_exc())
            raise
        finally:
            self.is_running = False

    async def execute_for_titles(
        self,
        paper_groups: List[dict],
        config: AppConfig,
        output_prefix: str,
    ):
        """
        全自动多论文流水线：
          paper_groups: [{title: str, aliases: [str]}]
          对每篇论文（含曾用名）：Phase 1 + Phase 2
          合并时把曾用名归一化到正式标题 + 去重
          → Phase 3（导出 Excel/JSON）
        """
        import json as _json
        from citationclaw.core.url_finder import PaperURLFinder

        # Note: is_running is set synchronously by the caller before creating the task.
        self.should_cancel = False
        self._year_traverse_event = None
        self._year_traverse_choice = False
        self._year_traverse_prompted = False
        self.quota_exceeded_event = asyncio.Event()

        # 初始化费用追踪器
        cost_tracker = CostTracker()

        # 初始化持久化缓存（在 try 外，确保 finally 可访问）
        author_cache = None
        desc_cache = None

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            result_dir = DATA_DIR / f"result-{timestamp}"
            result_dir.mkdir(parents=True, exist_ok=True)
            self.log_manager.info(f"结果目录: {result_dir}")

            # 运行前快照 LLM 额度 (token masked in logs)
            self.log_manager.info(
                f"[DEBUG] api_access_token={_mask_token(config.api_access_token)}, "
                f"api_user_id={repr(config.api_user_id)}"
            )
            if config.api_access_token and config.api_user_id:
                self.log_manager.info("正在查询 LLM API 额度（运行前）...")
                await cost_tracker.snapshot_before(
                    config.openai_base_url, config.api_access_token, config.api_user_id
                )
                if cost_tracker.llm_quota_before is not None:
                    remaining = cost_tracker.llm_quota_before / 500_000
                    self.log_manager.info(f"当前剩余额度: {remaining:.2f} 实际额度 (约 ¥{remaining * 2:.2f})")

            # 构建别名归一化映射：所有搜索标题 → 正式标题
            alias_to_canonical: dict[str, str] = {}
            canonical_titles: List[str] = []       # 仅正式标题（用于报告标题展示）
            all_search_titles: List[tuple[str, str]] = []  # [(search_title, canonical)]

            for group in paper_groups:
                canonical = group["title"]
                canonical_titles.append(canonical)
                alias_to_canonical[canonical] = canonical
                all_search_titles.append((canonical, canonical))
                for alias in group.get("aliases", []):
                    alias_to_canonical[alias] = canonical
                    all_search_titles.append((alias, canonical))

            author_info_files = []   # 收集每篇/每别名的 Phase 2 输出
            citing_files = []        # 收集每篇/每别名的 Phase 1 输出
            total = len(all_search_titles)

            # 目标论文作者缓存（每个 canonical 只查询一次 LLM，存完整文本）
            target_authors_cache: dict[str, str] = {}

            # 作者信息持久化缓存（跨运行复用）
            author_cache = AuthorInfoCache()
            desc_cache = CitingDescriptionCache()
            cache_stats = author_cache.stats()
            self.log_manager.info(
                f"作者信息缓存已加载：{cache_stats['total_entries']} 条历史记录"
                f"（路径: {author_cache.cache_file}）"
            )

            if config.test_mode:
                # ===== 测试模式：使用 test/mock_author_info.jsonl 伪造数据 =====
                self.log_manager.info("[测试模式] 跳过真实 API 调用，使用伪造数据")
                template_file = Path("test/mock_author_info.jsonl")
                if not template_file.exists():
                    self.log_manager.error("测试数据不存在: test/mock_author_info.jsonl")
                    return
                template_text = template_file.read_text(encoding="utf-8")

                for i, (title, canonical) in enumerate(all_search_titles):
                    if self.should_cancel:
                        break
                    alias_tag = f"（曾用名→{canonical}）" if title != canonical else ""
                    self.log_manager.info(f"[{i+1}/{total}] 注入伪造数据: {title}{alias_tag}")
                    paper_slug = f"paper{i+1}"
                    author_file = result_dir / f"{paper_slug}_authors.jsonl"
                    # 将模板中的占位符替换为当前正式标题
                    filled = template_text.replace("__CANONICAL__", canonical)
                    author_file.write_text(filled, encoding="utf-8")
                    author_info_files.append(author_file)
                    self.log_manager.info(f"  已写入 {len(template_text.splitlines())} 条伪造记录")

            else:
                # ===== 正常模式：URL 查找 → 爬取 → 作者搜索 =====
                url_finder = PaperURLFinder(
                    api_keys=config.scraper_api_keys,
                    log_callback=self.log_manager.info,
                    retry_max_attempts=config.retry_max_attempts,
                    retry_intervals=config.retry_intervals,
                    cost_tracker=cost_tracker,
                )

                for i, (title, canonical) in enumerate(all_search_titles):
                    if self.should_cancel:
                        break

                    alias_tag = f"（曾用名，归并至：{canonical}）" if title != canonical else ""
                    self.log_manager.info("=" * 50)
                    self.log_manager.info(f"搜索 {i+1}/{total}: {title}{alias_tag}")
                    self.log_manager.info("=" * 50)

                    # —— 查找引用 URL ——
                    url = await url_finder.find_citation_url(title)
                    if not url:
                        self.log_manager.warning(f"跳过（未找到引用链接）: {title}")
                        continue

                    paper_slug = f"paper{i+1}"

                    # —— Phase 1：爬取引用列表 ——
                    self.log_manager.info("Phase 1 · 施引文献检索")

                    # —— 预检测引用数，超1000时询问是否开启年份遍历 ——
                    if not config.enable_year_traverse and not self._year_traverse_prompted:
                        probe_data = await self._run_skill(
                            "phase1_citation_fetch",
                            config,
                            url=url,
                            probe_only=True,
                            cost_tracker=cost_tracker,
                        )
                        citation_count = int(probe_data.get("citation_count", 0))
                        if citation_count > 1000:
                            self._year_traverse_prompted = True
                            self._year_traverse_event = asyncio.Event()
                            self.log_manager.broadcast_event("year_traverse_prompt", {
                                "title": title,
                                "citation_count": citation_count,
                            })
                            self.log_manager.warning(
                                f"论文「{title}」检测到 {citation_count} 篇引用（超过 Google Scholar 1000 条限制）"
                            )
                            self.log_manager.warning(
                                "已暂停，等待用户选择是否启用按年份遍历（最多等待 60 秒）..."
                            )
                            try:
                                await asyncio.wait_for(self._year_traverse_event.wait(), timeout=60.0)
                                if self._year_traverse_choice:
                                    config = config.model_copy(update={"enable_year_traverse": True})
                                    self.log_manager.info("已启用按年份遍历，将逐年抓取完整数据")
                                else:
                                    self.log_manager.info("已跳过，继续普通模式（可能只抓取前 1000 条）")
                            except asyncio.TimeoutError:
                                self.log_manager.warning("等待超时（60s），以普通模式继续")
                            finally:
                                self._year_traverse_event = None

                    citing_file = result_dir / f"{paper_slug}_citing.jsonl"
                    await self._run_skill(
                        "phase1_citation_fetch",
                        config,
                        url=url,
                        output_file=citing_file,
                        start_page=0,
                        sleep_seconds=config.sleep_between_pages,
                        enable_year_traverse=config.enable_year_traverse,
                        cost_tracker=cost_tracker,
                    )
                    if self.should_cancel:
                        break
                    citing_files.append((citing_file, canonical))

            # Guard: 检查 Phase 1 是否爬取到任何引用文献
            _total_citing = 0
            for _cf, _ in citing_files:
                if not _cf.exists():
                    continue
                for _line in _cf.read_text(encoding="utf-8").splitlines():
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _data = _json.loads(_line)
                        for _page_data in _data.values():
                            _total_citing += len(_page_data.get("paper_dict", {}))
                    except Exception:
                        continue
            if _total_citing == 0:
                self.log_manager.warning("Phase 1 未爬取到任何引用文献，任务结束")
                return

            # ── New Pipeline: Phase 2 (API metadata) + Phase 3 (scholar assess + export) ──
            pipeline_result = await self._run_new_phase2_and_3(
                citing_files=citing_files,
                result_dir=result_dir,
                output_prefix=output_prefix,
                config=config,
                canonical_titles=canonical_titles,
            )
            if pipeline_result is None:
                self.log_manager.warning("管线未产出有效结果，任务结束")
                return
            merged_file, excel_file, json_file = pipeline_result

            # —— Phase 4：引文语境提取（可选，PDF 下载 + 本地解析）——
            citing_desc_excel = excel_file
            if config.enable_citing_description:
                import pandas as pd

                self.log_manager.info("=" * 50)
                self.log_manager.info("Phase 4 · 引文语境提取: PDF 下载 + 本地解析 + 轻量 LLM")
                self.log_manager.info("=" * 50)

                # Use new PDF-based citation extraction skill
                phase4_output_jsonl = result_dir / f"{output_prefix}_citing_desc.jsonl"
                target_title = canonical_titles[0] if canonical_titles else ""

                await self._run_skill(
                    "phase4_citation_extract",
                    config,
                    input_file=merged_file,
                    output_file=phase4_output_jsonl,
                    target_title=target_title,
                    target_authors=[],
                    citation_desc_cache=desc_cache,
                )

                # Merge descriptions back into Excel
                if phase4_output_jsonl.exists():
                    desc_map = {}
                    with open(phase4_output_jsonl, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                rec = _json.loads(line)
                                # Extract from legacy wrapped format {idx: {fields}}
                                if isinstance(rec, dict):
                                    inner = rec
                                    for v in rec.values():
                                        if isinstance(v, dict) and "Paper_Title" in v:
                                            inner = v
                                            break
                                    title = inner.get("Paper_Title", inner.get("paper_title", ""))
                                    desc = inner.get("Citing_Description", "")
                                    if title and desc:
                                        desc_map[title.strip()] = desc
                            except Exception:
                                continue

                    if desc_map:
                        df = pd.read_excel(excel_file)
                        df["Citing_Description"] = df["Paper_Title"].str.strip().map(desc_map).fillna("")
                        citing_desc_excel = result_dir / f"{output_prefix}_results_with_citing_desc.xlsx"
                        df.to_excel(citing_desc_excel, index=False)
                        n_with = (df["Citing_Description"].str.strip() != "").sum()
                        self.log_manager.success(
                            f"Phase 4 完成: {n_with}/{len(df)} 篇有引文语境描述"
                        )

            # —— Phase 5：生成 HTML 画像报告（可选）——
            html_file = None
            if config.enable_dashboard:
                self.log_manager.info("Phase 5 · 报告生成与导出")
                html_file = result_dir / f"{output_prefix}_dashboard.html"

                all_renowned = excel_file.with_stem(excel_file.stem + "_all_renowned_scholar")
                top_renowned = excel_file.with_stem(excel_file.stem + "_top-tier_scholar")

                def _fwd(p: Path) -> str:
                    return str(p).replace("\\", "/")

                await self._run_skill(
                    "phase5_report_generate",
                    config,
                    citing_desc_excel=citing_desc_excel,
                    renowned_all_xlsx=all_renowned,
                    renowned_top_xlsx=top_renowned,
                    output_html=html_file,
                    canonical_titles=canonical_titles,
                    download_filenames={
                        "excel": _fwd(citing_desc_excel),
                        "all_renowned": _fwd(all_renowned),
                        "top_renowned": _fwd(top_renowned),
                    },
                    skip_citing_analysis=config.dashboard_skip_citing_analysis,
                )

            # 运行后快照 LLM 额度
            if config.api_access_token and config.api_user_id:
                self.log_manager.info("正在查询 LLM API 额度（运行后）...")
                await cost_tracker.snapshot_after(
                    config.openai_base_url, config.api_access_token, config.api_user_id
                )

            # 生成费用摘要
            cost_summary = cost_tracker.get_summary()

            self.log_manager.success("=" * 50)
            self.log_manager.success("全部完成!")
            self.log_manager.success(f"Excel: {excel_file}")
            self.log_manager.success(f"JSON:  {json_file}")
            if html_file:
                self.log_manager.success(f"Dashboard: {html_file}")

            # 日志输出费用摘要
            self.log_manager.info("=" * 50)
            self.log_manager.info("费用摘要")
            self.log_manager.info(f"  ScraperAPI: {cost_summary['scraper_credits']} credits / {cost_summary['scraper_requests']} 次请求 (约 ${cost_summary['scraper_cost_usd']:.4f})")
            if cost_summary.get("llm_tracked"):
                self.log_manager.info(f"  LLM API: {cost_summary['llm_quota_consumed']:.4f} 实际额度 (约 ¥{cost_summary['llm_cost_rmb']:.2f})")
                self.log_manager.info(f"  LLM 剩余: {cost_summary['llm_remaining']:.2f} 实际额度 (约 ¥{cost_summary['llm_remaining_rmb']:.2f})")
                self.log_manager.info("  LLM 额度通过运行前后差值计算，可能包含同时段其他消耗")
            else:
                self.log_manager.info("  LLM API: 未配置系统令牌，无法追踪额度消耗")
            self.log_manager.info("=" * 50)

            self.log_manager.success("=" * 50)
            await self.log_manager._broadcast({"type": "all_done", "data": {
                "excel": str(excel_file),
                "json": str(json_file),
                "dashboard": str(html_file) if html_file else None,
                "cost_summary": cost_summary,
            }})

        except Exception as e:
            self.log_manager.error(f"任务错误: {e}")
            import traceback; self.log_manager.error(traceback.format_exc())
            raise
        finally:
            self.is_running = False
            if author_cache is not None:
                try:
                    await author_cache.flush()
                except Exception:
                    pass
            if desc_cache is not None:
                try:
                    await desc_cache.flush()
                except Exception:
                    pass

    async def build_report_from_cache(self, paper_title: str, config, output_prefix: str = "cached") -> dict:
        """
        从缓存直接生成 Phase 5 HTML 报告，跳过 Phase 1–4。

        Args:
            paper_title: 被引论文标题（用于过滤 desc 缓存）
            config:      AppConfig 实例
            output_prefix: 输出文件前缀

        Returns:
            {"html": str, "excel": str}
        """
        import json as _json
        import pandas as pd
        from citationclaw.core.exporter import ResultExporter

        # Note: is_running is set synchronously by the caller before creating the task.
        self.should_cancel = False

        try:
            self.log_manager.info("=" * 50)
            self.log_manager.info(f"从缓存生成报告: {paper_title}")
            self.log_manager.info("=" * 50)

            # 加载 desc 缓存
            desc_cache_file = DATA_DIR / "cache" / "citing_description_cache.json"
            if not desc_cache_file.exists():
                self.log_manager.error(f"引用描述缓存不存在: {desc_cache_file}")
                return {}
            desc_data: dict = _json.loads(desc_cache_file.read_text(encoding="utf-8"))

            # 过滤出目标论文的所有引用记录
            target_suffix = "||" + paper_title.strip().lower()
            matches = {k: v for k, v in desc_data.items() if k.lower().endswith(target_suffix)}
            if not matches:
                self.log_manager.warning(f"缓存中未找到论文「{paper_title}」的任何引用记录")
                return {}
            self.log_manager.info(f"找到 {len(matches)} 条引用记录")

            # 加载 author 缓存
            author_cache_file = DATA_DIR / "cache" / "author_info_cache.json"
            author_data: dict = {}
            if author_cache_file.exists():
                try:
                    author_data = _json.loads(author_cache_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

            # 构建行数据
            rows = []
            for key, entry in matches.items():
                paper_link = key[:key.lower().rfind(target_suffix)]
                paper_title_citing = entry.get("paper_title", "")
                citing_desc = entry.get("Citing_Description", "")

                # 查找作者信息：先按 link，再 fallback 到标题
                author_entry = author_data.get(paper_link) or author_data.get(paper_title_citing.strip().lower(), {})

                row = {
                    "Paper_Title": paper_title_citing,
                    "Paper_Link": paper_link,
                    "Paper_Year": author_entry.get("Paper_Year", ""),
                    "Citations": author_entry.get("Citations", ""),
                    "Citing_Paper": paper_title,
                    "Is_Self_Citation": False,
                    "Citing_Description": citing_desc,
                    "Searched Author-Affiliation": author_entry.get("Searched Author-Affiliation", ""),
                    "First_Author_Institution": author_entry.get("First_Author_Institution", ""),
                    "First_Author_Country": author_entry.get("First_Author_Country", ""),
                    "Searched Author Information": author_entry.get("Searched Author Information", ""),
                    "Renowned Scholar": author_entry.get("Renowned Scholar", ""),
                    "Formated Renowned Scholar": author_entry.get("Formated Renowned Scholar", []),
                }
                rows.append(row)

            df = pd.DataFrame(rows)

            # 创建输出目录
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            result_dir = DATA_DIR / f"result-{timestamp}"
            result_dir.mkdir(parents=True, exist_ok=True)
            self.log_manager.info(f"结果目录: {result_dir}")

            # 保存主 Excel（Phase 5 输入）
            citing_desc_excel = result_dir / f"{output_prefix}_results_with_citing_desc.xlsx"
            df.to_excel(citing_desc_excel, index=False)
            self.log_manager.info(f"已保存引用描述 Excel: {citing_desc_excel}")

            # 重建知名学者文件
            all_renowned = citing_desc_excel.with_stem(citing_desc_excel.stem + "_all_renowned_scholar")
            top_renowned = citing_desc_excel.with_stem(citing_desc_excel.stem + "_top-tier_scholar")
            exporter = ResultExporter(log_callback=self.log_manager.info)
            flattened = df.to_dict("records")
            try:
                exporter.highligh_renowned_scholar(flattened, [all_renowned, top_renowned])
                self.log_manager.info("已重建知名学者文件")
            except Exception as exc:
                self.log_manager.warning(f"知名学者文件重建失败（将使用空表）: {exc}")
                empty = pd.DataFrame()
                empty.to_excel(all_renowned, index=False)
                empty.to_excel(top_renowned, index=False)

            # 运行 Phase 5
            self.log_manager.info("Phase 5 · 报告生成与导出")
            html_file = result_dir / f"{output_prefix}_dashboard.html"

            def _fwd(p: Path) -> str:
                return str(p).replace("\\", "/")

            await self._run_skill(
                "phase5_report_generate",
                config,
                citing_desc_excel=citing_desc_excel,
                renowned_all_xlsx=all_renowned,
                renowned_top_xlsx=top_renowned,
                output_html=html_file,
                canonical_titles=[paper_title],
                download_filenames={
                    "excel": _fwd(citing_desc_excel),
                    "all_renowned": _fwd(all_renowned),
                    "top_renowned": _fwd(top_renowned),
                },
                skip_citing_analysis=config.dashboard_skip_citing_analysis,
            )

            self.log_manager.success("=" * 50)
            self.log_manager.success("缓存报告生成完成!")
            self.log_manager.success(f"Dashboard: {html_file}")
            self.log_manager.success("=" * 50)

            await self.log_manager._broadcast({"type": "all_done", "data": {
                "excel": str(citing_desc_excel),
                "json": None,
                "dashboard": str(html_file),
                "cost_summary": None,
            }})

            return {"html": str(html_file), "excel": str(citing_desc_excel)}

        except Exception as e:
            self.log_manager.error(f"缓存报告生成错误: {e}")
            import traceback; self.log_manager.error(traceback.format_exc())
            raise
        finally:
            self.is_running = False

    def _handle_quota_exceeded(self):
        """Called when any phase signals that API quota is exhausted."""
        self.should_cancel = True
        self.log_manager.error("API 配额不足，搜索已自动停止。已处理的数据已保存至本地缓存。")
        self.log_manager.broadcast_event("quota_exceeded", {
            "message": "API 配额不足，搜索已自动停止。已处理的数据已保存至本地缓存，充值后重新运行将自动续跑，无需重复花费 Token。"
        })

    def _filter_by_scholars(self, excel_file: Path, scholar_names: list, result_dir: Path, output_prefix: str) -> Path:
        """从 Excel 中过滤出含指定学者的行"""
        import pandas as pd
        df = pd.read_excel(excel_file)
        mask = df.apply(lambda row: any(
            scholar.lower() in str(row.get('Searched Author-Affiliation', '') or '').lower() or
            scholar.lower() in str(row.get('Authors_with_Profile', '') or '').lower() or
            scholar.lower() in str(row.get('Paper_Title', '') or '').lower()
            for scholar in scholar_names
        ), axis=1)
        filtered = df[mask]
        self.log_manager.info(f"  匹配到 {len(filtered)}/{len(df)} 篇论文")
        if filtered.empty:
            self.log_manager.warning("未匹配到任何论文，将搜索全部论文")
            return excel_file
        filtered_file = result_dir / f"{output_prefix}_filtered_for_scholars.xlsx"
        filtered.to_excel(filtered_file, index=False)
        return filtered_file

    def _match_scholars_in_citing(self, citing_files: list, scholar_names: list) -> tuple:
        """在 citing papers 的 authors 中匹配学者，返回 (matched, unmatched)"""
        import json as _json
        matched = {}   # scholar_name -> list of paper titles
        for scholar in scholar_names:
            found = []
            for cf, _canonical in citing_files:
                if not cf.exists():
                    continue
                for line in cf.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        data = _json.loads(line)
                        for page_data in data.values():
                            for paper in page_data.get("paper_dict", {}).values():
                                authors = paper.get("authors", {})
                                for author_name in authors.keys():
                                    if scholar.lower() in author_name.lower():
                                        found.append(paper.get("paper_title", ""))
                                        break
                    except Exception:
                        continue
            if found:
                matched[scholar] = found
        unmatched = [s for s in scholar_names if s not in matched]
        return matched, unmatched

    async def _fetch_target_authors(self, title: str, config: AppConfig) -> str:
        """通过LLM搜索获取目标论文的作者列表及单位信息（用于自引检测）。

        返回包含姓名和单位的完整文本，失败时返回空字符串（自引过滤将被跳过）。
        """
        try:
            from openai import AsyncOpenAI
            import httpx
            client = AsyncOpenAI(
                api_key=config.openai_api_key,
                base_url=config.openai_base_url,
                timeout=60.0,
                max_retries=2,
                http_client=httpx.AsyncClient(trust_env=False, timeout=60.0),
            )
            q = (f"请搜索论文《{title}》的所有作者，"
                 f"列出每位作者的姓名及其所在单位/机构。")
            comp = await client.chat.completions.create(
                model=config.openai_model,
                messages=[{"role": "user", "content": q}],
                extra_body={"web_search_options": {}}
            )
            response = comp.choices[0].message.content or ""
            self.log_manager.info(f"自引检测：目标论文作者信息已获取（{len(response)}字符）")
            return response
        except Exception as e:
            self.log_manager.warning(f"自引检测：无法获取目标论文《{title[:40]}》的作者，自引过滤将被跳过: {e}")
            return ""

    def cancel(self):
        """取消任务"""
        if self.is_running:
            self.should_cancel = True
            self.log_manager.warning("正在取消任务...")

    def get_status(self) -> dict:
        """
        获取任务状态

        Returns:
            状态信息
        """
        return {
            "is_running": self.is_running,
            "should_cancel": self.should_cancel,
            "has_stage1_result": self.stage1_result is not None
        }
