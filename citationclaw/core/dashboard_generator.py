"""
Phase 5: HTML 画像报告生成器
基于 generate_citation_dashboard_v4.py 改为类，使用 pandas 读取数据，
新增下载链接 + 院士引用描述折叠。
"""
import ast
import math
import re
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
from openai import OpenAI

_FAMOUS_INSTITUTIONS = {
    # 国际科技企业
    "Google":              {"keywords": ["google", "alphabet inc"],                     "category": "国际科技企业"},
    "DeepMind":            {"keywords": ["deepmind"],                                   "category": "国际科技企业"},
    "OpenAI":              {"keywords": ["openai"],                                     "category": "国际科技企业"},
    "Meta":                {"keywords": ["meta ai", "meta research", "fair,", "facebook ai", "facebook research"], "category": "国际科技企业"},
    "Microsoft Research":  {"keywords": ["microsoft research"],                         "category": "国际科技企业"},
    "NVIDIA":              {"keywords": ["nvidia"],                                      "category": "国际科技企业"},
    "Anthropic":           {"keywords": ["anthropic"],                                  "category": "国际科技企业"},
    "Apple":               {"keywords": ["apple inc", "apple research"],                "category": "国际科技企业"},
    "Amazon":              {"keywords": ["amazon research", "aws research"],            "category": "国际科技企业"},
    "IBM Research":        {"keywords": ["ibm research"],                               "category": "国际科技企业"},
    "Samsung Research":    {"keywords": ["samsung research"],                           "category": "国际科技企业"},
    "Adobe Research":      {"keywords": ["adobe research"],                             "category": "国际科技企业"},
    "Qualcomm":            {"keywords": ["qualcomm"],                                   "category": "国际科技企业"},
    # 国内科技企业
    "华为":                {"keywords": ["huawei", "华为"],                             "category": "国内科技企业"},
    "阿里巴巴/达摩院":     {"keywords": ["alibaba", "aliyun", "damo academy", "阿里巴巴", "达摩院", "taobao"], "category": "国内科技企业"},
    "字节跳动":            {"keywords": ["bytedance", "tiktok", "字节跳动"],            "category": "国内科技企业"},
    "腾讯":                {"keywords": ["tencent", "腾讯"],                            "category": "国内科技企业"},
    "百度":                {"keywords": ["baidu", "百度"],                              "category": "国内科技企业"},
    "商汤科技":            {"keywords": ["sensetime", "商汤"],                          "category": "国内科技企业"},
    "旷视科技":            {"keywords": ["megvii", "face++", "旷视"],                   "category": "国内科技企业"},
    "小米":                {"keywords": ["xiaomi", "小米"],                             "category": "国内科技企业"},
    "京东":                {"keywords": ["jd.com", "jingdong", "京东"],                 "category": "国内科技企业"},
    "美团":                {"keywords": ["meituan", "美团"],                            "category": "国内科技企业"},
    "快手":                {"keywords": ["kuaishou", "快手"],                           "category": "国内科技企业"},
    "网易":                {"keywords": ["netease", "网易"],                            "category": "国内科技企业"},
    "平安科技":            {"keywords": ["ping an", "平安"],                            "category": "国内科技企业"},
    "蚂蚁集团":            {"keywords": ["ant group", "antfin", "蚂蚁"],                "category": "国内科技企业"},
    # 海外顶尖高校
    "MIT":                 {"keywords": ["mit", "massachusetts institute of technology"], "category": "海外顶尖高校"},
    "Stanford":            {"keywords": ["stanford"],                                   "category": "海外顶尖高校"},
    "Harvard":             {"keywords": ["harvard"],                                    "category": "海外顶尖高校"},
    "UC Berkeley":         {"keywords": ["uc berkeley", "university of california, berkeley", "university of california berkeley"], "category": "海外顶尖高校"},
    "CMU":                 {"keywords": ["carnegie mellon", "cmu"],                     "category": "海外顶尖高校"},
    "Princeton":           {"keywords": ["princeton"],                                  "category": "海外顶尖高校"},
    "Yale":                {"keywords": ["yale"],                                       "category": "海外顶尖高校"},
    "Columbia":            {"keywords": ["columbia university"],                        "category": "海外顶尖高校"},
    "Cornell":             {"keywords": ["cornell"],                                    "category": "海外顶尖高校"},
    "Oxford":              {"keywords": ["oxford"],                                     "category": "海外顶尖高校"},
    "Cambridge":           {"keywords": ["cambridge"],                                  "category": "海外顶尖高校"},
    "ETH Zurich":          {"keywords": ["eth zurich", "ethz"],                         "category": "海外顶尖高校"},
    "Toronto":             {"keywords": ["university of toronto"],                      "category": "海外顶尖高校"},
    "Imperial College":    {"keywords": ["imperial college"],                           "category": "海外顶尖高校"},
    "NUS":                 {"keywords": ["national university of singapore", "nus"],    "category": "海外顶尖高校"},
    "NTU":                 {"keywords": ["nanyang technological"],                      "category": "海外顶尖高校"},
    # 国内顶尖高校/机构
    "清华大学":            {"keywords": ["tsinghua", "清华"],                           "category": "国内顶尖高校/机构"},
    "北京大学":            {"keywords": ["peking university", "pku", "北京大学", "北大"], "category": "国内顶尖高校/机构"},
    "中国科学院":          {"keywords": ["chinese academy of sciences", "中国科学院", "cas "], "category": "国内顶尖高校/机构"},
    "上海交通大学":        {"keywords": ["shanghai jiao tong", "sjtu", "上海交通"],     "category": "国内顶尖高校/机构"},
    "浙江大学":            {"keywords": ["zhejiang university", "zju", "浙江大学"],     "category": "国内顶尖高校/机构"},
    "复旦大学":            {"keywords": ["fudan", "复旦"],                              "category": "国内顶尖高校/机构"},
    "哈尔滨工业大学":      {"keywords": ["harbin institute of technology", "哈工大"],    "category": "国内顶尖高校/机构"},
    "中国人民大学":        {"keywords": ["renmin university", "人民大学"],              "category": "国内顶尖高校/机构"},
    "南京大学":            {"keywords": ["nanjing university", "南京大学"],             "category": "国内顶尖高校/机构"},
    "武汉大学":            {"keywords": ["wuhan university", "武汉大学"],               "category": "国内顶尖高校/机构"},
    "中山大学":            {"keywords": ["sun yat-sen university", "中山大学"],         "category": "国内顶尖高校/机构"},
    "北京航空航天大学":    {"keywords": ["beihang", "buaa", "北航"],                    "category": "国内顶尖高校/机构"},
    "华中科技大学":        {"keywords": ["huazhong university", "hust", "华中科技"],    "category": "国内顶尖高校/机构"},
    "国防科技大学":        {"keywords": ["national university of defense", "国防科技大学"], "category": "国内顶尖高校/机构"},
}

class DashboardGenerator:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        log_callback: Callable,
        test_mode: bool = False,
    ):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.log = log_callback
        self.test_mode = test_mode

    # ─────────────────────────────────────────────────────────────
    # LLM helpers
    # ─────────────────────────────────────────────────────────────
    def _llm(self, prompt: str) -> str:
        if self.test_mode:
            return ""   # 测试模式：跳过 LLM，让各处 fallback 自动生效
        try:
            comp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
            return comp.choices[0].message.content or ""
        except Exception as e:
            self.log(f"  [LLM error] {e}")
            return ""

    def _llm_json(self, prompt: str):
        raw = self._llm(prompt).strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        try:
            return json.loads(raw)
        except Exception:
            m = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    pass
            return None

    # ─────────────────────────────────────────────────────────────
    # Data loading
    # ─────────────────────────────────────────────────────────────
    @staticmethod
    def _parse_citation_count(raw) -> int:
        if raw is None:
            return 0
        nums = re.findall(r"\d+", str(raw))
        return int(nums[0]) if nums else 0

    @staticmethod
    def _parse_year(raw):
        """Parse year to int, returning None for NaN/invalid."""
        if raw is None:
            return None
        try:
            f = float(raw)
            if math.isnan(f):
                return None
            return int(f)
        except (ValueError, TypeError):
            return None

    def _load_citing_data(self, path: Path):
        """
        Returns:
            papers        — list of dicts sorted by citations desc
            total_papers  — unique paper count
            descriptions  — list of non-empty Citing_Description strings
            citing_pairs  — list of {paper_title, citing_paper, description}
            unique_citing_papers — deduplicated list of Citing_Paper values
        """
        df = pd.read_excel(path)
        papers_by_key = {}
        descriptions = []
        citing_pairs = []
        self_citing_keys = set()

        for _, row in df.iterrows():
            page_id = row.get('PageID', '')
            paper_id = row.get('PaperID', '')
            key = (str(page_id or ""), str(paper_id or ""))
            is_self = row.get('Is_Self_Citation', False)
            if is_self and str(is_self).lower() not in ('false', '0', 'nan', 'none', ''):
                self_citing_keys.add(key)

            if key not in papers_by_key:
                papers_by_key[key] = {
                    "id": key,
                    "title": str(row.get('Paper_Title', '') or ''),
                    "year": self._parse_year(row.get('Paper_Year', None)),
                    "link": str(row.get('Paper_Link', '') or ''),
                    "citations": self._parse_citation_count(row.get('Citations', 0)),
                    "country": str(row.get('First_Author_Country', '') or '').strip(),
                    "institution": str(row.get('First_Author_Institution', '') or '').strip(),
                    "authors": str(row.get('Authors_with_Profile', '') or '').strip(),
                    "author_affiliation": str(row.get('Searched Author-Affiliation', '') or '').strip(),
                }

            desc = str(row.get('Citing_Description', '') or '').strip()
            citing = str(row.get('Citing_Paper', '') or '').strip()
            paper_title = str(row.get('Paper_Title', '') or '').strip()
            if desc and desc.upper() not in ("NONE", ""):
                descriptions.append(desc)
                citing_pairs.append({
                    "paper_title": paper_title,
                    "citing_paper": citing,
                    "description": desc,
                })

        papers = sorted(papers_by_key.values(), key=lambda p: -p["citations"])
        total_papers = len(papers_by_key)

        seen_citing = set()
        unique_citing_papers = []
        for cp in citing_pairs:
            name = cp["citing_paper"].strip()
            if name and name not in seen_citing:
                seen_citing.add(name)
                unique_citing_papers.append(name)

        self_citation_count = len(self_citing_keys)
        return papers, total_papers, descriptions, citing_pairs, unique_citing_papers, self_citation_count

    def _load_renowned_scholars(self, all_path: Path, top_path: Path):
        """Load from two separate files."""
        def read_file(path: Path):
            if not path.exists():
                return []
            try:
                df = pd.read_excel(path)
            except Exception as e:
                self.log(f"  [读取学者文件失败] {path}: {e}")
                return []
            result = []
            for _, row in df.iterrows():
                name = str(row.get('Name', '') or '')
                if not name:
                    continue
                result.append({
                    "name": name,
                    "institution": str(row.get('Institution', '') or ''),
                    "country": str(row.get('Country', '') or ''),
                    "job": str(row.get('Job', '') or ''),
                    "title": str(row.get('Title', '') or ''),
                    "paper_title": str(row.get('PaperTitle', '') or ''),
                    "level": row.get('两院院士/其他院士/Fellow', ''),
                })
            return result

        all_scholars = read_file(all_path)
        top_scholars = read_file(top_path)
        top_names = set(s["name"] for s in top_scholars)
        for s in all_scholars:
            s["is_top"] = s["name"] in top_names
        return top_scholars, all_scholars

    def _compute_institution_stats(self, papers):
        """
        Match each citing paper's institution/affiliation fields against
        _FAMOUS_INSTITUTIONS keyword list.

        Returns dict[category, list[tuple[inst_name, list[paper_titles]]]]
        sorted by paper count desc within each category.
        Only categories with >=1 match are included.

        Example:
        {
            "国际科技企业": [("Google", ["Paper A", "Paper B"]), ("OpenAI", ["Paper C"])],
            "国内科技企业": [("华为", ["Paper D"])],
        }
        """
        _CATEGORY_ORDER = ["国际科技企业", "国内科技企业", "海外顶尖高校", "国内顶尖高校/机构"]
        inst_papers: dict = {}
        for p in papers:
            inst_raw = (p.get("institution", "") or "")
            affil_raw = (p.get("author_affiliation", "") or "")
            text = (inst_raw + " " + affil_raw).lower().strip()
            title = (p.get("title", "") or "").strip()
            for inst_name, info in _FAMOUS_INSTITUTIONS.items():
                if any(kw in text for kw in info["keywords"]):
                    if inst_name not in inst_papers:
                        inst_papers[inst_name] = set()
                    if title:
                        inst_papers[inst_name].add(title)
        grouped: dict = {}
        for cat in _CATEGORY_ORDER:
            entries = []
            for inst_name, info in _FAMOUS_INSTITUTIONS.items():
                if info["category"] == cat and inst_name in inst_papers:
                    entries.append((inst_name, sorted(inst_papers[inst_name])))
            entries.sort(key=lambda x: -len(x[1]))
            if entries:
                grouped[cat] = entries
        return grouped

    # ─────────────────────────────────────────────────────────────
    # Stats
    # ─────────────────────────────────────────────────────────────
    def _compute_stats(self, papers, total_papers, top_scholars, all_scholars):
        year_counter = Counter(p["year"] for p in papers if p["year"] is not None)
        all_years = sorted(year_counter.keys())

        # Country from First_Author_Country (all citing papers)
        def _valid_country(c):
            s = str(c).strip()
            return s not in ('', 'nan', 'None', 'NaN')

        country_counter_papers = Counter(
            p["country"] for p in papers if p.get("country") and _valid_country(p["country"])
        )

        # Country from all renowned scholars (deduplicated)
        seen_renowned = set()
        country_counter_renowned = Counter()
        level_counter = Counter()
        for s in all_scholars:
            if s["name"] in seen_renowned:
                continue
            seen_renowned.add(s["name"])
            if s["country"] and _valid_country(s["country"]):
                country_counter_renowned[s["country"]] += 1
            lv = s["level"]
            if lv and "院士" in str(lv) and "其他" not in str(lv) and "Fellow" not in str(lv):
                level_counter["两院院士"] += 1
            elif lv == "Fellow":
                level_counter["Fellow"] += 1
            elif lv and "其他院士" in str(lv):
                level_counter["其他院士"] += 1
            else:
                level_counter["其他知名学者"] += 1

        # Country from top scholars (deduplicated)
        seen_top = set()
        country_counter_top = Counter()
        for s in top_scholars:
            if s["name"] in seen_top:
                continue
            seen_top.add(s["name"])
            if s["country"] and _valid_country(s["country"]):
                country_counter_top[s["country"]] += 1

        unique_scholars = len(set(s["name"] for s in all_scholars))
        fellow_count = len(set(s["name"] for s in top_scholars))
        country_count = len(country_counter_papers) or len(country_counter_renowned)
        max_cit = max((p["citations"] for p in papers), default=0)
        total_cit = sum(p["citations"] for p in papers)

        return {
            "year_counter": year_counter,
            "all_years": all_years,
            "country_counter": country_counter_papers,       # primary (for legacy compat)
            "country_counter_papers": country_counter_papers,
            "country_counter_renowned": country_counter_renowned,
            "country_counter_top": country_counter_top,
            "level_counter": level_counter,
            "unique_scholars": unique_scholars,
            "fellow_count": fellow_count,
            "country_count": country_count,
            "max_cit": max_cit,
            "total_cit": total_cit,
            "total_papers": total_papers,
            "unique_papers": len(papers),
        }

    # ─────────────────────────────────────────────────────────────
    # LLM analysis modules
    # ─────────────────────────────────────────────────────────────
    def _analyze_keywords(self, titles):
        self.log("  → 提取关键词...")
        titles_str = "\n".join(f"- {t}" for t in titles)
        prompt = f"""以下是引用某目标论文的施引文献（引用论文）的标题列表，请分析这批施引文献所覆盖的研究方向，提取最具代表性的关键词/关键短语。注意：这些关键词反映的是施引文献群体的研究范围，而非目标论文本身：

{titles_str}

要求：
1. 提取 12-20 个最具代表性的关键词或短语（可中英文混合）
2. 每个关键词设置 1-10 的权重（出现频率+重要性综合判断，10最高）
3. 为每个关键词设置一个分类，分类名称根据实际研究领域自行确定（如：理论框架/研究方法/研究对象/核心概念/应用场景等，不限于计算机领域用语）
4. 如果关键词是英文，必须同时提供中文翻译（keyword_cn字段）；如果本身是中文则keyword_cn与keyword相同
5. 直接返回 JSON 数组，格式如下，不要任何其他文字：

[
  {{"keyword": "关键词示例", "keyword_cn": "中文翻译", "weight": 9, "category": "分类名称"}},
  {{"keyword": "另一关键词", "keyword_cn": "中文翻译", "weight": 8, "category": "分类名称"}}
]"""
        result = self._llm_json(prompt)
        if isinstance(result, list) and result:
            return result
        # Fallback: simple word frequency (supports both Latin and Chinese)
        en_stopwords = {"with", "from", "into", "that", "this", "for", "and", "the",
                        "via", "based", "using", "towards", "toward"}
        words = []
        for t in titles:
            # Extract English words (4+ chars)
            en_words = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", t) if w.lower() not in en_stopwords]
            words.extend(en_words)
            # Extract Chinese 2-character compounds
            cn_words = re.findall(r"[\u4e00-\u9fff]{2,4}", t)
            words.extend(cn_words)
        freq = Counter(words)
        top = freq.most_common(20)
        return [{"keyword": w, "keyword_cn": w, "weight": min(10, c + 3), "category": "关键词"}
                for w, c in top[:15]]

    def _analyze_citation_descriptions(self, descriptions, citing_pairs):
        self.log("  → 分析引用描述...")
        pairs_sample = citing_pairs[:]
        descs_text = "\n\n".join(
            f"【引用{i+1}】论文:《{p['citing_paper'][:60]}》\n描述摘要: {p['description'][:400]}"
            for i, p in enumerate(pairs_sample)
        )
        prompt = f"""以下是多篇论文引用目标论文时的引用描述信息（共 {len(descriptions)} 条，以下展示部分样本）：

{descs_text}

请对这些引用描述进行多维度分析，直接返回如下 JSON 格式（不要任何其他文字或Markdown）：

{{
  "citation_types": [
    {{"type": "类型名称（根据实际内容确定，如：理论借鉴/文献综述/批评探讨/实证支撑/正面肯定/背景铺垫等，不限于技术领域术语）", "count": 数量, "description": "简要说明"}},
    ...
  ],
  "citation_positions": [
    {{"position": "章节位置（根据论文实际结构确定，如：Introduction/Literature Review/Methodology/Discussion/Conclusion等，或中文章节名）", "count": 数量}},
    ...
  ],
  "citation_themes": [
    {{"theme": "核心主题短语（5-10个字）", "frequency": 1-10}},
    ...最多8个
  ],
  "sentiment_distribution": {{
    "positive": 正面引用占比（0-100整数），
    "neutral": 中性引用占比,
    "critical": 批评性引用占比
  }},
  "key_findings": [
    "发现句1（仅描述引用描述中实际观察到的现象，如引用位置、引用目的、引用频率等，不超过60字）",
    "发现句2",
    "发现句3"
  ],
  "citation_depth": {{
    "core_citation": 核心引用（作为主要方法依据）占比（0-100整数）,
    "reference_citation": 参考引用（作为背景或对比）占比,
    "supplementary_citation": 补充说明占比
  }}
}}

【分析原则——必须严格遵守】
1. sentiment_distribution 判断标准：
   - 正面（positive）：原文中存在明确的积极评价词汇，如 "state-of-the-art"、"pioneering"、"significantly outperforms"、"novel and effective"、"强力超越" 等，才计入正面；
   - 中性（neutral）：客观陈述、方法描述、背景介绍、转述结论等，均归为中性；
   - 批评（critical）：指出局限性、缺点或提出质疑的描述。
   - 注意：大多数学术引用是中性的，正面引用通常是少数，切勿将客观描述误判为正面。
2. key_findings 只陈述从引用描述中能直接观察到的事实，不做主观评价或过度解读，不使用"显著""重要""核心""不可忽视"等渲染性词汇。
3. 所有数量和占比基于实际样本估算，不同维度的占比之和为100。"""
        result = self._llm_json(prompt)
        if isinstance(result, dict) and "citation_types" in result:
            return result
        return {
            "citation_types": [
                {"type": "理论借鉴", "count": 8, "description": "借鉴理论框架或分析视角"},
                {"type": "文献综述", "count": 6, "description": "作为领域背景或综述引用"},
                {"type": "实证支撑", "count": 3, "description": "作为论据或比较参照"},
            ],
            "citation_positions": [
                {"position": "Introduction", "count": 7},
                {"position": "Related Work", "count": 8},
                {"position": "Experiments", "count": 4},
            ],
            "citation_themes": [
                {"theme": "视觉感知", "frequency": 8},
                {"theme": "领域泛化", "frequency": 7},
                {"theme": "基础模型", "frequency": 9},
            ],
            "sentiment_distribution": {"positive": 20, "neutral": 70, "critical": 10},
            "key_findings": [
                "引用多集中在Introduction和Related Work章节",
                "引用方式以方法描述和背景铺垫为主",
                "部分引用将该论文作为对比基线",
            ],
            "citation_depth": {"core_citation": 35, "reference_citation": 45, "supplementary_citation": 20},
        }

    def _generate_prediction(self, papers, stats):
        self.log("  → 生成影响力预测...")
        year_dist = dict(stats["year_counter"])
        top_papers = papers[:3]
        context = f"""目标论文的引用情况数据：
- 引用论文总数：{stats['total_papers']} 篇
- 不同期刊/来源的引用论文：{stats['unique_papers']} 篇
- 引用论文年份分布：{year_dist}
- 引用论文总被引量（各引用论文自身的被引）：{stats['total_cit']}
- 引用该论文的知名学者数：{stats['unique_scholars']}（其中院士/Fellow {stats['fellow_count']} 位）
- 最高单篇被引量：{stats['max_cit']}
- 引用最多的论文：{top_papers[0]['title'][:60] if top_papers else ''}（被引{top_papers[0]['citations'] if top_papers else 0}次）
"""
        now_year = datetime.now().year
        has_now_year = stats["year_counter"].get(now_year, 0) > 0
        actual_cutoff = now_year if has_now_year else now_year - 1
        forecast_y1 = now_year        # always predict current year (2026)
        forecast_y2 = now_year + 1   # always predict next year (2027)
        now_year_note = (f"数据中已包含 {now_year} 年的实际引用数据，"
                         f"请将其纳入 actual，同时也为 {now_year} 年提供 forecast 预测值。") if has_now_year else ""
        prompt = f"""{context}
当前年份为 {now_year} 年。{now_year_note}请对该目标论文的引用趋势进行预测分析，综合运用历史数据推断未来引用走势。重要原则：
1. actual 列填入各年实际数据（直到 {actual_cutoff} 年）；forecast 列必须包含 {forecast_y1} 年和 {forecast_y2} 年的预测值（若 {now_year} 已有实际数据，actual 和 forecast 可在该年同时非 null）
2. 预测方法不限于线性外推，可根据趋势特征灵活选用（线性、指数增长、平滑等），客观反映数据规律
3. 如果近3年引用量**持续增长**（每年同比增速 > 20%），可以适当乐观预测，年增速上限可达 +80%（快速发展领域可能更高，但须以数据为依据）
4. 如果近几年引用量**已出现下降**，必须保守预测，如实反映下降趋势，不得人为拔高
5. impact_scores 反映的是通过施引文献所展现的影响力扩散潜力，分数应客观合理（满分100，普通论文60以下，顶尖论文80以上），不要全部给出夸张的高分

直接返回如下 JSON 格式（不要任何其他文字）：

{{
  "trend_data": {{
    "labels": ["年份1", "年份2", ...],
    "actual": [实际值或null, ...],
    "forecast": [null或预测值, ...]
  }},
  "prediction_metrics": [
    {{"label": "预计{forecast_y1}年引用量", "value": "~XXX", "note": "预测值（15字内）"}},
    {{"label": "预计{forecast_y2}年引用量", "value": "~XXX", "note": "预测值（15字内）"}},
    {{"label": "引用年增速 (YoY)", "value": "+XX%或-XX%", "note": "基于近两年数据（15字内）"}}
  ],
  "impact_scores": [
    {{"label": "维度标签1", "score": 0-100, "color_class": "fill-cyan"}},
    {{"label": "维度标签2", "score": 0-100, "color_class": "fill-green"}},
    {{"label": "维度标签3", "score": 0-100, "color_class": "fill-purple"}},
    {{"label": "维度标签4", "score": 0-100, "color_class": "fill-orange"}}
  ],
  "prediction_commentary": "专业的影响力预测综合评语（中文，100-150字，客观有依据，如实反映趋势，不夸大）"
}}

要求：
- trend_data 的 labels 从数据最早年份起直到 {forecast_y2} 年；actual 列在有实际数据的年份填入真实值（其余为 null）；forecast 列在 {forecast_y1} 年和 {forecast_y2} 年填入预测值（其余为 null）。
- impact_scores 的4个维度标签须根据目标论文所在学科领域（人文/社科/理工/医学等）自行确定，选择最能反映该领域影响力的维度，禁止套用计算机/AI领域专有标签（如"开源社区关注度"）。"""
        result = self._llm_json(prompt)
        if isinstance(result, dict) and "trend_data" in result:
            return result
        # Fallback: linear regression from historical data, trend-aware
        years = sorted(stats["year_counter"].keys())
        now_year = datetime.now().year
        has_now_year = stats["year_counter"].get(now_year, 0) > 0
        if not years:
            years = [now_year - 1, now_year]
        min_y, max_y = years[0], years[-1]
        hist_values = [stats["year_counter"].get(y, 0) for y in years]
        n_hist = len(years)
        if n_hist >= 2:
            xv = list(range(n_hist))
            mx = sum(xv) / n_hist
            my = sum(hist_values) / n_hist
            denom = sum((xi - mx) ** 2 for xi in xv) or 1.0
            slope = sum((xi - mx) * (yi - my) for xi, yi in zip(xv, hist_values)) / denom
            intercept = my - slope * mx
            # Detect consistent recent growth: check last 3 years
            recent = hist_values[-3:] if n_hist >= 3 else hist_values
            rising = all(recent[k+1] > recent[k] for k in range(len(recent)-1)) if len(recent) >= 2 else False
            if rising and len(recent) >= 2 and recent[-2] > 0:
                recent_yoy = (recent[-1] - recent[-2]) / recent[-2]
                if recent_yoy > 0.2:
                    # Boost slope proportionally (up to 80% YoY cap)
                    capped_yoy = min(recent_yoy, 0.8)
                    slope = max(slope, hist_values[-1] * capped_yoy)
            next1 = max(0, round(slope * n_hist + intercept))
            next2 = max(0, round(slope * (n_hist + 1) + intercept))
            if len(hist_values) >= 2 and hist_values[-2] > 0:
                yoy = round(100 * (hist_values[-1] - hist_values[-2]) / hist_values[-2])
            else:
                yoy = round(100 * slope / max(my, 1))
            yoy_str = f"+{yoy}%" if yoy >= 0 else f"{yoy}%"
        else:
            slope = 0.0
            next1 = hist_values[0] if hist_values else 0
            next2 = hist_values[0] if hist_values else 0
            yoy_str = "~0%"
        forecast_start = now_year        # always forecast from current year
        forecast_end = now_year + 1
        labels = [str(y) for y in range(min_y, forecast_end + 1)]
        actual, forecast = [], []
        for y in range(min_y, forecast_end + 1):
            if y < now_year:
                actual.append(stats["year_counter"].get(y, 0))
                forecast.append(None)
            elif y == now_year:
                actual.append(stats["year_counter"].get(y, 0) if has_now_year else None)
                forecast.append(next1)
            else:
                actual.append(None)
                forecast.append(next2)
        trend_desc = "平稳扩散" if abs(slope) < 3 else ("积极增长" if slope > 0 else "缓和调整")
        return {
            "trend_data": {"labels": labels, "actual": actual, "forecast": forecast},
            "prediction_metrics": [
                {"label": f"预计{forecast_start}年引用量", "value": f"~{next1}", "note": "趋势外推估算"},
                {"label": f"预计{forecast_end}年引用量", "value": f"~{next2}", "note": "趋势外推估算"},
                {"label": "引用年增速 (YoY)", "value": yoy_str, "note": "基于近两年实际数据"},
            ],
            "impact_scores": [
                {"label": "理论创新影响力", "score": min(78, max(30, 45 + stats["fellow_count"] * 3)), "color_class": "fill-cyan"},
                {"label": "跨学科扩散潜力", "score": min(72, max(25, 35 + stats["total_papers"] // 8)), "color_class": "fill-green"},
                {"label": "政策与实践参考价值", "score": min(68, max(20, 30 + stats["country_count"] * 4)), "color_class": "fill-purple"},
                {"label": "国际学界认可度", "score": min(70, max(25, 32 + stats["unique_scholars"] // 4)), "color_class": "fill-orange"},
            ],
            "prediction_commentary": (
                f"基于历史引用数据的线性回归分析，该论文年均引用增量约 {round(slope, 1)} 篇，"
                f"目前共有 {stats['total_papers']} 篇施引文献、{stats['unique_scholars']} 位知名学者引用，"
                f"学术影响力整体处于{trend_desc}阶段。"
            ),
        }

    def _generate_insights(self, papers, stats, citation_analysis):
        self.log("  → 生成数据洞察...")
        year_dist = dict(stats["year_counter"])
        country_dist = dict(stats["country_counter"].most_common(5))
        key_findings = citation_analysis.get("key_findings", [])
        prompt = f"""基于以下学术引用数据，生成4条专业、精炼的数据洞察。

数据概况：
- 引用论文年份分布：{year_dist}
- 引用学者来源国家（前5）：{country_dist}
- 总知名学者数：{stats['unique_scholars']}（其中院士/Fellow {stats['fellow_count']}人）
- 引用描述分析发现：{key_findings}
- 引用论文最高被引量：{stats['max_cit']}

直接返回 JSON 数组，不要其他文字：

[
  {{
    "color": "teal",
    "icon": "📈",
    "title": "洞察标题（不超过20字）",
    "body": "洞察正文（80-120字，包含具体数字，要客观、专业、有价值）"
  }},
  {{"color": "sage", "icon": "🌏", "title": "...", "body": "..."}},
  {{"color": "amber", "icon": "🏆", "title": "...", "body": "..."}},
  {{"color": "violet", "icon": "🔬", "title": "...", "body": "..."}}
]

color 必须从 ["teal", "sage", "amber", "violet"] 中选择（每种各一个），body 中可用 <strong> 标签加粗关键数据。"""
        result = self._llm_json(prompt)
        if isinstance(result, list) and len(result) >= 4:
            return result[:4]
        # Fallback
        top_year = max(stats["year_counter"], key=stats["year_counter"].get) if stats["year_counter"] else 2025
        top_year_n = stats["year_counter"].get(top_year, 0)
        total = stats["unique_papers"]
        cn_pct = round(100 * stats["country_counter"].get("中国", 0) / max(stats["unique_scholars"], 1))
        return [
            {"color": "teal", "icon": "📈", "title": "引用时间：扩散势头强劲",
             "body": f"{total}篇引用论文中，{top_year}年发表的占比最高（{top_year_n}篇），表明该论文正处于影响力快速攀升期。"},
            {"color": "sage", "icon": "🌏", "title": "地域：以中国为核心，国际影响初现",
             "body": f"引用学者中约 <strong>{cn_pct}%</strong> 来自中国，同时覆盖{stats['country_count']}个国家/地区，国际认可度正在建立。"},
            {"color": "amber", "icon": "🏆", "title": "学者层次：高权威背书",
             "body": f"引用学者中包含<strong>{stats['fellow_count']}位院士/Fellow</strong>，共来自{stats['unique_scholars']}位知名学者，表明该论文获得顶尖学术圈的广泛认可。"},
            {"color": "violet", "icon": "🔬", "title": "引用方式：综述与借鉴为主",
             "body": "引用描述分析显示，引用者主要将该论文作为理论依据或背景综述引用，在相关研究中被广泛参考。"},
        ]

    def _summarize_citation_descriptions(self, descriptions: list, citing_pairs: list) -> str:
        self.log("  → 生成引用描述综合总结...")
        if not descriptions:
            return ""
        sample = citing_pairs[:60]
        descs_text = "\n\n".join(
            f"【引用{i+1}】引用论文：《{p['citing_paper'][:80]}》\n"
            f"引用描述：{p['description'][:500]}"
            for i, p in enumerate(sample)
        )
        total = len(descriptions)
        prompt = f"""以下是共 {total} 篇论文在引用某目标论文时的引用描述（以下展示 {len(sample)} 条样本）：

{descs_text}

请基于上述引用描述，撰写一份结构化的引用描述综合分析文档。

严格约束（必须遵守）：
- 只描述引用描述中实际出现的内容，不添加来源文本中不存在的判断或形容词
- 不使用「广受认可」「重要贡献」「具有重要意义」「突破性」等主观评价词语，除非这些词语直接出现在引用描述原文中
- 不对论文价值作整体性正面或负面定性，只描述引用者实际如何使用该论文
- 如引用描述中存在批评性、保留性或中性描述，应如实体现，不得遮蔽
- 第三节直接摘录原文，不加任何主观评语
- 描述引用规模时，必须使用「在 {total} 篇有效的引用样本中」这一表述，禁止使用「在提供的」「在给出的」等措辞

使用以下 Markdown 结构（按顺序，不得改变节标题）：

## 引用规模与分布
（2-3 句话，说明引用总数、来源论文数量、涉及的研究领域或方向，仅陈述事实）

## 主要引用用途
（描述引用者如何实际使用该论文：作为方法依据、背景综述、对比基准、数据来源等，举例说明，不作价值判断）

## 代表性引用描述原文
（直接摘录 3-4 条具有代表性的引用描述原文，使用 > 引用块格式，覆盖不同用途或语气，不加评语）

## 综合说明
（2-3 句话，基于以上描述，客观归纳这些引用共同呈现的使用模式，不超越文本范围作推断）

全程使用中文，语言简洁中性，总长度 300-500 字。
直接输出 Markdown 文本，不要代码块包裹。"""
        result = self._llm(prompt).strip()
        return result if result else ""

    # ─────────────────────────────────────────────────────────────
    # HTML builder
    # ─────────────────────────────────────────────────────────────
    _CSS = """
:root {
  --bg: #f7f8fc; --bg2: #eef0f8; --surface: #ffffff; --surface2: #f0f2fa;
  --border: #dde2f0; --border-accent: #c5cceb;
  --teal: #3b82c4; --teal-light: #e8f1fb; --teal-muted: #6ba3d6;
  --sage: #4caf8a; --sage-light: #e6f6f0;
  --amber: #d4892a; --amber-light: #fdf3e3;
  --violet: #7c5cbf; --violet-light: #f0ecfb;
  --rose: #c45a5a; --rose-light: #fdeaea;
  --text: #2d3748; --text-muted: #718096; --text-light: #a0aec0; --text-bright: #1a202c;
  --shadow-sm: 0 1px 4px rgba(0,0,0,0.06);
  --shadow: 0 4px 16px rgba(0,0,0,0.08);
  --shadow-lg: 0 8px 32px rgba(0,0,0,0.10);
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body { font-family: 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg); color: var(--text); min-height: 100vh; font-size: 14px; line-height: 1.6; }
.header { background: linear-gradient(135deg, #1e3a5f 0%, #2d5a9e 50%, #1e4d7b 100%);
  padding: 56px 64px 48px; position: relative; overflow: hidden; }
.header::before { content: ''; position: absolute; inset: 0;
  background: radial-gradient(ellipse 60% 80% at 85% 20%, rgba(100,160,255,0.15) 0%, transparent 60%),
              radial-gradient(ellipse 40% 60% at 10% 80%, rgba(60,180,130,0.10) 0%, transparent 55%);
  pointer-events: none; }
.header-eyebrow { font-size: 11px; font-weight: 600; letter-spacing: 3px; text-transform: uppercase;
  color: rgba(180,210,255,0.75); margin-bottom: 14px; }
.header h1 { font-size: 36px; font-weight: 700; color: #fff; line-height: 1.15; margin-bottom: 14px; }
.header h1 em { font-style: normal; color: #7db8f5; }
.header-subtitle { font-size: 13.5px; color: rgba(200,220,255,0.70); max-width: 580px; line-height: 1.75; }
.header-divider { position: absolute; bottom: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, transparent, rgba(120,180,255,0.6) 30%, rgba(80,200,160,0.5) 70%, transparent); }
.header-targets { display: flex; flex-direction: column; gap: 6px; margin: 14px 0 16px; }
.header-target-item { display: flex; align-items: baseline; gap: 10px; }
.header-target-num { font-size: 10px; font-weight: 700; color: rgba(147,197,253,0.6);
  letter-spacing: 1px; flex-shrink: 0; }
.header-target-title { font-size: 15px; font-weight: 600; color: #e2eeff;
  line-height: 1.4; word-break: break-word; }
.stats-bar { background: var(--surface); border-bottom: 1px solid var(--border);
  display: flex; overflow-x: auto; box-shadow: var(--shadow-sm); }
.stat-item { flex: 1; min-width: 120px; padding: 22px 20px; text-align: center;
  border-right: 1px solid var(--border); transition: background .2s; }
.stat-item:last-child { border-right: none; }
.stat-item:hover { background: var(--teal-light); }
.stat-icon { font-size: 20px; margin-bottom: 6px; }
.stat-num { font-size: 28px; font-weight: 700; color: var(--teal); line-height: 1.1; }
.stat-label { font-size: 11px; color: var(--text-muted); margin-top: 4px; letter-spacing: .3px; }
.download-bar { background: var(--surface2); border-bottom: 1px solid var(--border);
  padding: 12px 48px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.download-bar span { font-size: 13px; color: var(--text-muted); }
.download-bar a { display: inline-block; padding: 5px 14px; border-radius: 6px; font-size: 12px;
  font-weight: 500; text-decoration: none; border: 1px solid var(--border);
  background: var(--surface); color: var(--teal); transition: .15s; }
.download-bar a:hover { background: var(--teal-light); border-color: var(--teal); }
.main { max-width: 1480px; margin: 0 auto; padding: 36px 48px; }
.section-header { display: flex; align-items: center; gap: 12px; margin: 40px 0 20px; }
.section-num { font-size: 11px; font-weight: 700; color: var(--teal); letter-spacing: 2px;
  background: var(--teal-light); padding: 2px 8px; border-radius: 4px; }
.section-title { font-size: 15px; font-weight: 600; color: var(--text-bright); }
.section-title[data-tooltip] { position: relative; cursor: default; }
.section-title[data-tooltip]::after {
  content: attr(data-tooltip);
  position: absolute; left: 50%; top: calc(100% + 6px); transform: translateX(-50%);
  background: rgba(20,30,50,0.95); color: rgba(200,220,255,0.92); font-size: 11.5px;
  font-weight: 400; white-space: nowrap; padding: 4px 10px; border-radius: 5px;
  border: 1px solid rgba(100,160,255,0.2); pointer-events: none;
  opacity: 0; transition: opacity 0.15s; z-index: 10;
}
.section-title[data-tooltip]:hover::after { opacity: 1; }
.section-divider { flex: 1; height: 1px; background: var(--border); }
.grid-3 { display: grid; grid-template-columns: repeat(3,1fr); gap: 20px; margin-bottom: 20px; }
.grid-2 { display: grid; grid-template-columns: repeat(2,1fr); gap: 20px; margin-bottom: 20px; }
.grid-1 { margin-bottom: 20px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 24px; box-shadow: var(--shadow-sm); transition: box-shadow .2s, transform .2s; }
.card:hover { box-shadow: var(--shadow); transform: translateY(-1px); }
.card-title { font-size: 12px; font-weight: 600; color: var(--text-muted); text-transform: uppercase;
  letter-spacing: .8px; padding-bottom: 12px; border-bottom: 1px solid var(--border);
  margin-bottom: 16px; display: flex; align-items: center; gap: 7px; }
.card-title-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--teal); flex-shrink: 0; }
.card-title-dot.sage { background: var(--sage); }
.card-title-dot.amber { background: var(--amber); }
.card-title-dot.violet { background: var(--violet); }
.badge { display: inline-block; padding: 2px 9px; border-radius: 20px; font-size: 11px;
  font-weight: 500; white-space: nowrap; }
.b-ys { background: #fff3e0; color: #d4892a; border: 1px solid #f0c070; }
.b-fw { background: var(--teal-light); color: var(--teal); border: 1px solid #b3d4f0; }
.b-ot { background: var(--sage-light); color: #357a62; border: 1px solid #a0d9c0; }
.b-nm { background: var(--bg2); color: var(--text-muted); border: 1px solid var(--border); }
.b-cn { background: #fff0f0; color: var(--rose); border: 1px solid #f0c0c0; }
.b-int { background: var(--violet-light); color: var(--violet); border: 1px solid #c9b8ec; }
.scholar-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.scholar-table thead th { padding: 10px 12px; text-align: left; font-size: 11px; font-weight: 600;
  color: var(--text-muted); text-transform: uppercase; letter-spacing: .5px;
  border-bottom: 2px solid var(--border); background: var(--bg2); }
.scholar-table tbody tr { border-bottom: 1px solid var(--border); transition: background .15s; }
.scholar-table tbody tr:hover { background: var(--teal-light); }
.scholar-table td { padding: 10px 12px; vertical-align: middle; }
.scholar-table .sname { font-weight: 600; color: var(--text-bright); cursor: default; }
.scholar-tt { position: fixed; z-index: 9999; max-width: 420px; background: #1e2a3a;
  border: 1px solid rgba(100,160,255,0.25); border-radius: 8px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.4); padding: 8px 6px; display: none; }
.scholar-tt-item { display: block; padding: 6px 10px; font-size: 12px; line-height: 1.5;
  color: rgba(200,220,255,0.85); border-radius: 5px; text-decoration: none;
  transition: background .12s; }
.scholar-tt-item:hover { background: rgba(100,160,255,0.12); color: #7dd8b0; }
.scholar-tt-item-nolink { color: rgba(180,200,240,0.6); cursor: default; }
.scholar-tt-item-nolink:hover { background: none; color: rgba(180,200,240,0.6); }
.sname-hover { text-decoration: underline dotted rgba(100,180,255,0.4); cursor: pointer; }
.scholar-table .stitle { color: var(--text-muted); font-size: 11.5px; line-height: 1.5; }
.desc-btn { font-size: 11px; padding: 3px 10px; border: 1px solid var(--border);
  border-radius: 4px; background: var(--surface); color: var(--teal); cursor: pointer;
  transition: .15s; white-space: nowrap; }
.desc-btn:hover { background: var(--teal-light); border-color: var(--teal); }
.desc-row td { background: var(--bg2); padding: 12px 16px;
  word-wrap: break-word; }
.paper-item { display: flex; align-items: center; gap: 14px; padding: 14px 16px;
  margin-bottom: 8px; border: 1px solid var(--border); border-radius: 8px;
  border-left: 4px solid var(--teal); background: var(--surface); transition: box-shadow .2s, background .2s; }
.paper-item:hover { background: var(--teal-light); box-shadow: var(--shadow-sm); }
.paper-rank { font-size: 20px; font-weight: 800; color: #c5cceb; min-width: 32px; text-align: center; }
.paper-info { flex: 1; }
.paper-ttl { font-size: 13px; font-weight: 500; color: var(--text-bright); line-height: 1.5; margin-bottom: 3px; }
.paper-meta { font-size: 11px; color: var(--text-muted); }
.paper-cit-box { text-align: right; min-width: 64px; }
.paper-cit-num { font-size: 24px; font-weight: 700; color: var(--teal); line-height: 1; }
.paper-cit-lbl { font-size: 10px; color: var(--text-light); margin-top: 2px; }
.kw-cloud { display: flex; flex-wrap: wrap; gap: 8px; padding: 4px 0; }
.kw-tag { padding: 5px 14px; border-radius: 20px; font-size: 12px; font-weight: 500; transition: transform .15s; cursor: default; }
.kw-tag:hover { transform: translateY(-2px); }
.cite-type-bar { display: flex; flex-direction: column; gap: 10px; }
.ctb-label { display: flex; justify-content: space-between; font-size: 12px; color: var(--text); margin-bottom: 4px; }
.ctb-label span:last-child { color: var(--text-muted); }
.ctb-track { height: 7px; background: var(--bg2); border-radius: 10px; overflow: hidden; }
.ctb-fill { height: 100%; border-radius: 10px; }
.fill-teal   { background: linear-gradient(90deg, #3b82c4, #6ba3d6); }
.fill-sage   { background: linear-gradient(90deg, #4caf8a, #7dcaaa); }
.fill-amber  { background: linear-gradient(90deg, #d4892a, #e8a84a); }
.fill-violet { background: linear-gradient(90deg, #7c5cbf, #a07fd8); }
.fill-rose   { background: linear-gradient(90deg, #c45a5a, #d88080); }
.fill-cyan   { background: linear-gradient(90deg, #0891b2, #06b6d4); }
.fill-green  { background: linear-gradient(90deg, #16a34a, #22c55e); }
.fill-purple { background: linear-gradient(90deg, #7c3aed, #a855f7); }
.fill-orange { background: linear-gradient(90deg, #ea580c, #f97316); }
.theme-tags { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; }
.theme-tag { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
  padding: 5px 12px; font-size: 12px; color: var(--text); transition: .15s; }
.theme-tag:hover { border-color: var(--teal); background: var(--teal-light); color: var(--teal); }
.sentiment-ring { display: flex; gap: 12px; margin-top: 8px; flex-wrap: wrap; }
.sring-item { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-muted); }
.sring-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.findings-list { margin-top: 4px; }
.finding-item { display: flex; gap: 10px; padding: 9px 0; border-bottom: 1px solid var(--border);
  font-size: 12.5px; color: var(--text); line-height: 1.6; }
.finding-item:last-child { border-bottom: none; }
.finding-num { width: 20px; height: 20px; border-radius: 50%; background: var(--teal-light);
  color: var(--teal); font-size: 10px; font-weight: 700; display: flex; align-items: center;
  justify-content: center; flex-shrink: 0; margin-top: 1px; }
.prediction-band { background: linear-gradient(135deg, #1e3a5f 0%, #1a4a8f 100%);
  border-radius: 12px; padding: 28px; margin-bottom: 20px; position: relative; overflow: hidden; }
.prediction-band::before { content: ''; position: absolute; inset: 0;
  background: radial-gradient(ellipse 50% 80% at 90% 10%, rgba(100,180,255,0.12) 0%, transparent 60%);
  pointer-events: none; }
.pred-grid { display: grid; grid-template-columns: repeat(2,1fr); gap: 20px; }
.pred-card { background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.12);
  border-radius: 8px; padding: 20px; backdrop-filter: blur(4px); }
.pred-card-title { font-size: 13px; font-weight: 600; color: #e0eeff; margin-bottom: 14px;
  display: flex; align-items: center; gap: 8px; }
.pred-tag { font-size: 9px; padding: 2px 7px; border-radius: 4px;
  background: rgba(100,180,255,0.2); color: #90c0ff; letter-spacing: 1px; }
.pred-metric { display: flex; justify-content: space-between; align-items: center;
  padding: 7px 0; border-bottom: 1px solid rgba(255,255,255,0.08); }
.pred-metric:last-child { border-bottom: none; }
.pred-metric-label { font-size: 11.5px; color: rgba(180,210,255,0.75); }
.pred-metric-val { font-size: 13px; font-weight: 700; color: #7dd8b0; }
.pred-metric-note { font-size: 10px; color: rgba(150,190,255,0.55); margin-top: 1px; }
.impact-bar-wrap { display: flex; flex-direction: column; gap: 11px; }
.impact-row-label { display: flex; justify-content: space-between; font-size: 11.5px;
  color: rgba(180,210,255,0.8); margin-bottom: 4px; }
.impact-track { height: 6px; background: rgba(255,255,255,0.08); border-radius: 10px; overflow: hidden; }
.impact-fill { height: 100%; border-radius: 10px; }
.pred-commentary { margin-top: 14px; padding: 12px 14px; background: rgba(255,255,255,0.05);
  border: 1px solid rgba(100,180,255,0.2); border-radius: 6px; font-size: 12px;
  line-height: 1.75; color: rgba(200,225,255,0.75); }
.trend-wrap { position: relative; height: 200px; }
.insights-grid { display: grid; grid-template-columns: repeat(2,1fr); gap: 16px; margin-bottom: 40px; }
.insight-card { border-radius: 10px; padding: 22px 24px; border: 1px solid var(--border);
  background: var(--surface); border-left: 5px solid var(--teal); transition: box-shadow .2s; }
.insight-card:hover { box-shadow: var(--shadow); }
.insight-card.sage  { border-left-color: var(--sage); }
.insight-card.amber { border-left-color: var(--amber); }
.insight-card.violet{ border-left-color: var(--violet); }
.insight-card h4 { font-size: 13.5px; font-weight: 600; color: var(--teal); margin-bottom: 8px; }
.insight-card.sage h4  { color: var(--sage); }
.insight-card.amber h4 { color: var(--amber); }
.insight-card.violet h4{ color: var(--violet); }
.insight-card p { font-size: 12.5px; color: var(--text-muted); line-height: 1.75; }
.insight-card p strong { color: var(--text); }
.citing-papers-section { background: var(--surface); border-bottom: 1px solid var(--border);
  padding: 28px 48px; max-width: 100%; }
.citing-papers-header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.citing-papers-header-label { font-size: 11px; font-weight: 700; color: var(--teal); letter-spacing: 2px;
  background: var(--teal-light); padding: 3px 10px; border-radius: 4px; white-space: nowrap; }
.citing-papers-header-title { font-size: 14px; font-weight: 600; color: var(--text-bright); }
.citing-papers-header-count { font-size: 12px; color: var(--text-muted); background: var(--bg2);
  border: 1px solid var(--border); border-radius: 12px; padding: 2px 10px; white-space: nowrap; }
.citing-papers-header-divider { flex: 1; height: 1px; background: var(--border); }
.citing-papers-desc { font-size: 12px; color: var(--text-muted); margin-bottom: 16px;
  padding: 10px 14px; background: linear-gradient(90deg, var(--teal-light), transparent);
  border-left: 3px solid var(--teal); border-radius: 0 6px 6px 0; line-height: 1.7; }
.citing-papers-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 8px; max-height: 320px; overflow-y: auto; padding-right: 4px; }
.citing-paper-item { display: flex; align-items: center; gap: 10px; padding: 8px 12px;
  border: 1px solid var(--border); border-radius: 6px; background: var(--bg); transition: background .15s, border-color .15s; }
.citing-paper-item:hover { background: var(--teal-light); border-color: var(--teal-muted); }
.citing-paper-num { font-size: 11px; font-weight: 700; color: var(--text-light);
  min-width: 22px; text-align: right; flex-shrink: 0; }
.citing-paper-name { font-size: 12.5px; color: var(--text); line-height: 1.45; word-break: break-word; }
.footer { text-align: center; padding: 28px; font-size: 11px; color: var(--text-light);
  border-top: 1px solid var(--border); letter-spacing: .5px; }
@keyframes fadeUp { from { opacity:0; transform:translateY(16px) } to { opacity:1; transform:none } }
.card, .insight-card, .paper-item { animation: fadeUp .4s ease both; }
.stat-sub { font-size: 10px; color: var(--text-light); margin-top: 2px; }
.inst-section { margin-bottom: 20px; }
.inst-category-label { font-size: 10px; font-weight: 700; letter-spacing: 1.5px;
  text-transform: uppercase; color: var(--text-muted); margin-bottom: 10px; }
.inst-tags { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-start; }
.inst-tag { padding: 5px 12px; border-radius: 20px; font-size: 12.5px; font-weight: 500;
  cursor: pointer; transition: opacity .15s, transform .15s; user-select: none; }
.inst-tag:hover { opacity: 0.85; transform: translateY(-1px); }
.inst-count { font-size: 10px; font-weight: 400; opacity: 0.8; margin-left: 4px; }
.inst-paper-list { display: none; width: 100%; margin-top: 8px; padding: 10px 14px;
  background: var(--bg2); border-radius: 6px; border-left: 3px solid var(--border-accent); }
.inst-paper-item { font-size: 12px; color: var(--text-muted); padding: 3px 0; line-height: 1.5; }
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border-accent); border-radius: 3px; }
/* ── Paper Detail Items (PDI) ── */
.pdi-scroll { max-height: 600px; overflow-y: auto; padding-right: 4px; }
.pdi-scroll::-webkit-scrollbar { width: 5px; }
.pdi-scroll::-webkit-scrollbar-thumb { background: var(--border-accent); border-radius: 3px; }
.pdi-item { display: flex; gap: 12px; padding: 14px; margin-bottom: 8px;
  border: 1px solid var(--border); border-radius: 8px; border-left: 4px solid var(--teal);
  background: var(--surface); transition: box-shadow .2s, background .2s; }
.pdi-item:hover { background: var(--teal-light); box-shadow: var(--shadow-sm); }
.pdi-rank { font-size: 17px; font-weight: 800; min-width: 28px; text-align: center;
  color: #c5cceb; line-height: 1; padding-top: 2px; }
.pdi-body { flex: 1; min-width: 0; }
.pdi-title { font-size: 12.5px; font-weight: 600; color: var(--text-bright); line-height: 1.5; margin-bottom: 4px; }
.pdi-title-link { color: var(--teal); text-decoration: none; }
.pdi-title-link:hover { text-decoration: underline; }
.pdi-authors { font-size: 11px; color: var(--text-muted); margin-bottom: 3px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pdi-meta-row { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.pdi-inst-tag { font-size: 10.5px; color: var(--text-muted); background: var(--bg2);
  border: 1px solid var(--border); border-radius: 4px; padding: 1px 7px; }
.pdi-country-tag { font-size: 10.5px; color: var(--violet); background: var(--violet-light);
  border: 1px solid #c9b8ec; border-radius: 4px; padding: 1px 7px; }
.pdi-footer { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.pdi-year { font-size: 11px; background: var(--bg2); border: 1px solid var(--border);
  border-radius: 4px; padding: 1px 7px; color: var(--text-muted); }
.pdi-cit { font-size: 12px; }
.pdi-affil-btn { font-size: 10.5px; padding: 2px 9px; border: 1px solid var(--border);
  border-radius: 4px; background: var(--surface); color: var(--teal); cursor: pointer;
  transition: .15s; white-space: nowrap; }
.pdi-affil-btn:hover { background: var(--teal-light); border-color: var(--teal); }
.pdi-affil-box { margin-top: 8px; padding: 10px 12px; background: var(--bg2);
  border-radius: 6px; font-size: 11px; color: var(--text); line-height: 1.7;
  word-break: break-word; border: 1px solid var(--border); }
.md-content { font-size: 12.5px; line-height: 1.75; color: var(--text); }
.md-content p { margin: 0 0 7px; }
.md-content p:last-child { margin-bottom: 0; }
.md-content ul, .md-content ol { margin: 0 0 7px 20px; padding: 0; }
.md-content li { margin-bottom: 3px; }
.md-content strong { font-weight: 600; }
.md-content em { font-style: italic; color: var(--text-muted); }
.md-content h1, .md-content h2 { font-size: 13px; font-weight: 700; margin: 10px 0 5px; border-bottom: 1px solid var(--border); padding-bottom: 3px; }
.md-content h3, .md-content h4 { font-size: 12.5px; font-weight: 600; margin: 8px 0 4px; }
.md-content code { font-family: 'Fira Code', 'Consolas', monospace; font-size: 11px;
  background: var(--bg); padding: 1px 5px; border-radius: 4px; color: var(--teal); }
.md-content pre { background: var(--bg); border-radius: 6px; padding: 8px 12px;
  overflow-x: auto; margin: 6px 0; }
.md-content pre code { background: none; padding: 0; color: var(--text); }
.md-content blockquote { border-left: 3px solid var(--teal); margin: 6px 0;
  padding: 3px 10px; background: var(--teal-light); color: var(--text-muted); font-style: italic; border-radius: 0 4px 4px 0; }
.md-content hr { border: none; border-top: 1px solid var(--border); margin: 10px 0; }
.md-content a { color: var(--teal); text-decoration: underline; }
.cite-sum-card-title { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
.cite-sum-preview { font-size: 12.5px; color: var(--text-muted); line-height: 1.75; padding: 8px 0 10px; border-bottom: 1px solid var(--border); margin: 0; }
.cite-sum-body { padding-top: 14px; }
.cite-sum-body .md-content blockquote { border-left: 4px solid var(--teal); background: var(--teal-light); color: var(--text); font-style: normal; padding: 8px 14px; margin: 8px 0; border-radius: 0 6px 6px 0; }
.pdi-authors-pills { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 5px; }
.author-pill { display: inline-block; padding: 2px 9px; border-radius: 12px;
  font-size: 11px; background: var(--bg2); border: 1px solid var(--border);
  color: var(--text-muted); text-decoration: none; white-space: nowrap;
  transition: background .15s, border-color .15s; }
a.author-pill:hover { background: var(--teal-light); border-color: var(--teal); color: var(--teal); }
.kw-cn { font-size: 9px; color: inherit; opacity: 0.7; margin-left: 3px; }
/* ── Balanced card layout (fill available height) ── */
.card-flex { display: flex; flex-direction: column; }
.chart-fill-wrap { flex: 1; position: relative; min-height: 200px; }
.chart-fill-wrap canvas { position: absolute !important; inset: 0; width: 100% !important; height: 100% !important; }
.chart-center-wrap { flex: 1; display: flex; align-items: center; justify-content: center; width: 100%; padding: 8px 0; }
.chart-center-wrap canvas { max-width: min(100%, 320px) !important; }
"""

    @staticmethod
    def _truncate(s, n=50):
        return s[:n] + "…" if len(s) > n else s

    @staticmethod
    def _js(obj):
        return json.dumps(obj, ensure_ascii=False)

    @staticmethod
    def _level_badge(level):
        lv = str(level or "")
        if "院士" in lv and "其他" not in lv and "Fellow" not in lv:
            return "b-ys", "两院院士"
        if lv == "Fellow":
            return "b-fw", "Fellow"
        if "其他院士" in lv:
            return "b-ot", "其他院士"
        return "b-nm", "知名学者"

    @staticmethod
    def _country_badge(country):
        china_like = {"中国", "China", "中国香港", "中国澳门", "中国台湾"}
        if country in china_like:
            return f'<span class="badge b-cn">{country}</span>'
        return f'<span class="badge b-int">{country}</span>'

    def _build_html(self, papers, total_papers, top_scholars, all_scholars,
                    stats, keywords, citation_analysis, prediction, insights,
                    unique_citing_papers=None, download_filenames=None, citing_pairs=None,
                    canonical_titles=None, citation_summary="",
                    self_citation_count=0, institution_stats=None):
        # ── Citation summary collapsible card ──────────────────────────────────────
        if citation_summary:
            _cs_preview = ""
            for _line in citation_summary.split('\n'):
                _line = _line.strip()
                if _line and not _line.startswith('#') and not _line.startswith('>') \
                        and not _line.startswith('*') and not _line.startswith('-'):
                    _cs_preview = _line[:200] + ("…" if len(_line) > 200 else "")
                    break
            _cs_preview_safe = (_cs_preview
                                 .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
            n_descs = len(citing_pairs or [])
            citation_summary_html = (
                '\n<div class="card grid-1">\n'
                '  <div class="card-title cite-sum-card-title">\n'
                '    <div style="display:flex;align-items:center;gap:7px">\n'
                '      <div class="card-title-dot teal"></div>引用描述综合总结\n'
                '      <span style="font-size:10px;color:var(--text-light);font-weight:400">'
                'AI 综合归纳 &nbsp;·&nbsp; 客观呈现 &nbsp;·&nbsp; 基于 '
                + str(n_descs) + ' 条引用描述</span>\n'
                '    </div>\n'
                '    <button class="desc-btn" id="citeSumBtn" '
                'onclick="toggleCiteSummary()">展开全文 ▾</button>\n'
                '  </div>\n'
                '  <p class="cite-sum-preview">' + _cs_preview_safe + '</p>\n'
                '  <div id="citeSummaryContent" style="display:none">\n'
                '    <div class="cite-sum-body"><div class="md-content">'
                + citation_summary
                + '</div></div>\n'
                '  </div>\n'
                '</div>'
            )
        else:
            citation_summary_html = ""
        now = datetime.now()
        download_filenames = download_filenames or {}
        citing_pairs = citing_pairs or []
        institution_stats = institution_stats or {}
        self_cite_sub = (
            f'<div class="stat-sub">含 {self_citation_count} 篇自引</div>'
            if self_citation_count > 0 else ''
        )
        # ── Institution section HTML ────────────────────────────────────────────
        INST_CATEGORY_ORDER = ["国际科技企业", "国内科技企业", "海外顶尖高校", "国内顶尖高校/机构"]
        INST_CATEGORY_COLORS = {
            "国际科技企业":      ("#3b82c4", "#e8f1fb"),
            "国内科技企业":      ("#c45a5a", "#fdeaea"),
            "海外顶尖高校":      ("#7c5cbf", "#f0ecfb"),
            "国内顶尖高校/机构": ("#4caf8a", "#e6f6f0"),
        }
        inst_counter = 0
        inst_sections_html = ""
        for cat in INST_CATEGORY_ORDER:
            entries = institution_stats.get(cat, [])
            if not entries:
                continue
            fg, bg = INST_CATEGORY_COLORS[cat]
            tags_html = ""
            for inst_name, paper_titles in entries:
                iid = f"inst_{inst_counter}"
                inst_counter += 1
                paper_items = "".join(
                    f'<div class="inst-paper-item">&middot; {t[:80]}</div>'
                    for t in paper_titles
                )
                tags_html += (
                    f'<span class="inst-tag" style="color:{fg};background:{bg};border:1px solid {fg}44" '
                    f'onclick="toggleInst(\'{iid}\')">'
                    f'{inst_name} <span class="inst-count">{len(paper_titles)}篇</span></span>'
                    f'<div id="{iid}" class="inst-paper-list">{paper_items}</div>'
                )
            inst_sections_html += (
                f'<div class="inst-section">'
                f'<div class="inst-category-label">{cat}</div>'
                f'<div class="inst-tags">{tags_html}</div>'
                f'</div>'
            )
        if inst_sections_html:
            institution_section_html = f"""
<!-- SECTION 02 -->
<div class="section-header">
  <span class="section-num">02</span>
  <span class="section-title">著名机构引用 · 大学 / 企业 / 研究院</span>
  <div class="section-divider"></div>
</div>
<div class="card grid-1">
  <div class="card-title"><div class="card-title-dot sage"></div>引用该论文的知名大学与科技机构（基于施引作者单位信息匹配，点击机构可展开论文列表）</div>
  {inst_sections_html}
</div>"""
        else:
            institution_section_html = """
<!-- SECTION 02 -->
<div class="section-header">
  <span class="section-num">02</span>
  <span class="section-title">著名机构引用 · 大学 / 企业 / 研究院</span>
  <div class="section-divider"></div>
</div>
<div class="card grid-1" style="text-align:center;padding:48px 24px;color:var(--text-light)">
  <div style="font-size:14px;margin-bottom:6px">未从施引文献数据中识别到预设著名机构</div>
  <div style="font-size:12px">可能原因：施引论文作者单位信息不完整，或该论文引用者主要来自非预设机构</div>
</div>"""

        # Build description lookup: paper_title -> list of unique descriptions
        desc_lookup = {}
        for cp in citing_pairs:
            pt = cp.get("paper_title", "").strip()
            if not pt:
                continue
            if pt not in desc_lookup:
                desc_lookup[pt] = []
            desc = cp.get("description", "").strip()
            if desc and desc.upper() not in ("NONE", "NAN", "N/A", "NA", "") and desc not in desc_lookup[pt]:
                desc_lookup[pt].append(desc)

        # ── Citing papers list
        unique_citing_papers = unique_citing_papers or []
        n_citing = len(unique_citing_papers)
        citing_list_items = ""
        for i, name in enumerate(unique_citing_papers):
            citing_list_items += f"""
        <div class="citing-paper-item">
          <span class="citing-paper-num">{str(i+1).zfill(2)}</span>
          <span class="citing-paper-name">{name}</span>
        </div>"""

        # ── Download bar
        dl_links = ""
        excel_fname = download_filenames.get("excel", "")
        if excel_fname:
            excel_label = "完整数据（含引用描述）.xlsx" if "citing_desc" in excel_fname else "完整数据.xlsx"
            dl_links += f'<a href="/api/results/download/{excel_fname}">{excel_label}</a>\n'
        for key, label in [("all_renowned", "著名学者.xlsx"), ("top_renowned", "顶尖学者.xlsx")]:
            fname = download_filenames.get(key, "")
            if fname:
                dl_links += f'<a href="/api/results/download/{fname}">{label}</a>\n'
        download_bar_html = f"""
<div class="download-bar">
  <span>📥 下载数据文件：</span>
  {dl_links}
</div>""" if dl_links else ""

        # ── Chart data
        all_years = stats["all_years"]
        year_labels = self._js([int(y) for y in all_years])   # int, not float
        year_vals_list = [stats["year_counter"][y] for y in all_years]
        year_data = self._js(year_vals_list)
        # Connector line = same data as bars (red dashed line through bar tops)
        connector_year_js = year_data  # same values, rendered as line

        # Three country charts
        cp_sorted = stats["country_counter_papers"].most_common(10)
        cr_sorted = stats["country_counter_renowned"].most_common(10)
        ct_sorted = stats["country_counter_top"].most_common(10)
        country_p_labels = self._js([c for c, _ in cp_sorted])
        country_p_data   = self._js([n for _, n in cp_sorted])
        country_r_labels = self._js([c for c, _ in cr_sorted])
        country_r_data   = self._js([n for _, n in cr_sorted])
        country_t_labels = self._js([c for c, _ in ct_sorted])
        country_t_data   = self._js([n for _, n in ct_sorted])

        n_scholars = stats["unique_scholars"]
        lc = stats["level_counter"]
        fellow_labels = self._js([
            f"其他知名学者 {lc.get('其他知名学者', 0)}人",
            f"Fellow {lc.get('Fellow', 0)}人",
            f"其他院士 {lc.get('其他院士', 0)}人",
            f"两院院士 {lc.get('两院院士', 0)}人",
        ])
        fellow_data = self._js([lc.get("其他知名学者", 0), lc.get("Fellow", 0),
                                 lc.get("其他院士", 0), lc.get("两院院士", 0)])
        top10 = papers[:10]
        total_cit_all = sum(p["citations"] for p in papers)
        cite_labels = self._js([self._truncate(p["title"], 48) for p in top10])
        cite_data = self._js([p["citations"] for p in top10])

        # ── Keyword cloud
        kw_items = ""
        kw_colors = [
            ("#3b82c4", "#e8f1fb"), ("#4caf8a", "#e6f6f0"),
            ("#7c5cbf", "#f0ecfb"), ("#d4892a", "#fdf3e3"),
            ("#c45a5a", "#fdeaea"),
        ]
        for i, kw in enumerate(keywords):
            w = kw.get("weight", 5)
            size = 11 + int(7 * (w - 1) / 9)
            fg, bg = kw_colors[i % len(kw_colors)]
            en = kw.get("keyword", "?")
            cn = kw.get("keyword_cn", "")
            cn_part = f'<span class="kw-cn">({cn})</span>' if cn and cn != en else ""
            kw_items += (f'<span class="kw-tag" style="font-size:{size}px;color:{fg};'
                         f'background:{bg};border:1px solid {fg}33;">'
                         f'{en}{cn_part}</span>')

        # ── Citation type bars
        ct_colors = ["fill-teal", "fill-sage", "fill-amber", "fill-violet", "fill-rose"]
        ct_items = ""
        ctypes = citation_analysis.get("citation_types", [])
        total_ct = sum(x.get("count", 0) for x in ctypes) or 1
        for i, ct in enumerate(ctypes):
            pct = round(100 * ct.get("count", 0) / total_ct)
            col = ct_colors[i % len(ct_colors)]
            ct_items += f"""
        <div class="ctb-row">
          <div class="ctb-label"><span>{ct.get('type', '')}</span><span>{ct.get('count', 0)} 篇 ({pct}%)</span></div>
          <div class="ctb-track"><div class="ctb-fill {col}" style="width:{pct}%"></div></div>
        </div>"""

        positions = citation_analysis.get("citation_positions", [])
        pos_labels = self._js([p.get("position", "") for p in positions])
        pos_data = self._js([p.get("count", 0) for p in positions])
        sent = citation_analysis.get("sentiment_distribution", {"positive": 20, "neutral": 70, "critical": 10})
        sentiment_html = f"""
        <div class="sentiment-ring">
          <div class="sring-item"><div class="sring-dot" style="background:#4caf8a"></div>正面肯定 {sent.get('positive', 0)}%</div>
          <div class="sring-item"><div class="sring-dot" style="background:#a0aec0"></div>中性引用 {sent.get('neutral', 0)}%</div>
          <div class="sring-item"><div class="sring-dot" style="background:#c45a5a"></div>批评探讨 {sent.get('critical', 0)}%</div>
        </div>"""
        depth = citation_analysis.get("citation_depth", {"core_citation": 35, "reference_citation": 45, "supplementary_citation": 20})
        depth_data = self._js([depth.get("core_citation", 0), depth.get("reference_citation", 0), depth.get("supplementary_citation", 0)])
        themes = citation_analysis.get("citation_themes", [])
        theme_html = ""
        for th in themes:
            freq = th.get("frequency", 5)
            size = 11 + max(0, freq - 3)
            theme_html += f'<span class="theme-tag" style="font-size:{size}px">{th.get("theme", "")}</span>'
        findings_html = ""
        for i, f in enumerate(citation_analysis.get("key_findings", [])[:5]):
            findings_html += f"""
        <div class="finding-item">
          <div class="finding-num">{i+1}</div>
          <div>{f}</div>
        </div>"""

        # ── Section 03 body: show placeholder when citing analysis was skipped
        has_citing_data = bool(ctypes) or bool(themes) or bool(citation_analysis.get("key_findings"))
        if has_citing_data:
            section_03_body = f"""<div class="grid-2">
  <div class="card">
    <div class="card-title"><div class="card-title-dot"></div>引用类型分布</div>
    <div class="cite-type-bar">{ct_items}</div>
    <div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--border)">
      <div class="card-title" style="margin-bottom:12px"><div class="card-title-dot sage"></div>引用情感倾向</div>
      <canvas id="cSentiment" style="max-height:200px"></canvas>
      {sentiment_html}
    </div>
  </div>
  <div class="card">
    <div class="card-title"><div class="card-title-dot amber"></div>引用出现位置分布</div>
    <canvas id="cPosition" height="200"></canvas>
    <div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--border)">
      <div class="card-title" style="margin-bottom:10px"><div class="card-title-dot violet"></div>高频引用主题词</div>
      <div class="theme-tags">{theme_html}</div>
    </div>
  </div>
</div>
<div class="grid-2">
  <div class="card">
    <div class="card-title"><div class="card-title-dot violet"></div>引用深度结构（核心 vs 参考 vs 补充）</div>
    <canvas id="cDepth" style="max-height:200px"></canvas>
  </div>
  <div class="card">
    <div class="card-title"><div class="card-title-dot sage"></div>AI 引用洞察摘要</div>
    <div class="findings-list">{findings_html}</div>
  </div>
</div>"""
        else:
            section_03_body = """<div class="grid-1">
  <div class="card" style="text-align:center;padding:48px 24px;color:rgba(180,210,255,0.45)">
    <div style="font-size:32px;margin-bottom:12px">🚫</div>
    <div style="font-size:14px;font-weight:600;margin-bottom:6px;color:rgba(180,210,255,0.6)">引文描述分析不可用</div>
    <div style="font-size:12px">当前选择「基础服务」，未开启引文描述搜索（Phase 4），此部分无数据。如需查看，请在首页选择「进阶服务/全面服务」后重新运行。</div>
  </div>
</div>"""

        # ── Build Section 05: combined citation analysis (summary + deep analysis)
        _sec05_header = (
            '\n<!-- SECTION 05 -->\n'
            '<div class="section-header">\n'
            '  <span class="section-num">05</span>\n'
            '  <span class="section-title">引用描述综合分析</span>\n'
            '  <div class="section-divider"></div>\n'
            '</div>\n'
        )
        section_05_html = (
            _sec05_header
            + (citation_summary_html + '\n' if citation_summary else '')
            + section_03_body
        )

        # ── Scholar deduplication: normalize names to detect variants
        # e.g. "Zhang Wei", "Zhang Wei (张伟)", "张伟" → same person
        def _norm_scholar_name(name):
            clean = re.sub(r'\s*[\(（][^\)）]*[\)）]', '', name).strip()
            chinese = re.sub(r'[^\u4e00-\u9fff]', '', clean)
            ascii_c = re.sub(r'[^a-zA-Z]', '', clean).lower()
            return chinese, ascii_c

        scholar_groups = []   # list of merged scholar dicts
        seen_zh = {}          # chinese_chars -> group index
        seen_en = {}          # ascii_str -> group index

        for s in all_scholars:
            name = s.get("name", "").strip()
            if not name:
                continue
            zh, en = _norm_scholar_name(name)
            group_idx = None
            if zh and len(zh) >= 2 and zh in seen_zh:
                group_idx = seen_zh[zh]
            elif en and len(en) >= 4 and en in seen_en:
                group_idx = seen_en[en]
            if group_idx is None:
                group_idx = len(scholar_groups)
                scholar_groups.append({**s, "_paper_titles": [s.get("paper_title", "").strip()]})
                if zh and len(zh) >= 2:
                    seen_zh[zh] = group_idx
                if en and len(en) >= 4:
                    seen_en[en] = group_idx
            else:
                pt = s.get("paper_title", "").strip()
                if pt and pt not in scholar_groups[group_idx]["_paper_titles"]:
                    scholar_groups[group_idx]["_paper_titles"].append(pt)

        # Sort scholars: 两院院士 → 其他院士 → Fellow → 其他知名学者
        _level_order_map = {"b-ys": 0, "b-ot": 1, "b-fw": 2, "b-nm": 3}
        scholar_groups.sort(key=lambda s: _level_order_map.get(self._level_badge(s["level"])[0], 4))

        # Build title → link lookup for the interactive scholar tooltip
        _title_to_link = {}
        for _p in papers:
            _t = (_p.get("title") or "").strip()
            _l = (_p.get("link") or "").strip()
            if _t and _l and _l not in ("nan", "None"):
                _title_to_link[_t] = _l

        # Build scholar table rows
        scholar_rows = ""
        for idx, s in enumerate(scholar_groups, 1):
            bc, bl = self._level_badge(s["level"])
            is_top = bc in ("b-ys", "b-fw", "b-ot")
            # Collect all unique descriptions across all papers this scholar appears in
            all_descs = []
            for pt in s.get("_paper_titles", []):
                for d in desc_lookup.get(pt, []):
                    if d and d not in all_descs:
                        all_descs.append(d)
            if all_descs:
                sep = '\n\n— — —\n\n'
                merged = sep.join(all_descs)
                lbl = f"引用描述 ({len(all_descs)}篇) ▾" if len(all_descs) > 1 else "引用描述 ▾"
                safe_merged = merged.replace('<', '&lt;').replace('>', '&gt;')
                desc_btn = f'<button class="desc-btn" onclick="toggleDesc(\'desc_{idx}\')">{lbl}</button>'
                desc_row = f"""
        <tr id="desc_{idx}" class="desc-row" style="display:none">
          <td colspan="6"><div class="md-content">{safe_merged}</div></td>
        </tr>"""
            else:
                desc_btn = ""
                desc_row = ""
            _paper_links_data = [
                {"t": pt, "l": _title_to_link.get(pt, "")}
                for pt in s.get("_paper_titles", []) if pt
            ]
            if _paper_links_data:
                _papers_json = json.dumps(_paper_links_data, ensure_ascii=False)
                _papers_json_safe = _papers_json.replace('"', '&quot;')
                _name_td = (
                    f'<td class="sname" data-papers="{_papers_json_safe}">'
                    f'<span class="sname-hover">{s["name"]}</span></td>'
                )
            else:
                _name_td = f'<td class="sname">{s["name"]}</td>'
            scholar_rows += f"""
        <tr>
          <td style="color:var(--text-light);font-size:11px">{str(idx).zfill(2)}</td>
          {_name_td}
          <td>{self._country_badge(s['country'])}</td>
          <td><span class="badge {bc}">{bl}</span></td>
          <td class="stitle">{s['title'][:90]}</td>
          <td>{desc_btn}</td>
        </tr>{desc_row}"""

        # Helper: parse Authors_with_Profile string (Python dict repr) → list of (name, url)
        # Keys are stored as "author_N_RealName" (e.g. "author_0_John Smith") by the scraper.
        def _parse_authors_with_profile(raw: str):
            try:
                d = ast.literal_eval(raw)
                if isinstance(d, dict):
                    result = []
                    for k, v in d.items():
                        # Strip "author_N_" prefix → keep only the actual name
                        name = re.sub(r'^author_\d+_', '', str(k)).strip()
                        if name:
                            result.append((name, str(v)))
                    return result
            except Exception:
                pass
            return [(raw.strip(), "")] if raw.strip() else []

        # ── High-impact paper detail items (section 05 right)
        paper_detail_items = ""
        for i, p in enumerate(papers[:10]):
            c = p["citations"]
            year = str(int(p["year"])) if p.get("year") is not None else ""
            link = (p.get("link", "") or "").strip()
            authors_raw = (p.get("authors", "") or "").strip()
            institution = (p.get("institution", "") or "").strip()
            country = p.get("country", "").strip()
            author_affil = (p.get("author_affiliation", "") or "").strip()
            col = "#c45a5a" if c >= 50 else "#d4892a" if c >= 20 else "#3b82c4" if c >= 8 else "#7c5cbf"
            if link and link not in ("nan", "None", ""):
                title_part = (f'<a href="{link}" target="_blank" class="pdi-title-link">'
                              f'{p["title"]}</a>')
            else:
                title_part = p["title"]
            # Authors as clickable pills
            authors_html = ""
            if authors_raw:
                author_pairs = _parse_authors_with_profile(authors_raw)
                pills = []
                for aname, aurl in author_pairs[:10]:
                    aname_safe = aname.replace('<', '&lt;').replace('>', '&gt;')
                    if aurl and aurl.startswith('http'):
                        pills.append(f'<a href="{aurl}" target="_blank" class="author-pill">{aname_safe}</a>')
                    else:
                        pills.append(f'<span class="author-pill">{aname_safe}</span>')
                if pills:
                    authors_html = (
                        f'<div class="pdi-authors" style="margin-bottom:4px">部分带有谷歌学术主页的作者：</div>'
                        f'<div class="pdi-authors-pills">{"".join(pills)}</div>'
                    )
            meta_parts = []
            if institution:
                meta_parts.append(f'<span class="pdi-inst-tag">{institution[:55]}</span>')
            if country:
                meta_parts.append(f'<span class="pdi-country-tag">{country}</span>')
            meta_html = f'<div class="pdi-meta-row">{"".join(meta_parts)}</div>' if meta_parts else ""
            affil_btn = ""
            affil_div = ""
            if author_affil:
                safe_affil = author_affil.replace('<', '&lt;').replace('>', '&gt;')
                affil_btn = (f'<button class="pdi-affil-btn" '
                             f'onclick="toggleAffil(\'pdi_{i}\', this)">作者信息 ▾</button>')
                affil_div = (f'<div id="pdi_{i}" class="pdi-affil-box" style="display:none">'
                             f'<div class="md-content">{safe_affil}</div></div>')
            paper_detail_items += f"""
      <div class="pdi-item" style="border-left-color:{col}">
        <div class="pdi-rank" style="color:{col}44">{str(i+1).zfill(2)}</div>
        <div class="pdi-body">
          <div class="pdi-title">{title_part}</div>
          {authors_html}
          {meta_html}
          <div class="pdi-footer">
            {"<span class='pdi-year'>" + year + "</span>" if year else ""}
            <span class="pdi-cit" style="color:{col}">被引 <strong>{c}</strong> 次</span>
            {affil_btn}
          </div>
          {affil_div}
        </div>
      </div>"""

        # ── Prediction
        td = prediction.get("trend_data", {})
        trend_labels = self._js(td.get("labels", []))
        trend_actual = self._js(td.get("actual", []))
        trend_forecast = self._js(td.get("forecast", []))
        metrics_html = ""
        for m in prediction.get("prediction_metrics", []):
            metrics_html += f"""
        <div class="pred-metric">
          <div>
            <div class="pred-metric-label">{m.get('label', '')}</div>
            <div class="pred-metric-note">{m.get('note', '')}</div>
          </div>
          <div class="pred-metric-val">{m.get('value', '')}</div>
        </div>"""
        impact_html = ""
        for imp in prediction.get("impact_scores", []):
            score = imp.get("score", 50)
            col_class = imp.get("color_class", "fill-teal")
            impact_html += f"""
        <div>
          <div class="impact-row-label"><span>{imp.get('label', '')}</span><span style="color:#7dd8b0">{score}%</span></div>
          <div class="impact-track"><div class="impact-fill {col_class}" style="width:{score}%"></div></div>
        </div>"""
        commentary = prediction.get("prediction_commentary", "")

        # ── Insights
        insights_html = ""
        for ins in insights:
            color = ins.get("color", "teal")
            icon = ins.get("icon", "📊")
            title = ins.get("title", "")
            body = ins.get("body", "")
            insights_html += f"""
      <div class="insight-card {color}">
        <h4>{icon} {title}</h4>
        <p>{body}</p>
      </div>"""

        gen_date = f"{now.year}.{str(now.month).zfill(2)}.{str(now.day).zfill(2)}"

        # ── Knowledge graph data ────────────────────────────────────────────────
        KG_MAX = 120
        kg_paper_list = papers[:KG_MAX]
        kg_nodes = []
        for _i, _ct in enumerate(canonical_titles or []):
            kg_nodes.append({
                "id": f"c{_i}", "type": "center",
                "title": _ct[:80], "year": None, "citations": None,
                "link": "", "country": "", "institution": "",
            })
        _n_centers = len(canonical_titles or []) or 1
        for _i, _p in enumerate(kg_paper_list):
            kg_nodes.append({
                "id": f"p{_i}", "type": "paper",
                "title": (_p.get("title") or "Unknown")[:80],
                "year": int(_p["year"]) if _p.get("year") else None,
                "citations": int(_p["citations"]) if _p.get("citations") else 0,
                "link": _p.get("link", ""),
                "country": _p.get("country", ""),
                "institution": (_p.get("institution") or "")[:55],
            })
        # Each citing paper links to ALL center nodes
        kg_links = []
        for _i in range(len(kg_paper_list)):
            for _ci in range(_n_centers):
                kg_links.append({"source": f"p{_i}", "target": f"c{_ci}"})
        # Connect center nodes to each other
        for _ci in range(_n_centers - 1):
            for _cj in range(_ci + 1, _n_centers):
                kg_links.append({"source": f"c{_ci}", "target": f"c{_cj}",
                                 "center_edge": True})
        kg_data_json = json.dumps({"nodes": kg_nodes, "links": kg_links},
                                  ensure_ascii=False)

        # ── Compact report context for chat widget ──────────────────────────────
        _chat_ctx = {
            "target_papers": canonical_titles or [],
            "stats": {
                "total":    total_papers,
                "scholars": stats.get("unique_scholars", 0),
                "fellows":  stats.get("fellow_count", 0),
                "countries": stats.get("country_count", 0),
                "max_cit":  stats.get("max_cit", 0),
            },
            "scholars": [
                {"name": s.get("name", ""), "level": s.get("level", ""),
                 "country": s.get("country", "")}
                for s in all_scholars[:30]
            ],
            "keywords": [{"keyword": k.get("keyword", ""), "keyword_cn": k.get("keyword_cn", "")}
                         for k in keywords[:25]],
            "top_papers": [
                {"title": p.get("title", "")[:80], "year": p.get("year"),
                 "citations": p.get("citations", 0), "country": p.get("country", "")}
                for p in papers[:20]
            ],
            "year_dist": dict(stats.get("year_counter", {})),
            "citation_types":      citation_analysis.get("citation_types", []),
            "citation_positions":  citation_analysis.get("citation_positions", []),
            "key_findings":        citation_analysis.get("key_findings", []),
            "insights": [{"title": i.get("title", ""), "body": i.get("body", "")}
                         for i in insights],
        }
        _chat_ctx_json = json.dumps(_chat_ctx, ensure_ascii=False)

        kg_section_html = """
<!-- SECTION 09 -->
<div class="section-header">
  <span class="section-num">09</span>
  <span class="section-title">知识图谱 · 引用关系可视化</span>
  <div class="section-divider"></div>
</div>
<div class="card grid-1">
  <div class="card-title" style="display:flex;justify-content:space-between;align-items:center">
    <div style="display:flex;align-items:center;gap:7px">
      <div class="card-title-dot teal"></div>
      以目标论文为中心的引用关系图谱（支持拖拽节点 · 滚轮缩放 · 点击打开论文）
    </div>
    <button id="kg-reset-btn" style="font-size:10px;padding:3px 12px;border:1px solid rgba(66,153,225,0.45);background:rgba(66,153,225,0.1);color:#bee3f8;border-radius:5px;cursor:pointer;transition:background .2s" onmouseover="this.style.background='rgba(66,153,225,0.25)'" onmouseout="this.style.background='rgba(66,153,225,0.1)'">重置视图</button>
  </div>
  <div id="kg-container" style="width:100%;height:580px;background:#0c1220;border-radius:12px;overflow:hidden;position:relative;border:1px solid rgba(66,153,225,0.12)">
    <div id="kg-loading" style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:rgba(180,210,255,0.35);font-size:13px;pointer-events:none">正在渲染知识图谱…</div>
  </div>
  <div style="display:flex;align-items:center;gap:22px;margin-top:12px;flex-wrap:wrap;font-size:11px;color:var(--text-light)">
    <div style="display:flex;align-items:center;gap:8px">
      <span style="color:#a0aec0">节点颜色 = 年份：</span>
      <svg id="kg-legend-svg" width="160" height="12" style="border-radius:3px;vertical-align:middle"></svg>
      <span id="kg-year-min" style="color:#9f7aea;font-size:10px"></span>
      <span style="color:#718096">→</span>
      <span id="kg-year-max" style="color:#48bb78;font-size:10px"></span>
    </div>
    <div style="display:flex;align-items:center;gap:6px">
      <svg width="44" height="14" style="vertical-align:middle">
        <circle cx="6" cy="7" r="4" fill="rgba(72,187,120,0.65)"></circle>
        <circle cx="28" cy="7" r="7" fill="rgba(72,187,120,0.65)"></circle>
      </svg>
      <span style="color:#a0aec0">节点大小 = 施引论文被引量</span>
    </div>
    <div style="display:flex;align-items:center;gap:6px">
      <svg width="16" height="16"><circle cx="8" cy="8" r="7" fill="#0f2744" stroke="#4299e1" stroke-width="2.5"></circle></svg>
      <span style="color:#a0aec0">蓝色大节点 = 目标论文</span>
    </div>
    <div style="display:flex;align-items:center;gap:6px">
      <span style="color:#718096;font-size:10px">内圈 = 较旧 · 外圈 = 较新</span>
    </div>
  </div>
</div>"""

        _js_code = r"""
(function() {
  var data = KG_DATA_PLACEHOLDER;
  var container = document.getElementById('kg-container');
  if (!container || !window.d3 || !data.nodes.length) return;
  var loading = document.getElementById('kg-loading');
  if (loading) loading.style.display = 'none';

  var W = container.clientWidth || 820;
  var H = 580;

  var svg = d3.select(container).append('svg')
    .attr('width', W).attr('height', H).style('display', 'block');

  /* ── Glow filter ── */
  var defs = svg.append('defs');
  var flt = defs.append('filter').attr('id', 'kg-glow')
    .attr('x', '-60%').attr('y', '-60%').attr('width', '220%').attr('height', '220%');
  flt.append('feGaussianBlur').attr('stdDeviation', '5').attr('result', 'blur');
  var fm = flt.append('feMerge');
  fm.append('feMergeNode').attr('in', 'blur');
  fm.append('feMergeNode').attr('in', 'SourceGraphic');

  var flt2 = defs.append('filter').attr('id', 'kg-glow2')
    .attr('x', '-40%').attr('y', '-40%').attr('width', '180%').attr('height', '180%');
  flt2.append('feGaussianBlur').attr('stdDeviation', '2.5').attr('result', 'blur2');
  var fm2 = flt2.append('feMerge');
  fm2.append('feMergeNode').attr('in', 'blur2');
  fm2.append('feMergeNode').attr('in', 'SourceGraphic');

  /* ── Background ── */
  svg.append('rect').attr('width', W).attr('height', H)
    .attr('fill', '#0c1220').attr('rx', 11);

  /* subtle grid */
  var gridG = svg.append('g').attr('opacity', 0.025);
  for (var gx = 0; gx <= W; gx += 50)
    gridG.append('line').attr('x1', gx).attr('y1', 0).attr('x2', gx).attr('y2', H)
      .attr('stroke', '#4299e1').attr('stroke-width', 0.5);
  for (var gy = 0; gy <= H; gy += 50)
    gridG.append('line').attr('x1', 0).attr('y1', gy).attr('x2', W).attr('y2', gy)
      .attr('stroke', '#4299e1').attr('stroke-width', 0.5);

  var g = svg.append('g');

  /* ── Zoom ── */
  var zoom = d3.zoom().scaleExtent([0.12, 9]).on('zoom', function(event) {
    g.attr('transform', event.transform);
  });
  svg.call(zoom).on('dblclick.zoom', null);

  /* ── Year color scale ── */
  var paperNodes = data.nodes.filter(function(n) { return n.type === 'paper'; });
  var years = paperNodes.map(function(n) { return n.year; }).filter(Boolean);
  var minY = years.length ? Math.min.apply(null, years) : 2015;
  var maxY = years.length ? Math.max.apply(null, years) : 2024;

  function yearColor(y) {
    if (!y) return '#4299e1';
    var t = Math.max(0, Math.min(1, (y - minY) / Math.max(maxY - minY, 1)));
    var r, gr, b;
    if (t < 0.5) {
      var tt = t * 2;
      r = Math.round(159 * (1 - tt) + 66 * tt);
      gr = Math.round(122 * (1 - tt) + 153 * tt);
      b = Math.round(234 * (1 - tt) + 225 * tt);
    } else {
      var tt = (t - 0.5) * 2;
      r = Math.round(66 * (1 - tt) + 72 * tt);
      gr = Math.round(153 * (1 - tt) + 187 * tt);
      b = Math.round(225 * (1 - tt) + 120 * tt);
    }
    return 'rgb(' + r + ',' + gr + ',' + b + ')';
  }

  /* ── Node radius ── */
  var allCits = paperNodes.map(function(n) { return n.citations || 0; });
  var maxCit = allCits.length ? Math.max.apply(null, allCits) : 1;

  function nodeRadius(d) {
    if (d.type === 'center') return 36;
    var cit = d.citations || 0;
    return 5 + Math.pow(cit / Math.max(maxCit, 1), 0.42) * 25;
  }

  /* ── Fix center nodes at symmetric positions ── */
  var centerNodes = data.nodes.filter(function(n) { return n.type === 'center'; });
  var nC = centerNodes.length;
  var cx = W / 2, cy = H / 2;
  var centerSpread = Math.min(W, H) * 0.22;
  centerNodes.forEach(function(n, i) {
    var angle = (2 * Math.PI * i / nC) - Math.PI / 2;
    n.fx = nC === 1 ? cx : cx + centerSpread * Math.cos(angle);
    n.fy = nC === 1 ? cy : cy + centerSpread * Math.sin(angle);
    n.x  = n.fx; n.y = n.fy;
  });
  /* centroid of centers (for radial force anchor) */
  var rcx = centerNodes.reduce(function(s, n) { return s + n.fx; }, 0) / Math.max(nC, 1);
  var rcy = centerNodes.reduce(function(s, n) { return s + n.fy; }, 0) / Math.max(nC, 1);

  /* ── Force simulation ── */
  var sim = d3.forceSimulation(data.nodes)
    .force('link', d3.forceLink(data.links)
      .id(function(d) { return d.id; })
      .distance(function(d) {
        var src = typeof d.source === 'object' ? d.source : {};
        var tgt = typeof d.target === 'object' ? d.target : {};
        if (src.type === 'center' && tgt.type === 'center') return centerSpread * 2;
        var cit = src.citations || 0;
        return 95 + (1 - Math.pow(cit / Math.max(maxCit, 1), 0.38)) * 150;
      })
      .strength(function(d) {
        var src = typeof d.source === 'object' ? d.source : {};
        var tgt = typeof d.target === 'object' ? d.target : {};
        return (src.type === 'center' && tgt.type === 'center') ? 1 : 0.28;
      }))
    .force('charge', d3.forceManyBody().strength(function(d) {
      return d.type === 'center' ? -500 : -45;
    }))
    .force('center', d3.forceCenter(cx, cy).strength(0.04))
    .force('radial', d3.forceRadial(function(d) {
      if (d.type === 'center') return 0;
      var t = d.year ? Math.max(0, Math.min(1, (d.year - minY) / Math.max(maxY - minY, 1))) : 0.5;
      return 90 + t * 210;
    }, rcx, rcy).strength(0.18))
    .force('collision', d3.forceCollide().radius(function(d) {
      return nodeRadius(d) + 4;
    }).strength(0.75))
    .alphaDecay(0.013);

  /* ── Links ── */
  var paperLinks  = data.links.filter(function(l) { return !l.center_edge; });
  var centerLinks = data.links.filter(function(l) { return  l.center_edge; });

  /* center-to-center edges: thick dashed */
  var centerLink = g.append('g').selectAll('line').data(centerLinks).join('line')
    .attr('stroke', 'rgba(66,153,225,0.55)')
    .attr('stroke-width', 2)
    .attr('stroke-dasharray', '6,4');

  /* paper-to-center edges: thin */
  var link = g.append('g').selectAll('line').data(paperLinks).join('line')
    .attr('stroke', 'rgba(66,153,225,0.09)')
    .attr('stroke-width', 0.85);

  /* ── Drag behavior ── */
  var drag = d3.drag()
    .on('start', function(event, d) {
      if (!event.active) sim.alphaTarget(0.3).restart();
      d.fx = d.x; d.fy = d.y;
    })
    .on('drag', function(event, d) { d.fx = event.x; d.fy = event.y; })
    .on('end', function(event, d) {
      if (!event.active) sim.alphaTarget(0);
      d.fx = null; d.fy = null;
    });

  /* ── Nodes ── */
  var node = g.append('g').selectAll('g').data(data.nodes).join('g')
    .style('cursor', function(d) {
      return (d.link && d.type === 'paper') ? 'pointer' : 'default';
    })
    .call(drag);

  /* Center: outer glow ring */
  node.filter(function(d) { return d.type === 'center'; })
    .append('circle').attr('r', 54)
    .attr('fill', 'none').attr('stroke', 'rgba(66,153,225,0.12)').attr('stroke-width', 1);

  node.filter(function(d) { return d.type === 'center'; })
    .append('circle').attr('r', 44)
    .attr('fill', 'none').attr('stroke', 'rgba(66,153,225,0.2)').attr('stroke-width', 1);

  node.filter(function(d) { return d.type === 'center'; })
    .append('circle').attr('r', 36)
    .attr('fill', '#0f2744').attr('stroke', '#4299e1').attr('stroke-width', 2.5)
    .style('filter', 'url(#kg-glow)');

  /* Center label */
  node.filter(function(d) { return d.type === 'center'; })
    .each(function(d) {
      var el = d3.select(this);
      el.append('text').text('🎯')
        .attr('text-anchor', 'middle').attr('dy', '-22px').attr('font-size', '15px')
        .style('pointer-events', 'none');
      var words = d.title.split(' ');
      var lines = [''];
      words.forEach(function(w) {
        if ((lines[lines.length - 1] + ' ' + w).trim().length > 13) {
          lines.push(w);
        } else {
          lines[lines.length - 1] = (lines[lines.length - 1] + ' ' + w).trim();
        }
      });
      lines = lines.slice(0, 4);
      var textEl = el.append('text')
        .attr('text-anchor', 'middle').attr('fill', '#bee3f8')
        .attr('font-size', 8).attr('font-weight', '600')
        .style('pointer-events', 'none');
      lines.forEach(function(l, i) {
        textEl.append('tspan').attr('x', 0)
          .attr('dy', i === 0 ? (-(lines.length - 1) * 6) + 'px' : '12px')
          .text(l);
      });
    });

  /* Paper nodes: outer glow for high-citation */
  node.filter(function(d) { return d.type === 'paper' && d.citations > maxCit * 0.5; })
    .append('circle')
    .attr('r', function(d) { return nodeRadius(d) + 4; })
    .attr('fill', 'none')
    .attr('stroke', function(d) { return yearColor(d.year); })
    .attr('stroke-width', 0.5).attr('stroke-opacity', 0.5)
    .style('filter', 'url(#kg-glow2)');

  /* Paper nodes: main circle */
  node.filter(function(d) { return d.type === 'paper'; })
    .append('circle')
    .attr('r', nodeRadius)
    .attr('fill', function(d) { return yearColor(d.year); })
    .attr('fill-opacity', 0.72)
    .attr('stroke', function(d) { return yearColor(d.year); })
    .attr('stroke-width', 1.2).attr('stroke-opacity', 0.9);

  /* ── Tooltip ── */
  var tt = d3.select('body').append('div').attr('id', 'kg-tt')
    .style('position', 'fixed').style('display', 'none')
    .style('background', 'rgba(8,14,28,0.97)')
    .style('border', '1px solid rgba(66,153,225,0.4)')
    .style('border-radius', '10px').style('padding', '10px 14px')
    .style('max-width', '310px').style('pointer-events', 'none')
    .style('z-index', '9999').style('font-size', '11px')
    .style('font-family', "'Noto Sans SC', sans-serif")
    .style('line-height', '1.6')
    .style('box-shadow', '0 6px 28px rgba(0,0,0,0.7)');

  function posT(event) {
    var x = event.clientX + 14, y = event.clientY - 10;
    if (x + 320 > window.innerWidth) x = event.clientX - 322;
    if (y + 130 > window.innerHeight) y = event.clientY - 130;
    tt.style('left', x + 'px').style('top', y + 'px');
  }

  node.on('mouseover', function(event, d) {
    var html = '';
    if (d.type === 'center') {
      html = '<div style="color:#63b3ed;font-weight:700;margin-bottom:5px;font-size:12px">🎯 目标论文</div>' +
             '<div style="color:#e2e8f0;font-size:11px">' + d.title + '</div>';
    } else {
      var col = yearColor(d.year);
      html = '<div style="color:#e2e8f0;font-weight:600;margin-bottom:6px;font-size:11px;line-height:1.5">' + d.title + '</div>';
      var meta = [];
      if (d.year) meta.push('<span style="color:#a0aec0">📅 ' + d.year + '</span>');
      if (d.citations) meta.push('<span style="color:#68d391">📊 被引 ' + d.citations + ' 次</span>');
      if (d.country) meta.push('<span style="color:#fbd38d">🌍 ' + d.country + '</span>');
      if (meta.length)
        html += '<div style="font-size:10px;display:flex;flex-wrap:wrap;gap:6px;margin-bottom:3px">' + meta.join('') + '</div>';
      if (d.institution)
        html += '<div style="color:#718096;font-size:10px">' + d.institution + '</div>';
      if (d.link)
        html += '<div style="color:#63b3ed;font-size:10px;margin-top:5px">点击打开论文链接 →</div>';
    }
    tt.style('display', 'block').html(html);
    posT(event);
    d3.select(this).select('circle:last-of-type').attr('fill-opacity', d.type === 'paper' ? 1 : null);
  }).on('mousemove', posT)
    .on('mouseout', function(event, d) {
      tt.style('display', 'none');
      d3.select(this).select('circle:last-of-type').attr('fill-opacity', d.type === 'paper' ? 0.72 : null);
    })
    .on('click', function(event, d) {
      if (d.link && d.type === 'paper') {
        event.stopPropagation();
        window.open(d.link, '_blank');
      }
    });

  /* ── Tick ── */
  sim.on('tick', function() {
    function setLine(sel) {
      sel.attr('x1', function(d) { return d.source.x; })
         .attr('y1', function(d) { return d.source.y; })
         .attr('x2', function(d) { return d.target.x; })
         .attr('y2', function(d) { return d.target.y; });
    }
    setLine(link);
    setLine(centerLink);
    node.attr('transform', function(d) {
      return 'translate(' + d.x + ',' + d.y + ')';
    });
  });

  /* ── Reset button ── */
  var resetBtn = document.getElementById('kg-reset-btn');
  if (resetBtn) {
    resetBtn.addEventListener('click', function() {
      svg.transition().duration(650)
        .call(zoom.transform, d3.zoomIdentity.translate(0, 0).scale(1));
    });
  }

  /* ── Year legend ── */
  var legSvg = d3.select('#kg-legend-svg');
  if (!legSvg.empty()) {
    var lgDefs = legSvg.append('defs');
    var grad = lgDefs.append('linearGradient').attr('id', 'kg-leg-grad')
      .attr('x1', '0%').attr('y1', '0%').attr('x2', '100%').attr('y2', '0%');
    grad.append('stop').attr('offset', '0%').attr('stop-color', '#9f7aea');
    grad.append('stop').attr('offset', '50%').attr('stop-color', '#4299e1');
    grad.append('stop').attr('offset', '100%').attr('stop-color', '#48bb78');
    legSvg.append('rect').attr('width', 160).attr('height', 12).attr('rx', 3)
      .attr('fill', 'url(#kg-leg-grad)');
  }
  var minYEl = document.getElementById('kg-year-min');
  var maxYEl = document.getElementById('kg-year-max');
  if (minYEl) minYEl.textContent = minY;
  if (maxYEl) maxYEl.textContent = maxY;
})();
"""
        kg_script = ('<script>\n' +
                     _js_code.replace('KG_DATA_PLACEHOLDER', kg_data_json) +
                     '\n</script>')

        # ── Target paper title display
        canonical_titles = canonical_titles or []
        if canonical_titles:
            target_items = "".join(
                f'<div class="header-target-item">'
                f'<span class="header-target-num">{str(i+1).zfill(2)}</span>'
                f'<span class="header-target-title">{t}</span>'
                f'</div>'
                for i, t in enumerate(canonical_titles)
            )
            header_targets_html = f'<div class="header-targets">{target_items}</div>'
            page_title = canonical_titles[0] if len(canonical_titles) == 1 else f"{canonical_titles[0]} 等 {len(canonical_titles)} 篇论文"
        else:
            header_targets_html = ""
            page_title = "论文被引多维画像分析报告"

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{page_title} · 被引画像报告</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked@9/marked.min.js"></script>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>{self._CSS}</style>
</head>
<body>

<!-- ═══ HEADER ═══ -->
<div class="header">
  <div class="header-eyebrow">Citation Intelligence · {now.year}</div>
  <h1>引用论文<em>多维画像</em>分析报告</h1>
  {header_targets_html}
  <p class="header-subtitle">
    基于 {total_papers} 篇引用论文与 {stats['unique_scholars']} 位知名学者（含 {stats['fellow_count']} 位院士/Fellow）数据，
    结合大模型对引用描述的深度解读，全面呈现学术影响力格局
  </p>
  <div class="header-divider"></div>
</div>

<!-- ═══ STATS BAR ═══ -->
<div class="stats-bar">
  <div class="stat-item"><div class="stat-icon">📄</div><div class="stat-num">{total_papers}</div><div class="stat-label">引用论文总数</div>{self_cite_sub}</div>
  <div class="stat-item"><div class="stat-icon">🎓</div><div class="stat-num">{stats['unique_scholars']}</div><div class="stat-label">知名学者数量</div></div>
  <div class="stat-item"><div class="stat-icon">🏅</div><div class="stat-num">{stats['fellow_count']}</div><div class="stat-label">院士 / Fellow</div></div>
  <div class="stat-item"><div class="stat-icon">🌍</div><div class="stat-num">{stats['country_count']}</div><div class="stat-label">覆盖国家/地区</div></div>
  <div class="stat-item"><div class="stat-icon">🔥</div><div class="stat-num">{stats['max_cit']}</div><div class="stat-label">最高单篇被引量</div></div>
</div>

{download_bar_html}

<!-- ═══ CITING PAPERS LIST ═══ -->
<div class="citing-papers-section">
  <div class="citing-papers-header">
    <span class="citing-papers-header-label">SCOPE</span>
    <span class="citing-papers-header-title">本报告分析范围：引用论文列表</span>
    <span class="citing-papers-header-count">共 {n_citing} 篇</span>
    <div class="citing-papers-header-divider"></div>
  </div>
  <div class="citing-papers-desc">
    以下论文均为主动引用目标论文的施引文献，本报告所有多维画像分析均基于这 <strong>{n_citing}</strong> 篇论文展开。
  </div>
  <div class="citing-papers-grid">
    {citing_list_items}
  </div>
</div>

<!-- ═══ MAIN ═══ -->
<div class="main">

<!-- SECTION 01 -->
<div class="section-header">
  <span class="section-num">01</span>
  <span class="section-title">引用时间 · 地域分布 · 学者层级</span>
  <div class="section-divider"></div>
</div>
<div class="grid-2">
  <div class="card">
    <div class="card-title"><div class="card-title-dot"></div>引用论文年份分布</div>
    <div style="position:relative;height:200px"><canvas id="cYear"></canvas></div>
  </div>
  <div class="card">
    <div class="card-title"><div class="card-title-dot amber"></div>知名学者头衔层级分布</div>
    <div style="position:relative;height:200px"><canvas id="cFellow"></canvas></div>
  </div>
</div>
<div class="grid-3">
  <div class="card"><div class="card-title"><div class="card-title-dot sage"></div>第一作者国家/地区分布（全部施引文献）</div><div style="position:relative;height:200px"><canvas id="cCountryAll"></canvas></div></div>
  <div class="card"><div class="card-title"><div class="card-title-dot"></div>知名学者国家/地区分布</div><div style="position:relative;height:200px"><canvas id="cCountryRenowned"></canvas></div></div>
  <div class="card"><div class="card-title"><div class="card-title-dot amber"></div>顶尖学者国家/地区分布</div><div style="position:relative;height:200px"><canvas id="cCountryTop"></canvas></div></div>
</div>

{institution_section_html}

<!-- SECTION 03 -->
<div class="section-header">
  <span class="section-num">03</span>
  <span class="section-title">研究主题关键词（施引文献领域分析）</span>
  <div class="section-divider"></div>
</div>
<div class="card grid-1">
  <div class="card-title"><div class="card-title-dot violet"></div>关键词云（AI 动态提取 · 基于施引文献标题，反映施引文献所覆盖的研究范围）</div>
  <div class="kw-cloud">{kw_items}</div>
</div>

<!-- SECTION 04 -->
<div class="section-header">
  <span class="section-num">04</span>
  <span class="section-title" data-tooltip="以下为施引作者">知名学者画像一览</span>
  <div class="section-divider"></div>
</div>
<div class="card grid-1">
  <div class="card-title"><div class="card-title-dot"></div>引用论文中出现的权威学者详细信息（AI搜索生成，已自动去重合并同一学者，仅供参考）</div>
  <div style="overflow-x:auto">
    <table class="scholar-table">
      <thead><tr><th>#</th><th>学者</th><th>国家/地区</th><th>层级</th><th>头衔 / 荣誉</th><th>引用描述</th></tr></thead>
      <tbody>{scholar_rows}</tbody>
    </table>
  </div>
</div>

{section_05_html}

<!-- SECTION 06 -->
<div class="section-header">
  <span class="section-num">06</span>
  <span class="section-title">引用热度 · 高影响力引用论文 TOP 10</span>
  <div class="section-divider"></div>
</div>
<div class="grid-1">
  <div class="card">
    <div class="card-title"><div class="card-title-dot"></div>引用论文被引次数 TOP 10</div>
    <div style="position:relative;height:260px"><canvas id="cCite"></canvas></div>
  </div>
</div>
<div class="grid-1">
  <div class="card">
    <div class="card-title"><div class="card-title-dot amber"></div>高影响力引用论文详细信息（按自身被引量排序）</div>
    <div class="pdi-scroll">{paper_detail_items}</div>
  </div>
</div>

<!-- SECTION 06 -->
<div class="section-header">
  <span class="section-num">07</span>
  <span class="section-title">影响力预测分析</span>
  <div class="section-divider"></div>
</div>
<div class="prediction-band">
  <div class="pred-grid">
    <div class="pred-card">
      <div class="pred-card-title">📈 引用趋势预测 <span class="pred-tag">FORECAST · 线性回归</span></div>
      <div class="trend-wrap"><canvas id="cTrend"></canvas></div>
      {metrics_html}
    </div>
    <div class="pred-card">
      <div class="pred-card-title">🚀 施引文献影响力扩散评估 <span class="pred-tag">IMPACT</span></div>
      <div style="font-size:10.5px;color:rgba(180,210,255,0.5);margin-bottom:10px">以下评分基于施引文献群体特征，反映影响力在各维度的扩散潜力</div>
      <div class="impact-bar-wrap">{impact_html}</div>
      <div class="pred-commentary">{commentary}</div>
    </div>
  </div>
</div>

<!-- SECTION 07 -->
<div class="section-header">
  <span class="section-num">08</span>
  <span class="section-title">数据洞察与画像总结</span>
  <div class="section-divider"></div>
</div>
<div class="insights-grid">{insights_html}</div>

{kg_section_html}

</div><!-- /main -->
<div class="footer">
  Citation Intelligence Dashboard &nbsp;·&nbsp; Generated {gen_date} &nbsp;·&nbsp; Powered by AI Analysis
</div>

<script>
function renderMdInside(container) {{
  container.querySelectorAll('.md-content').forEach(function(el) {{
    if (el._mdDone) return;
    el._mdDone = true;
    var raw = el.textContent;
    el.innerHTML = (typeof marked !== 'undefined') ? marked.parse(raw) : el.innerHTML;
  }});
}}
function toggleDesc(id) {{
  var el = document.getElementById(id);
  if (!el) return;
  el.style.display = (el.style.display === 'none') ? 'table-row' : 'none';
  if (el.style.display !== 'none') renderMdInside(el);
}}
function toggleAffil(id, btn) {{
  var el = document.getElementById(id);
  if (!el) return;
  if (el.style.display === 'none') {{
    el.style.display = 'block';
    if (btn) btn.textContent = '作者信息 ▴';
    renderMdInside(el);
  }} else {{
    el.style.display = 'none';
    if (btn) btn.textContent = '作者信息 ▾';
  }}
}}
function toggleCiteSummary() {{
  var el = document.getElementById('citeSummaryContent');
  var btn = document.getElementById('citeSumBtn');
  if (!el) return;
  if (el.style.display === 'none') {{
    el.style.display = 'block';
    if (btn) btn.textContent = '折叠 ▴';
    renderMdInside(el);
  }} else {{
    el.style.display = 'none';
    if (btn) btn.textContent = '展开全文 ▾';
  }}
}}
function toggleInst(id) {{
  var el = document.getElementById(id);
  if (!el) return;
  el.style.display = (el.style.display === 'none' || el.style.display === '') ? 'block' : 'none';
}}

Chart.defaults.color = '#718096';
Chart.defaults.borderColor = 'rgba(180,190,220,0.35)';
Chart.defaults.font.family = "'Noto Sans SC', sans-serif";
Chart.defaults.font.size = 11;
// Register datalabels plugin globally only for year chart (unregister after)
Chart.register(ChartDataLabels);

const TEAL='#3b82c4', SAGE='#4caf8a', AMBER='#d4892a', VIO='#7c5cbf', ROSE='#c45a5a';
const TEAL_L='rgba(59,130,196,0.15)', SAGE_L='rgba(76,175,138,0.15)';
const VIO_L='rgba(124,92,191,0.15)', AMBER_L='rgba(212,137,42,0.15)';
const COUNTRY_COLORS = [TEAL+'cc',SAGE+'aa',VIO+'aa',AMBER+'aa',ROSE+'aa',
                        TEAL+'66',SAGE+'66',VIO+'66',AMBER+'66',ROSE+'66'];

// Year chart: bars with count labels + red dashed connector line through bar tops
new Chart(document.getElementById('cYear'), {{
  type: 'bar',
  data: {{ labels: {year_labels}, datasets: [
    {{ type: 'bar', label: '引用数量', data: {year_data},
      backgroundColor: TEAL+'99', borderColor: TEAL, borderWidth: 2, borderRadius: 5,
      order: 1,
      datalabels: {{ anchor: 'end', align: 'end', color: TEAL, font: {{ size: 10, weight: '600' }},
        formatter: v => v }} }},
    {{ type: 'line', label: '趋势连线', data: {connector_year_js},
      borderColor: '#e53e3e', backgroundColor: 'transparent',
      pointRadius: 4, pointBackgroundColor: '#e53e3e',
      borderWidth: 2, tension: 0, borderDash: [5, 3], order: 0,
      datalabels: {{ display: false }} }}
  ] }},
  options: {{ responsive: true, maintainAspectRatio: false,
    layout: {{ padding: {{ top: 20 }} }},
    plugins: {{
      legend: {{ display: true, position: 'bottom', labels: {{ padding: 8, font: {{ size: 10 }} }} }},
      tooltip: {{ callbacks: {{ label: c => c.dataset.label + ': ' + c.raw }} }},
      datalabels: {{ }}
    }},
    scales: {{ y: {{ beginAtZero: true, grid: {{ color: 'rgba(180,190,220,0.2)' }} }},
      x: {{ grid: {{ display: false }} }} }} }}
}});
Chart.unregister(ChartDataLabels);

function makeCountryChart(id, labels, data) {{
  new Chart(document.getElementById(id), {{
    type: 'bar',
    data: {{ labels: labels, datasets: [{{ data: data,
      backgroundColor: COUNTRY_COLORS, borderWidth: 0, borderRadius: 4 }}] }},
    options: {{ indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }},
        tooltip: {{ callbacks: {{ label: c => c.raw + ' 篇/人' }} }} }},
      scales: {{ x: {{ beginAtZero: true, grid: {{ color: 'rgba(180,190,220,0.2)' }} }},
        y: {{ grid: {{ display: false }} }} }} }}
  }});
}}
makeCountryChart('cCountryAll', {country_p_labels}, {country_p_data});
makeCountryChart('cCountryRenowned', {country_r_labels}, {country_r_data});
makeCountryChart('cCountryTop', {country_t_labels}, {country_t_data});

new Chart(document.getElementById('cFellow'), {{
  type: 'doughnut',
  data: {{ labels: {fellow_labels}, datasets: [{{ data: {fellow_data},
    backgroundColor: ['rgba(160,174,192,0.5)', TEAL+'bb', SAGE+'bb', AMBER+'bb'],
    borderColor: ['#a0aec0', TEAL, SAGE, AMBER], borderWidth: 2, hoverOffset: 8 }}] }},
  options: {{ cutout: '62%', responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ position: 'bottom', labels: {{ padding: 10, font: {{ size: 10 }} }} }},
      tooltip: {{ callbacks: {{ label: c=>`${{c.label}}: ${{Math.round(c.raw/{n_scholars or 1}*100)}}%` }} }} }} }}
}});

const citeData = {cite_data};
const totalCitAll = {total_cit_all};
new Chart(document.getElementById('cCite'), {{
  type: 'bar',
  data: {{ labels: {cite_labels}, datasets: [{{ data: citeData,
    backgroundColor: citeData.map(v=>v>=50?ROSE+'cc':v>=20?AMBER+'cc':v>=8?TEAL+'99':VIO+'88'),
    borderColor: citeData.map(v=>v>=50?ROSE:v>=20?AMBER:v>=8?TEAL:VIO),
    borderWidth: 2, borderRadius: 4 }}] }},
  options: {{ indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }},
      tooltip: {{ callbacks: {{ label: c=>`被引 ${{c.raw}} 次 (${{Math.round(c.raw/(totalCitAll||1)*100)}}%)` }} }} }},
    scales: {{ x: {{ beginAtZero: true, grid: {{ color: 'rgba(180,190,220,0.2)' }} }},
      y: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 10 }} }} }} }} }}
}});

if (document.getElementById('cPosition')) {{
new Chart(document.getElementById('cPosition'), {{
  type: 'bar',
  data: {{ labels: {pos_labels}, datasets: [{{ data: {pos_data},
    backgroundColor: [AMBER+'cc',TEAL+'cc',SAGE+'cc',VIO+'cc',ROSE+'cc',AMBER+'88'],
    borderWidth: 0, borderRadius: 5 }}] }},
  options: {{ indexAxis: 'y', responsive: true, plugins: {{ legend: {{ display: false }} }},
    scales: {{ x: {{ beginAtZero: true, grid: {{ color:'rgba(180,190,220,0.2)' }} }}, y: {{ grid: {{ display: false }} }} }} }}
}});
}}

if (document.getElementById('cSentiment')) {{
new Chart(document.getElementById('cSentiment'), {{
  type: 'doughnut',
  data: {{ labels: ['正面引用','中性引用','批评探讨'],
    datasets: [{{ data: [{sent.get('positive',75)},{sent.get('neutral',20)},{sent.get('critical',5)}],
      backgroundColor: [SAGE+'cc','rgba(160,174,192,0.5)',ROSE+'aa'],
      borderColor: [SAGE,'#a0aec0',ROSE], borderWidth: 2, hoverOffset: 6 }}] }},
  options: {{ cutout:'60%', responsive:true, maintainAspectRatio:true,
    plugins:{{ legend:{{ position:'bottom', labels:{{ padding:8,font:{{size:10}} }} }} }} }}
}});
}}

if (document.getElementById('cDepth')) {{
new Chart(document.getElementById('cDepth'), {{
  type: 'doughnut',
  data: {{ labels: ['核心引用 (方法依据)','参考引用 (背景对比)','补充说明'],
    datasets: [{{ data: {depth_data}, backgroundColor: [TEAL+'cc',SAGE+'cc',VIO+'88'],
      borderColor: [TEAL, SAGE, VIO], borderWidth: 2, hoverOffset: 8 }}] }},
  options: {{ cutout:'55%', responsive:true, maintainAspectRatio:true,
    plugins:{{ legend:{{ position:'bottom', labels:{{ padding:10,font:{{size:10}} }} }} }} }}
}});
}}

new Chart(document.getElementById('cTrend'), {{
  type: 'line',
  data: {{ labels: {trend_labels}, datasets: [
    {{ label: '实际引用', data: {trend_actual}, borderColor: '#7db8f5', backgroundColor: 'transparent',
      pointBackgroundColor: '#7db8f5', pointRadius: 5, tension: .4, borderWidth: 2 }},
    {{ label: '趋势预测', data: {trend_forecast}, borderColor: '#7dd8b0', backgroundColor: 'rgba(125,216,176,0.10)',
      borderDash: [6,4], pointBackgroundColor: '#7dd8b0', pointRadius: 4, tension: .3, borderWidth: 2, fill: true }}
  ] }},
  options: {{ responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ position:'bottom', labels:{{ padding:8, color:'rgba(200,220,255,0.7)', font:{{size:10}} }} }} }},
    scales: {{ y: {{ beginAtZero:true, grid:{{ color:'rgba(255,255,255,0.06)' }},
        ticks:{{ color:'rgba(180,210,255,0.7)', font:{{size:10}} }} }},
      x: {{ grid:{{ color:'rgba(255,255,255,0.04)' }}, ticks:{{ color:'rgba(180,210,255,0.7)', font:{{size:9}}, maxRotation:30 }} }} }} }}
}});
// ── Scholar name tooltip ──────────────────────────────────────────────────
(function() {{
  var tt = document.createElement('div');
  tt.id = 'scholar-tt';
  tt.className = 'scholar-tt';
  document.body.appendChild(tt);
  var hideTimer = null;

  function positionAndShow(anchor) {{
    var papers = JSON.parse(anchor.getAttribute('data-papers') || '[]');
    if (!papers.length) return;
    tt.innerHTML = papers.map(function(p) {{
      var safeT = p.t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      if (p.l) {{
        var safeL = p.l.replace(/"/g,'&quot;');
        return '<a href="' + safeL + '" target="_blank" class="scholar-tt-item">' + safeT + '</a>';
      }}
      return '<span class="scholar-tt-item scholar-tt-item-nolink">' + safeT + '</span>';
    }}).join('');
    tt.style.display = 'block';
    var r = anchor.getBoundingClientRect();
    var top = r.bottom + 6;
    var left = r.left;
    tt.style.top = top + 'px';
    tt.style.left = left + 'px';
    // Clamp to viewport right edge
    var w = tt.offsetWidth;
    if (left + w > window.innerWidth - 12) {{
      tt.style.left = Math.max(8, window.innerWidth - w - 12) + 'px';
    }}
    // Clamp to viewport bottom edge — flip above the row if not enough space
    if (top + tt.offsetHeight > window.innerHeight - 12) {{
      tt.style.top = (r.top - tt.offsetHeight - 6) + 'px';
    }}
  }}

  document.addEventListener('mouseover', function(e) {{
    var anchor = e.target.closest('.sname[data-papers]');
    if (anchor) {{
      clearTimeout(hideTimer);
      positionAndShow(anchor);
      return;
    }}
    if (e.target.closest('#scholar-tt')) {{
      clearTimeout(hideTimer);
      return;
    }}
    hideTimer = setTimeout(function() {{ tt.style.display = 'none'; }}, 180);
  }});
}})();
</script>
{kg_script}

<!-- ═══ CHAT WIDGET ═══ -->
<style>
#cc-fab{{position:fixed;right:24px;bottom:24px;width:54px;height:54px;border-radius:50%;
  background:linear-gradient(135deg,#2563eb,#1e40af);border:none;cursor:pointer;
  font-size:26px;display:flex;align-items:center;justify-content:center;
  box-shadow:0 4px 20px rgba(37,99,235,0.5);z-index:10000;transition:transform .2s,box-shadow .2s;
  color:#fff;line-height:1}}
#cc-fab:hover{{transform:scale(1.1);box-shadow:0 6px 28px rgba(37,99,235,0.65)}}
#cc-win{{position:fixed;right:24px;bottom:90px;width:400px;height:530px;
  background:#0d1421;border:1px solid rgba(66,153,225,0.35);border-radius:16px;
  display:flex;flex-direction:column;z-index:9999;
  box-shadow:0 12px 48px rgba(0,0,0,0.7);
  animation:ccSlideUp .25s ease;overflow:hidden}}
@keyframes ccSlideUp{{from{{opacity:0;transform:translateY(18px)}}to{{opacity:1;transform:none}}}}
#cc-header{{display:flex;align-items:center;justify-content:space-between;
  padding:13px 16px;background:rgba(37,99,235,0.15);
  border-bottom:1px solid rgba(66,153,225,0.2);flex-shrink:0}}
#cc-header-title{{display:flex;align-items:center;gap:8px;font-size:14px;
  font-weight:600;color:#bee3f8}}
#cc-header-sub{{font-size:10px;color:rgba(180,210,255,0.5);margin-top:1px}}
#cc-close{{background:none;border:none;color:rgba(180,210,255,0.5);
  cursor:pointer;font-size:18px;padding:0 2px;line-height:1}}
#cc-close:hover{{color:#fff}}
#cc-msgs{{flex:1;overflow-y:auto;padding:14px 14px 6px;display:flex;
  flex-direction:column;gap:10px;scroll-behavior:smooth}}
#cc-msgs::-webkit-scrollbar{{width:4px}}
#cc-msgs::-webkit-scrollbar-thumb{{background:rgba(66,153,225,0.3);border-radius:2px}}
.cc-bubble{{max-width:88%;padding:9px 13px;border-radius:12px;font-size:12.5px;
  line-height:1.6;word-break:break-word;white-space:pre-wrap}}
.cc-bubble.user{{align-self:flex-end;background:rgba(37,99,235,0.35);
  color:#e2e8f0;border-bottom-right-radius:3px}}
.cc-bubble.ai{{align-self:flex-start;background:rgba(255,255,255,0.05);
  color:#cbd5e0;border-bottom-left-radius:3px;border:1px solid rgba(66,153,225,0.12)}}
.cc-bubble.ai.typing::after{{content:'▌';animation:ccBlink .7s step-end infinite}}
@keyframes ccBlink{{0%,100%{{opacity:1}}50%{{opacity:0}}}}
.cc-bubble.ai.md-rendered{{white-space:normal}}
.cc-bubble.ai p{{margin:0 0 5px}}.cc-bubble.ai p:last-child{{margin-bottom:0}}
.cc-bubble.ai ul,.cc-bubble.ai ol{{padding-left:16px;margin:4px 0}}
.cc-bubble.ai li{{margin:2px 0}}
.cc-bubble.ai strong{{color:#e2e8f0;font-weight:600}}
.cc-bubble.ai a{{color:#63b3ed;text-decoration:underline}}
.cc-bubble.ai code{{background:rgba(0,0,0,0.25);padding:1px 5px;border-radius:3px;font-size:11px;font-family:monospace}}
.cc-bubble.ai pre{{background:rgba(0,0,0,0.3);border:1px solid rgba(66,153,225,0.2);padding:8px 10px;border-radius:6px;overflow-x:auto;margin:6px 0}}
.cc-bubble.ai pre code{{background:none;padding:0}}
.cc-bubble.ai h1,.cc-bubble.ai h2,.cc-bubble.ai h3{{font-size:13px;font-weight:700;margin:8px 0 4px;color:#bee3f8}}
#cc-offline{{text-align:center;padding:16px 12px;font-size:11.5px;color:rgba(180,210,255,0.5);
  background:rgba(255,200,0,0.06);border-top:1px solid rgba(255,200,0,0.15);
  margin:8px 14px;border-radius:8px;display:none}}
#cc-input-row{{display:flex;gap:8px;padding:10px 12px;
  border-top:1px solid rgba(66,153,225,0.15);flex-shrink:0}}
#cc-input{{flex:1;background:rgba(255,255,255,0.06);border:1px solid rgba(66,153,225,0.25);
  border-radius:8px;padding:8px 12px;color:#e2e8f0;font-size:12.5px;
  font-family:'Noto Sans SC',sans-serif;resize:none;outline:none;
  transition:border-color .2s}}
#cc-input:focus{{border-color:rgba(66,153,225,0.6)}}
#cc-input::placeholder{{color:rgba(180,210,255,0.3)}}
#cc-send{{background:linear-gradient(135deg,#2563eb,#1e40af);border:none;
  border-radius:8px;padding:8px 14px;color:#fff;font-size:12px;cursor:pointer;
  flex-shrink:0;transition:opacity .2s;font-family:'Noto Sans SC',sans-serif}}
#cc-send:hover{{opacity:0.85}}
#cc-send:disabled{{opacity:0.4;cursor:default}}
</style>

<button id="cc-fab" onclick="ccToggle()" title="CitationClaw 智能助手">🦞</button>
<div id="cc-win" style="display:none">
  <div id="cc-header">
    <div>
      <div id="cc-header-title">🦞 CitationClaw 智能助手</div>
      <div id="cc-header-sub">基于本报告数据 · AI 驱动</div>
    </div>
    <button id="cc-close" onclick="ccToggle()">✕</button>
  </div>
  <div id="cc-msgs">
    <div class="cc-bubble ai">你好！我是 CitationClaw 智能助手🦞，已读取本报告所有数据。<br>你可以问我：引用趋势、知名学者、关键词分析、各类统计数据等任何问题。</div>
  </div>
  <div id="cc-offline">⚠️ 离线模式：请通过 CitationClaw 应用打开报告以启用 AI 问答功能。</div>
  <div id="cc-input-row">
    <textarea id="cc-input" rows="2" placeholder="问我关于这份报告的问题…（Enter 发送，Shift+Enter 换行）"></textarea>
    <button id="cc-send" onclick="ccSend()">发送</button>
  </div>
</div>

<script>
(function(){{
  var CTX = {_chat_ctx_json};
  var history = [];
  var isOpen = false;
  var isStreaming = false;

  /* ── Toggle window ── */
  window.ccToggle = function() {{
    isOpen = !isOpen;
    var win = document.getElementById('cc-win');
    var fab = document.getElementById('cc-fab');
    win.style.display = isOpen ? 'flex' : 'none';
    fab.textContent = isOpen ? '✕' : '🦞';
    fab.style.fontSize = isOpen ? '20px' : '26px';
    if (isOpen) {{
      // Check if running on server (not file://)
      var offline = window.location.protocol === 'file:';
      document.getElementById('cc-offline').style.display = offline ? 'block' : 'none';
      var input = document.getElementById('cc-input');
      if (input) input.focus();
    }}
  }};

  /* ── Keyboard shortcut: Enter to send ── */
  document.addEventListener('DOMContentLoaded', function() {{
    var inp = document.getElementById('cc-input');
    if (inp) {{
      inp.addEventListener('keydown', function(e) {{
        if (e.key === 'Enter' && !e.shiftKey) {{ e.preventDefault(); ccSend(); }}
      }});
    }}
  }});

  /* ── Append message bubble ── */
  function addBubble(role, text, streaming) {{
    var msgs = document.getElementById('cc-msgs');
    var div = document.createElement('div');
    div.className = 'cc-bubble ' + role + (streaming ? ' typing' : '');
    div.textContent = text;
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
    return div;
  }}

  /* ── Send ── */
  function renderMd(el, text) {{
    el.classList.add('md-rendered');
    el.innerHTML = (typeof marked !== 'undefined') ? marked.parse(text) : text;
  }}

  window.ccSend = function() {{
    if (isStreaming) return;
    if (window.location.protocol === 'file:') return;
    var inp = document.getElementById('cc-input');
    var msg = (inp.value || '').trim();
    if (!msg) return;
    inp.value = '';

    addBubble('user', msg, false);
    history.push({{ role: 'user', content: msg }});

    var aiBubble = addBubble('ai', '', true);
    isStreaming = true;
    var sendBtn = document.getElementById('cc-send');
    if (sendBtn) sendBtn.disabled = true;

    var fullText = '';

    fetch('/api/chat/report', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ messages: history, context: CTX }})
    }}).then(function(res) {{
      if (!res.ok) {{
        return res.text().then(function(t) {{
          aiBubble.classList.remove('typing');
          aiBubble.textContent = '请求失败：' + t;
          isStreaming = false;
          if (sendBtn) sendBtn.disabled = false;
        }});
      }}
      var reader = res.body.getReader();
      var decoder = new TextDecoder('utf-8');
      var searchIndicator = null;
      var buf = '';
      var searchHandled = false;

      function ensureSearchIndicator() {{
        if (!searchIndicator) {{
          searchIndicator = document.createElement('div');
          searchIndicator.style.cssText = 'font-size:11px;color:rgba(99,179,237,0.8);padding:4px 0;';
          searchIndicator.textContent = '🔍 正在联网搜索…';
          aiBubble.parentNode.insertBefore(searchIndicator, aiBubble);
        }}
      }}
      function removeSearchIndicator() {{
        if (searchIndicator) {{ searchIndicator.remove(); searchIndicator = null; }}
      }}

      function read() {{
        reader.read().then(function(result) {{
          if (result.done) {{
            aiBubble.classList.remove('typing');
            removeSearchIndicator();
            renderMd(aiBubble, fullText);
            if (fullText) history.push({{ role: 'assistant', content: fullText }});
            isStreaming = false;
            if (sendBtn) sendBtn.disabled = false;
            return;
          }}
          var chunk = decoder.decode(result.value, {{ stream: true }});
          if (!searchHandled) {{
            buf += chunk;
            if (buf.indexOf('__SEARCHING__') !== -1) {{
              searchHandled = true;
              ensureSearchIndicator();
              var rest = buf.replace('__SEARCHING__\n', '').replace('__SEARCHING__', '');
              if (rest) {{ fullText += rest; aiBubble.textContent = fullText; }}
            }} else if (buf.length > 30) {{
              searchHandled = true;
              fullText += buf; aiBubble.textContent = fullText; buf = '';
            }}
          }} else {{
            removeSearchIndicator();
            fullText += chunk;
            aiBubble.textContent = fullText;
          }}
          document.getElementById('cc-msgs').scrollTop = 9999;
          read();
        }}).catch(function(err) {{
          aiBubble.classList.remove('typing');
          removeSearchIndicator();
          aiBubble.textContent = fullText + '\n[读取中断：' + err + ']';
          isStreaming = false;
          if (sendBtn) sendBtn.disabled = false;
        }});
      }}
      read();
    }}).catch(function(err) {{
      aiBubble.classList.remove('typing');
      aiBubble.textContent = '网络错误：' + err;
      isStreaming = false;
      if (sendBtn) sendBtn.disabled = false;
    }});
  }};
}})();
</script>
</body>
</html>"""
        return html

    # ─────────────────────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────────────────────
    def generate(
        self,
        citing_desc_excel: Path,
        renowned_all_xlsx: Path,
        renowned_top_xlsx: Path,
        output_html: Path,
        canonical_titles: Optional[list] = None,
        download_filenames: Optional[dict] = None,
        skip_citing_analysis: bool = False,
    ) -> Path:
        """
        Full pipeline: load data → LLM analysis → build HTML → write file.
        Returns output_html path.
        """
        self.log("📂 加载 citing_with_description 数据...")
        papers, total_papers, descriptions, citing_pairs, unique_citing_papers, self_citation_count = \
            self._load_citing_data(citing_desc_excel)
        self.log(f"   → {total_papers} 篇论文 / {len(descriptions)} 条有效引用描述")

        self.log("📂 加载知名学者数据...")
        top_scholars, all_scholars = self._load_renowned_scholars(renowned_all_xlsx, renowned_top_xlsx)
        self.log(f"   → {len(all_scholars)} 条学者记录 / 顶尖学者 {len(top_scholars)} 位")

        self.log("📊 计算基础统计...")
        stats = self._compute_stats(papers, total_papers, top_scholars, all_scholars)
        institution_stats = self._compute_institution_stats(papers)

        self.log("🤖 启动 AI 分析...")
        titles = [p["title"] for p in papers]
        keywords = self._analyze_keywords(titles)
        if skip_citing_analysis:
            self.log("⏭ 跳过引用描述分析（dashboard_skip_citing_analysis=True）")
            citation_analysis = {
                "citation_types": [], "citation_positions": [],
                "citation_themes": [],
                "sentiment_distribution": {"positive": 0, "neutral": 0, "critical": 0},
                "key_findings": [],
                "citation_depth": {"core_citation": 0, "reference_citation": 0, "supplementary_citation": 0},
            }
        else:
            citation_analysis = self._analyze_citation_descriptions(descriptions, citing_pairs)
        prediction = self._generate_prediction(papers, stats)
        insights = self._generate_insights(papers, stats, citation_analysis)
        citation_summary = (
            self._summarize_citation_descriptions(descriptions, citing_pairs)
            if (descriptions and not skip_citing_analysis)
            else ""
        )

        self.log("🏗  构建 HTML...")
        html = self._build_html(
            papers, total_papers, top_scholars, all_scholars,
            stats, keywords, citation_analysis, prediction, insights,
            unique_citing_papers=unique_citing_papers,
            download_filenames=download_filenames or {},
            citing_pairs=citing_pairs,
            canonical_titles=canonical_titles or [],
            citation_summary=citation_summary,
            self_citation_count=self_citation_count,
            institution_stats=institution_stats,
        )

        output_html.parent.mkdir(parents=True, exist_ok=True)
        output_html.write_text(html, encoding="utf-8")
        size_kb = len(html.encode()) // 1024
        self.log(f"✅ HTML 报告已生成: {output_html} ({size_kb} KB)")
        return output_html
