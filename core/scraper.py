import requests
import json
import asyncio
import re
from pathlib import Path
from typing import Callable, Optional, Tuple
from core.parser import google_scholar_html_parser


class GoogleScholarScraper:
    # 优先使用的国家代码（前20次数据中心重试循环使用）
    # 选择标准：学术流量大、不太可能被 Google Scholar 屏蔽的地区
    PREFERRED_GEO_COUNTRIES = [
        'us', 'uk', 'de', 'fr', 'ca', 'au', 'jp', 'kr', 'sg', 'nl',
        'se', 'ch', 'at', 'dk', 'fi', 'no', 'nz', 'ie', 'be', 'it',
    ]

    # 全部 Standard Geo 国家代码池（20次之后随机抽取）
    # 来源：ScraperAPI Standard Geo 文档（Business Plan and higher）
    ALL_GEO_COUNTRIES = [
        # 北美
        'us', 'ca', 'mx',
        # 欧洲
        'eu', 'uk', 'de', 'fr', 'it', 'es', 'pt', 'nl', 'be', 'ch', 'at',
        'se', 'no', 'dk', 'fi', 'is', 'ie', 'pl', 'cz', 'sk', 'hu', 'ro',
        'bg', 'hr', 'si', 'lt', 'lv', 'ee', 'cy', 'mt', 'gr', 'li', 'ua',
        # 亚太
        'jp', 'kr', 'sg', 'au', 'nz', 'in', 'th', 'vn', 'my', 'ph', 'id',
        'tw', 'hk', 'pk', 'bd',
        # 南美
        'br', 'ar', 'cl', 'co', 'pe', 'ec', 'pa', 've',
        # 中东
        'tr', 'il', 'ae', 'sa', 'jo',
        # 非洲
        'za', 'ng', 'ke', 'eg',
        # 其他
        'ru', 'cn',
    ]

    def __init__(self, api_keys: list, log_callback: Callable, progress_callback: Callable,
                 debug_mode: bool = False, premium: bool = False, ultra_premium: bool = False,
                 retry_max_attempts: int = 3, retry_intervals: str = "5,10,20",
                 session: bool = False, no_filter: bool = False, geo_rotate: bool = False,
                 dc_retry_max_attempts: int = 5):
        """
        Google Scholar引用列表抓取器

        Args:
            api_keys: ScraperAPI的API Keys列表
            log_callback: 日志回调函数,签名为 log_callback(message: str)
            progress_callback: 进度回调函数,签名为 progress_callback(current: int, total: int)
            debug_mode: 是否启用调试模式（输出HTML和详细日志）
            premium: 是否启用ScraperAPI Premium代理
            ultra_premium: 是否启用ScraperAPI Ultra Premium代理
            retry_max_attempts: HTTP/登录页错误的最大重试次数
            retry_intervals: 重试间隔，逗号分隔的秒数，如 "5,10,20"
            session: 是否启用ScraperAPI会话保持
            no_filter: 是否在Google Scholar链接后追加&filter=0
            geo_rotate: 数据中心重试时是否通过country_code切换国家
            dc_retry_max_attempts: 数据中心不一致时的最大重试次数（每次自动切换国家代码）
        """
        import random
        self.api_keys = api_keys
        self.parser = google_scholar_html_parser()
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.debug_mode = debug_mode
        self.premium = premium
        self.ultra_premium = ultra_premium
        self.no_filter = no_filter
        self.geo_rotate = geo_rotate

        # 会话保持：生成随机session number
        self.session_enabled = session
        self.session_number = random.randint(100000, 999999) if session else None

        # 重试配置
        self.retry_max_attempts = retry_max_attempts if retry_max_attempts == -1 else max(1, retry_max_attempts)
        self.retry_intervals = self._parse_intervals(retry_intervals)
        self.dc_retry_max_attempts = dc_retry_max_attempts if dc_retry_max_attempts == -1 else max(1, dc_retry_max_attempts)

        # 错误跟踪
        self.consecutive_failures = 0  # 连续失败次数
        self.max_consecutive_failures = 5  # 最大连续失败次数

        # 调试模式提示
        if self.debug_mode:
            self.log_callback("🐛 调试模式已启用：将保存HTML和详细日志")
        retry_display = "无限" if self.retry_max_attempts == -1 else str(self.retry_max_attempts)
        dc_retry_display_init = "无限" if self.dc_retry_max_attempts == -1 else str(self.dc_retry_max_attempts)
        self.log_callback(f"🔄 重试配置: HTTP失败最多 {retry_display} 次，数据中心不一致最多 {dc_retry_display_init} 次，间隔 {self.retry_intervals} 秒")
        if self.session_enabled:
            self.log_callback(f"🔗 会话保持已启用 (session={self.session_number})")
        if self.no_filter:
            self.log_callback("🔍 filter=0 已启用：显示全部结果不过滤")
        if self.geo_rotate:
            self.log_callback("🌍 数据中心国家轮换已启用：重试时将切换 country_code")

    @staticmethod
    def _parse_intervals(intervals_str: str) -> list:
        """解析重试间隔字符串为数字列表"""
        try:
            parts = [float(s.strip()) for s in intervals_str.split(',') if s.strip()]
            return parts if parts else [5.0]
        except (ValueError, AttributeError):
            return [5.0]

    def _get_retry_wait(self, attempt: int) -> float:
        """获取第 attempt 次重试（0-indexed）的等待秒数，超出列表长度则重复最后一个值"""
        if attempt < len(self.retry_intervals):
            return self.retry_intervals[attempt]
        return self.retry_intervals[-1]

    def _rotate_session(self):
        """更换session number以尝试不同的代理路由"""
        if self.session_enabled:
            import random
            old = self.session_number
            self.session_number = random.randint(100000, 999999)
            self.log_callback(f"🔗 更换session: {old} → {self.session_number}")

    def _get_retry_country(self, retry_num: int) -> str:
        """
        获取第 retry_num 次数据中心重试使用的国家代码

        前 20 次从 PREFERRED_GEO_COUNTRIES 中按顺序选取，
        之后从 ALL_GEO_COUNTRIES 中随机抽取。

        Args:
            retry_num: 重试序号（1-indexed）

        Returns:
            国家代码，如 'us', 'de', 'jp'
        """
        import random
        if retry_num <= len(self.PREFERRED_GEO_COUNTRIES):
            return self.PREFERRED_GEO_COUNTRIES[retry_num - 1]
        else:
            return random.choice(self.ALL_GEO_COUNTRIES)

    async def request_fn(self, url: str, idx: int, max_retries: int = 3, country_code: str = None) -> Optional[str]:
        """
        通过ScraperAPI请求URL（带重试机制）

        Args:
            url: 目标URL
            idx: API Key索引
            max_retries: 最大重试次数
            country_code: 指定代理国家代码（Premium Geo），如 'us', 'de'

        Returns:
            HTML文本,失败返回None
        """
        # -1 表示上层无限重试，但单次HTTP请求限制为3次
        if max_retries <= 0:
            max_retries = 3
        for attempt in range(max_retries):
            try:
                # 轮换API Key
                current_idx = (idx + attempt) % len(self.api_keys)
                # 如果启用了 filter=0，在目标URL后追加参数
                target_url = url
                if self.no_filter and 'scholar.google' in url:
                    if 'filter=0' not in url:
                        separator = '&' if '?' in url else '?'
                        target_url = f"{url}{separator}filter=0"

                payload = {'api_key': self.api_keys[current_idx], 'url': target_url}
                if self.ultra_premium:
                    payload['ultra_premium'] = 'true'
                elif self.premium:
                    payload['premium'] = 'true'
                if country_code:
                    payload['country_code'] = country_code
                if self.session_number:
                    payload['session_number'] = str(self.session_number)

                r = requests.get('https://api.scraperapi.com/', params=payload, timeout=90)

                if r.status_code == 200:
                    self.consecutive_failures = 0  # 重置连续失败计数
                    return r.text
                else:
                    self.log_callback(f"请求失败(尝试 {attempt + 1}/{max_retries}), 状态码: {r.status_code}")
                    if r.status_code == 400:
                        self.log_callback(f"  → ScraperAPI错误详情: {r.text[:200]}")
                    elif r.status_code == 403:
                        self.log_callback(f"  → API Key可能无效或配额用尽")

            except Exception as e:
                self.log_callback(f"请求错误(尝试 {attempt + 1}/{max_retries}): {e}")

            # 按配置间隔等待
            if attempt < max_retries - 1:
                wait_time = self._get_retry_wait(attempt)
                self.log_callback(f"等待 {wait_time} 秒后重试...")
                await asyncio.sleep(wait_time)

        self.consecutive_failures += 1
        self.log_callback(f"⚠️  请求失败,已重试 {max_retries} 次（连续失败: {self.consecutive_failures}）")
        return None

    def _parse_citation_count(self, html: str) -> int:
        """
        从 HTML 中提取引用总数

        Returns:
            引用数，未找到返回 0
        """
        citation_count = 0

        # 第一步：用 BeautifulSoup 找 gs_ab_mdw 元素（Google Scholar 结果统计专用容器）
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')
            stat_patterns = [
                r'找到约\s*([\d,]+)\s*条',
                r'获得\s*([\d,]+)\s*条',
                r'约\s*([\d,]+)\s*条',
                r'([\d,]+)\s*条结果',
                r'About\s+([\d,]+)\s+results?',
                r'^([\d,]+)\s+results?\b',
            ]
            candidates = []
            id_elem = soup.find(id='gs_ab_mdw')
            if id_elem:
                candidates.append(id_elem)
            candidates.extend(soup.find_all(class_='gs_ab_mdw'))

            for elem in candidates:
                stat_text = elem.get_text(separator=' ', strip=True)
                for pat in stat_patterns:
                    m = re.search(pat, stat_text, re.IGNORECASE)
                    if m:
                        citation_count = int(m.group(1).replace(',', ''))
                        self.log_callback(f"🔍 结果统计元素文本: {stat_text[:100]}")
                        return citation_count
        except Exception:
            pass

        # 第二步：对整个 HTML 做正则（数字可能含 <b> 标签）
        patterns = [
            r'找到约\s*(?:<[^>]+>)?\s*([\d,]+)\s*(?:<[^>]+>)?\s*条',
            r'获得\s*(?:<[^>]+>)?\s*([\d,]+)\s*(?:<[^>]+>)?\s*条',
            r'约\s*(?:<[^>]+>)?\s*([\d,]+)\s*(?:<[^>]+>)?\s*条结果',
            r'About\s+(?:<[^>]+>)?\s*([\d,]+)\s+results?',
            r'([\d,]+)\s*条结果',
            r'>(\d[\d,]*)\s+results?\b',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                return int(match.group(1).replace(',', ''))

        return 0

    def _log_citation_debug(self, html: str):
        """未匹配到引用数时输出诊断信息"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')
            all_elems = []
            id_e = soup.find(id='gs_ab_mdw')
            if id_e:
                all_elems.append(id_e)
            all_elems.extend(soup.find_all(class_='gs_ab_mdw'))
            if all_elems:
                self.log_callback(f"   找到 {len(all_elems)} 个 gs_ab_mdw 元素:")
                for i, e in enumerate(all_elems[:5]):
                    self.log_callback(f"   [{i}] {e.get_text(strip=True)[:120]}")
            else:
                self.log_callback("   未找到任何 gs_ab_mdw 元素")
        except Exception:
            pass

        result_snippets = []
        for snippet_pattern in [r'.{0,50}结果.{0,50}', r'.{0,50}results.{0,50}', r'.{0,50}获得.{0,50}', r'.{0,50}条.{0,50}']:
            snippet_matches = re.findall(snippet_pattern, html, re.IGNORECASE)
            result_snippets.extend(snippet_matches[:2])

        if result_snippets:
            self.log_callback("⚠️  未匹配到引用数，发现以下可能相关的文本片段：")
            for snippet in result_snippets[:6]:
                clean_snippet = ' '.join(snippet.split())
                self.log_callback(f"   → {clean_snippet}")

    async def detect_citation_count(self, url: str) -> Tuple[int, int]:
        """
        检测引用列表的总引用数和预估页数（带重试）

        Args:
            url: Google Scholar引用列表URL

        Returns:
            (引用数, 预估页数)，失败返回(0, 0)
        """
        self.log_callback("🔍 正在检测引用数量...")

        max_attempts = 10 if self.retry_max_attempts == -1 else self.retry_max_attempts
        for attempt in range(max_attempts):
            try:
                api_idx = attempt % len(self.api_keys)
                html = await self.request_fn(url, api_idx, max_retries=self.retry_max_attempts)
                if not html:
                    self.log_callback(f"⚠️  第 {attempt + 1}/{max_attempts} 次请求失败，无法获取页面")
                    if attempt < max_attempts - 1:
                        wait = self._get_retry_wait(attempt)
                        self.log_callback(f"⏳ 等待 {wait} 秒后重试...")
                        await asyncio.sleep(wait)
                    continue

                # 调试模式：保存HTML
                if self.debug_mode:
                    from datetime import datetime
                    debug_dir = Path("debug")
                    debug_dir.mkdir(exist_ok=True)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    html_file = debug_dir / f"detect_citation_{timestamp}.html"
                    with open(html_file, 'w', encoding='utf-8') as f:
                        f.write(html)
                    self.log_callback(f"🐛 已保存检测页面HTML: {html_file}")

                citation_count = self._parse_citation_count(html)

                if citation_count > 0:
                    estimated_pages = (citation_count + 9) // 10
                    self.log_callback(f"✅ 检测到引用数: {citation_count}")
                    self.log_callback(f"📊 预估页数: {estimated_pages} 页")
                    return (citation_count, estimated_pages)

                # 引用数为 0：页面可能异常，输出诊断并重试
                self.log_callback(f"⚠️  第 {attempt + 1}/{max_attempts} 次未能提取引用数")
                self._log_citation_debug(html)

                if attempt < max_attempts - 1:
                    wait = self._get_retry_wait(attempt)
                    self.log_callback(f"🔄 可能是异常页面，等待 {wait} 秒后换 API Key 重试...")
                    await asyncio.sleep(wait)

            except Exception as e:
                self.log_callback(f"⚠️  第 {attempt + 1}/{max_attempts} 次检测引用数失败: {e}")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(self._get_retry_wait(attempt))

        self.log_callback("⚠️  多次尝试后仍未能提取引用数，将按未知数量继续")
        return (0, 0)

    def _extract_year_data(self, html: str) -> list:
        """
        从 HTML 中提取年份分布数据

        Args:
            html: Google Scholar 引用列表页面的 HTML

        Returns:
            年份列表（按从旧到新排序），每个元素为 (year, count) 元组
            例如: [(2020, 50), (2021, 80), (2022, 100)]
        """
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, 'html.parser')
            year_data = []

            # 查找所有 class="gs_hist_g_a" 的元素
            elements = soup.find_all(class_='gs_hist_g_a')

            for elem in elements:
                year_str = elem.get('data-year')
                count_str = elem.get('data-count')

                if year_str and count_str:
                    try:
                        year = int(year_str)
                        count = int(count_str)
                        year_data.append((year, count))
                    except ValueError:
                        continue

            # 去重（可能有侧边栏和弹窗两处数据）
            year_data = list(set(year_data))

            # 按年份从旧到新排序
            year_data.sort(key=lambda x: x[0])

            if year_data:
                self.log_callback(f"📅 检测到年份分布: {len(year_data)} 个年份")
                for year, count in year_data:
                    self.log_callback(f"   - {year} 年: {count} 篇")

            return year_data

        except Exception as e:
            self.log_callback(f"⚠️  提取年份数据失败: {e}")
            return []

    def _is_real_paper(self, paper: dict) -> tuple:
        """
        判断一个解析结果是否像真实学术论文（而非导航菜单项）

        Returns:
            (is_real, reason) - is_real=True 表示是真实论文，reason 是判断依据
        """
        link = paper.get('paper_link', '').strip()
        title = paper.get('paper_title', '').strip()
        authors = paper.get('authors', {})

        # 链接是导航/登录链接 → 不是真实论文
        BAD_LINK_PATTERNS = ['accounts.google.com', '/sorry/', 'javascript:']
        for bad in BAD_LINK_PATTERNS:
            if bad in link:
                return False, f"链接异常: {link[:80]}"
        if not link:
            return False, "链接为空"
        # 只拦截纯锚点链接(如 href="#")，不拦截带fragment的正常URL(如 xxx.pdf#page=31)
        if link == '#' or (link.startswith('#') and '/' not in link):
            return False, f"链接为空锚点: {link[:80]}"

        # 标题过短且是已知导航文字 → 不是真实论文
        NAV_TITLES = [
            '登录', 'login', 'sign in', '自定义范围', 'custom range',
            '错误', 'error', 'access denied', '访问被拒绝', 'google scholar',
            '时间不限', '任何时间',
        ]
        if len(title) < 20 and title.lower() in [t.lower() for t in NAV_TITLES]:
            return False, f"导航标题: {title}"

        # 作者字段包含菜单关键词 → 不是真实论文
        MENU_KEYWORDS = ['个人学术档案', '我的个人学术档案', '统计指标', 'My Profile', 'Metrics']
        if isinstance(authors, dict):
            for author_key in authors.keys():
                for kw in MENU_KEYWORDS:
                    if kw in author_key:
                        return False, f"作者字段含菜单项: {kw}"

        return True, "正常"

    def _detect_login_page(self, html: str, paper_dict: dict, page_count: int = 0, debug: bool = True) -> tuple:
        """
        检测是否返回了登录页面或异常页面

        核心逻辑：只看解析到的"论文"内容是否像真实学术论文。
        HTML 级别的关键词（如 accounts.google.com/Login）在所有 Google Scholar 页面
        的导航栏里都有，不能作为判断依据。

        Returns:
            (is_login_page, matched_indicators) 元组
        """
        matched_indicators = []

        # 评估每篇解析结果的真实性
        real_count = 0
        fake_reasons = []
        for paper_id, paper in paper_dict.items():
            is_real, reason = self._is_real_paper(paper)
            if is_real:
                real_count += 1
            else:
                fake_reasons.append(f"{paper_id}: {reason}")
                matched_indicators.append(f"非真实论文 - {paper_id}: {reason}")

        total = len(paper_dict)

        # 仅当 paper_dict 为空时，才补充 HTML 级别的辅助检测
        # （recaptcha/sorry 页面不会有任何论文解析结果）
        if total == 0:
            html_lower = html.lower()
            for kw in ['recaptcha', '/sorry/', 'unusual traffic', 'not a robot', '验证码', '机器人']:
                if kw in html_lower:
                    matched_indicators.append(f"HTML指纹(空结果页): {kw}")
            matched_indicators.append("paper_dict为空")

        # 判断逻辑：以"真实论文数量"为核心依据
        if real_count >= 2:
            # 至少2篇真实论文 → 正常页面
            is_login_page = False
        elif real_count == 1 and total == 1:
            # 只有1篇且是真实论文 → 正常（引用数确实可能为1）
            is_login_page = False
        elif real_count == 1 and total >= 2:
            # 解析到多条但只有1篇是真实论文 → 可疑，当作异常处理
            is_login_page = True
            matched_indicators.append(f"⚠️ 解析到 {total} 条，但只有 1 篇是真实论文，其余均为导航项")
        else:
            # real_count == 0：全部都是假论文或空
            is_login_page = True
            if fake_reasons:
                matched_indicators.append(f"⚠️ 解析到 {total} 条，但全部不是真实论文")
            else:
                matched_indicators.append("⚠️ 未解析到任何内容")

        # 有异常时保存解析详情（HTML已在主循环里由 _save_debug_html 提前保存了）
        if is_login_page or len(matched_indicators) > 0:
            self._save_debug_info(html, paper_dict, page_count, matched_indicators, force_save=False)

        return is_login_page, matched_indicators

    def _save_debug_html(self, html: str, page_count: int):
        """调试模式：只保存原始 HTML，供人工查看"""
        try:
            from datetime import datetime
            debug_dir = Path("debug")
            debug_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            html_file = debug_dir / f"page_{page_count}_{timestamp}.html"
            with open(html_file, 'w', encoding='utf-8') as f:
                f.write(html)
            self.log_callback(f"🐛 [调试] 第 {page_count} 页 HTML 已保存: {html_file}")
        except Exception as e:
            self.log_callback(f"⚠️ 保存调试HTML失败: {e}")

    def _save_debug_info(self, html: str, paper_dict: dict, page_count: int, matched_indicators: list, force_save: bool = False):
        """
        保存调试信息到文件

        Args:
            force_save: 强制保存（调试模式下即使没有问题也保存）
        """
        try:
            from datetime import datetime
            debug_dir = Path("debug")
            debug_dir.mkdir(exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # 保存HTML
            html_file = debug_dir / f"page_{page_count}_{timestamp}.html"
            with open(html_file, 'w', encoding='utf-8') as f:
                f.write(html)

            # 保存解析结果和匹配信息
            info_file = debug_dir / f"page_{page_count}_{timestamp}_info.txt"
            with open(info_file, 'w', encoding='utf-8') as f:
                f.write(f"=== 第 {page_count} 页调试信息 ===\n\n")
                f.write(f"时间: {timestamp}\n\n")

                f.write("匹配到的指纹:\n")
                for indicator in matched_indicators:
                    f.write(f"  - {indicator}\n")
                f.write("\n")

                f.write(f"解析到的论文数: {len(paper_dict)}\n\n")

                if len(paper_dict) > 0:
                    f.write("解析到的论文:\n")
                    for paper_id, paper_content in paper_dict.items():
                        f.write(f"\n{paper_id}:\n")
                        f.write(f"  标题: {paper_content.get('paper_title', '')}\n")
                        f.write(f"  链接: {paper_content.get('paper_link', '')}\n")
                        f.write(f"  引用: {paper_content.get('citation', '')}\n")
                        f.write(f"  作者: {paper_content.get('authors', {})}\n")
                else:
                    f.write("未解析到任何论文\n")

                f.write(f"\n\nHTML长度: {len(html)} 字符\n")
                f.write(f"HTML预览 (前500字符):\n{html[:500]}\n")

            self.log_callback(f"🔍 调试信息已保存:")
            self.log_callback(f"   - HTML: {html_file}")
            self.log_callback(f"   - 详情: {info_file}")

        except Exception as e:
            self.log_callback(f"⚠️ 保存调试信息失败: {e}")

    async def _scrape_single_year(
        self,
        base_url: str,
        year: int,
        output_file: Path,
        sleep_seconds: int = 10,
        cancel_check: Optional[Callable[[], bool]] = None,
        expected_count: int = 0
    ) -> dict:
        """
        抓取单个年份的引用数据

        Args:
            base_url: 基础 Google Scholar 引用列表 URL
            year: 要抓取的年份
            output_file: 输出 JSONL 文件路径
            sleep_seconds: 每页抓取间隔(秒)
            cancel_check: 取消检查函数
            expected_count: 该年份预期的论文数（用于数据中心不一致检测）

        Returns:
            包含抓取统计信息的字典: {'year': year, 'pages': 页数, 'papers': 论文数, 'hit_limit': 是否达到限制}
        """
        # 构造年份过滤 URL
        year_url = f"{base_url}&as_ylo={year}&as_yhi={year}"
        self.log_callback(f"📅 开始抓取 {year} 年的引用...")

        page_count = 0
        current_url = year_url
        previous_url = None
        total_papers = 0
        last_page_had_papers = False
        hit_limit = False
        estimated_pages = (expected_count + 9) // 10 if expected_count > 0 else 0

        # 确保输出目录存在
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, 'w', encoding='utf-8') as f:
            while current_url != 'EMPTY':
                # 检查是否取消
                if cancel_check and cancel_check():
                    self.log_callback(f"   {year} 年抓取已取消")
                    break

                # 检查是否达到 Google Scholar 的 1000 条限制
                if page_count >= 100:
                    self.log_callback(f"⚠️  {year} 年已达到 Google Scholar 的 100 页限制（1000条）")
                    hit_limit = True
                    break

                # 检查连续失败次数
                if self.consecutive_failures >= self.max_consecutive_failures:
                    self.log_callback(f"❌ {year} 年连续失败过多，暂停抓取")
                    break

                # 抓取当前页
                api_key_idx = page_count % len(self.api_keys)
                html = await self.request_fn(current_url, api_key_idx, max_retries=self.retry_max_attempts)

                if not html:
                    self.log_callback(f"❌ {year} 年第 {page_count} 页抓取失败")
                    # 尝试使用不同的 API Key 再次请求
                    await asyncio.sleep(self._get_retry_wait(0))
                    html = await self.request_fn(current_url, (api_key_idx + 1) % len(self.api_keys), max_retries=self.retry_max_attempts)
                    if not html:
                        self.log_callback(f"❌ {year} 年第 {page_count} 页仍然失败，跳过")
                        if self.consecutive_failures >= self.max_consecutive_failures:
                            break
                        continue

                # 解析 HTML
                try:
                    paper_dict, next_page = self.parser.parse_page(html)
                except Exception as e:
                    self.log_callback(f"❌ {year} 年解析失败: {e}")
                    self.consecutive_failures += 1
                    break

                # 调试模式：每页都保存原始HTML
                if self.debug_mode:
                    self._save_debug_html(html, page_count)

                # 检测登录页面
                is_login_page, matched_indicators = self._detect_login_page(html, paper_dict, page_count, debug=False)

                if is_login_page:
                    self.log_callback(f"🚫 {year} 年第 {page_count} 页检测到登录页面，启动重试...")

                    retry_success = False
                    retry_display = "∞" if self.retry_max_attempts == -1 else str(self.retry_max_attempts)
                    retry_num = 0
                    while True:
                        retry_num += 1
                        if self.retry_max_attempts != -1 and retry_num > self.retry_max_attempts:
                            break
                        if cancel_check and cancel_check():
                            break
                        retry_wait = self._get_retry_wait(retry_num - 1)
                        self.log_callback(f"🔄 第 {retry_num}/{retry_display} 次重试，等待 {retry_wait} 秒...")
                        await asyncio.sleep(retry_wait)

                        # 更换session以尝试不同路由
                        self._rotate_session()

                        retry_api_idx = (api_key_idx + retry_num) % len(self.api_keys)
                        retry_html = await self.request_fn(current_url, retry_api_idx, max_retries=self.retry_max_attempts)

                        if not retry_html:
                            self.log_callback(f"❌ 重试 {retry_num} 失败：无法获取HTML")
                            continue

                        try:
                            retry_paper_dict, retry_next_page = self.parser.parse_page(retry_html)
                        except Exception as e:
                            self.log_callback(f"❌ 重试 {retry_num} 解析失败: {e}")
                            continue

                        retry_is_login, _ = self._detect_login_page(retry_html, retry_paper_dict, page_count, debug=False)

                        if not retry_is_login:
                            self.log_callback(f"✅ 重试成功！第 {page_count} 页正常抓取")
                            html = retry_html
                            paper_dict = retry_paper_dict
                            next_page = retry_next_page
                            self.consecutive_failures = 0
                            retry_success = True
                            break
                        else:
                            self.log_callback(f"⚠️ 重试 {retry_num} 仍检测到登录页面")

                    if not retry_success:
                        self.log_callback(f"❌ {year} 年第 {page_count} 页重试 {retry_display} 次后仍失败，跳过该年")
                        self.consecutive_failures += 1
                        break

                # 数据中心不一致检测
                paper_count_this_page = len(paper_dict)
                if expected_count > 0 and estimated_pages > 0 and paper_count_this_page > 0:
                    expected_last_page = estimated_pages - 1
                    expected_on_this_page = 10
                    if page_count == expected_last_page:
                        expected_on_this_page = expected_count % 10 or 10

                    page_is_short = paper_count_this_page < expected_on_this_page
                    page_ends_early = (page_count < expected_last_page and next_page == 'EMPTY')

                    if page_is_short or page_ends_early:
                        reason_parts = []
                        if page_is_short:
                            reason_parts.append(f"应有{expected_on_this_page}篇，实际{paper_count_this_page}篇")
                        if page_ends_early:
                            reason_parts.append(f"非末页但无下一页")
                        self.log_callback(f"⚠️ {year} 年第 {page_count} 页数据异常: {'；'.join(reason_parts)}（疑似数据中心不一致）")

                        best_paper_dict = paper_dict
                        best_next_page = next_page
                        best_count = paper_count_this_page

                        dc_retry_display = "∞" if self.dc_retry_max_attempts == -1 else str(self.dc_retry_max_attempts)
                        dc_retry = 0
                        dc_retry_success = False
                        while True:
                            dc_retry += 1
                            if self.dc_retry_max_attempts != -1 and dc_retry > self.dc_retry_max_attempts:
                                break
                            if cancel_check and cancel_check():
                                break
                            dc_wait = self._get_retry_wait(dc_retry - 1)
                            # 数据中心重试：始终切换国家代码以强制切换DC
                            country_code = self._get_retry_country(dc_retry)
                            self.log_callback(f"🔄 数据中心重试 {dc_retry}/{dc_retry_display}，国家={country_code}，等待 {dc_wait} 秒...")
                            await asyncio.sleep(dc_wait)

                            self._rotate_session()

                            dc_api_idx = (api_key_idx + dc_retry) % len(self.api_keys)

                            # 如果有上一页URL，从上一页重新获取next_page链接
                            retry_url = current_url
                            if previous_url:
                                self.log_callback(f"↩️ 重新请求上一页以获取正确的下一页链接...")
                                prev_html = await self.request_fn(previous_url, dc_api_idx, max_retries=2, country_code=country_code)
                                if prev_html:
                                    try:
                                        _, new_next_page = self.parser.parse_page(prev_html)
                                        if new_next_page != 'EMPTY':
                                            retry_url = new_next_page
                                    except Exception:
                                        pass

                            dc_html = await self.request_fn(retry_url, dc_api_idx, max_retries=self.retry_max_attempts, country_code=country_code)
                            if not dc_html:
                                continue

                            try:
                                dc_paper_dict, dc_next_page = self.parser.parse_page(dc_html)
                            except Exception:
                                continue

                            dc_count = len(dc_paper_dict)
                            if dc_count > best_count:
                                best_paper_dict = dc_paper_dict
                                best_next_page = dc_next_page
                                best_count = dc_count

                            improved = False
                            if page_is_short and dc_count >= expected_on_this_page:
                                improved = True
                            elif page_ends_early and dc_next_page != 'EMPTY':
                                improved = True

                            if improved:
                                self.log_callback(f"✅ 数据中心重试成功！获得 {dc_count} 篇论文")
                                paper_dict = dc_paper_dict
                                next_page = dc_next_page
                                paper_count_this_page = dc_count
                                dc_retry_success = True
                                break
                            else:
                                self.log_callback(f"⚠️ 重试 {dc_retry} 仍异常（{dc_count} 篇）")

                        if not dc_retry_success:
                            if best_count > paper_count_this_page:
                                self.log_callback(f"⚠️ 使用最佳结果（{best_count} 篇）继续")
                                paper_dict = best_paper_dict
                                next_page = best_next_page
                                paper_count_this_page = best_count
                            else:
                                self.log_callback(f"⚠️ 多次重试后仍异常，使用当前结果继续")

                # 统计论文数
                if paper_count_this_page > 0:
                    last_page_had_papers = True
                    total_papers += paper_count_this_page
                else:
                    last_page_had_papers = False

                # 保存结果
                record = {
                    'paper_dict': paper_dict,
                    'next_page': next_page
                }
                f.write(json.dumps({f'page_{page_count}': record}, ensure_ascii=False) + '\n')
                f.flush()

                # 准备下一页
                previous_url = current_url
                current_url = next_page
                page_count += 1

                if current_url != 'EMPTY':
                    await asyncio.sleep(sleep_seconds)

        self.log_callback(f"✅ {year} 年抓取完成: {page_count} 页, {total_papers} 篇论文{' (达到限制)' if hit_limit else ''}")

        return {
            'year': year,
            'pages': page_count,
            'papers': total_papers,
            'hit_limit': hit_limit
        }

    async def scrape(
        self,
        url: str,
        output_file: Path,
        start_page: int = 0,
        sleep_seconds: int = 10,
        cancel_check: Optional[Callable[[], bool]] = None,
        enable_year_traverse: bool = False
    ):
        """
        抓取Google Scholar引用列表（带自动重启和完整性核对）

        Args:
            url: Google Scholar引用列表URL(包含cites=参数)
            output_file: 输出JSONL文件路径
            start_page: 起始页码(用于断点续爬)
            sleep_seconds: 每页抓取间隔(秒)
            cancel_check: 取消检查函数,返回True时停止抓取
            enable_year_traverse: 是否启用按年份遍历（绕过1000条限制）
        """
        self.consecutive_failures = 0  # 重置连续失败计数

        # 确保输出目录存在
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # 检测引用数和预估页数
        citation_count, estimated_pages = await self.detect_citation_count(url)

        # 如果启用年份遍历，走年份遍历流程
        if enable_year_traverse:
            self.log_callback("=" * 60)
            self.log_callback("📅 启用按年份遍历模式")
            self.log_callback("=" * 60)

            # 获取第一页的 HTML 以提取年份数据
            html = await self.request_fn(url, 0, max_retries=2)
            if not html:
                self.log_callback("❌ 无法获取页面数据，切换到普通模式")
                enable_year_traverse = False
            else:
                year_data = self._extract_year_data(html)

                if not year_data:
                    self.log_callback("⚠️  未检测到年份分布数据，切换到普通模式")
                    enable_year_traverse = False
                else:
                    # 按年份遍历
                    year_stats = []
                    total_papers_all_years = 0
                    temp_files = []

                    for idx, (year, expected_count) in enumerate(year_data):
                        # 检查是否取消
                        if cancel_check and cancel_check():
                            self.log_callback("任务已取消")
                            break

                        self.log_callback(f"\n{'=' * 60}")
                        self.log_callback(f"📅 正在抓取 {year} 年（{idx + 1}/{len(year_data)}）")
                        self.log_callback(f"   预期引用数: {expected_count} 篇")
                        self.log_callback(f"{'=' * 60}")

                        # 为每个年份创建临时输出文件
                        temp_file = output_file.parent / f"{output_file.stem}_{year}.jsonl"
                        temp_files.append(temp_file)

                        # 抓取该年份
                        stats = await self._scrape_single_year(
                            base_url=url,
                            year=year,
                            output_file=temp_file,
                            sleep_seconds=sleep_seconds,
                            cancel_check=cancel_check,
                            expected_count=expected_count
                        )

                        year_stats.append(stats)
                        total_papers_all_years += stats['papers']

                        # 如果达到1000条限制，弹出警告
                        if stats['hit_limit']:
                            self.log_callback(f"⚠️  警告：{year} 年的引用数超过 1000 条（Google Scholar 限制）")
                            self.log_callback(f"   只能抓取前 1000 条，剩余数据无法访问")

                        # 更新总体进度
                        self.progress_callback(idx + 1, len(year_data))

                    # 合并所有年份的文件
                    self.log_callback("\n" + "=" * 60)
                    self.log_callback("📦 正在合并所有年份的数据...")
                    self.log_callback("=" * 60)

                    self._merge_year_files(temp_files, output_file)

                    # 输出统计信息
                    self.log_callback("\n" + "=" * 60)
                    self.log_callback("📊 年份遍历统计:")
                    self.log_callback("=" * 60)
                    for stats in year_stats:
                        self.log_callback(f"   {stats['year']} 年: {stats['papers']} 篇 ({stats['pages']} 页){' ⚠️ 达到限制' if stats['hit_limit'] else ''}")
                    self.log_callback(f"\n   总计: {total_papers_all_years} 篇")

                    if citation_count > 0:
                        diff = citation_count - total_papers_all_years
                        self.log_callback(f"   预期: {citation_count} 篇")
                        if diff == 0:
                            self.log_callback(f"   ✅ 数量完全匹配！")
                        else:
                            self.log_callback(f"   ⚠️  相差 {abs(diff)} 篇（{'多抓了' if diff < 0 else '少抓了'}）")

                    self.log_callback("=" * 60)
                    self.log_callback("✅ 按年份遍历完成！")
                    return

        # 普通模式（不按年份遍历）
        page_count = start_page
        current_url = url

        # 断点续爬时，为 URL 添加 start= 偏移，跳到正确的页面
        if start_page > 0:
            import urllib.parse
            start_offset = start_page * 10
            parsed = urllib.parse.urlparse(current_url)
            params = urllib.parse.parse_qs(parsed.query)
            params['start'] = [str(start_offset)]
            new_query = urllib.parse.urlencode(params, doseq=True)
            current_url = urllib.parse.urlunparse(parsed._replace(query=new_query))
            self.log_callback(f"📌 断点续爬: 从第 {start_page} 页开始 (start={start_offset})")

        self.log_callback(f"开始抓取,起始页: {page_count}")
        if estimated_pages > 0:
            self.log_callback(f"目标: 抓取约 {citation_count} 篇引用论文，预计 {estimated_pages} 页")

        # 统计信息
        total_papers = 0
        last_page_had_papers = False
        previous_url = None  # 记录上一页URL，用于数据中心异常时从上一页重试

        # 使用 'w' 模式（覆盖）而不是 'a' 模式（追加），避免重复数据
        # 如果是断点续爬（start_page > 0），则需要先读取已有数据
        mode = 'w' if start_page == 0 else 'a'
        if mode == 'w' and output_file.exists():
            self.log_callback(f"⚠️  文件已存在，将被覆盖: {output_file}")

        with open(output_file, mode, encoding='utf-8') as f:
            while current_url != 'EMPTY':
                # 检查是否取消
                if cancel_check and cancel_check():
                    self.log_callback("任务已取消")
                    break

                # 检查连续失败次数
                if self.consecutive_failures >= self.max_consecutive_failures:
                    self.log_callback(f"❌ 连续失败 {self.max_consecutive_failures} 次,暂停抓取")
                    self.log_callback(f"💾 当前进度已保存,下次从第 {page_count} 页继续")
                    # 保存当前进度到配置
                    self._save_resume_progress(page_count)
                    self.log_callback("⚠️  建议稍后重新启动任务,或检查 ScraperAPI 配额")
                    break

                self.log_callback(f"正在抓取第 {page_count} 页...")

                # 抓取当前页（带重试）
                api_key_idx = page_count % len(self.api_keys)
                html = await self.request_fn(current_url, api_key_idx, max_retries=self.retry_max_attempts)

                if not html:
                    self.log_callback(f"❌ 第 {page_count} 页抓取失败（已重试{self.retry_max_attempts}次）")
                    # 不要跳过，而是等待后再次尝试
                    extra_wait = self._get_retry_wait(self.retry_max_attempts - 1) * 2
                    self.log_callback(f"⏳ 等待 {extra_wait} 秒后重新尝试该页...")
                    await asyncio.sleep(extra_wait)
                    # 再次尝试，使用不同的 API Key
                    html = await self.request_fn(current_url, (api_key_idx + 1) % len(self.api_keys), max_retries=self.retry_max_attempts)
                    if not html:
                        self.log_callback(f"❌ 第 {page_count} 页仍然失败，暂时跳过")
                        # 只有在连续失败次数过多时才停止
                        if self.consecutive_failures >= self.max_consecutive_failures:
                            break
                        continue  # 跳过这一页，继续下一页

                # 解析HTML
                try:
                    paper_dict, next_page = self.parser.parse_page(html)
                except Exception as e:
                    self.log_callback(f"❌ 解析HTML失败: {e}")
                    self.consecutive_failures += 1
                    self.log_callback(f"💾 保存当前进度: 第 {page_count} 页")
                    self._save_resume_progress(page_count)
                    break

                # 调试模式：每页都立即保存原始HTML（不等检测结果）
                if self.debug_mode:
                    self._save_debug_html(html, page_count)

                # ⚠️ 检测登录页面或异常页面
                is_login_page, matched_indicators = self._detect_login_page(html, paper_dict, page_count, debug=True)

                if is_login_page:
                    self.log_callback(f"🚫 第 {page_count} 页检测到登录页面！Google Scholar可能已拦截")

                    # 输出匹配到的指纹
                    self.log_callback(f"🔎 检测详情 (共 {len(matched_indicators)} 个异常):")
                    for indicator in matched_indicators:
                        self.log_callback(f"   • {indicator}")

                    # 输出解析到的论文信息
                    self.log_callback(f"📄 解析到 {len(paper_dict)} 篇论文")
                    if len(paper_dict) > 0 and len(paper_dict) <= 3:
                        for paper_id, paper_content in paper_dict.items():
                            self.log_callback(f"   - 标题: {paper_content.get('paper_title', '')[:80]}")
                            self.log_callback(f"     链接: {paper_content.get('paper_link', '')[:80]}")

                    # 🔄 启动重试机制
                    retry_success = False
                    retry_display = "∞" if self.retry_max_attempts == -1 else str(self.retry_max_attempts)
                    retry_num = 0
                    while True:
                        retry_num += 1
                        if self.retry_max_attempts != -1 and retry_num > self.retry_max_attempts:
                            break
                        if cancel_check and cancel_check():
                            break
                        retry_wait = self._get_retry_wait(retry_num - 1)
                        self.log_callback(f"🔄 第 {retry_num}/{retry_display} 次重试当前页...")
                        self.log_callback(f"⏳ 等待 {retry_wait} 秒后重新请求（换API Key和session）...")
                        await asyncio.sleep(retry_wait)

                        # 更换session以尝试不同路由
                        self._rotate_session()

                        # 换一个API Key重试
                        retry_api_idx = (api_key_idx + retry_num) % len(self.api_keys)
                        retry_html = await self.request_fn(current_url, retry_api_idx, max_retries=self.retry_max_attempts)

                        if not retry_html:
                            self.log_callback(f"❌ 重试 {retry_num} 失败：无法获取HTML")
                            continue

                        # 重新解析
                        try:
                            retry_paper_dict, retry_next_page = self.parser.parse_page(retry_html)
                        except Exception as e:
                            self.log_callback(f"❌ 重试 {retry_num} 失败：解析错误 {e}")
                            continue

                        # 重新检测
                        retry_is_login, retry_indicators = self._detect_login_page(retry_html, retry_paper_dict, page_count, debug=False)

                        if not retry_is_login:
                            # 重试成功！
                            self.log_callback(f"✅ 重试成功！第 {page_count} 页正常抓取")
                            html = retry_html
                            paper_dict = retry_paper_dict
                            next_page = retry_next_page
                            retry_success = True
                            break
                        else:
                            self.log_callback(f"⚠️ 重试 {retry_num} 仍然检测到登录页面")

                    if not retry_success:
                        # 所有重试都失败了
                        self.log_callback(f"❌ 第 {page_count} 页重试 {retry_display} 次后仍然失败")
                        self.consecutive_failures += 1
                        self.log_callback(f"💾 保存当前进度: 第 {page_count} 页")
                        self._save_resume_progress(page_count)
                        self.log_callback("⏸️  暂停抓取，请检查配置后从第 {} 页继续".format(page_count))
                        break
                    else:
                        # 重试成功，重置失败计数
                        self.consecutive_failures = 0

                # 检查是否有论文
                paper_count_this_page = len(paper_dict)

                # 检测数据中心不一致：论文数量与预期不符则重试
                # 预估有 N 条引用 → 前 (N//10 - 1) 页应各有 10 篇，末页有 N%10 篇
                if citation_count > 0 and estimated_pages > 0 and paper_count_this_page > 0:
                    expected_last_page = estimated_pages - 1  # 0-indexed
                    expected_on_this_page = 10  # 非末页默认 10
                    if page_count == expected_last_page:
                        expected_on_this_page = citation_count % 10 or 10

                    page_is_short = paper_count_this_page < expected_on_this_page
                    page_ends_early = (page_count < expected_last_page and next_page == 'EMPTY')

                    if page_is_short or page_ends_early:
                        reason_parts = []
                        if page_is_short:
                            reason_parts.append(f"应有{expected_on_this_page}篇，实际{paper_count_this_page}篇")
                        if page_ends_early:
                            reason_parts.append(f"非末页但无下一页（第{page_count}页，预计共{estimated_pages}页）")
                        self.log_callback(f"⚠️ 第 {page_count} 页数据异常: {'；'.join(reason_parts)}（疑似命中不同数据中心）")

                        # 从上一页重新获取下一页链接（换session以尝试不同路由）
                        best_paper_dict = paper_dict
                        best_next_page = next_page
                        best_count = paper_count_this_page

                        dc_retry_display = "∞" if self.dc_retry_max_attempts == -1 else str(self.dc_retry_max_attempts)
                        dc_retry = 0
                        dc_retry_success = False
                        while True:
                            dc_retry += 1
                            if self.dc_retry_max_attempts != -1 and dc_retry > self.dc_retry_max_attempts:
                                break
                            if cancel_check and cancel_check():
                                break
                            dc_wait = self._get_retry_wait(dc_retry - 1)
                            # 数据中心重试：始终切换国家代码以强制切换DC
                            country_code = self._get_retry_country(dc_retry)
                            self.log_callback(f"🔄 数据中心重试 {dc_retry}/{dc_retry_display}，国家={country_code}，等待 {dc_wait} 秒...")
                            await asyncio.sleep(dc_wait)

                            # 更换session number以尝试不同的代理路由
                            self._rotate_session()

                            dc_api_idx = (api_key_idx + dc_retry) % len(self.api_keys)

                            # 如果有上一页URL，从上一页重新获取next_page链接
                            retry_url = current_url
                            if previous_url:
                                self.log_callback(f"↩️ 重新请求上一页以获取正确的下一页链接...")
                                prev_html = await self.request_fn(previous_url, dc_api_idx, max_retries=2, country_code=country_code)
                                if prev_html:
                                    try:
                                        _, new_next_page = self.parser.parse_page(prev_html)
                                        if new_next_page != 'EMPTY':
                                            retry_url = new_next_page
                                        else:
                                            self.log_callback(f"⚠️ 上一页无下一页链接，使用原URL重试")
                                    except Exception:
                                        self.log_callback(f"⚠️ 上一页解析失败，使用原URL重试")

                            dc_html = await self.request_fn(retry_url, dc_api_idx, max_retries=self.retry_max_attempts, country_code=country_code)
                            if not dc_html:
                                continue

                            try:
                                dc_paper_dict, dc_next_page = self.parser.parse_page(dc_html)
                            except Exception:
                                continue

                            # 检查重试结果是否更好
                            dc_count = len(dc_paper_dict)

                            # 保留最佳结果
                            if dc_count > best_count:
                                best_paper_dict = dc_paper_dict
                                best_next_page = dc_next_page
                                best_count = dc_count

                            improved = False
                            if page_is_short and dc_count >= expected_on_this_page:
                                improved = True
                            elif page_ends_early and dc_next_page != 'EMPTY':
                                improved = True

                            if improved:
                                self.log_callback(f"✅ 数据中心重试成功！获得 {dc_count} 篇论文")
                                paper_dict = dc_paper_dict
                                next_page = dc_next_page
                                paper_count_this_page = dc_count
                                if self.debug_mode:
                                    self._save_debug_html(dc_html, page_count)
                                dc_retry_success = True
                                break
                            else:
                                self.log_callback(f"⚠️ 重试 {dc_retry} 仍异常（{dc_count} 篇）")

                        if not dc_retry_success:
                            # 所有重试都失败，使用最佳结果
                            if best_count > paper_count_this_page:
                                self.log_callback(f"⚠️ 多次重试后仍异常，使用最佳结果（{best_count} 篇）继续")
                                paper_dict = best_paper_dict
                                next_page = best_next_page
                                paper_count_this_page = best_count
                            else:
                                self.log_callback(f"⚠️ 多次重试后仍异常，使用当前结果继续")

                if paper_count_this_page > 0:
                    last_page_had_papers = True
                    total_papers += paper_count_this_page
                else:
                    last_page_had_papers = False
                    self.log_callback(f"⚠️  第 {page_count} 页没有论文数据")

                # 保存结果
                record = {
                    'paper_dict': paper_dict,
                    'next_page': next_page
                }
                f.write(json.dumps({f'page_{page_count}': record}, ensure_ascii=False) + '\n')
                f.flush()  # 立即写入磁盘

                self.log_callback(f"✅ 第 {page_count} 页完成,共 {paper_count_this_page} 篇论文")

                # 更新进度（使用预估页数，如果可用）
                total_for_progress = estimated_pages if estimated_pages > 0 else page_count + 2
                self.progress_callback(page_count, total_for_progress)

                # 准备下一页
                previous_url = current_url
                current_url = next_page
                page_count += 1

                if current_url != 'EMPTY':
                    self.log_callback(f"等待 {sleep_seconds} 秒后继续...")
                    await asyncio.sleep(sleep_seconds)

        # 完整性核对
        self._verify_completeness(
            pages_scraped=page_count - start_page,
            total_papers=total_papers,
            last_page_had_papers=last_page_had_papers,
            final_url=current_url,
            expected_citation_count=citation_count  # 传入预期引用数
        )

        self.log_callback(f"✅ 抓取完成!共 {page_count - start_page} 页,{total_papers} 篇论文")

    def _merge_year_files(self, temp_files: list, output_file: Path):
        """
        合并多个年份的临时文件到最终输出文件

        Args:
            temp_files: 临时文件路径列表
            output_file: 最终输出文件路径
        """
        try:
            total_lines = 0
            with open(output_file, 'w', encoding='utf-8') as out_f:
                for temp_file in temp_files:
                    if not temp_file.exists():
                        self.log_callback(f"⚠️  临时文件不存在: {temp_file}")
                        continue

                    # 读取临时文件并写入最终文件
                    with open(temp_file, 'r', encoding='utf-8') as in_f:
                        lines = in_f.readlines()
                        out_f.writelines(lines)
                        total_lines += len(lines)

                    self.log_callback(f"   ✅ 已合并: {temp_file.name} ({len(lines)} 页)")

            self.log_callback(f"\n📦 合并完成! 总计 {total_lines} 页数据")
            self.log_callback(f"📁 输出文件: {output_file}")

            # 删除临时文件
            for temp_file in temp_files:
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                        self.log_callback(f"   🗑️  已删除临时文件: {temp_file.name}")
                    except Exception as e:
                        self.log_callback(f"   ⚠️  删除临时文件失败: {temp_file.name} - {e}")

        except Exception as e:
            self.log_callback(f"❌ 合并文件失败: {e}")

    def _save_resume_progress(self, page_count: int):
        """保存断点进度到 config.json"""
        try:
            import os
            config_path = Path('config.json')
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                config['resume_page_count'] = page_count
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(config, f, ensure_ascii=False, indent=2)
                self.log_callback(f"💾 断点已保存: 第 {page_count} 页")
        except Exception as e:
            self.log_callback(f"⚠️  保存断点失败: {e}")

    def _verify_completeness(self, pages_scraped: int, total_papers: int, last_page_had_papers: bool, final_url: str, expected_citation_count: int = 0):
        """核对抓取完整性"""
        self.log_callback("=" * 60)
        self.log_callback("📊 抓取完整性核对:")
        self.log_callback(f"   - 抓取页数: {pages_scraped}")
        self.log_callback(f"   - 总论文数: {total_papers}")

        # Google Scholar 的 1000 条限制（100页）
        GOOGLE_SCHOLAR_MAX_RESULTS = 1000
        GOOGLE_SCHOLAR_MAX_PAGES = 100
        hit_google_limit = (pages_scraped >= GOOGLE_SCHOLAR_MAX_PAGES or total_papers >= GOOGLE_SCHOLAR_MAX_RESULTS)

        # 对比预期引用数和实际抓取数
        if expected_citation_count > 0:
            self.log_callback(f"   - 预期引用数: {expected_citation_count}")
            diff = expected_citation_count - total_papers

            if diff == 0:
                self.log_callback(f"   - 数量匹配: ✅ 完全一致")
            else:
                # 检查是否是 Google Scholar 的 1000 条限制导致的差异
                if hit_google_limit and expected_citation_count > GOOGLE_SCHOLAR_MAX_RESULTS:
                    self.log_callback(f"   - 数量匹配: ⚠️  相差 {abs(diff)} 篇")
                    self.log_callback(f"   - 📢 这是 Google Scholar 的已知限制：")
                    self.log_callback(f"      • Google Scholar 最多只显示前 {GOOGLE_SCHOLAR_MAX_RESULTS} 条结果（{GOOGLE_SCHOLAR_MAX_PAGES} 页）")
                    self.log_callback(f"      • 即使实际引用数是 {expected_citation_count} 篇，也只能访问前 {GOOGLE_SCHOLAR_MAX_RESULTS} 篇")
                    self.log_callback(f"      • ✅ 这是正常现象，不是抓取错误")
                else:
                    self.log_callback(f"   - 数量匹配: ❌ 相差 {abs(diff)} 篇（少抓了）")

        self.log_callback(f"   - 最后一页有数据: {'是' if last_page_had_papers else '否'}")
        self.log_callback(f"   - 下一页URL: {'空（正常结束）' if final_url == 'EMPTY' else '非空（可能未完成）'}")

        # 判断是否完整
        if final_url == 'EMPTY' and last_page_had_papers:
            if expected_citation_count > 0 and total_papers == expected_citation_count:
                self.log_callback("✅ 抓取完整！数量完全匹配")
            elif expected_citation_count > 0 and hit_google_limit:
                self.log_callback("✅ 抓取完整！已达到 Google Scholar 的最大结果数限制")
            elif expected_citation_count > 0:
                self.log_callback("⚠️  已无下一页，但数量不匹配（可能是数据更新或页面异常）")
            else:
                self.log_callback("✅ 抓取看起来是完整的！")
        elif final_url == 'EMPTY' and not last_page_had_papers:
            self.log_callback("⚠️  最后一页没有数据,但已无下一页（可能是正常结束）")
        elif final_url != 'EMPTY':
            self.log_callback("❌ 抓取可能不完整！还有下一页但任务已停止")
            self.log_callback("💡 建议：重新运行任务以继续抓取剩余页面")
        else:
            self.log_callback("⚠️  抓取状态不确定,请手动检查结果")

        self.log_callback("=" * 60)
