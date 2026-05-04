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

        # Phase 2 登录检查点（CDP 启用时 Phase 2 开始前弹浏览器等登录）
        self._phase2_login_event: Optional[asyncio.Event] = None
        self._phase2_login_done: bool = False  # 本次运行已完成/跳过，不重复提示

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

    # ── Phase 2 login-stamp sentinel helpers ──────────────────────────
    # Path is lazily resolved via pdf_downloader's DEBUG_BROWSER_PROFILE_DIR
    # so we inherit the 2026-04-20 absolute-path fix automatically.
    _STAMP_FILENAME = "phase2_login_stamp.json"
    _TRUSTED_PHASE2_STAMP_OUTCOMES = {"user_confirmed", "probe_pass"}

    def _phase2_stamp_path(self):
        from citationclaw.core.pdf_downloader import DEBUG_BROWSER_PROFILE_DIR
        return DEBUG_BROWSER_PROFILE_DIR / self._STAMP_FILENAME

    def _phase2_stamp_is_fresh(self, ttl_hours: int) -> tuple:
        """Return (is_fresh, stamp_dict_or_None, age_hours_or_None).

        Fresh = stamp file exists, parses as JSON, has a trusted outcome,
        and its `timestamp` is within `ttl_hours` of now. Returns
        (False, None, None) on missing/corrupt stamps. Untrusted outcomes
        such as timeout/probe_failed return the stamp data for diagnostics
        but are never allowed to skip the login checkpoint.
        """
        if ttl_hours <= 0:
            return (False, None, None)
        path = self._phase2_stamp_path()
        if not path.exists():
            return (False, None, None)
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
            ts_iso = data.get("timestamp", "")
            stamp_at = datetime.fromisoformat(ts_iso)
        except Exception:
            return (False, None, None)
        age_s = (datetime.now() - stamp_at).total_seconds()
        age_hours = age_s / 3600.0
        outcome = str(data.get("outcome", ""))
        is_trusted = outcome in self._TRUSTED_PHASE2_STAMP_OUTCOMES
        return (is_trusted and age_hours < ttl_hours, data, age_hours)

    def _phase2_stamp_write(self, outcome: str, urls: list) -> None:
        """Persist a stamp file so the next run knows we logged in recently.

        outcome: 'user_confirmed' | 'timeout' | 'probe_pass' | 'probe_failed'
          - user_confirmed: user clicked 继续 / POSTed ready endpoint
          - timeout: checkpoint timed out (record only; not trusted)
          - probe_pass: we skipped the checkpoint via stamp AND the
            post-probe passed, so we refresh the stamp forward in time
          - probe_failed: cached cookies no longer look usable; next run
            must prompt again
        """
        path = self._phase2_stamp_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_json.dumps({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "outcome": outcome,
                "urls": urls,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            # Never let stamp write failure abort the pipeline.
            self.log_manager.warning(
                f"[Phase2登录] 无法写入 sentinel 文件: {e}"
            )

    async def _prompt_phase2_login(self, config: AppConfig) -> None:
        """Phase 2 入口：自动弹出浏览器 + 出版商登录页，等待用户完成登录。

        触发条件（任一不满足即静默跳过，行为与旧版一致）：
          - `config.cdp_debug_port > 0` （没开 CDP 根本不需要浏览器登录）
          - `config.enable_phase2_login_checkpoint` 为 True
          - 本次任务尚未完成过该检查点（`_phase2_login_done` 为 False）

        流程：
          0. 若 `runtime/debug_browser_profile/phase2_login_stamp.json`
             存在且年龄 < `phase2_login_stamp_hours` 小时，跳过 tab/等待
             阶段，直接进 probe。老用户一天内多次跑免 180s 等待。
          1. 调用 `_cdp_ensure_browser` 自动启动 Chrome/Edge（若未启动）
             并使用 `runtime/debug_browser_profile` 持久化 cookies。
          2. 用 `_cdp_open_login_pages` 批量打开 `phase2_login_urls`。
          3. 通过 WebSocket 广播 `phase2_login_prompt`，让 UI 弹模态。
          4. 在 `asyncio.Event` 上 wait，最多 `phase2_login_wait_seconds` 秒。
          5. 用户点「继续」POST `/api/task/phase2-login-ready` 即解锁；
             否则超时自动继续（已登录过的用户 cookies 仍有效）。
          6. 写 sentinel 文件供下次跑复用。
          7. 可选：跑 probe 验证各出版商 auth 状态。
        """
        if self._phase2_login_done:
            return
        self._phase2_login_done = True  # 至多触发一次，哪怕早退

        cdp_port = int(getattr(config, "cdp_debug_port", 0) or 0)
        if cdp_port <= 0:
            return
        if not getattr(config, "enable_phase2_login_checkpoint", True):
            return

        login_urls = list(getattr(config, "phase2_login_urls", []) or [])
        wait_seconds = int(getattr(config, "phase2_login_wait_seconds", 180) or 180)
        stamp_ttl_hours = int(getattr(config, "phase2_login_stamp_hours", 24) or 0)

        # 1) 启动浏览器（幂等，已在跑就直接复用）
        from citationclaw.core.pdf_downloader import (
            _cdp_ensure_browser,
            _cdp_check_connection,
            _cdp_open_login_pages,
            _cdp_available,
        )

        if not _cdp_available():
            self.log_manager.warning(
                "[Phase2登录] 未安装 websocket-client，跳过自动弹出登录页。"
                "运行 `pip install websocket-client` 后重启服务即可启用。"
            )
            return

        launched = _cdp_ensure_browser(cdp_port)
        if not launched and not _cdp_check_connection(cdp_port):
            self.log_manager.warning(
                f"[Phase2登录] 无法启动调试浏览器（port={cdp_port}）。"
                "请手动运行 chrome.exe --remote-debugging-port=9222 后重试。"
            )
            return

        # 0) Sentinel short-circuit: if we successfully completed the
        # checkpoint within `stamp_ttl_hours`, skip tab-open + wait.
        # User already knows the drill; no need to pop 5 tabs again.
        fresh, stamp_data, age_hours = self._phase2_stamp_is_fresh(stamp_ttl_hours)
        if fresh:
            age_str = (f"{age_hours:.1f}h" if age_hours is not None else "?")
            prev_outcome = (stamp_data or {}).get("outcome", "?")
            self.log_manager.info(
                f"[Phase2登录] {age_str} 前已完成过检查点 (outcome={prev_outcome})，"
                f"跳过 tab 弹出 + 等待（sentinel TTL {stamp_ttl_hours}h）。"
            )
            # Still run the probe so we catch "cookies expired since last run".
            if getattr(config, "enable_phase2_login_probe", True):
                probe_ok = await self._run_phase2_login_probe(cdp_port)
                if probe_ok:
                    # Refresh the stamp forward after a successful skip-then-probe,
                    # so a long-running session keeps the TTL rolling.
                    self._phase2_stamp_write("probe_pass", login_urls)
                    return
                self._phase2_stamp_write("probe_failed", login_urls)
                self.log_manager.warning(
                    "[Phase2登录] 缓存登录检查未通过，重新弹出登录页并等待确认。"
                )
            else:
                return

        # 2) 弹开出版商登录页
        opened = _cdp_open_login_pages(cdp_port, login_urls) if login_urls else 0
        if opened:
            self.log_manager.info(
                f"[Phase2登录] 已弹出 {opened} 个出版商页面（IEEE / Springer / Elsevier 等）。"
            )
        else:
            self.log_manager.info(
                "[Phase2登录] 未配置 phase2_login_urls 或打开失败，"
                "仅启动了调试浏览器。"
            )

        # 3) 广播 WebSocket 事件 + 准备 asyncio.Event
        self._phase2_login_event = asyncio.Event()
        self.log_manager.broadcast_event("phase2_login_prompt", {
            "urls": login_urls,
            "wait_seconds": wait_seconds,
            "cdp_port": cdp_port,
        })
        self.log_manager.warning(
            f"[Phase2登录] 请在弹出的浏览器中完成出版商登录，然后在页面点击"
            f"「继续」(或 POST /api/task/phase2-login-ready)。"
            f"{wait_seconds}s 后自动继续。"
        )

        # 4) 等用户确认或超时
        outcome = "timeout"
        try:
            await asyncio.wait_for(self._phase2_login_event.wait(), timeout=wait_seconds)
            self.log_manager.info("[Phase2登录] 用户已确认登录完成，继续 Phase 2")
            outcome = "user_confirmed"
        except asyncio.TimeoutError:
            self.log_manager.warning(
                f"[Phase2登录] 等待超时（{wait_seconds}s），按现有 cookies 继续。"
                "若下载失败较多，下次可提高 phase2_login_wait_seconds。"
            )
        finally:
            self._phase2_login_event = None

        # 5) 登录后自动验证各出版商 CDP auth 状态（opt-in via config flag）。
        probe_ok = None
        if getattr(config, "enable_phase2_login_probe", True):
            probe_ok = await self._run_phase2_login_probe(cdp_port)

        # 6) Persist sentinel so subsequent runs within TTL can skip only
        # when the user explicitly confirmed or the probe passed. Timeout is
        # recorded for diagnostics, but it is not trusted by
        # _phase2_stamp_is_fresh.
        if stamp_ttl_hours > 0:
            if probe_ok is True:
                self._phase2_stamp_write("probe_pass", login_urls)
            elif probe_ok is False:
                self._phase2_stamp_write("probe_failed", login_urls)
            else:
                self._phase2_stamp_write(outcome, login_urls)

    async def _run_phase2_login_probe(self, cdp_port: int) -> bool:
        """Post-login diagnostic: probe IEEE/ACM/Elsevier/Springer/Wiley via CDP.

        Runs `core.cdp_login_probe.probe_all` synchronously on a worker
        thread (each probe opens a tab + 8s wait + PDF fetch, so 5 x ~10s
        = ~50s total) and logs per-publisher results. Non-fatal: any
        probe exception is caught and logged as a warning; the pipeline
        continues regardless.

        Rationale: if ACM still wants a captcha or step-up auth that the
        login checkpoint didn't clear, the user should know BEFORE we
        burn 20 minutes on 122 paper downloads. A failed probe just
        means "expect ACM CDP tier to miss"; doesn't abort anything.
        """
        try:
            from citationclaw.core.cdp_login_probe import (
                probe_all, format_summary, PASSING_STATUSES,
            )
        except Exception as e:
            self.log_manager.warning(
                f"[Phase2验证] 无法加载 cdp_login_probe 模块: {e}"
            )
            return False

        self.log_manager.info(
            "[Phase2验证] 正在验证 5 个出版商的 CDP 认证状态 (IEEE / ACM / "
            "Elsevier / Springer / Wiley，约 50s)..."
        )
        try:
            # probe_all is blocking (time.sleep + _cdp_evaluate network I/O).
            # Run on a worker thread to avoid stalling the asyncio loop.
            results = await asyncio.to_thread(probe_all, cdp_port, None)
        except Exception as e:
            self.log_manager.warning(
                f"[Phase2验证] probe_all 异常: {type(e).__name__}: {e}"
            )
            return False

        # Per-publisher line: INFO for passing, WARNING for failing.
        for r in results:
            line = (f"  [{r.publisher:<9}] {r.icon():<11} "
                    f"({r.elapsed_s:>4.1f}s)  {r.detail[:75]}")
            if r.status in PASSING_STATUSES:
                self.log_manager.info(line)
            else:
                self.log_manager.warning(line)

        # Roll-up summary line.
        passed = sum(1 for r in results if r.status in PASSING_STATUSES)
        total = len(results)
        summary_line = (f"[Phase2验证] {passed}/{total} 出版商认证通过 "
                        f"({format_summary(results)})")
        if passed == total:
            self.log_manager.info(summary_line)
        else:
            self.log_manager.warning(summary_line)
        return passed == total

    async def _run_new_phase2_and_3(
        self,
        citing_files: List[Tuple[Path, str]],
        result_dir: Path,
        output_prefix: str,
        config: AppConfig,
        canonical_titles: List[str] = None,
    ) -> Optional[Tuple[Path, Path, Path, list]]:
        """New Phase 2 (structured API metadata) + Phase 3 (scholar assess) pipeline.

        Returns: (merged_jsonl, excel_file, json_file, pdf_paths) or None on failure.
        """
        # 2026-04-21: apply log-level threshold from config. Setting
        # `log_min_level=SUCCESS` in config.json collapses the UI to
        # just green [PDF OK] / [PDF缓存] + yellow warnings + red
        # [PDF失败] blocks -- hides the cascade-internal "HTTP 403" /
        # "Connection error" INFO noise that makes users feel the
        # pipeline is broken. User request: "在最后的产品阶段可以
        # 关掉输出调试信息（INFO），只输出绿色的成功和红色的失败".
        _lvl = (getattr(config, "log_min_level", "INFO") or "INFO").upper()
        self.log_manager.set_min_level(_lvl)
        if _lvl != "INFO":
            # One-time hint at WARNING level so the user remembers this
            # is suppressing output (WARNING passes through at any
            # setting except ERROR).
            self.log_manager.warning(
                f"[日志] log_min_level={_lvl}：已屏蔽 INFO 级别的 cascade "
                f"诊断行；仍会看到成功/警告/错误。完整日志在 run.log 里。"
            )

        # 登录检查点：让用户先在真实浏览器里登录 IEEE/Springer/Elsevier 等，
        # 之后所有需要 CDP 的 PDF 下载都能复用 cookies。
        await self._prompt_phase2_login(config)

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
        _s2_key = getattr(config, 's2_api_key', '') or ''
        if _s2_key:
            self.log_manager.info(
                f"Phase 2 · 作者信息采集: S2 优先模式 (API Key: {_s2_key[:6]}***) — 高速并行查询"
            )
        else:
            self.log_manager.info(
                "Phase 2 · 作者信息采集: S2 优先模式 (无 API Key — 免费限速 1req/s，建议填入 S2 Key 提速)"
            )
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
                paper_link = paper.get("paper_link", "")
                try:
                    cached = await metadata_cache.get(title=title)
                    if cached:
                        metadata = cached
                        api_hits += 1
                    else:
                        # S2-first: search by title, then by URL if title miss
                        metadata = await collector.collect(title, paper_url=paper_link)
                        if metadata:
                            await metadata_cache.update(metadata.get("doi", ""), title, metadata)
                        api_queries += 1
                    results_slots[idx] = metadata
                except Exception as e:
                    # Don't let one paper's API failure crash the entire batch
                    self.log_manager.warning(f"  metadata 查询异常 ({title[:40]}): {str(e)[:60]}")
                    results_slots[idx] = None

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
        gs_fallback_count = 0
        for idx, (orig_i, paper, canonical) in enumerate(deduped_papers):
            metadata = results_slots[idx]

            # Build GS author list from Phase 1 data (always available as fallback)
            import re as _re
            gs_authors = []
            for key in (paper.get("authors_raw") or {}):
                m = _re.match(r'author_\d+_(.*)', key)
                name = m.group(1) if m else key
                if name:
                    gs_authors.append({"name": name, "affiliation": "", "country": ""})

            if metadata is None:
                # All APIs failed → build from GS data entirely
                gs_fallback_count += 1
                metadata = {
                    "title": paper.get("paper_title", ""),
                    "year": paper.get("paper_year"),
                    "doi": "", "s2_id": "", "arxiv_id": "",
                    "cited_by_count": 0, "influential_citation_count": 0,
                    "pdf_url": "", "oa_pdf_url": "", "venue": "",
                    "authors": gs_authors,
                    "sources": ["scholar"],
                }
            elif not metadata.get("authors") and gs_authors:
                # API found paper but returned no authors → use GS authors
                metadata["authors"] = gs_authors

            # Final check: if STILL no authors, try filtering empty-name entries
            # (S2 sometimes returns authors with IDs but no names)
            if metadata.get("authors"):
                valid_authors = [a for a in metadata["authors"] if a.get("name", "").strip()]
                if not valid_authors and gs_authors:
                    metadata["authors"] = gs_authors
                elif valid_authors:
                    metadata["authors"] = valid_authors

            # Cap unreasonable author counts (e.g. arXiv consortium papers)
            _MAX_AUTHORS = 100
            if metadata.get("authors") and len(metadata["authors"]) > _MAX_AUTHORS:
                self.log_manager.info(
                    f"  ⚠ {paper['paper_title'][:40]}... 有 {len(metadata['authors'])} 位作者，"
                    f"截断为前 {_MAX_AUTHORS} 位"
                )
                metadata["authors"] = metadata["authors"][:_MAX_AUTHORS]

            src_tag = ",".join(metadata.get("sources", [])) or "scholar"
            n_authors = len(metadata.get("authors", []))
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

        api_found = len(records_data) - gs_fallback_count
        self.log_manager.success(
            f"Phase 2 完成: API找到 {api_found} / GS兜底 {gs_fallback_count} / "
            f"缓存 {api_hits} / 共 {len(records_data)} 篇"
        )
        if gs_fallback_count > len(records_data) * 0.5:
            self.log_manager.warning(
                f"⚠ {gs_fallback_count} 篇论文 API 未找到（S2/OpenAlex 均未收录），"
                f"作者信息仅来自 Google Scholar（无机构），PDF 下载来源有限"
            )

        # ── Self-citation detection (BEFORE PDF download — skip downloading self-citations) ──
        self.log_manager.info("[自引检测] 标记自引论文...")
        self_cite_map: dict = {}  # index → bool
        self_cite_count = 0
        for i, (paper, metadata, canonical) in enumerate(records_data):
            target_authors = target_authors_map.get(canonical, [])
            paper_authors = (metadata or {}).get("authors", [])
            result = self_cite_detector.check(target_authors, paper_authors)
            is_self = result.get("is_self_citation", False)
            self_cite_map[i] = is_self
            if is_self:
                self_cite_count += 1
                matched = result.get("matched_pair", ("?", "?"))
                self.log_manager.info(
                    f"  ↩️ 自引: {paper.get('paper_title', '')[:40]}... "
                    f"(匹配: {matched[0]} ↔ {matched[1]})"
                )

        non_self_count = len(records_data) - self_cite_count
        self.log_manager.info(
            f"[自引检测] {self_cite_count} 篇自引 / {non_self_count} 篇需分析 / {len(records_data)} 篇总计"
        )

        # Snapshot API authors NOW — before any enrichment (for Excel comparison)
        import copy
        api_snapshots: dict = {}  # idx → raw API author list (deep copy)
        pdf_snapshots: dict = {}  # idx → PDF-extracted author list
        for idx, (paper, metadata, canonical) in enumerate(records_data):
            api_snapshots[idx] = copy.deepcopy((metadata or {}).get("authors", []))

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

        # NOTE: S2 Author API fallback moved to AFTER PDF parse
        # (PDF extraction provides affiliations for most authors,
        #  S2 Author API only needed for the remaining few)

        await collector.close()

        # ── Phase 2 · PDF 下载 + 解析 + 交叉验证 ──
        self.log_manager.info("=" * 50)
        self.log_manager.info("Phase 2 · PDF 并行下载 + MinerU 解析 + 作者交叉验证")
        self.log_manager.info("=" * 50)

        _cdp_port = getattr(config, 'cdp_debug_port', 0)
        _enable_llm_search = getattr(config, 'enable_pdf_llm_search', False)
        downloader = PDFDownloader(
            scraper_api_keys=config.scraper_api_keys,
            llm_api_key=config.openai_api_key,
            llm_base_url=config.openai_base_url,
            # Use the main model (typically search-grounded) for PDF search,
            # NOT the dashboard_model (lightweight/nothinking).
            # The LLM search needs web search capability to find preprints.
            llm_model=config.openai_model,
            cdp_debug_port=_cdp_port,
            # Read from config (was hardcoded True; flipped to opt-in flag).
            disable_llm_search=not _enable_llm_search,
            # S2 key bumps rate-limit from 1 req/s → 100 req/s
            s2_api_key=getattr(config, 's2_api_key', ''),
            # CORE API enables the aggregator source (free tier: 1000 req/day)
            core_api_key=getattr(config, 'core_api_key', ''),
        )

        # CDP availability diagnostic — tells the user *why* CDP tier is/isn't used
        _cdp_status = "未启用"
        if _cdp_port:
            from citationclaw.core.pdf_downloader import _cdp_check_connection, _cdp_available
            if not _cdp_available():
                _cdp_status = f"端口 {_cdp_port} 但 websocket-client 未安装"
            elif _cdp_check_connection(_cdp_port):
                _cdp_status = f"端口 {_cdp_port} 已连通"
            else:
                _cdp_status = f"端口 {_cdp_port} 未连通（Chrome 未启动或无 --remote-debugging-port）"
        self.log_manager.info(
            f"[PDF下载] CDP: {_cdp_status}, "
            f"LLM搜索: {'启用' if _enable_llm_search else '禁用'}"
        )
        parser = MinerUParser(
            log_callback=self.log_manager.info,
            mineru_api_token=getattr(config, 'mineru_api_token', ''),
        )
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
            _meta = metadata or {}
            dl_papers.append({
                "doi": _meta.get("doi", ""),
                "pdf_url": _meta.get("pdf_url", ""),
                "oa_pdf_url": _meta.get("oa_pdf_url", ""),
                "s2_id": _meta.get("s2_id", ""),
                "arxiv_id": _meta.get("arxiv_id", ""),
                "venue": _meta.get("venue", ""),
                "paper_link": paper.get("paper_link", ""),
                "gs_pdf_link": paper.get("gs_pdf_link", ""),
                "gs_all_versions": paper.get("gs_all_versions", ""),
                "Paper_Title": paper.get("paper_title", ""),
                "title": paper.get("paper_title", ""),
                "paper_year": paper.get("paper_year"),
                "authors_raw": paper.get("authors_raw", {}),
            })

        # Parallel PDF download — skip self-citations (saves time + bandwidth)
        # Use 5 workers (not 10) to avoid rate-limiting on S2/Sci-Hub/LLM APIs
        _DL_CONCURRENCY = 5
        need_download = sum(1 for i in range(len(dl_papers)) if not self_cite_map.get(i, False))

        # ── Dedupe by cache path: two Phase-1 records can flatten to the
        # same paper (identical DOI / title after normalization). We coalesce
        # them BEFORE dispatching so the second record reuses the first
        # record's download result instead of racing for the same cache file.
        _key_to_leader: dict = {}          # cache_path_str -> leader idx
        _follower_to_leader: dict = {}     # follower idx -> leader idx
        for i, paper in enumerate(dl_papers):
            if self_cite_map.get(i, False):
                continue
            try:
                _key = str(downloader._cache_path(paper))
            except Exception:
                continue
            if _key in _key_to_leader:
                _follower_to_leader[i] = _key_to_leader[_key]
            else:
                _key_to_leader[_key] = i
        if _follower_to_leader:
            for follower, leader in _follower_to_leader.items():
                lt = dl_papers[leader].get("Paper_Title", "?")[:45]
                ft = dl_papers[follower].get("Paper_Title", "?")[:45]
                self.log_manager.info(
                    f"  [PDF去重] #{follower+1} ({ft}) 与 #{leader+1} ({lt}) 映射到同一 cache，共享下载结果"
                )
        unique_downloads = need_download - len(_follower_to_leader)
        self.log_manager.info(
            f"[PDF下载] 并行下载 {unique_downloads} 篇非自引论文 "
            f"(跳过 {self_cite_count} 篇自引"
            + (f", {len(_follower_to_leader)} 篇与其他记录同 cache 共享" if _follower_to_leader else "")
            + f") ({_DL_CONCURRENCY} workers)..."
        )

        # Set self-citation papers to None (skip download); followers wait
        # for their leader and reuse the result.
        _leader_results: dict = {}  # leader idx -> asyncio.Future

        async def _dl_leader(idx, paper):
            if self_cite_map.get(idx, False):
                return None
            try:
                # Three-tier log routing (2026-04-21):
                #   log       = INFO (cascade chatter, "HTTP 403" etc.)
                #   log_ok    = SUCCESS (green [PDF OK] + [PDF缓存])
                #   log_error = ERROR (red terminal-failure diagnostic)
                # Users can set config.log_min_level=SUCCESS to collapse
                # the UI to just green + red without losing any info in
                # run.log file (still captured at INFO).
                return await downloader.download(
                    paper,
                    log=self.log_manager.info,
                    log_error=self.log_manager.error,
                    log_ok=self.log_manager.success,
                )
            except Exception as e:
                title = paper.get("Paper_Title", "?")[:40]
                self.log_manager.warning(f"  PDF 下载异常 ({title}): {str(e)[:60]}")
                return None

        sem = asyncio.Semaphore(_DL_CONCURRENCY)

        async def _dl_with_sem(idx, paper):
            # Follower: wait for the leader's result
            if idx in _follower_to_leader:
                leader = _follower_to_leader[idx]
                fut = _leader_results.get(leader)
                if fut is not None:
                    return await fut
                return None  # leader never scheduled (shouldn't happen)
            # Leader or standalone: acquire semaphore and download
            async with sem:
                return await _dl_leader(idx, paper)

        # Create futures for leaders so followers can await them
        loop = asyncio.get_event_loop()
        _leader_tasks = {}
        for i in range(len(dl_papers)):
            if i in _follower_to_leader:
                continue
            _leader_tasks[i] = loop.create_task(_dl_with_sem(i, dl_papers[i]))
            _leader_results[i] = _leader_tasks[i]

        # Gather in original index order (followers reuse leader future)
        async def _resolve(i):
            if i in _leader_tasks:
                return await _leader_tasks[i]
            return await _dl_with_sem(i, dl_papers[i])

        pdf_paths = await asyncio.gather(*[_resolve(i) for i in range(len(dl_papers))])

        for idx, _pdf_path in enumerate(pdf_paths):
            src_idx = _follower_to_leader.get(idx, idx)
            if src_idx >= len(dl_papers) or idx >= len(records_data):
                continue
            src_paper = dl_papers[src_idx]
            record_paper = records_data[idx][0]
            if src_paper.get("_pdf_source"):
                record_paper["_pdf_source"] = src_paper["_pdf_source"]
            if src_paper.get("_pdf_failures"):
                record_paper["_pdf_failures"] = src_paper["_pdf_failures"]

        downloaded = sum(1 for i, p in enumerate(pdf_paths) if p and not self_cite_map.get(i, False))
        failed = need_download - downloaded
        self.log_manager.success(
            f"PDF 下载: {downloaded}/{need_download} 篇成功"
            f"（{failed} 篇失败, {self_cite_count} 篇自引已跳过）"
        )

        # 2026-04-21: When any paper fails, emit a consolidated ERROR-level
        # summary listing the titles. Each individual [PDF失败] block
        # (emitted by PDFDownloader.download's diagnostic dump) already
        # contains the full per-paper cascade trace, so this rollup just
        # gives the user the "scoreboard" + a pointer to find the details.
        if failed > 0:
            failed_titles = []
            for i, p in enumerate(pdf_paths):
                if p is None and not self_cite_map.get(i, False):
                    t = dl_papers[i].get("Paper_Title") or \
                        dl_papers[i].get("title") or "?"
                    failed_titles.append(t)
            self.log_manager.error(
                f"[PDF失败汇总] {failed}/{need_download} 篇未能下载 "
                f"(cascade + {downloader._RETRY_ATTEMPTS} 次重试均未命中)："
            )
            for idx, t in enumerate(failed_titles, start=1):
                self.log_manager.error(f"  {idx}. {t[:80]}")
            # Point users at the per-paper diagnostic blocks that
            # PDFDownloader.download has already emitted at ERROR level.
            self.log_manager.error(
                "  每篇失败 paper 的完整 cascade 尝试链（15+ tiers × 3 attempts）"
                "已在 run.log 里以 [PDF失败] 开头的 ERROR 块形式打印。"
                "用 `grep '\\[PDF失败\\]' run.log` 或在 UI 里搜索 ERROR 级别即可定位。"
            )

        # Parse + extract authors + cross-validate (parallel, 10 workers for Cloud API)
        if downloaded > 0:
            self.log_manager.info(
                f"[PDF解析] 并行解析 {downloaded} 篇 PDF (Cloud API → 本地 MinerU → PyMuPDF) + LLM 提取作者..."
            )
            parse_sem = asyncio.Semaphore(10)  # Cloud API can handle high concurrency
            validated_count = 0
            parse_counter = {"done": 0, "lock": asyncio.Lock()}

            async def _parse_and_validate(idx: int):
                nonlocal validated_count
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
                            pdf_snapshots[idx] = cached_authors
                            api_authors = (metadata or {}).get("authors", [])
                            merged = validator.validate(api_authors, cached_authors)
                            if metadata:
                                metadata["authors"] = merged
                            validated_count += 1
                            async with parse_counter["lock"]:
                                parse_counter["done"] += 1
                                n = parse_counter["done"]
                            self.log_manager.info(
                                f"  [解析 {n}/{downloaded}] 💾 {title[:50]}... (缓存)"
                            )
                            return

                    # Parse PDF (async: Cloud Agent → Cloud Precision → Local → PyMuPDF)
                    try:
                        parsed = await parser.parse_async(pdf_path, pkey)
                    except Exception as e:
                        async with parse_counter["lock"]:
                            parse_counter["done"] += 1
                            n = parse_counter["done"]
                        self.log_manager.info(
                            f"  [解析 {n}/{downloaded}] ⚠ {title[:40]}... 失败: {str(e)[:50]}"
                        )
                        parsed = None
                    if not parsed:
                        return

                    # Store parse cache
                    parse_cache.store(pkey, {
                        "title": title,
                        "source": parsed.get("source", ""),
                        "has_content_list": bool(parsed.get("content_list")),
                    })

                    async with parse_counter["lock"]:
                        parse_counter["done"] += 1
                        n = parse_counter["done"]
                    self.log_manager.info(
                        f"  [解析 {n}/{downloaded}] {title[:50]}... "
                        f"({parsed.get('source','?')})"
                    )

                    # Extract authors from first page via LLM
                    pdf_authors = await author_extractor.extract(
                        parsed.get("first_page_blocks", [])
                    )
                    if pdf_authors:
                        parse_cache.store_authors(pkey, pdf_authors)
                        pdf_snapshots[idx] = pdf_authors

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

        # ── S2 Author API: fill missing affiliations (AFTER PDF parse) ──
        # PDF extraction fills most affiliations. S2 Author API only for remaining.
        s2_fallback = []
        _seen_s2 = set()
        for _, metadata, _ in records_data:
            if not metadata:
                continue
            for a in metadata.get("authors", []):
                if not a.get("affiliation") and a.get("s2_id"):
                    nl = a.get("name", "").strip().lower()
                    if nl and nl not in _seen_s2:
                        _seen_s2.add(nl)
                        s2_fallback.append((nl, a["s2_id"], a))

        if s2_fallback:
            from citationclaw.core.s2_client import S2Client
            s2_for_authors = S2Client(api_key=getattr(config, 's2_api_key', None))
            self.log_manager.info(
                f"[S2补充] 查询 {len(s2_fallback)} 位仍缺机构的作者 (去重后, 并行3)..."
            )
            s2_author_sem = asyncio.Semaphore(3)
            s2_enriched = 0

            async def _fetch_s2_author(nl, s2_id, author_dict):
                nonlocal s2_enriched
                async with s2_author_sem:
                    try:
                        data = await s2_for_authors.get_author(s2_id)
                        if data:
                            if data.get("affiliation") and not author_dict.get("affiliation"):
                                author_dict["affiliation"] = data["affiliation"]
                                s2_enriched += 1
                            if data.get("h_index") and not author_dict.get("h_index"):
                                author_dict["h_index"] = data["h_index"]
                    except Exception:
                        pass

            await asyncio.gather(*[_fetch_s2_author(n, s, a) for n, s, a in s2_fallback])
            await s2_for_authors.close()
            self.log_manager.info(f"  → S2 补充了 {s2_enriched} 位作者的机构信息")

        # ── Phase 2e: 补查 pdf_only 作者的 h-index / ID (OpenAlex Author Search) ──
        # PDF-only authors have no openalex_id/h_index, would be filtered out by prefilter.
        # Search OpenAlex Author API by name to enrich them.
        pdf_only_authors: list = []  # (author_dict_ref, name)
        for _, metadata, _ in records_data:
            if not metadata:
                continue
            for a in metadata.get("authors", []):
                if a.get("affiliation_source") == "pdf_only" and not a.get("openalex_id"):
                    pdf_only_authors.append((a, a.get("name", "")))

        if pdf_only_authors:
            # Deduplicate by name
            seen_names: set = set()
            unique_pdf_only: list = []
            for a, name in pdf_only_authors:
                nl = name.strip().lower()
                if nl and nl not in seen_names:
                    seen_names.add(nl)
                    unique_pdf_only.append((a, name))

            self.log_manager.info(
                f"[PDF作者补查] {len(unique_pdf_only)} 位 PDF-only 作者缺少 h-index/ID，"
                f"通过 OpenAlex Author API 按姓名补查..."
            )
            # Re-open collector for author search (collector was closed above)
            from citationclaw.core.openalex_client import OpenAlexClient
            oa_client = OpenAlexClient()
            enrich_sem = asyncio.Semaphore(10)
            pdf_only_enriched = 0

            # Build name→detail lookup (search once per unique name)
            pdf_only_details: dict = {}  # name_lower → author detail dict

            async def _search_pdf_only_author(name: str):
                async with enrich_sem:
                    try:
                        detail = await oa_client.search_author_by_name(name)
                        if detail:
                            pdf_only_details[name.strip().lower()] = detail
                    except Exception:
                        pass

            await asyncio.gather(*[_search_pdf_only_author(name) for _, name in unique_pdf_only])

            # Apply enrichment to all pdf_only author dicts across records_data
            for _, metadata, _ in records_data:
                if not metadata:
                    continue
                for a in metadata.get("authors", []):
                    if a.get("affiliation_source") != "pdf_only":
                        continue
                    nl = a.get("name", "").strip().lower()
                    detail = pdf_only_details.get(nl)
                    if not detail:
                        continue
                    if detail.get("h_index") and not a.get("h_index"):
                        a["h_index"] = detail["h_index"]
                        pdf_only_enriched += 1
                    if detail.get("openalex_id") and not a.get("openalex_id"):
                        a["openalex_id"] = detail["openalex_id"]
                    if detail.get("citation_count") and not a.get("citation_count"):
                        a["citation_count"] = detail["citation_count"]
                    # Keep PDF affiliation (more reliable), don't overwrite

            await oa_client.close()
            self.log_manager.info(
                f"  → {pdf_only_enriched} 位 PDF-only 作者补充了 h-index"
                f"（共查到 {len(pdf_only_details)} 位）"
            )

        # Build non_self_cite_records (self_cite_map already computed above)
        non_self_cite_records = [
            (paper, metadata, canonical)
            for i, (paper, metadata, canonical) in enumerate(records_data)
            if not self_cite_map.get(i, False)
        ]

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
        from citationclaw.core.scholar_search_cache import ScholarSearchCache
        scholar_cache = ScholarSearchCache()
        cache_stats = scholar_cache.stats()
        self.log_manager.info(
            f"[学者缓存] 已加载 {cache_stats['total_entries']} 条历史记录"
        )

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

        # Parallel search LLM calls with retry + real-time logging
        search_sem = asyncio.Semaphore(10)
        paper_scholar_map: dict = {}  # paper_title_lower → list of scholar dicts
        global_seen_keys: set = set()
        global_unique_count = 0
        search_done = 0

        async def _search_one(idx: int, paper: dict, metadata: dict):
            nonlocal search_done, global_unique_count
            title = paper.get("paper_title", "")

            async with search_sem:
                if self.should_cancel:
                    return

                # Check cache first
                cached_scholars = scholar_cache.get(title)
                if cached_scholars is not None:
                    search_done += 1
                    if cached_scholars:
                        paper_scholar_map[title.lower().strip()] = cached_scholars
                        for s in cached_scholars:
                            name_keys = ScholarSearchAgent._extract_name_keys(s.get("name", ""))
                            if not (name_keys & global_seen_keys):
                                global_unique_count += 1
                            global_seen_keys.update(name_keys)
                        labels = "; ".join(f"{s['tier']}: {s['name']}" for s in cached_scholars)
                        self.log_manager.info(f"  [{search_done}/{n_search}] 💾 {title[:45]}... → {labels}")
                    else:
                        self.log_manager.info(f"  [{search_done}/{n_search}] 💾 {title[:45]}... → 无知名学者")
                    return

                # Retry up to 2 times on connection errors
                raw = []
                for attempt in range(3):
                    try:
                        raw = await search_agent.search_paper_authors(
                            paper_title=title,
                            authors=metadata.get("authors", []),
                        )
                        break  # Success
                    except Exception as e:
                        if attempt < 2:
                            await asyncio.sleep(3 * (attempt + 1))  # 3s, 6s backoff
                        else:
                            self.log_manager.info(f"  [{idx+1}/{n_search}] {title[:45]}... → ⚠ 失败: {str(e)[:40]}")
                            raw = []

                search_done += 1

                # Process results immediately (real-time logging)
                found = []
                if raw:
                    for r in raw:
                        if r.name and r.tier:
                            found.append({
                                "name": r.name, "tier": r.tier,
                                "honors": [r.honors] if r.honors else [],
                                "affiliation": r.affiliation,
                                "country": r.country, "position": r.position,
                            })
                            name_keys = ScholarSearchAgent._extract_name_keys(r.name)
                            if not (name_keys & global_seen_keys):
                                global_unique_count += 1
                            global_seen_keys.update(name_keys)

                if found:
                    paper_scholar_map[title.lower().strip()] = found
                    labels = "; ".join(f"{s['tier']}: {s['name']}" for s in found)
                    self.log_manager.info(f"  [{search_done}/{n_search}] {title[:45]}... → {labels}")
                else:
                    self.log_manager.info(f"  [{search_done}/{n_search}] {title[:45]}... → 无知名学者")

                # Store to cache (both found and not-found)
                await scholar_cache.update(title, found)

        try:
            await asyncio.gather(*[
                _search_one(idx, paper, metadata)
                for idx, (paper, metadata, canonical) in enumerate(papers_with_candidates)
            ])
        finally:
            await search_agent.close()
            await scholar_cache.flush()

        cache_final = scholar_cache.stats()
        self.log_manager.success(
            f"Phase 3 完成: {global_unique_count} 位知名学者（去重）/ {n_search} 篇论文"
            f"（缓存命中 {cache_final['hits']} / 新查询 {cache_final['misses']}）"
        )

        # Scholar verification is now integrated into the search prompt
        # (self-verification step 5 in SCHOLAR_SEARCH_PROMPT)

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

                # PDF download info. Resolve to absolute path so downstream
                # consumers (dashboards, Phase 4, eval harnesses) can open the
                # file regardless of their working directory. Empty string
                # when no PDF was obtained.
                _pdf = pdf_paths[i] if i < len(pdf_paths) else None
                _pdf_ok = _pdf is not None
                if _pdf:
                    try:
                        _pdf_rel = str(Path(_pdf).resolve())
                    except Exception:
                        _pdf_rel = str(_pdf)
                else:
                    _pdf_rel = ""

                record_idx += 1
                record = adapter.to_legacy_record(
                    paper=paper,
                    metadata=metadata,
                    self_citation=self_cite_result,
                    renowned_scholars=paper_scholars,
                    citing_paper=canonical,
                    record_index=record_idx,
                    api_authors_snapshot=api_snapshots.get(i),
                    pdf_authors_snapshot=pdf_snapshots.get(i),
                    pdf_downloaded=_pdf_ok,
                    pdf_path=_pdf_rel,
                )
                f.write(_json.dumps(record, ensure_ascii=False) + "\n")

        # ── Affiliation enrichment: fill 未知机构 for top-tier scholars only ──
        await self._enrich_unknown_affiliations(merged_file, config, paper_scholar_map)

        # ── Data validation: fix country labels & detect non-country content ──
        self.log_manager.info("[数据校验] 修正国家标签...")
        fixed_count = await self._validate_and_fix_records(merged_file, config)
        if fixed_count:
            self.log_manager.info(f"  → 修正了 {fixed_count} 条记录的国家/字段信息")

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

        return merged_file, excel_file, json_file, pdf_paths

    async def _enrich_unknown_affiliations(self, merged_file: Path, config,
                                             paper_scholar_map: dict = None):
        """Use Search LLM to fill 未知机构 — only for identified top-tier scholars."""
        if not config.openai_api_key:
            return

        # Collect scholar names from paper_scholar_map (Phase 3 results)
        scholar_names = set()
        if paper_scholar_map:
            for scholars in paper_scholar_map.values():
                for s in scholars:
                    name = s.get("name", "")
                    if name:
                        scholar_names.add(name.strip().lower())

        if not scholar_names:
            return

        lines = []
        with open(merged_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(_json.loads(line))

        # Only search scholars with missing affiliations
        author_paper_map = {}  # author_name_lower → (display_name, paper_title)
        papers_with_unknown = []  # (line_idx, rec_key) for write-back

        for i, record_wrapper in enumerate(lines):
            for rec_key, rec in record_wrapper.items():
                affil = str(rec.get("Authors_Affiliation", "") or "")
                if "未知机构" not in affil and "未知" not in affil:
                    continue
                title = rec.get("Paper_Title", "")
                affil_lines = [l.strip() for l in affil.split('\n') if l.strip()]
                for j in range(0, len(affil_lines) - 1, 2):
                    name = affil_lines[j]
                    inst = affil_lines[j + 1] if j + 1 < len(affil_lines) else ""
                    if "未知" in inst or not inst.strip():
                        nl = name.strip().lower()
                        if nl in scholar_names and nl not in author_paper_map:
                            author_paper_map[nl] = (name, title)
                        if (i, rec_key) not in papers_with_unknown:
                            papers_with_unknown.append((i, rec_key))

        if not author_paper_map:
            self.log_manager.info("[机构补全] 知名学者机构均已知，跳过")
            return

        self.log_manager.info(
            f"[机构补全] {len(author_paper_map)} 位知名学者机构未知，"
            f"使用 Search LLM 查询..."
        )

        try:
            from openai import AsyncOpenAI
            from citationclaw.core.http_utils import make_async_client
            client = AsyncOpenAI(
                api_key=config.openai_api_key,
                base_url=(config.openai_base_url or "").rstrip("/") + "/",
                http_client=make_async_client(timeout=60.0),
            )

            affil_results = {}  # author_name_lower → institution
            sem = asyncio.Semaphore(3)

            async def _search_one(name_lower, display_name, paper_title):
                prompt = (
                    f"请搜索学者 {display_name} 的当前任职机构。"
                    f"该学者是论文《{paper_title[:50]}》的作者之一。\n"
                    f"只需回答机构全称（如 Tsinghua University），无法确定则回答「未知」。"
                )
                try:
                    async with sem:
                        resp = await asyncio.wait_for(
                            client.chat.completions.create(
                                model=config.openai_model,
                                messages=[{"role": "user", "content": prompt}],
                                temperature=0.0,
                                extra_body={"web_search_options": {}},
                            ),
                            timeout=45,
                        )
                    answer = (resp.choices[0].message.content or "").strip()
                    # Clean up: remove quotes, markdown, etc.
                    answer = answer.strip('"\'`').strip()
                    if answer and "未知" not in answer and len(answer) < 100:
                        affil_results[name_lower] = answer
                        self.log_manager.info(f"    ✓ {display_name} → {answer}")
                    else:
                        self.log_manager.info(f"    - {display_name} → 未知")
                except Exception:
                    pass

            tasks = [
                _search_one(nl, info[0], info[1])
                for nl, info in author_paper_map.items()
            ]
            await asyncio.gather(*tasks)

            # Apply results to records
            fixed_total = 0
            for line_idx, rec_key in papers_with_unknown:
                rec = lines[line_idx][rec_key]
                affil_text = str(rec.get("Authors_Affiliation", ""))
                affil_lines = affil_text.split('\n')
                changed = False
                for j in range(0, len(affil_lines) - 1, 2):
                    name = affil_lines[j].strip()
                    inst = affil_lines[j + 1].strip() if j + 1 < len(affil_lines) else ""
                    if "未知" in inst or not inst:
                        new_inst = affil_results.get(name.strip().lower())
                        if new_inst:
                            affil_lines[j + 1] = new_inst
                            changed = True
                            fixed_total += 1
                if changed:
                    rec["Authors_Affiliation"] = '\n'.join(affil_lines)
                    if not rec.get("First_Author_Institution") and len(affil_lines) >= 2:
                        first_inst = affil_lines[1].strip()
                        if first_inst and "未知" not in first_inst:
                            rec["First_Author_Institution"] = first_inst

            # Write back
            if fixed_total > 0:
                with open(merged_file, "w", encoding="utf-8") as f:
                    for record in lines:
                        f.write(_json.dumps(record, ensure_ascii=False) + "\n")
                self.log_manager.info(f"  → 补全了 {fixed_total} 位作者的机构信息")
            else:
                self.log_manager.info("  → 未能补全更多机构信息")

        except Exception as e:
            self.log_manager.info(f"  ⚠ 机构补全失败: {str(e)[:50]}")

    async def _validate_and_fix_records(self, merged_file: Path, config) -> int:
        """Post-pass: validate and fix country labels and other field issues.

        Reads merged JSONL, fixes in-place, returns number of fixed records.
        """
        from citationclaw.core.scholar_search_agent import ScholarSearchAgent

        lines = []
        with open(merged_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(_json.loads(line))

        fixed = 0
        # Collect records that need LLM-based country inference
        llm_fix_needed = []  # (line_idx, record_key, field_name, bad_value, affiliation)

        for i, record_wrapper in enumerate(lines):
            for rec_key, rec in record_wrapper.items():
                changed = False

                # Fix First_Author_Country
                country = str(rec.get("First_Author_Country", "") or "").strip()
                if country:
                    normalized = ScholarSearchAgent._normalize_country(country)
                    if normalized != country:
                        rec["First_Author_Country"] = normalized
                        changed = True

                    if not ScholarSearchAgent._is_valid_country(rec["First_Author_Country"]):
                        # Non-country content detected — try to infer from affiliation
                        affil = str(rec.get("First_Author_Institution", "") or "").strip()
                        from citationclaw.core.affiliation_validator import AffiliationValidator
                        inferred = AffiliationValidator._infer_country(affil)
                        if inferred:
                            rec["First_Author_Country"] = ScholarSearchAgent._normalize_country(inferred)
                            changed = True
                        else:
                            # Can't infer from rules — queue for LLM
                            llm_fix_needed.append((i, rec_key, "First_Author_Country",
                                                    rec["First_Author_Country"], affil))
                            rec["First_Author_Country"] = ""  # Clear invalid value for now
                            changed = True

                # Also validate country in Authors_Affiliation text isn't corrupted
                if not rec.get("First_Author_Country") and rec.get("First_Author_Institution"):
                    from citationclaw.core.affiliation_validator import AffiliationValidator
                    inferred = AffiliationValidator._infer_country(rec["First_Author_Institution"])
                    if inferred:
                        rec["First_Author_Country"] = ScholarSearchAgent._normalize_country(inferred)
                        changed = True

                # Fix scholar metadata — validate country in Formated Renowned Scholar
                scholars = rec.get("Formated Renowned Scholar", [])
                if isinstance(scholars, list):
                    for s in scholars:
                        if not isinstance(s, dict):
                            continue
                        # Check 国家 field for non-country content
                        s_country_key = '国家' if '国家' in s else 'country'
                        s_country = str(s.get(s_country_key, '') or '').strip()
                        if s_country:
                            norm = ScholarSearchAgent._normalize_country(s_country)
                            if norm != s_country:
                                s[s_country_key] = norm
                                changed = True
                            if not ScholarSearchAgent._is_valid_country(s.get(s_country_key, '')):
                                # Non-country content in scholar record — infer from institution
                                s_inst = str(s.get('机构', s.get('institution', '')) or '')
                                from citationclaw.core.affiliation_validator import AffiliationValidator
                                inferred = AffiliationValidator._infer_country(s_inst)
                                if inferred:
                                    s[s_country_key] = ScholarSearchAgent._normalize_country(inferred)
                                    changed = True
                                else:
                                    # Queue for LLM
                                    llm_fix_needed.append((
                                        i, rec_key,
                                        f"scholar:{s.get('姓名', s.get('name', ''))}:country",
                                        s.get(s_country_key, ''), s_inst
                                    ))
                                    s[s_country_key] = ''
                                    changed = True

                if changed:
                    fixed += 1

        # LLM-based fix for unresolvable cases (batch)
        if llm_fix_needed and config.openai_api_key:
            llm_model = getattr(config, 'dashboard_model', '') or config.openai_model
            try:
                from openai import AsyncOpenAI
                from citationclaw.core.http_utils import make_async_client
                client = AsyncOpenAI(
                    api_key=config.openai_api_key,
                    base_url=(config.openai_base_url or "").rstrip("/") + "/",
                    http_client=make_async_client(timeout=30.0),
                )
                # Batch all into one LLM call
                items = []
                for _, _, _, bad_val, affil in llm_fix_needed[:20]:
                    items.append(f"- 原始值: \"{bad_val}\", 机构: \"{affil}\"")
                prompt = (
                    "以下记录的'国家'字段内容异常（可能是职务、头衔等非国家内容）。"
                    "请根据机构信息推断每条记录应属于哪个国家，输出中文国家名。"
                    "如无法判断输出\"未知\"。每行一个国家，顺序对应。\n\n"
                    + "\n".join(items)
                )
                resp = await client.chat.completions.create(
                    model=llm_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                )
                answers = resp.choices[0].message.content.strip().split('\n')
                for j, (line_idx, rec_key, field, _, _) in enumerate(llm_fix_needed[:20]):
                    if j < len(answers):
                        country_fix = answers[j].strip().strip('-').strip()
                        if country_fix and country_fix != "未知":
                            if field.startswith("scholar:"):
                                # Fix scholar's country: field = "scholar:Name:country"
                                parts = field.split(":")
                                s_name = parts[1] if len(parts) > 1 else ""
                                scholars = lines[line_idx][rec_key].get("Formated Renowned Scholar", [])
                                for s in scholars:
                                    if isinstance(s, dict):
                                        sn = s.get('姓名', s.get('name', ''))
                                        if sn == s_name:
                                            s_key = '国家' if '国家' in s else 'country'
                                            s[s_key] = country_fix
                                            break
                                self.log_manager.info(f"  [LLM修正] 学者 {s_name} 国家: → {country_fix}")
                            else:
                                lines[line_idx][rec_key][field] = country_fix
                                self.log_manager.info(f"  [LLM修正] {field}: → {country_fix}")
            except Exception as e:
                self.log_manager.info(f"  ⚠ LLM国家修正失败: {str(e)[:50]}")

        # Write back
        if fixed > 0:
            with open(merged_file, "w", encoding="utf-8") as f:
                for record in lines:
                    f.write(_json.dumps(record, ensure_ascii=False) + "\n")

        return fixed

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
            _folder_prefix = getattr(config, "result_folder_prefix", "") or ""
            folder_name = f"{_folder_prefix}-result-{timestamp}" if _folder_prefix else f"result-{timestamp}"
            result_dir = DATA_DIR / folder_name
            result_dir.mkdir(parents=True, exist_ok=True)
            file_prefix = f"{output_prefix}-{timestamp}"

            # Start persistent log file in result directory
            self.log_manager.set_log_file(result_dir / "run.log")

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

            # ==================== 阶段4: 引文语境提取（可选）====================
            citing_desc_excel = excel_file
            if config.enable_citing_description:
                self.log_manager.info("=" * 50)
                self.log_manager.info("Phase 4 · 引文语境提取: PDF 解析 + 轻量 LLM")
                self.log_manager.info("=" * 50)

                import pandas as pd
                phase4_output_jsonl = result_dir / f"{file_prefix}_citing_desc.jsonl"
                await self._run_skill(
                    "phase4_citation_extract",
                    config,
                    input_file=author_info_file,
                    output_file=phase4_output_jsonl,
                    target_title=output_prefix,
                    target_authors=[],
                    citation_desc_cache=desc_cache,
                )

                # Merge descriptions back into Excel
                if phase4_output_jsonl.exists():
                    import json
                    desc_map = {}
                    with open(phase4_output_jsonl, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                rec = json.loads(line)
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
                        citing_desc_excel = result_dir / f"{file_prefix}_results_with_citing_desc.xlsx"
                        df.to_excel(citing_desc_excel, index=False)
                        n_with = (df["Citing_Description"].str.strip() != "").sum()
                        self.log_manager.success(
                            f"Phase 4 完成: {n_with}/{len(df)} 篇有引文语境描述"
                        )

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
            self.log_manager.close_log_file()
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
            self.log_manager.close_log_file()

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
            self.log_manager.close_log_file()

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
        self._phase2_login_event = None
        self._phase2_login_done = False
        self.quota_exceeded_event = asyncio.Event()

        # 初始化费用追踪器
        cost_tracker = CostTracker()

        # 初始化持久化缓存（在 try 外，确保 finally 可访问）
        author_cache = None
        desc_cache = None

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            _folder_prefix = getattr(config, "result_folder_prefix", "") or ""
            folder_name = f"{_folder_prefix}-result-{timestamp}" if _folder_prefix else f"result-{timestamp}"
            result_dir = DATA_DIR / folder_name
            result_dir.mkdir(parents=True, exist_ok=True)

            # Start persistent log file in result directory
            self.log_manager.set_log_file(result_dir / "run.log")

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
            merged_file, excel_file, json_file, phase2_pdf_paths = pipeline_result

            # —— Phase 4：引文语境提取（可选，PDF 下载 + 本地解析）——
            citing_desc_excel = excel_file
            if config.enable_citing_description:
                import pandas as pd

                self.log_manager.info("=" * 50)
                self.log_manager.info("Phase 4 · 引文语境提取: PDF 解析 + 轻量 LLM (复用 Phase 2 PDF)")
                self.log_manager.info("=" * 50)

                phase4_output_jsonl = result_dir / f"{output_prefix}_citing_desc.jsonl"
                target_title = canonical_titles[0] if canonical_titles else ""

                await self._run_skill(
                    "phase4_citation_extract",
                    config,
                    input_file=merged_file,
                    output_file=phase4_output_jsonl,
                    target_title=target_title,
                    target_authors=[],
                    pdf_paths=phase2_pdf_paths,
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
            self.log_manager.close_log_file()
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
                    "Authors_Affiliation": author_entry.get("Authors_Affiliation", ""),
                    "First_Author_Institution": author_entry.get("First_Author_Institution", ""),
                    "First_Author_Country": author_entry.get("First_Author_Country", ""),
                    "Authors_Detail": author_entry.get("Authors_Detail", ""),
                    "Renowned Scholar": author_entry.get("Renowned Scholar", ""),
                    "Formated Renowned Scholar": author_entry.get("Formated Renowned Scholar", []),
                }
                rows.append(row)

            df = pd.DataFrame(rows)

            # 创建输出目录
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            _folder_prefix = getattr(config, "result_folder_prefix", "") or ""
            folder_name = f"{_folder_prefix}-result-{timestamp}" if _folder_prefix else f"result-{timestamp}"
            result_dir = DATA_DIR / folder_name
            result_dir.mkdir(parents=True, exist_ok=True)

            # Start persistent log file in result directory
            self.log_manager.set_log_file(result_dir / "run.log")

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
            self.log_manager.close_log_file()

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
            scholar.lower() in str(row.get('Authors_Affiliation', '') or '').lower() or
            scholar.lower() in str(row.get('GS_Authors', '') or '').lower() or
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
