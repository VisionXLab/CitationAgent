import asyncio
from pathlib import Path
from typing import Optional, List
from datetime import datetime
from core.scraper import GoogleScholarScraper
from core.author_searcher import AuthorSearcher
from core.exporter import ResultExporter
from app.log_manager import LogManager
from app.config_manager import AppConfig


class TaskExecutor:
    def __init__(self, log_manager: LogManager):
        """
        任务执行器,负责协调整个工作流

        Args:
            log_manager: 日志管理器
        """
        self.log_manager = log_manager
        self.current_task: Optional[asyncio.Task] = None
        self.is_running = False
        self.should_cancel = False

        # 保存阶段1的结果，供阶段2使用
        self.stage1_result: Optional[dict] = None

    async def execute_full_pipeline(
        self,
        url: str,
        config: AppConfig,
        output_prefix: str,
        resume_page: int = 0
    ):
        """
        执行完整流程:抓取 -> 搜索作者 -> 导出

        Args:
            url: Google Scholar引用列表URL
            config: 应用配置
            output_prefix: 输出文件前缀
            resume_page: 断点续爬起始页
        """
        self.is_running = True
        self.should_cancel = False

        try:
            # 生成带时间戳的文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_prefix = f"{output_prefix}-{timestamp}"

            self.log_manager.info(f"📁 文件前缀: {file_prefix}")

            # ==================== 阶段1: 抓取引用列表 ====================
            self.log_manager.info("=" * 50)
            self.log_manager.info("阶段1: 开始抓取Google Scholar引用列表")
            self.log_manager.info("=" * 50)

            scraper = GoogleScholarScraper(
                api_keys=config.scraper_api_keys,
                log_callback=self.log_manager.info,
                progress_callback=self.log_manager.update_progress,
                debug_mode=config.debug_mode,
                premium=config.scraper_premium,
                ultra_premium=config.scraper_ultra_premium,
                retry_max_attempts=config.retry_max_attempts,
                retry_intervals=config.retry_intervals,
                session=config.scraper_session,
                no_filter=config.scholar_no_filter,
                geo_rotate=config.scraper_geo_rotate,
                dc_retry_max_attempts=config.dc_retry_max_attempts,
            )

            citing_papers_file = Path(f"data/jsonl/{file_prefix}_citing_papers.jsonl")
            await scraper.scrape(
                url=url,
                output_file=citing_papers_file,
                start_page=resume_page,
                sleep_seconds=config.sleep_between_pages,
                cancel_check=lambda: self.should_cancel,
                enable_year_traverse=config.enable_year_traverse
            )

            if self.should_cancel:
                self.log_manager.warning("任务已被用户取消")
                return

            self.log_manager.success("阶段1完成: 引用列表抓取成功")

            # ==================== 阶段2: 搜索作者信息 ====================
            self.log_manager.info("=" * 50)
            self.log_manager.info("阶段2: 开始搜索作者学术信息")
            self.log_manager.info("=" * 50)

            searcher = AuthorSearcher(
                api_key=config.openai_api_key,
                base_url=config.openai_base_url,
                model=config.openai_model,
                log_callback=self.log_manager.info,
                progress_callback=self.log_manager.update_progress,
                prompt1=config.author_search_prompt1,
                prompt2=config.author_search_prompt2,
                enable_renowned_scholar=config.enable_renowned_scholar_filter,
                renowned_scholar_model=config.renowned_scholar_model,
                renowned_scholar_prompt=config.renowned_scholar_prompt,
                enable_author_verification=config.enable_author_verification,
                author_verify_model=config.author_verify_model,
                author_verify_prompt=config.author_verify_prompt,
                debug_mode=config.debug_mode
            )

            author_info_file = Path(f"data/jsonl/{file_prefix}_author_information.jsonl")
            await searcher.search(
                input_file=citing_papers_file,
                output_file=author_info_file,
                sleep_seconds=config.sleep_between_authors,
                parallel_workers=config.parallel_author_search,
                cancel_check=lambda: self.should_cancel
            )

            if self.should_cancel:
                self.log_manager.warning("任务已被用户取消")
                return

            self.log_manager.success("阶段2完成: 作者信息搜索成功")

            # ==================== 阶段3: 导出结果 ====================
            self.log_manager.info("=" * 50)
            self.log_manager.info("阶段3: 开始导出结果")
            self.log_manager.info("=" * 50)

            exporter = ResultExporter(log_callback=self.log_manager.info)

            excel_file = Path(f"data/excel/{file_prefix}_author_information.xlsx")
            json_file = Path(f"data/json/{file_prefix}_author_information.json")

            await exporter.export(
                input_file=author_info_file,
                excel_output=excel_file,
                json_output=json_file
            )

            self.log_manager.success("=" * 50)
            self.log_manager.success("全部任务完成!")
            self.log_manager.success(f"Excel文件: {excel_file}")
            self.log_manager.success(f"JSON文件: {json_file}")
            self.log_manager.success("=" * 50)

        except Exception as e:
            self.log_manager.error(f"任务执行错误: {str(e)}")
            import traceback
            self.log_manager.error(traceback.format_exc())
            raise
        finally:
            self.is_running = False

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
        self.is_running = True
        self.should_cancel = False

        try:
            # 生成带时间戳的文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_prefix = f"{output_prefix}-{timestamp}"

            self.log_manager.info(f"📁 文件前缀: {file_prefix}")

            # ==================== 阶段1: 抓取引用列表 ====================
            self.log_manager.info("=" * 50)
            self.log_manager.info("阶段1: 开始抓取Google Scholar引用列表")
            self.log_manager.info("=" * 50)

            scraper = GoogleScholarScraper(
                api_keys=config.scraper_api_keys,
                log_callback=self.log_manager.info,
                progress_callback=self.log_manager.update_progress,
                debug_mode=config.debug_mode,
                premium=config.scraper_premium,
                ultra_premium=config.scraper_ultra_premium,
                retry_max_attempts=config.retry_max_attempts,
                retry_intervals=config.retry_intervals,
                session=config.scraper_session,
                no_filter=config.scholar_no_filter,
                geo_rotate=config.scraper_geo_rotate,
                dc_retry_max_attempts=config.dc_retry_max_attempts,
            )

            citing_papers_file = Path(f"data/jsonl/{file_prefix}_citing_papers.jsonl")
            await scraper.scrape(
                url=url,
                output_file=citing_papers_file,
                start_page=resume_page,
                sleep_seconds=config.sleep_between_pages,
                cancel_check=lambda: self.should_cancel,
                enable_year_traverse=config.enable_year_traverse
            )

            if self.should_cancel:
                self.log_manager.warning("任务已被用户取消")
                return

            self.log_manager.success("=" * 50)
            self.log_manager.success("✅ 阶段1完成: 引用列表抓取成功")
            self.log_manager.success(f"📄 输出文件: {citing_papers_file}")
            self.log_manager.success("=" * 50)

            # 保存阶段1结果供阶段2使用
            self.stage1_result = {
                "file_prefix": file_prefix,
                "citing_papers_file": str(citing_papers_file),
                "config": config
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
            target_path = Path(f"data/jsonl/{file_prefix}_citing_papers.jsonl")
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # 复制文件
            import shutil
            shutil.copy2(file_path, target_path)

            # 保存阶段1结果供阶段2使用
            self.stage1_result = {
                "file_prefix": file_prefix,
                "citing_papers_file": str(target_path),
                "config": config
            }

            self.log_manager.success(f"✅ 成功导入历史记录: {file_path.name}")
            self.log_manager.info(f"📄 论文数量: {paper_count}")
            self.log_manager.info(f"📁 保存位置: {target_path}")

            return {
                "success": True,
                "file_name": file_path.name,
                "paper_count": paper_count,
                "file_prefix": file_prefix
            }

        except Exception as e:
            self.log_manager.error(f"❌ 导入失败: {str(e)}")
            return {"success": False, "message": str(e)}

    async def execute_stage2_and_3(self):
        """
        执行阶段2和3: 搜索作者信息 + 导出结果
        需要先执行阶段1，或者有保存的阶段1结果
        """
        if not self.stage1_result:
            self.log_manager.error("❌ 错误: 未找到阶段1的结果，请先执行阶段1或导入历史记录")
            return

        self.is_running = True
        self.should_cancel = False

        try:
            file_prefix = self.stage1_result["file_prefix"]
            citing_papers_file = Path(self.stage1_result["citing_papers_file"])
            config = self.stage1_result["config"]

            # ==================== 阶段2: 搜索作者信息 ====================
            self.log_manager.info("=" * 50)
            self.log_manager.info("阶段2: 开始搜索作者学术信息")
            self.log_manager.info("=" * 50)

            searcher = AuthorSearcher(
                api_key=config.openai_api_key,
                base_url=config.openai_base_url,
                model=config.openai_model,
                log_callback=self.log_manager.info,
                progress_callback=self.log_manager.update_progress,
                prompt1=config.author_search_prompt1,
                prompt2=config.author_search_prompt2,
                enable_renowned_scholar=config.enable_renowned_scholar_filter,
                renowned_scholar_model=config.renowned_scholar_model,
                renowned_scholar_prompt=config.renowned_scholar_prompt,
                enable_author_verification=config.enable_author_verification,
                author_verify_model=config.author_verify_model,
                author_verify_prompt=config.author_verify_prompt,
                debug_mode=config.debug_mode
            )

            author_info_file = Path(f"data/jsonl/{file_prefix}_author_information.jsonl")
            await searcher.search(
                input_file=citing_papers_file,
                output_file=author_info_file,
                sleep_seconds=config.sleep_between_authors,
                parallel_workers=config.parallel_author_search,
                cancel_check=lambda: self.should_cancel
            )

            if self.should_cancel:
                self.log_manager.warning("任务已被用户取消")
                return

            self.log_manager.success("阶段2完成: 作者信息搜索成功")

            # ==================== 阶段3: 导出结果 ====================
            self.log_manager.info("=" * 50)
            self.log_manager.info("阶段3: 开始导出结果")
            self.log_manager.info("=" * 50)

            exporter = ResultExporter(log_callback=self.log_manager.info)

            excel_file = Path(f"data/excel/{file_prefix}_author_information.xlsx")
            json_file = Path(f"data/json/{file_prefix}_author_information.json")

            exporter.export(
                input_file=author_info_file,
                excel_output=excel_file,
                json_output=json_file
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
        from core.url_finder import PaperURLFinder

        self.is_running = True
        self.should_cancel = False

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            result_dir = Path(f"data/result-{timestamp}")
            result_dir.mkdir(parents=True, exist_ok=True)
            self.log_manager.info(f"📁 结果目录: {result_dir}")

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

            if config.test_mode:
                # ===== 测试模式：使用 test/mock_author_info.jsonl 伪造数据 =====
                self.log_manager.info("🧪 [测试模式] 跳过真实 API 调用，使用伪造数据")
                template_file = Path("test/mock_author_info.jsonl")
                if not template_file.exists():
                    self.log_manager.error("❌ 测试数据不存在: test/mock_author_info.jsonl")
                    return
                template_text = template_file.read_text(encoding="utf-8")

                for i, (title, canonical) in enumerate(all_search_titles):
                    if self.should_cancel:
                        break
                    alias_tag = f"（曾用名→{canonical}）" if title != canonical else ""
                    self.log_manager.info(f"🧪 [{i+1}/{total}] 注入伪造数据: {title}{alias_tag}")
                    paper_slug = f"paper{i+1}"
                    author_file = result_dir / f"{paper_slug}_authors.jsonl"
                    # 将模板中的占位符替换为当前正式标题
                    filled = template_text.replace("__CANONICAL__", canonical)
                    author_file.write_text(filled, encoding="utf-8")
                    author_info_files.append(author_file)
                    self.log_manager.info(f"  ✅ 已写入 {len(template_text.splitlines())} 条伪造记录")

            else:
                # ===== 正常模式：URL 查找 → 爬取 → 作者搜索 =====
                url_finder = PaperURLFinder(
                    api_keys=config.scraper_api_keys,
                    log_callback=self.log_manager.info,
                    retry_max_attempts=config.retry_max_attempts,
                    retry_intervals=config.retry_intervals,
                )

                for i, (title, canonical) in enumerate(all_search_titles):
                    if self.should_cancel:
                        break

                    alias_tag = f"（曾用名，归并至：{canonical}）" if title != canonical else ""
                    self.log_manager.info("=" * 50)
                    self.log_manager.info(f"📄 搜索 {i+1}/{total}: {title}{alias_tag}")
                    self.log_manager.info("=" * 50)

                    # —— 查找引用 URL ——
                    url = url_finder.find_citation_url(title)
                    if not url:
                        self.log_manager.warning(f"⚠️ 跳过（未找到引用链接）: {title}")
                        continue

                    paper_slug = f"paper{i+1}"

                    # —— Phase 1：爬取引用列表 ——
                    self.log_manager.info("▶ Phase 1: 爬取引用列表")
                    scraper = GoogleScholarScraper(
                        api_keys=config.scraper_api_keys,
                        log_callback=self.log_manager.info,
                        progress_callback=self.log_manager.update_progress,
                        debug_mode=config.debug_mode,
                        premium=config.scraper_premium,
                        ultra_premium=config.scraper_ultra_premium,
                        retry_max_attempts=config.retry_max_attempts,
                        retry_intervals=config.retry_intervals,
                        session=config.scraper_session,
                        no_filter=config.scholar_no_filter,
                        geo_rotate=config.scraper_geo_rotate,
                    )
                    citing_file = result_dir / f"{paper_slug}_citing.jsonl"
                    await scraper.scrape(
                        url=url,
                        output_file=citing_file,
                        start_page=0,
                        sleep_seconds=config.sleep_between_pages,
                        cancel_check=lambda: self.should_cancel,
                        enable_year_traverse=config.enable_year_traverse,
                    )
                    if self.should_cancel:
                        break
                    citing_files.append((citing_file, canonical))

                    if not config.skip_author_search:
                        # —— Phase 2：搜索作者信息（以 canonical 为 Citing_Paper 值）——
                        self.log_manager.info("▶ Phase 2: 搜索作者信息")
                        searcher = AuthorSearcher(
                            api_key=config.openai_api_key,
                            base_url=config.openai_base_url,
                            model=config.openai_model,
                            log_callback=self.log_manager.info,
                            progress_callback=self.log_manager.update_progress,
                            prompt1=config.author_search_prompt1,
                            prompt2=config.author_search_prompt2,
                            enable_renowned_scholar=config.enable_renowned_scholar_filter,
                            renowned_scholar_model=config.renowned_scholar_model,
                            renowned_scholar_prompt=config.renowned_scholar_prompt,
                            enable_author_verification=config.enable_author_verification,
                            author_verify_model=config.author_verify_model,
                            author_verify_prompt=config.author_verify_prompt,
                            debug_mode=config.debug_mode,
                        )
                        author_file = result_dir / f"{paper_slug}_authors.jsonl"
                        await searcher.search(
                            input_file=citing_file,
                            output_file=author_file,
                            sleep_seconds=config.sleep_between_authors,
                            parallel_workers=config.parallel_author_search,
                            cancel_check=lambda: self.should_cancel,
                            citing_paper=canonical,   # 始终用正式标题写入 Citing_Paper
                        )
                        if self.should_cancel:
                            break
                        author_info_files.append(author_file)
                    else:
                        self.log_manager.info("⏭ 跳过 Phase 2（skip_author_search=True）")

            if not config.skip_author_search:
                if not author_info_files:
                    self.log_manager.warning("没有成功处理的论文，任务结束")
                    return

                # —— 合并所有 author JSONL，按 (Paper_Link, Citing_Paper) 去重 ——
                # 注意：每行格式为 {count: record_dict}，需提取内层 record_dict 才能访问字段
                merged_file = result_dir / "merged_authors.jsonl"
                seen_links: set[str] = set()
                with open(merged_file, "w", encoding="utf-8") as out_f:
                    for af in author_info_files:
                        if not af.exists():
                            continue
                        for line in af.read_text(encoding="utf-8").splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                outer = _json.loads(line)
                                # 每行格式：{count_key: record_dict}
                                inner = next(iter(outer.values())) if outer else {}
                            except Exception:
                                out_f.write(line + "\n")
                                continue
                            # 用 (Paper_Link, Citing_Paper) 组合去重：
                            #   同一篇引用论文 + 同一目标论文 → 去重（处理曾用名重复）
                            #   同一篇引用论文 + 不同目标论文 → 保留（引用了不同目标）
                            link = str(inner.get("Paper_Link") or "").strip()
                            citing = str(inner.get("Citing_Paper") or "").strip()
                            title = str(inner.get("Paper_Title") or "").strip()
                            dedup_key = (
                                f"{link}::{citing}" if link
                                else f"{title}::{citing}" if title
                                else line[:80]
                            )
                            if dedup_key in seen_links:
                                continue
                            seen_links.add(dedup_key)
                            out_f.write(_json.dumps(outer, ensure_ascii=False) + "\n")

                # —— Phase 3：导出 ——
                self.log_manager.info("▶ Phase 3: 导出结果")
                exporter = ResultExporter(log_callback=self.log_manager.info)
                excel_file = result_dir / f"{output_prefix}_results.xlsx"
                json_file = result_dir / f"{output_prefix}_results.json"
                exporter.export(
                    input_file=merged_file,
                    excel_output=excel_file,
                    json_output=json_file,
                )
            else:
                self.log_manager.info("⏭ 跳过合并和 Phase 3（skip_author_search=True）")
                # Build a simple Excel from citing files for Phase 4
                import pandas as pd
                rows = []
                for cf, canonical in citing_files:
                    if not cf.exists():
                        continue
                    for line in cf.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = _json.loads(line)
                            for page_data in data.values():
                                for paper in page_data.get("paper_dict", {}).values():
                                    authors_dict = paper.get("authors", {})
                                    rows.append({
                                        "Paper_Title": paper.get("paper_title", ""),
                                        "Paper_Link": paper.get("paper_link", ""),
                                        "Paper_Year": paper.get("paper_year"),
                                        "Citations": paper.get("citation", ""),
                                        "Citing_Paper": canonical,
                                        "Authors_with_Profile": _json.dumps(list(authors_dict.keys()), ensure_ascii=False),
                                    })
                        except Exception:
                            continue
                excel_file = result_dir / f"{output_prefix}_results.xlsx"
                json_file = result_dir / f"{output_prefix}_results.json"
                if rows:
                    df = pd.DataFrame(rows)
                    excel_file.parent.mkdir(parents=True, exist_ok=True)
                    df.to_excel(excel_file, index=False)
                    df.to_json(json_file, orient="records", force_ascii=False, indent=2)
                    self.log_manager.info(f"📊 从 Phase 1 构建了 {len(rows)} 条记录")
                else:
                    self.log_manager.warning("没有成功处理的论文，任务结束")
                    return

            # —— Phase 4：搜索引用描述（可选）——
            citing_desc_excel = excel_file
            if config.enable_citing_description:
                citing_desc_excel = result_dir / f"{output_prefix}_results_with_citing_desc.xlsx"
                if config.test_mode:
                    # 测试模式：直接添加伪造引用描述，不调用 LLM
                    self.log_manager.info("🧪 [测试模式] Phase 4: 注入伪造引用描述")
                    import pandas as pd
                    df = pd.read_excel(excel_file)
                    fake_descs = [
                        "该论文在 Related Work 部分明确引用了目标论文，指出其在自注意力机制方面的奠基性贡献，并以此为基础展开研究。",
                        "Introduction 章节中正面引用目标论文，称其为'近年来最具影响力的工作之一'，并借鉴其架构设计思路。",
                        "Methodology 部分将目标论文作为主要对比基线，实验结果表明在多个指标上超越了目标论文的方法。",
                        "Experiments 章节引用目标论文的评估框架，作为标准 benchmark 测试集的来源。",
                        "Related Work 中将目标论文归类为预训练语言模型的代表性工作，对其局限性进行了客观分析。",
                    ]
                    df["Citing_Description"] = [
                        fake_descs[i % len(fake_descs)] for i in range(len(df))
                    ]
                    citing_desc_excel.parent.mkdir(parents=True, exist_ok=True)
                    df.to_excel(citing_desc_excel, index=False)
                    self.log_manager.info(f"🧪 已生成伪造引用描述: {citing_desc_excel}")
                else:
                    self.log_manager.info("▶ Phase 4: 搜索引用描述")
                    from core.citing_description_searcher import CitingDescriptionSearcher
                    desc_searcher = CitingDescriptionSearcher(
                        api_key=config.openai_api_key,
                        base_url=config.openai_base_url,
                        model=config.openai_model,
                        log_callback=self.log_manager.info,
                        progress_callback=self.log_manager.update_progress,
                    )
                    await desc_searcher.search(
                        input_excel=excel_file,
                        output_excel=citing_desc_excel,
                        parallel_workers=config.parallel_author_search,
                        cancel_check=lambda: self.should_cancel,
                    )

            # —— Phase 5：生成 HTML 画像报告（可选）——
            html_file = None
            if config.enable_dashboard:
                self.log_manager.info("▶ Phase 5: 生成 HTML 画像报告")
                from core.dashboard_generator import DashboardGenerator
                html_file = result_dir / f"{output_prefix}_dashboard.html"

                all_renowned = excel_file.with_stem(excel_file.stem + "_all_renowned_scholar")
                top_renowned = excel_file.with_stem(excel_file.stem + "_top-tier_scholar")

                gen = DashboardGenerator(
                    api_key=config.openai_api_key,
                    base_url=config.openai_base_url,
                    model=config.dashboard_model,
                    log_callback=self.log_manager.info,
                    test_mode=config.test_mode,
                )
                def _fwd(p: Path) -> str:
                    return str(p).replace("\\", "/")

                gen.generate(
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
                )

            self.log_manager.success("=" * 50)
            self.log_manager.success("✅ 全部完成!")
            self.log_manager.success(f"📊 Excel: {excel_file}")
            self.log_manager.success(f"📋 JSON:  {json_file}")
            if html_file:
                self.log_manager.success(f"📊 Dashboard: {html_file}")
            self.log_manager.success("=" * 50)
            await self.log_manager._broadcast({"type": "all_done", "data": {
                "excel": str(excel_file),
                "json": str(json_file),
                "dashboard": str(html_file) if html_file else None,
            }})

        except Exception as e:
            self.log_manager.error(f"任务错误: {e}")
            import traceback; self.log_manager.error(traceback.format_exc())
            raise
        finally:
            self.is_running = False

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
