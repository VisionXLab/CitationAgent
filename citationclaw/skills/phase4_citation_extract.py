"""Phase 4: 引文语境提取 — PDF parse + lightweight LLM extract."""
import json
from pathlib import Path
from typing import Optional

from citationclaw.skills.base import SkillContext, SkillResult
from citationclaw.core.pdf_downloader import PDFDownloader
from citationclaw.core.pdf_parser import PDFCitationParser
from citationclaw.core.pdf_mineru_parser import MinerUParser
from citationclaw.core.pdf_parse_cache import PDFParseCache
from citationclaw.config.prompt_loader import PromptLoader


class CitationExtractSkill:
    name = "phase4_citation_extract"

    async def run(self, ctx: SkillContext, **kwargs) -> SkillResult:
        input_file = Path(kwargs["input_file"])
        output_file = Path(kwargs["output_file"])
        target_title = kwargs["target_title"]
        target_authors = kwargs.get("target_authors", [])
        cache = kwargs.get("citation_desc_cache")

        downloader = PDFDownloader()
        parser = PDFCitationParser()
        mineru_parser = MinerUParser()
        parse_cache = PDFParseCache()
        prompt_loader = PromptLoader()

        papers = self._read_jsonl(input_file)
        total = len(papers)
        results = []
        stats = {"pdf_found": 0, "pdf_missing": 0, "cached": 0, "extracted": 0}

        try:
            for i, paper in enumerate(papers):
                if ctx.cancel_check and ctx.cancel_check():
                    break

                citing_title = paper.get("Paper_Title", paper.get("Citing_Paper_Title", paper.get("title", "")))

                # Skip self-citation papers
                if paper.get("Is_Self_Citation"):
                    ctx.log(f"[引文语境] ({i+1}/{total}) [自引跳过] {citing_title[:50]}...")
                    paper["Citing_Description"] = "自引论文，已跳过"
                    paper["citing_desc_source"] = "self_citation_skip"
                    results.append(paper)
                    if ctx.progress:
                        ctx.progress(i + 1, total)
                    continue

                ctx.log(f"[引文语境] ({i+1}/{total}) {citing_title[:50]}...")

                # Check cache
                if cache:
                    cached_desc = cache.get(
                        paper.get("Paper_Link", paper.get("Citing_Paper_Link", "")),
                        target_title,
                        citing_title,
                    )
                    if cached_desc:
                        paper["Citing_Description"] = cached_desc
                        paper["citing_desc_source"] = "cache"
                        results.append(paper)
                        stats["cached"] += 1
                        if ctx.progress:
                            ctx.progress(i + 1, total)
                        continue

                # Step 1: Check MinerU parse cache for pre-parsed content
                pkey = mineru_parser.paper_key(paper)
                cached_parsed = None
                if parse_cache.has(pkey):
                    parse_dir = parse_cache.get_parsed_dir(pkey)
                    cached_parsed = mineru_parser._load_cached(parse_dir)

                # Step 2: Download PDF if not cached
                pdf_path = None
                if not cached_parsed:
                    pdf_path = await downloader.download(paper, log=ctx.log)
                    if not pdf_path:
                        paper["Citing_Description"] = "PDF不可用"
                        paper["citing_desc_source"] = "unavailable"
                        results.append(paper)
                        stats["pdf_missing"] += 1
                        if ctx.progress:
                            ctx.progress(i + 1, total)
                        continue

                stats["pdf_found"] += 1

                # Step 3: Use cached MinerU content or fallback to PyMuPDF
                if cached_parsed and cached_parsed.get("references_md"):
                    # Use MinerU's parsed references for better citation context
                    full_text = cached_parsed["full_md"]
                    ref_id = parser._find_reference_id(full_text, target_title, target_authors)
                    contexts_raw = parser._extract_contexts(full_text, ref_id, target_title)
                    contexts = [{"section": parser._detect_section(c), "text": c.strip(), "source": "mineru_cache"} for c in contexts_raw]
                else:
                    contexts = parser.extract_citation_contexts(
                        pdf_path, target_title, target_authors
                    )

                if contexts:
                    # Step 3: LLM extracts description from parsed text
                    parsed_text = "\n\n".join(
                        f"[{c['section']}] {c['text']}" for c in contexts
                    )
                    description = await self._llm_extract(
                        ctx, prompt_loader, citing_title, target_title, parsed_text
                    )
                    paper["Citing_Description"] = description
                    paper["citing_desc_source"] = "pdf"
                    stats["extracted"] += 1
                else:
                    paper["Citing_Description"] = "未在PDF中找到相关引用描述"
                    paper["citing_desc_source"] = "pdf_no_context"

                # Cache result
                if cache and paper.get("Citing_Description"):
                    await cache.update(
                        paper.get("Paper_Link", paper.get("Citing_Paper_Link", "")),
                        target_title,
                        citing_title,
                        paper["Citing_Description"],
                    )

                results.append(paper)
                if ctx.progress:
                    ctx.progress(i + 1, total)

        finally:
            await downloader.close()
            if cache:
                await cache.flush()

        # Write output
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        return SkillResult(name=self.name, data={
            "output_file": str(output_file),
            "total": total,
            **stats,
        })

    async def _llm_extract(self, ctx: SkillContext, prompt_loader: PromptLoader,
                            citing_title: str, target_title: str,
                            parsed_paragraphs: str) -> str:
        """Use lightweight LLM to extract citation description from parsed text."""
        try:
            from openai import AsyncOpenAI
            prompt = prompt_loader.render(
                "citation_extract",
                citing_title=citing_title,
                target_title=target_title,
                parsed_paragraphs=parsed_paragraphs,
            )
            import httpx
            client = AsyncOpenAI(
                api_key=ctx.config.openai_api_key,
                base_url=ctx.config.openai_base_url,
                http_client=httpx.AsyncClient(trust_env=False),
            )
            response = await client.chat.completions.create(
                model=ctx.config.openai_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            ctx.log(f"  ⚠ LLM提取失败: {e}")
            return "LLM提取失败"

    def _read_jsonl(self, path: Path) -> list:
        """Read JSONL, handling both flat and legacy wrapped {idx: record} formats."""
        papers = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                # Unwrap legacy format: {"1": {"Paper_Title": ...}} → inner dict
                if isinstance(data, dict):
                    inner = data
                    for v in data.values():
                        if isinstance(v, dict) and ("Paper_Title" in v or "paper_title" in v):
                            inner = v
                            break
                    papers.append(inner)
                else:
                    papers.append(data)
        return papers
