"""
Phase 4: 批量搜索每篇引用论文对目标论文的引用描述
"""
import asyncio
import pandas as pd
from pathlib import Path
from typing import Callable, Optional
from citationclaw.core.gemini_client import AsyncGeminiClient
from citationclaw.core.citing_description_cache import CitingDescriptionCache


class CitingDescriptionSearcher:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        log_callback: Callable,
        progress_callback: Callable,
        cache: Optional[CitingDescriptionCache] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ):
        self.client = AsyncGeminiClient(
            api_key=api_key,
            base_url=base_url,
            timeout=30.0,
        )
        self.model = model
        self.log = log_callback
        self.progress = progress_callback
        self.cache = cache
        self.cancel_event = cancel_event
        self._authors_cache: dict[str, str] = {}
        self._authors_locks: dict[str, asyncio.Lock] = {}

    async def _search_fn(self, query: str, retries: int = 3, log_prefix: str = "") -> str:
        """调用搜索API（启用web_search_options）"""
        quota_failures = 0
        for i in range(retries):
            try:
                comp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": query}],
                    extra_body={"web_search_options": {}}
                )
                return comp.choices[0].message.content or ""
            except Exception as e:
                error_msg = str(e).lower()
                is_timeout = 'timed out' in error_msg or 'timeout' in error_msg
                is_quota = 'rate' in error_msg or 'quota' in error_msg or 'limit' in error_msg

                if is_quota:
                    quota_failures += 1
                    if quota_failures >= 2:
                        if self.cancel_event and not self.cancel_event.is_set():
                            self.log(f"{log_prefix}❌ API配额持续不足，已停止重试。")
                            self.cancel_event.set()
                        return "NONE"
                    # If another concurrent task already hit quota limit, exit immediately without waiting
                    if self.cancel_event and self.cancel_event.is_set():
                        return "NONE"
                    self.log(f"{log_prefix}⚠️ API配额超限，60秒后重试（第{quota_failures}/2次）...")
                    if self.cancel_event:
                        try:
                            await asyncio.wait_for(asyncio.shield(self.cancel_event.wait()), timeout=60)
                            return "NONE"
                        except asyncio.TimeoutError:
                            pass
                    else:
                        await asyncio.sleep(60)
                    continue
                if i < retries - 1:
                    if i == 0 and not is_timeout:
                        self.log(f"{log_prefix}⚠️ 搜索API错误: {e}，正在启用重试机制，请耐心等待！")
                    await asyncio.sleep(2 ** i)
                else:
                    if not is_timeout:
                        self.log(f"{log_prefix}⚠️ 搜索API错误: {e}")
                    return "NONE"
        return "NONE"

    async def _get_target_authors(self, target_title: str, log_prefix: str = "") -> str:
        """获取目标论文作者（带缓存+锁，同一目标论文只查一次LLM）"""
        if target_title in self._authors_cache:
            return self._authors_cache[target_title]
        # 为每个 target_title 创建独立锁，防止并发重复查询
        if target_title not in self._authors_locks:
            self._authors_locks[target_title] = asyncio.Lock()
        async with self._authors_locks[target_title]:
            # double-check: 可能在等锁期间已被其他协程填充
            if target_title in self._authors_cache:
                return self._authors_cache[target_title]
            q = (f"请搜索论文《{target_title}》的所有作者，"
                 f"只需按顺序列出姓名，格式：作者1, 作者2, ...")
            authors = await self._search_fn(q, log_prefix=log_prefix)
            self._authors_cache[target_title] = authors
            self.log(f"✅ 已缓存目标论文作者: 《{target_title[:40]}...》")
            return authors

    async def _find_description(
        self, target_title: str, citing_title: str, citing_url: str, log_prefix: str = ""
    ) -> str:
        """搜索 citing_title 中对 target_title 的引用描述"""
        if self.cancel_event and self.cancel_event.is_set():
            return "NONE"
        # Step1: 找目标论文作者（缓存，同一目标论文只查一次LLM）
        authors = await self._get_target_authors(target_title, log_prefix=log_prefix)

        # Step2: 搜索引用描述
        q2 = (
            f"请访问以下链接，阅读论文《{citing_title}》全文：{citing_url}\n\n"
            f"找出该论文在正文中引用《{target_title}》({authors})的具体描述或表述。\n"
            f"要求：\n"
            f"1. 只摘录原文中真实存在的引用描述，不能编造，严格按原文转录。\n"
            f"2. 直接引用原文句子/段落，注明出现在哪个部分（Introduction/Related Work等）。\n"
            f"3. 【情感判断标准】仅当原文中出现明确的积极评价词汇（如 'state-of-the-art'、'pioneering'、'significantly outperforms'、'groundbreaking'、'novel and effective' 等），才在摘录后注明\"【正面引用】\"；若是客观陈述、中立转述或背景铺垫，不做任何情感标注。\n"
            f"4. 找不到则输出'无法找到相关引用描述'。"
        )
        return await self._search_fn(q2, log_prefix=log_prefix)

    async def search(
        self,
        input_excel: Path,
        output_excel: Path,
        parallel_workers: int = 5,
        cancel_check: Optional[Callable] = None,
    ) -> Path:
        """
        读取 input_excel（result.xlsx），为每行搜索引用描述，
        写入 Citing_Description 列，保存到 output_excel。
        """
        df = pd.read_excel(input_excel)
        total = len(df)
        self.log(f"共 {total} 篇论文需要搜索引用描述")

        df['Citing_Description'] = ''
        semaphore = asyncio.Semaphore(parallel_workers)
        completed = 0

        async def process_row(idx, row, sem):
            nonlocal completed
            async with sem:
                if cancel_check and cancel_check():
                    return idx, ""
                if self.cancel_event and self.cancel_event.is_set():
                    return idx, ""
                # 自引论文跳过引用描述搜索
                is_self = row.get('Is_Self_Citation', False)
                if is_self and str(is_self).lower() not in ('false', '0', 'nan', 'none', ''):
                    completed += 1
                    self.progress(completed, total)
                    return idx, "自引，已跳过引用描述搜索"
                target = str(row.get('Citing_Paper', '') or '')
                citing_title = str(row.get('Paper_Title', '') or '')
                citing_url = str(row.get('Paper_Link', '') or '')
                if not target or not citing_title:
                    completed += 1
                    self.progress(completed, total)
                    return idx, ""
                # 命中缓存则直接返回，跳过 LLM 调用
                if self.cache is not None:
                    cached = self.cache.get(citing_url, citing_title, target)
                    if cached is not None:
                        completed += 1
                        self.progress(completed, total)
                        self.log(f"[{completed}/{total}] 缓存命中，跳过搜索: {citing_title[:40]}...")
                        return idx, cached
                log_prefix = f"[{idx + 1}/{total}] "
                desc = await self._find_description(target, citing_title, citing_url, log_prefix=log_prefix)
                # 写入缓存（包括 "NONE" / "自引" 等结果，避免重复搜索）
                if self.cache is not None:
                    await self.cache.update(citing_url, citing_title, target, desc)
                completed += 1
                self.progress(completed, total)
                self.log(f"[{completed}/{total}] 引用描述搜索完成: {citing_title[:40]}...")
                return idx, desc

        tasks = [
            asyncio.create_task(process_row(i, row, semaphore))
            for i, row in df.iterrows()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for item in results:
            if not isinstance(item, Exception):
                i, desc = item
                df.at[i, 'Citing_Description'] = desc

        if self.cache is not None:
            await self.cache.flush()
        output_excel.parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(output_excel, index=False)
        self.log(f"引用描述已保存: {output_excel}")
        return output_excel
