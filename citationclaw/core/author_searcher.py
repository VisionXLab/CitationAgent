import json
import asyncio
from pathlib import Path
from typing import Callable, Optional
from openai import AsyncOpenAI
import httpx
from citationclaw.core.author_cache import AuthorInfoCache


class AuthorSearcher:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        log_callback: Callable,
        progress_callback: Callable,
        prompt1: str = None,
        prompt2: str = None,
        enable_renowned_scholar: bool = False,
        renowned_scholar_model: str = "gemini-3-flash-preview-nothinking",
        renowned_scholar_prompt: str = None,
        enable_author_verification: bool = False,
        author_verify_model: str = "gemini-3-pro-preview-search",
        author_verify_prompt: str = None,
        debug_mode: bool = False,
        target_paper_authors: Optional[str] = None,
        author_cache: Optional[AuthorInfoCache] = None,
    ):
        """
        作者学术信息搜索器

        Args:
            api_key: OpenAI兼容API Key
            base_url: API Base URL
            model: 模型名称
            log_callback: 日志回调函数
            progress_callback: 进度回调函数
            prompt1: 搜索作者列表的Prompt（可选，使用配置中的默认值）
            prompt2: 搜索作者详细信息的Prompt（可选，使用配置中的默认值）
            enable_renowned_scholar: 是否启用二次筛选重要学者
            renowned_scholar_model: 二次筛选使用的模型
            renowned_scholar_prompt: 二次筛选的Prompt
        """
        # 使用AsyncOpenAI客户端，配置适当的连接池限制
        # 根据API文档建议，支持高并发（最高2000）
        try:
            # 配置httpx连接池限制，避免TCP连接积累
            http_client = httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=100,      # 最大连接数
                    max_keepalive_connections=20,  # 保持活跃的连接数
                    keepalive_expiry=30.0     # 连接保持时间（秒）
                ),
                timeout=30.0
            )

            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                http_client=http_client,
                max_retries=2
            )
        except Exception as e:
            self.log_callback(f"初始化AsyncOpenAI客户端失败: {e}")
            # 尝试不带自定义http_client的初始化（兼容某些API中转平台）
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=30.0,
                max_retries=2
            )

        self.model = model
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.debug_mode = debug_mode

        # 调试模式提示
        if self.debug_mode:
            self.log_callback("🐛 调试模式已启用（作者搜索）：将输出详细请求/响应信息")

        # 目标论文作者信息（名字+单位，用于LLM自引检测）
        self.target_paper_authors: Optional[str] = target_paper_authors

        # 作者信息持久化缓存
        self.author_cache: Optional[AuthorInfoCache] = author_cache

        # 自引检测 Prompt（使用轻量级模型）
        self.self_citation_check_prompt = (
            "【任务】判断一篇施引论文是否为自引。\n"
            "【自引定义】若施引论文的任意一位作者，同时也是被引目标论文的作者，则视为自引。\n\n"
            "【被引目标论文的作者信息（含姓名和单位）】\n"
            "{target_info}\n\n"
            "【施引论文的作者信息】\n"
            "来源1（Google Scholar显示的作者）：\n{authors_with_profile}\n\n"
            "来源2（网络搜索获得的作者及单位）：\n{searched_affiliation}\n\n"
            "【注意】姓名可能有中英文差异、缩写差异，请结合单位信息综合判断。\n"
            "请直接回答：是（存在自引）或 否（不是自引）"
        )

        # 保存prompt模板
        self.prompt1 = prompt1 or "这是一篇论文。请你根据这个paper_link和paper_title，去搜索查阅这篇论文的作者列表，然后输出每个作者的名字及其对应的单位名称。"
        self.prompt2 = prompt2 or """这是一篇论文及作者列表。请你根据这篇论文、作者名字和作者单位，去搜索该每位作者的个人信息，输出每位作者的以下信息：

1. **谷歌学术累积引用**（如有）

2. **重大学术头衔**（严格限定以下类别，其他一概不关注）：
   - **国际顶级奖项得主**：诺贝尔奖（Nobel Prize）、图灵奖（Turing Award）、菲尔兹奖（Fields Medal）、阿贝尔奖（Abel Prize）、沃尔夫奖（Wolf Prize）、克拉福德奖（Crafoord Prize）、奈望林纳奖（Nevanlinna Prize/IMU Abacus Medal）、哥德尔奖（Gödel Prize）、ACM Prize in Computing、IEEE Medal of Honor、富兰克林奖章（Franklin Medal）、科学突破奖（Breakthrough Prize）、拉斯克奖（Lasker Award）、邵逸夫奖（Shaw Prize）等
   - **院士头衔**：中国科学院院士、中国工程院院士、国外院士（如欧洲科学院院士、美国国家科学院院士、美国国家工程院院士、美国艺术与科学院院士、英国皇家学会院士/会士、德国科学院院士、法国科学院院士、瑞典皇家科学院院士等）
   - **学会Fellow**：IEEE Fellow、ACM Fellow、ACL Fellow、AAAI Fellow、AAAS Fellow、SIAM Fellow、APS Fellow、AMS Fellow等

3. **学术机构任职**：国内外顶尖高校（如清华、北大、MIT、Stanford、CMU、Berkeley、Caltech、Harvard、Princeton、Oxford、Cambridge等）或国家级研究机构的教授、研究员职称

**注意**：
- 不搜索杰青、长江学者、优青、万人计划等国内院士以下的头衔
- 不关注公司/企业任职（如Google、DeepMind、OpenAI、Meta AI、Microsoft Research等）
- 只关注纯学术界的院士/Fellow级别和顶级奖项得主"""

        # 二次筛选配置
        self.enable_renowned_scholar = enable_renowned_scholar
        self.renowned_scholar_model = renowned_scholar_model

        self.renowned_scholar_prompt = renowned_scholar_prompt or (
            "以上是一篇论文的作者列表信息。\n"
            "### 任务指南：\n"
            "1. **严格的高影响力判定 (is_high_impact)**：只保留以下类别的学者（满足任一条件）：\n"
            "   - **国际顶级奖项得主**：诺贝尔奖、图灵奖、菲尔兹奖、阿贝尔奖、沃尔夫奖、克拉福德奖、奈望林纳奖/IMU Abacus Medal、哥德尔奖、ACM Prize in Computing、IEEE Medal of Honor、科学突破奖、拉斯克奖、邵逸夫奖等\n"
            "   - **院士头衔**：中科院院士、工程院院士、各国国家科学院/工程院院士、欧洲科学院院士、英国皇家学会会士等\n"
            "   - **知名学会Fellow**：IEEE Fellow、ACM Fellow、ACL Fellow、AAAI Fellow、AAAS Fellow、SIAM Fellow、APS Fellow等\n"
            "   **必须排除（不保留）**：\n"
            "   - 国内院士以下的头衔：杰青、长江学者、优青、万人计划、青千等\n"
            "   - 公司/企业任职：Google/DeepMind/OpenAI/Meta AI/Microsoft Research等企业员工\n"
            "   - 普通大学教授、普通研究员\n"
            "   除此之外，其他人员一律不保留。\n\n"
            "2. **无重量级作者**：若作者信息明确说明无重量级作者，或所有作者都不符合上述严格标准，只需要输出'无任何重量级学者'。\n\n"
            "3. **有重量级作者**：若存在符合条件的学者，只输出这些顶级学者，进一步总结每位重量级作者的元信息，包括姓名、机构、国家、职务、荣誉称号。每位重量级作者之间用 $$$分隔符$$$ 来隔开，输出格式参考如下：\n\n"
            "（输出格式参考）：\n"
            "$$$分隔符$$$\n"
            "重量级作者1\n"
            "姓名\n"
            "机构（当前最新任职单位）\n"
            "国家\n"
            "职务（教授/研究员等学术职称）\n"
            "荣誉称号（院士/Fellow/顶级奖项得主，必须包含具体奖项名称如'图灵奖得主2018'、'菲尔兹奖得主2014'等）\n"
            "$$$分隔符$$$\n"
            "重量级作者2\n"
            "姓名\n"
            "机构（当前最新任职单位）\n"
            "国家\n"
            "职务（教授/研究员等学术职称）\n"
            "荣誉称号（院士/Fellow/顶级奖项得主）\n"

            "直至所有的重量级作者都被记录下来。记住，无需任何前言后记。")

        self.renowned_scholar_formatoutput_prompt = (
            f"以上是一位重量级作者信息：\n"
            "### 任务指南：\n"
            "**JSON格式化输出**：以JSON格式输出每位重量级作者的元信息，包括姓名、机构、国家、职务、荣誉称号。\n\n"
            "### 输出格式参考（共5个键值对）：\n"
            " {\n"
            "    \"姓名\": \"姓名\",\n"
            "    \"机构\": \"当前最新任职单位\",\n"
            "    \"国家\": \"作者所在机构或单位的所在国家\",\n"
            "    \"职务\": \"作者所担任的职务\",\n"
            "    \"荣誉称号\":  \"作者所获取的重量级头衔\",\n"
            " }"
            
            "记住：不要任何前言后记。")

        # 作者信息校验配置
        self.enable_author_verification = enable_author_verification
        self.author_verify_model = author_verify_model
        self.author_verify_prompt = author_verify_prompt or (
            "这是一份已经整理好的作者学术信息列表。请你对列表中的每一位作者信息进行真实性校验。你需要执行以下任务：\n"
            "1. 针对每位作者，核查其姓名、所属单位、谷歌学术引用量、学术头衔、行政职位是否真实存在。\n"
            "2. 必须通过可靠公开来源进行核验，包括但不限于：\n"
            "   - 学术数据库：Google Scholar、DBLP、ORCID、ResearchGate、Web of Science\n"
            "   - 官方奖项网站：诺贝尔奖官网(nobelprize.org)、图灵奖官网(acm.org/turing-award)、菲尔兹奖官网(mathunion.org)、阿贝尔奖官网(abelprize.no)、沃尔夫奖官网(wolffund.org.il)、克拉福德奖官网(crafoordprize.se)、科学突破奖官网(breakthroughprize.org)、邵逸夫奖官网(shawprize.org)\n"
            "   - 学会官方：IEEE Fellow Directory、ACM Awards、ACL Awards、AAAI Fellows\n"
            "   - 官方机构：中国科学院官网、中国工程院官网、各国科学院官网\n"
            "   - 学术主页：大学官网主页、ResearchGate\n"
            "3. 对每条信息分别标注核验结果，格式为：\n"
            "   - 正确（Verified）：可被权威来源明确证实。\n"
            "   - 存疑（Uncertain）：存在部分证据但不充分或信息冲突。\n"
            "   - 错误（Incorrect）：无法找到可信来源或存在明显错误。\n"
            "4. 若发现错误或存疑，请给出修正后的准确信息（若能确定）。\n"
            "5. 对每条核验内容，必须给出对应的来源链接或来源名称。\n"
            "6. 最终输出结构化结果，包括：作者姓名、原始信息、核验结论、修正信息（如有）、核验来源。\n"
            "7. 若无法找到任何可信来源，请明确说明\"未检索到可信来源支持该信息\"，禁止基于推测补充信息。"
        )

    async def search_fn(self, query: str, retry_count: int = 0, max_retries: int = 5, log_prefix: str = "") -> str:
        """
        调用搜索模型（启用web搜索）

        Args:
            query: 查询内容
            retry_count: 当前重试次数
            max_retries: 最大重试次数

        Returns:
            搜索结果,失败返回'ERROR'
        """
        try:
            # 使用AsyncOpenAI的原生async调用，无需run_in_executor
            if self.debug_mode:
                self.log_callback(f"🔍 [DEBUG] 发送搜索请求 (模型: {self.model})")
                self.log_callback(f"🔍 [DEBUG] 请求内容: {query[:200]}...")

            completion = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": query}],
                temperature=0.1,
                extra_body={"web_search_options": {}}  # 启用web搜索
            )
            response = completion.choices[0].message.content

            if self.debug_mode:
                self.log_callback(f"✅ [DEBUG] 搜索响应: {response[:200]}...")

            return response
        except Exception as e:
            error_msg = str(e).lower()

            if self.debug_mode:
                self.log_callback(f"❌ [DEBUG] 搜索API异常: {type(e).__name__}: {e}")

            # 检查是否是配额限制错误
            if 'rate' in error_msg or 'quota' in error_msg or 'limit' in error_msg:
                self.log_callback("⚠️ API配额超限,等待60秒后重试...")
                await asyncio.sleep(60)
                return await self.search_fn(query, retry_count, max_retries)

            # 其他错误（包括超时）- 使用指数退避重试
            is_timeout = 'timed out' in error_msg or 'timeout' in error_msg
            if retry_count < max_retries:
                wait_time = min(2 ** retry_count, 30)  # 指数退避，最多等待30秒
                if not is_timeout:
                    self.log_callback(f"{log_prefix}⚠️ 搜索API错误: {e}，{wait_time}秒后重试 (第{retry_count + 1}/{max_retries}次)，请耐心等待！")
                await asyncio.sleep(wait_time)
                return await self.search_fn(query, retry_count + 1, max_retries, log_prefix)
            else:
                if not is_timeout:
                    self.log_callback(f"❌ 搜索API错误（已达最大重试次数）: {e}")
                return 'ERROR'

    async def chat_fn(self, query: str, retry_count: int = 0, max_retries: int = 5, log_prefix: str = "") -> str:
        """
        调用对话模型（不启用web搜索，用于二次筛选）

        Args:
            query: 查询内容
            retry_count: 当前重试次数
            max_retries: 最大重试次数

        Returns:
            对话结果,失败返回'ERROR'
        """
        try:
            if self.debug_mode:
                self.log_callback(f"🔍 [DEBUG] 发送二次筛选请求 (模型: {self.renowned_scholar_model})")

            completion = await self.client.chat.completions.create(
                model=self.renowned_scholar_model,
                messages=[{"role": "user", "content": query}],
                temperature=0.1
            )
            response = completion.choices[0].message.content

            if self.debug_mode:
                self.log_callback(f"✅ [DEBUG] 二次筛选响应: {response[:200]}...")

            return response
        except Exception as e:
            error_msg = str(e).lower()

            if self.debug_mode:
                self.log_callback(f"❌ [DEBUG] 二次筛选API异常: {type(e).__name__}: {e}")

            # 检查是否是配额限制错误
            if 'rate' in error_msg or 'quota' in error_msg or 'limit' in error_msg:
                self.log_callback("⚠️ API配额超限,等待60秒后重试...")
                await asyncio.sleep(60)
                return await self.chat_fn(query, retry_count, max_retries)

            # 其他错误（包括超时）- 使用指数退避重试
            is_timeout = 'timed out' in error_msg or 'timeout' in error_msg
            if retry_count < max_retries:
                wait_time = min(2 ** retry_count, 30)  # 指数退避，最多等待30秒
                if not is_timeout:
                    self.log_callback(f"{log_prefix}⚠️ 二次筛选API错误: {e}，{wait_time}秒后重试 (第{retry_count + 1}/{max_retries}次)，请耐心等待！")
                await asyncio.sleep(wait_time)
                return await self.chat_fn(query, retry_count + 1, max_retries, log_prefix)
            else:
                if not is_timeout:
                    self.log_callback(f"❌ 二次筛选API错误（已达最大重试次数）: {e}")
                return 'ERROR'

    async def format_fn(self, query: str, retry_count: int = 0, max_retries: int = 5, log_prefix: str = "") -> str:
        """
        调用格式输出模型（不启用web搜索，用于输出JSON）

        Args:
            query: 查询内容
            retry_count: 当前重试次数
            max_retries: 最大重试次数

        Returns:
            对话结果,失败返回'ERROR'
        """
        try:
            if self.debug_mode:
                self.log_callback(f"🔍 [DEBUG] 发送格式化输出重量级学者请求 (模型: {self.renowned_scholar_model})")

            completion = await self.client.chat.completions.create(
                model=self.renowned_scholar_model,
                messages=[{"role": "user", "content": query}],
                response_format={
                    "type": "json_object"
                }
            )
            response = completion.choices[0].message.content

            if self.debug_mode:
                self.log_callback(f"✅ [DEBUG] 格式化输出重量级学者响应: {response[:200]}...")

            return response
        except Exception as e:
            error_msg = str(e).lower()

            if self.debug_mode:
                self.log_callback(f"❌ [DEBUG] 格式化输出重量级学者API异常: {type(e).__name__}: {e}")

            # 检查是否是配额限制错误
            if 'rate' in error_msg or 'quota' in error_msg or 'limit' in error_msg:
                self.log_callback("⚠️ API配额超限,等待60秒后重试...")
                await asyncio.sleep(60)
                return await self.format_fn(query, retry_count, max_retries)

            # 其他错误（包括超时）- 使用指数退避重试
            is_timeout = 'timed out' in error_msg or 'timeout' in error_msg
            if retry_count < max_retries:
                wait_time = min(2 ** retry_count, 30)  # 指数退避，最多等待30秒
                if not is_timeout:
                    self.log_callback(f"{log_prefix}⚠️ 格式化输出重量级学者API错误: {e}，{wait_time}秒后重试 (第{retry_count + 1}/{max_retries}次)，请耐心等待！")
                await asyncio.sleep(wait_time)
                return await self.format_fn(query, retry_count + 1, max_retries, log_prefix)
            else:
                if not is_timeout:
                    self.log_callback(f"❌ 格式化输出重量级学者API错误（已达最大重试次数）: {e}")
                return 'ERROR'

    async def verify_fn(self, query: str, retry_count: int = 0, max_retries: int = 5, log_prefix: str = "") -> str:
        """
        调用校验模型（启用web搜索，用于作者信息真实性校验）

        Args:
            query: 查询内容
            retry_count: 当前重试次数
            max_retries: 最大重试次数

        Returns:
            校验结果,失败返回'ERROR'
        """
        try:
            if self.debug_mode:
                self.log_callback(f"🔍 [DEBUG] 发送作者校验请求 (模型: {self.author_verify_model})")

            completion = await self.client.chat.completions.create(
                model=self.author_verify_model,
                messages=[{"role": "user", "content": query}],
                temperature=0.1,
                extra_body={"web_search_options": {}}  # 启用web搜索用于核验
            )
            response = completion.choices[0].message.content

            if self.debug_mode:
                self.log_callback(f"✅ [DEBUG] 作者校验响应: {response[:200]}...")

            return response
        except Exception as e:
            error_msg = str(e).lower()

            if self.debug_mode:
                self.log_callback(f"❌ [DEBUG] 作者校验API异常: {type(e).__name__}: {e}")

            # 检查是否是配额限制错误
            if 'rate' in error_msg or 'quota' in error_msg or 'limit' in error_msg:
                self.log_callback("⚠️ API配额超限,等待60秒后重试...")
                await asyncio.sleep(60)
                return await self.verify_fn(query, retry_count, max_retries)

            # 其他错误（包括超时）- 使用指数退避重试
            is_timeout = 'timed out' in error_msg or 'timeout' in error_msg
            if retry_count < max_retries:
                wait_time = min(2 ** retry_count, 30)  # 指数退避，最多等待30秒
                if not is_timeout:
                    self.log_callback(f"{log_prefix}⚠️ 作者校验API错误: {e}，{wait_time}秒后重试 (第{retry_count + 1}/{max_retries}次)，请耐心等待！")
                await asyncio.sleep(wait_time)
                return await self.verify_fn(query, retry_count + 1, max_retries, log_prefix)
            else:
                if not is_timeout:
                    self.log_callback(f"❌ 作者校验API错误（已达最大重试次数）: {e}")
                return 'ERROR'

    async def _check_self_citation_llm(
        self,
        authors_with_profile: str,
        searched_affiliation: str,
        retry_count: int = 0,
        max_retries: int = 3,
    ) -> bool:
        """用轻量级LLM判断施引论文是否为自引。

        综合三方信息：
        - 目标论文作者（名字+单位，已缓存）
        - Authors_with_Profile（Google Scholar抓取，可能不全）
        - Searched Author-Affiliation（网络搜索，更完整）
        """
        if not self.target_paper_authors:
            return False
        prompt = self.self_citation_check_prompt.format(
            target_info=self.target_paper_authors,
            authors_with_profile=authors_with_profile,
            searched_affiliation=searched_affiliation,
        )
        try:
            completion = await self.client.chat.completions.create(
                model=self.renowned_scholar_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            answer = (completion.choices[0].message.content or "").strip()
            return answer.startswith("是")
        except Exception as e:
            error_msg = str(e).lower()
            if 'rate' in error_msg or 'quota' in error_msg or 'limit' in error_msg:
                await asyncio.sleep(60)
                return await self._check_self_citation_llm(
                    authors_with_profile, searched_affiliation, retry_count, max_retries
                )
            if retry_count < max_retries:
                wait_time = min(2 ** retry_count, 15)
                await asyncio.sleep(wait_time)
                return await self._check_self_citation_llm(
                    authors_with_profile, searched_affiliation, retry_count + 1, max_retries
                )
            self.log_callback(f"⚠️ 自引检测LLM调用失败，默认视为非自引: {e}")
            return False

    async def _search_single_paper(
        self,
        page_id: str,
        paper_id: str,
        paper_content: dict,
        count: int,
        total_papers: int,
        semaphore: asyncio.Semaphore,
        citing_paper: str = "",
        completed_state: Optional[dict] = None,
    ) -> tuple:
        """
        搜索单篇论文的作者信息（并行任务单元）。
        优先从持久化缓存读取，未命中时调用 LLM 并将结果写入缓存。

        Returns:
            (count, record_dict) 元组
        """
        async with semaphore:
            paper_title = paper_content['paper_title']
            paper_link  = paper_content['paper_link']
            log_prefix  = f"[{count}/{total_papers}] "

            record_dict = {
                'PageID': page_id,
                'PaperID': paper_id,
                'Paper_Title': paper_title,
                'Paper_Year': paper_content['paper_year'],
                'Paper_Link': paper_link,
                'Citations': paper_content['citation'],
                'Authors_with_Profile': str(paper_content['authors']),
            }

            # ── 查询缓存（取出已有字段供后续各步使用）────────────────────────
            cached = self.author_cache.get(paper_link, paper_title) if self.author_cache else None

            # ── Step 1: 作者列表及单位 ────────────────────────────────────────
            if cached and cached.get('Searched Author-Affiliation'):
                response1 = cached['Searched Author-Affiliation']
                record_dict['Searched Author-Affiliation'] = response1
                record_dict['First_Author_Institution'] = cached.get('First_Author_Institution', '')
                record_dict['First_Author_Country'] = cached.get('First_Author_Country', '')
                self.log_callback(f"  💾 [缓存] 作者-单位: {paper_title[:40]}...")
            else:
                query1 = f'Paper_Link: {paper_link}, Paper_Title: {paper_title}.'
                query1 += '\n' + self.prompt1
                response1 = await self.search_fn(query1, log_prefix=log_prefix)
                record_dict['Searched Author-Affiliation'] = response1

                first_author_query = (
                    response1 + "\n\n"
                    "请从上述作者-机构列表中，提取**排在最前面的第一作者**的机构和国家。"
                    "以JSON格式输出（只输出JSON，无其他文字）：\n"
                    '{"first_author_institution": "机构全称", "first_author_country": "国家（中文）"}'
                )
                response_first = await self.format_fn(first_author_query, log_prefix=log_prefix)
                try:
                    fa = json.loads(response_first)
                    record_dict['First_Author_Institution'] = fa.get('first_author_institution', '')
                    record_dict['First_Author_Country'] = fa.get('first_author_country', '')
                except Exception:
                    record_dict['First_Author_Institution'] = ''
                    record_dict['First_Author_Country'] = ''

                if self.author_cache:
                    await self.author_cache.update(paper_link, paper_title, {
                        'Searched Author-Affiliation': response1,
                        'First_Author_Institution': record_dict['First_Author_Institution'],
                        'First_Author_Country': record_dict['First_Author_Country'],
                    })

            # ── 自引检测（不缓存：依赖目标论文，每次运行需重新判断）──────────
            record_dict['Citing_Paper'] = citing_paper
            is_self_citation = await self._check_self_citation_llm(
                authors_with_profile=record_dict['Authors_with_Profile'],
                searched_affiliation=response1,
            )
            record_dict['Is_Self_Citation'] = is_self_citation
            if is_self_citation:
                self.log_callback(f"  ↩️ 自引：{paper_title[:50]}... 已标记，跳过知名学者筛选")

            # ── Step 2: 详细作者信息 ──────────────────────────────────────────
            if cached and cached.get('Searched Author Information'):
                response2 = cached['Searched Author Information']
                record_dict['Searched Author Information'] = response2
            else:
                query2 = f'Paper_Link: {paper_link}, Paper_Title: {paper_title}, Author-Affiliation: {response1}'
                query2 += '\n' + self.prompt2
                response2 = await self.search_fn(query2, log_prefix=log_prefix)
                record_dict['Searched Author Information'] = response2
                if self.author_cache:
                    await self.author_cache.update(paper_link, paper_title, {
                        'Searched Author Information': response2,
                    })

            # ── Step 3: 作者信息校验（可选）──────────────────────────────────
            if self.enable_author_verification:
                if cached and cached.get('Author Verification'):
                    record_dict['Author Verification'] = cached['Author Verification']
                else:
                    query_verify = response2 + '\n\n' + self.author_verify_prompt
                    response_verify = await self.verify_fn(query_verify, log_prefix=log_prefix)
                    record_dict['Author Verification'] = response_verify
                    if self.author_cache:
                        await self.author_cache.update(paper_link, paper_title, {
                            'Author Verification': response_verify,
                        })

            # ── Step 5: 知名学者筛选（可选，自引时跳过）─────────────────────
            if self.enable_renowned_scholar:
                if is_self_citation:
                    record_dict['Renowned Scholar'] = '自引，已跳过知名学者筛选'
                    record_dict['Formated Renowned Scholar'] = []
                elif cached and 'Formated Renowned Scholar' in cached:
                    record_dict['Renowned Scholar'] = cached.get('Renowned Scholar', '')
                    record_dict['Formated Renowned Scholar'] = cached['Formated Renowned Scholar']
                    self.log_callback(f"  💾 [缓存] 知名学者: {paper_title[:40]}...")
                else:
                    query_filter = response2 + '\n\n' + self.renowned_scholar_prompt
                    response_filter = await self.chat_fn(query_filter, log_prefix=log_prefix)
                    record_dict['Renowned Scholar'] = response_filter
                    format_scholar_record = []
                    scholar_count = 0
                    if "无任何重量级学者" not in response_filter:
                        if "$$$分隔符$$$" in response_filter:
                            scholars = response_filter.split("$$$分隔符$$$")
                            scholars = [s for s in scholars if s != '' and "无" not in s]
                            for scholar in scholars:
                                scholar_format_query = scholar + '\n\n' + self.renowned_scholar_formatoutput_prompt
                                response_scholar_format = await self.format_fn(scholar_format_query, log_prefix=log_prefix)
                                try:
                                    res = json.loads(response_scholar_format)
                                except Exception:
                                    res = ''
                                if isinstance(res, dict) and res.get('姓名', 'EMPTY') != 'EMPTY':
                                    scholar_count += 1
                                    format_scholar_record.append({
                                        '序号': scholar_count,
                                        '姓名': res.get('姓名', ''),
                                        '机构': res.get('机构', ''),
                                        '国家': res.get('国家', ''),
                                        '职务': res.get('职务', ''),
                                        '荣誉称号': res.get('荣誉称号', ''),
                                    })
                    record_dict['Formated Renowned Scholar'] = format_scholar_record
                    if self.author_cache:
                        await self.author_cache.update(paper_link, paper_title, {
                            'Renowned Scholar': response_filter,
                            'Formated Renowned Scholar': format_scholar_record,
                        })

            if completed_state is not None:
                async with completed_state["lock"]:
                    completed_state["n"] += 1
                    log_num = completed_state["n"]
            else:
                log_num = count
            self.log_callback(f"[{log_num}/{total_papers}] 搜索完成: {paper_title[:50]}...")
            return (count, record_dict)

    async def search(
        self,
        input_file: Path,
        output_file: Path,
        sleep_seconds: float = 0.5,
        parallel_workers: int = 1,
        cancel_check: Optional[Callable[[], bool]] = None,
        citing_paper: str = "",
    ):
        """
        搜索所有论文的作者信息

        Args:
            input_file: 输入JSONL文件(来自scraper)
            output_file: 输出JSONL文件
            sleep_seconds: 每条查询间隔(秒) - 并行模式下不使用
            parallel_workers: 并行处理数量(默认1为串行,>1为并行)
            cancel_check: 取消检查函数
        """
        # 读取引用列表
        with open(input_file, 'r', encoding='utf-8') as f:
            data = [json.loads(line) for line in f]

        # 统计总论文数并收集所有任务
        tasks_to_process = []
        count = 0
        for d in data:
            for page_id, page_content in d.items():
                paper_dict = page_content['paper_dict']
                for paper_id, paper_content in paper_dict.items():
                    count += 1
                    tasks_to_process.append({
                        'page_id': page_id,
                        'paper_id': paper_id,
                        'paper_content': paper_content,
                        'count': count
                    })

        total_papers = len(tasks_to_process)
        self.log_callback(f"共需要搜索 {total_papers} 篇论文的作者信息")

        if parallel_workers > 1:
            self.log_callback(f"使用并行模式，并发数: {parallel_workers}")
        else:
            self.log_callback(f"使用串行模式")

        # 确保输出目录存在
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # 创建信号量控制并发数
        semaphore = asyncio.Semaphore(parallel_workers)

        # 创建共享完成计数器（用于日志按完成顺序编号）
        completed_state = {"n": 0, "lock": asyncio.Lock()}

        # 创建所有任务
        tasks = []
        for task_info in tasks_to_process:
            # 检查是否取消
            if cancel_check and cancel_check():
                self.log_callback("任务已取消")
                return

            task = asyncio.create_task(
                self._search_single_paper(
                    page_id=task_info['page_id'],
                    paper_id=task_info['paper_id'],
                    paper_content=task_info['paper_content'],
                    count=task_info['count'],
                    total_papers=total_papers,
                    semaphore=semaphore,
                    citing_paper=citing_paper,
                    completed_state=completed_state,
                )
            )
            tasks.append(task)

            # 串行模式下逐个等待，并行模式下批量等待
            if parallel_workers == 1:
                result = await task
                count_num, record_dict = result

                # 立即写入文件
                with open(output_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({count_num: record_dict}, ensure_ascii=False) + '\n')

                # 更新进度
                self.progress_callback(count_num, total_papers)

                # 间隔
                if sleep_seconds > 0:
                    await asyncio.sleep(sleep_seconds)

        # 并行模式：等待所有任务完成
        if parallel_workers > 1:
            self.log_callback(f"等待所有并行任务完成...")
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 检查错误
            errors = [r for r in results if isinstance(r, Exception)]
            if errors:
                self.log_callback(f"⚠️ 有 {len(errors)} 个任务失败")

            # 过滤出成功的结果并排序
            successful_results = [r for r in results if not isinstance(r, Exception)]
            successful_results.sort(key=lambda x: x[0])  # 按count排序

            # 写入文件
            with open(output_file, 'w', encoding='utf-8') as f:
                for count_num, record_dict in successful_results:
                    f.write(json.dumps({count_num: record_dict}, ensure_ascii=False) + '\n')
                    # 更新进度
                    self.progress_callback(count_num, total_papers)

        self.log_callback(f"✅ 作者信息搜索完成!共处理 {total_papers} 篇论文")
