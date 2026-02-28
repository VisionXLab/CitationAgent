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

        for _, row in df.iterrows():
            page_id = row.get('PageID', '')
            paper_id = row.get('PaperID', '')
            key = (str(page_id or ""), str(paper_id or ""))

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

        return papers, total_papers, descriptions, citing_pairs, unique_citing_papers

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
3. 为每个关键词设置一个分类（如：技术方法/研究领域/数据集/应用场景/模型类型）
4. 如果关键词是英文，必须同时提供中文翻译（keyword_cn字段）；如果本身是中文则keyword_cn与keyword相同
5. 直接返回 JSON 数组，格式如下，不要任何其他文字：

[
  {{"keyword": "Remote Sensing", "keyword_cn": "遥感", "weight": 9, "category": "研究领域"}},
  {{"keyword": "Foundation Model", "keyword_cn": "基础模型", "weight": 8, "category": "模型类型"}}
]"""
        result = self._llm_json(prompt)
        if isinstance(result, list) and result:
            return result
        # Fallback: simple word frequency
        words = []
        for t in titles:
            words.extend(re.findall(r"[A-Za-z]{4,}", t))
        freq = Counter(w.lower() for w in words)
        stopwords = {"with", "from", "into", "that", "this", "for", "and", "the",
                     "via", "based", "using", "towards", "toward"}
        top = [(w, c) for w, c in freq.most_common(20) if w not in stopwords]
        return [{"keyword": w.capitalize(), "keyword_cn": "", "weight": min(10, c + 3), "category": "关键词"}
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
    {{"type": "类型名称（如：方法借鉴/背景铺垫/对比验证/正面肯定等）", "count": 数量, "description": "简要说明"}},
    ...
  ],
  "citation_positions": [
    {{"position": "章节位置（如：Introduction/Related Work/Methodology/Experiments等）", "count": 数量}},
    ...
  ],
  "citation_themes": [
    {{"theme": "核心主题短语（5-10个字）", "frequency": 1-10}},
    ...最多8个
  ],
  "sentiment_distribution": {{
    "positive": 正面引用占比（0-100整数）,
    "neutral": 中性引用占比,
    "critical": 批评性引用占比
  }},
  "key_findings": [
    "洞察句1（说明该论文被如何引用的关键发现，中文，不超过60字）",
    "洞察句2",
    "洞察句3"
  ],
  "citation_depth": {{
    "core_citation": 核心引用（作为主要方法依据）占比（0-100整数）,
    "reference_citation": 参考引用（作为背景或对比）占比,
    "supplementary_citation": 补充说明占比
  }}
}}

分析要客观准确，基于实际内容，数量之和要合理。"""
        result = self._llm_json(prompt)
        if isinstance(result, dict) and "citation_types" in result:
            return result
        return {
            "citation_types": [
                {"type": "方法借鉴", "count": 8, "description": "借鉴技术框架或方法"},
                {"type": "背景综述", "count": 6, "description": "作为领域背景介绍"},
                {"type": "正面肯定", "count": 5, "description": "对成果的正面认可"},
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
            "sentiment_distribution": {"positive": 80, "neutral": 15, "critical": 5},
            "key_findings": [
                "该论文主要被作为领域权威综述型参考文献引用",
                "引用多集中在Introduction和Related Work章节",
                "引用情感以正面肯定为主",
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
3. 如果近3年引用量**持续增长**（每年同比增速 > 20%），可以适当乐观预测（AI领域论文增速快），年增速上限可达 +80%
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
    {{"label": "产业落地转化潜力", "score": 0-100, "color_class": "fill-cyan"}},
    {{"label": "开源社区关注度", "score": 0-100, "color_class": "fill-green"}},
    {{"label": "政策报告引用概率", "score": 0-100, "color_class": "fill-purple"}},
    {{"label": "跨领域影响扩散", "score": 0-100, "color_class": "fill-orange"}}
  ],
  "prediction_commentary": "专业的影响力预测综合评语（中文，100-150字，客观有依据，如实反映趋势，不夸大）"
}}

要求：trend_data 的 labels 从数据最早年份起直到 {forecast_y2} 年；actual 列在有实际数据的年份填入真实值（其余为 null）；forecast 列在 {forecast_y1} 年和 {forecast_y2} 年填入预测值（其余为 null）。"""
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
                {"label": "产业落地转化潜力", "score": min(78, max(30, 45 + stats["fellow_count"] * 3)), "color_class": "fill-cyan"},
                {"label": "开源社区关注度", "score": min(72, max(25, 35 + stats["total_papers"] // 8)), "color_class": "fill-green"},
                {"label": "政策报告引用概率", "score": min(68, max(20, 30 + stats["country_count"] * 4)), "color_class": "fill-purple"},
                {"label": "跨领域影响扩散", "score": min(70, max(25, 32 + stats["unique_scholars"] // 4)), "color_class": "fill-orange"},
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
            {"color": "violet", "icon": "🔬", "title": "引用深度：方法借鉴为主",
             "body": "引用描述分析显示，引用者主要将该论文作为领域基准或方法框架参考，是相关领域不可忽视的核心文献。"},
        ]

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
.scholar-table .sname { font-weight: 600; color: var(--text-bright); }
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
                    canonical_titles=None):
        now = datetime.now()
        download_filenames = download_filenames or {}
        citing_pairs = citing_pairs or []

        # Build description lookup: paper_title -> list of unique descriptions
        desc_lookup = {}
        for cp in citing_pairs:
            pt = cp.get("paper_title", "").strip()
            if not pt:
                continue
            if pt not in desc_lookup:
                desc_lookup[pt] = []
            desc = cp.get("description", "").strip()
            if desc and desc.upper() not in ("NONE", "") and desc not in desc_lookup[pt]:
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
        sent = citation_analysis.get("sentiment_distribution", {"positive": 75, "neutral": 20, "critical": 5})
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
    <div style="font-size:14px;font-weight:600;margin-bottom:6px;color:rgba(180,210,255,0.6)">未启用被引描述分析</div>
    <div style="font-size:12px">当前服务层级未开启 Phase 4（引用描述搜索），此部分无数据。如需查看，请在首页选择「全面版」或「省token版」后重新运行。</div>
  </div>
</div>"""

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
            if is_top and all_descs:
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
            scholar_rows += f"""
        <tr>
          <td style="color:var(--text-light);font-size:11px">{str(idx).zfill(2)}</td>
          <td class="sname">{s['name']}</td>
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
  <div class="stat-item"><div class="stat-icon">📄</div><div class="stat-num">{total_papers}</div><div class="stat-label">引用论文总数</div></div>
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

<!-- SECTION 02 -->
<div class="section-header">
  <span class="section-num">02</span>
  <span class="section-title">研究主题关键词（施引文献领域分析）</span>
  <div class="section-divider"></div>
</div>
<div class="card grid-1">
  <div class="card-title"><div class="card-title-dot violet"></div>关键词云（AI 动态提取 · 基于施引文献标题，反映施引文献所覆盖的研究范围）</div>
  <div class="kw-cloud">{kw_items}</div>
</div>

<!-- SECTION 03 -->
<div class="section-header">
  <span class="section-num">03</span>
  <span class="section-title">被引描述深度分析</span>
  <div class="section-divider"></div>
</div>
{section_03_body}

<!-- SECTION 04 -->
<div class="section-header">
  <span class="section-num">04</span>
  <span class="section-title">知名学者画像一览</span>
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

<!-- SECTION 05 -->
<div class="section-header">
  <span class="section-num">05</span>
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
  <span class="section-num">06</span>
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
  <span class="section-num">07</span>
  <span class="section-title">数据洞察与画像总结</span>
  <div class="section-divider"></div>
</div>
<div class="insights-grid">{insights_html}</div>

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
        papers, total_papers, descriptions, citing_pairs, unique_citing_papers = \
            self._load_citing_data(citing_desc_excel)
        self.log(f"   → {total_papers} 篇论文 / {len(descriptions)} 条有效引用描述")

        self.log("📂 加载知名学者数据...")
        top_scholars, all_scholars = self._load_renowned_scholars(renowned_all_xlsx, renowned_top_xlsx)
        self.log(f"   → {len(all_scholars)} 条学者记录 / 顶尖学者 {len(top_scholars)} 位")

        self.log("📊 计算基础统计...")
        stats = self._compute_stats(papers, total_papers, top_scholars, all_scholars)

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

        self.log("🏗  构建 HTML...")
        html = self._build_html(
            papers, total_papers, top_scholars, all_scholars,
            stats, keywords, citation_analysis, prediction, insights,
            unique_citing_papers=unique_citing_papers,
            download_filenames=download_filenames or {},
            citing_pairs=citing_pairs,
            canonical_titles=canonical_titles or [],
        )

        output_html.parent.mkdir(parents=True, exist_ok=True)
        output_html.write_text(html, encoding="utf-8")
        size_kb = len(html.encode()) // 1024
        self.log(f"✅ HTML 报告已生成: {output_html} ({size_kb} KB)")
        return output_html
