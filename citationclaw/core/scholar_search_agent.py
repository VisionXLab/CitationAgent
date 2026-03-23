"""Scholar search agent using search LLM with structured API data.

Instead of Playwright browser search, uses the search-capable LLM
(e.g. gemini-3-flash-preview-search) that the user already configured.
The key advantage over v1.0.9: the LLM receives structured data
(real author names, affiliations, h-index from APIs) instead of
searching blindly from just a paper title.
"""
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from openai import AsyncOpenAI
import httpx


# Scholar tier prompt template
SCHOLAR_SEARCH_PROMPT = """以下是一篇施引论文的作者信息（来自学术数据库 OpenAlex / Semantic Scholar，数据可靠）。

【论文标题】{paper_title}
【作者列表及结构化数据】
{author_data}

请你根据以上结构化数据，结合网络搜索，判断哪些作者属于顶级学者。

判定标准（国内外通用）：
- 院士：中国两院院士、NAE/NAS/FRS/欧洲科学院等国外院士
- Fellow：IEEE/ACM/ACL/AAAI 等国际学术组织 Fellow
- 重大奖项：图灵奖、诺贝尔奖、国家最高科技奖、国家科技进步奖等
- 国家级人才：杰青、长江学者、优青、万人计划
- 知名机构核心：Google/DeepMind/OpenAI 等首席科学家、研究VP
- 大学领导层：知名大学校长/院长

若无顶级学者，直接输出"无"。
若有，对每位顶级学者输出以下格式（用 $$$分隔符$$$ 分隔）：

$$$分隔符$$$
姓名
机构（该作者在此论文中的任职单位，必须与论文署名一致）
国家
职务（在行政单位或著名研究机构的职务或职称）
荣誉称号（所获得的学术头衔或国际重量级头衔）
$$$分隔符$$$

【严格要求——务必遵守】：
1. 绝对严谨：每一条信息必须经过搜索确认，严禁编造或猜测任何荣誉、头衔、职务
2. 姓名与单位必须对得上：如果搜索到的学者单位与上方数据中的机构不一致，说明可能是同名不同人，此时必须放弃该条，宁缺毋滥
3. 只输出真正的顶级学者，普通教授/副教授/研究员不算
4. h-index 仅供参考，不能作为判定依据
5. 不确定的信息宁可不写，不要输出任何未经验证的内容
6. 不要输出判定理由或说明文字，只输出上述格式的学者信息
"""


@dataclass
class ScholarResult:
    name: str = ""
    affiliation: str = ""
    country: str = ""
    position: str = ""
    honors: str = ""
    tier: str = ""


class ScholarSearchAgent:
    """Search for scholars' titles/honors using search LLM with structured API data."""

    def __init__(self, api_key: str = "", base_url: str = "", model: str = "",
                 log_callback=None):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._log = log_callback or (lambda msg: None)
        self._client: Optional[AsyncOpenAI] = None

    def _ensure_client(self):
        if not self._client and self._api_key:
            from citationclaw.core.http_utils import make_async_client
            # Ensure base_url ends with / to avoid path join issues
            base = self._base_url.rstrip("/") + "/" if self._base_url else ""
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=base,
                http_client=make_async_client(timeout=120.0),
            )

    async def search_paper_authors(self, paper_title: str, authors: List[dict]) -> List[ScholarResult]:
        """Search for renowned scholars among a paper's authors.

        Args:
            paper_title: The citing paper's title
            authors: List of author dicts with structured data from APIs
                     Each dict has: name, affiliation, country, h_index, citation_count, etc.

        Returns:
            List of ScholarResult for identified top scholars (may be empty)
        """
        self._ensure_client()
        if not self._client:
            self._log("    ⚠ 搜索LLM未配置，跳过学者搜索")
            return []

        # Build structured author data string
        author_lines = []
        for i, a in enumerate(authors):
            parts = [f"  {i+1}. {a.get('name', '未知')}"]
            if a.get("affiliation"):
                parts.append(f"机构: {a['affiliation']}")
            if a.get("country"):
                parts.append(f"国家: {a['country']}")
            if a.get("h_index"):
                parts.append(f"h-index: {a['h_index']}")
            if a.get("citation_count"):
                parts.append(f"总引用: {a['citation_count']}")
            author_lines.append(" | ".join(parts))

        author_data = "\n".join(author_lines) if author_lines else "无作者信息"

        prompt = SCHOLAR_SEARCH_PROMPT.format(
            paper_title=paper_title,
            author_data=author_data,
        )

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                timeout=60.0,
            )
            text = response.choices[0].message.content.strip()
            return self._parse_response(text)
        except Exception as e:
            self._log(f"    ⚠ 搜索LLM调用失败: {e}")
            return []

    def _parse_response(self, text: str) -> List[ScholarResult]:
        """Parse the LLM response into ScholarResult list."""
        if not text or "无" in text[:10]:
            return []

        results = []
        seen_name_keys: set = set()  # All name variant keys seen so far
        # Split by separator
        parts = text.split("$$$分隔符$$$")
        parts = [p.strip() for p in parts if p.strip() and "无" not in p[:5]]

        for part in parts:
            # Skip "判定依据/理由/说明" blocks that LLM sometimes appends
            if any(k in part[:30] for k in ["判定", "说明", "理由", "注：", "注:", "**判定", "**说明"]):
                continue
            # Skip numbered explanations like "1. **王飞跃..."
            if re.match(r'^\d+[\.\s]', part.strip()):
                continue

            lines = [l.strip() for l in part.strip().split("\n") if l.strip()]
            if len(lines) >= 2:
                r = ScholarResult()
                r.name = self._clean_name(lines[0]) if len(lines) > 0 else ""
                r.affiliation = self._clean_field(lines[1]) if len(lines) > 1 else ""
                r.country = self._normalize_country(self._clean_field(lines[2])) if len(lines) > 2 else ""
                r.position = self._clean_field(lines[3]) if len(lines) > 3 else ""
                r.honors = self._clean_field(lines[4]) if len(lines) > 4 else ""

                # Skip if name looks like an explanation, not a person
                if not r.name or any(k in r.name for k in ["判定", "说明", "理由", "注："]):
                    continue

                r.tier = self._determine_tier(r)
                if not r.tier:
                    continue

                # Deduplicate: check if ANY name variant was already seen
                name_keys = self._extract_name_keys(r.name)
                if name_keys & seen_name_keys:
                    continue  # At least one variant already seen → duplicate
                seen_name_keys.update(name_keys)
                results.append(r)

        return results

    @staticmethod
    def _clean_name(raw: str) -> str:
        """Clean scholar name: remove markdown, field prefixes, etc."""
        name = raw.strip()
        # Remove markdown bold: **Name** → Name
        name = re.sub(r'\*\*', '', name)
        # Remove "姓名：" or "姓名:" prefix
        name = re.sub(r'^姓名[：:]\s*', '', name)
        # Remove trailing colons or special chars
        name = name.strip(':：')
        return name.strip()

    @staticmethod
    def _clean_field(raw: str) -> str:
        """Remove field label prefixes like 机构：, 国家：, 职务：, 荣誉称号："""
        s = raw.strip()
        s = re.sub(r'\*\*', '', s)
        s = re.sub(r'^(机构|国家|职务|荣誉称号|头衔|职位)[：:]\s*', '', s)
        return s.strip()

    @staticmethod
    def _normalize_country(raw: str) -> str:
        """Normalize country names to Chinese."""
        s = raw.strip()
        # Remove parenthetical codes like "(CN)" "（US）"
        s = re.sub(r'[（(]\s*[A-Z]{2,3}\s*[）)]', '', s).strip()
        # Map common codes and English names to Chinese
        _map = {
            'cn': '中国', 'china': '中国', 'chinese': '中国',
            'us': '美国', 'usa': '美国', 'united states': '美国',
            'uk': '英国', 'united kingdom': '英国', 'gb': '英国', 'england': '英国',
            'jp': '日本', 'japan': '日本',
            'de': '德国', 'germany': '德国',
            'fr': '法国', 'france': '法国',
            'it': '意大利', 'italy': '意大利',
            'kr': '韩国', 'south korea': '韩国',
            'au': '澳大利亚', 'australia': '澳大利亚',
            'ca': '加拿大', 'canada': '加拿大',
            'sg': '新加坡', 'singapore': '新加坡',
            'hu': '匈牙利', 'hungary': '匈牙利',
            'in': '印度', 'india': '印度',
            'es': '西班牙', 'spain': '西班牙',
            'nl': '荷兰', 'netherlands': '荷兰',
            'ch': '瑞士', 'switzerland': '瑞士',
            'se': '瑞典', 'sweden': '瑞典',
            'il': '以色列', 'israel': '以色列',
            'hk': '中国香港', 'hong kong': '中国香港',
            'tw': '中国台湾', 'taiwan': '中国台湾',
        }
        # Try direct lookup
        key = s.lower().strip()
        if key in _map:
            return _map[key]
        # Try splitting "CN / HU" → take first
        if '/' in s:
            first = s.split('/')[0].strip()
            fk = first.lower().strip()
            if fk in _map:
                return _map[fk]
        return s  # Return as-is if already Chinese or unrecognized

    @staticmethod
    def _extract_name_keys(name: str) -> set:
        """Extract all name variants for deduplication.

        e.g. "李德仁 (Deren Li)" → {"李德仁", "deren li"}
             "Zhenwei Shi (史振威)" → {"zhenwei shi", "史振威"}
             "Z Y Zou (Zhengxia Zou / 邹征夏)" → {"z y zou", "zhengxia zou", "邹征夏"}
        """
        keys = set()
        cleaned = name.strip()
        if not cleaned:
            return keys

        # Split on parentheses and slashes to get all name variants
        # "李德仁 (Deren Li)" → ["李德仁", "Deren Li"]
        # "Z Y Zou (Zhengxia Zou / 邹征夏)" → ["Z Y Zou", "Zhengxia Zou", "邹征夏"]
        parts = re.split(r'[()（）/／]', cleaned)
        for part in parts:
            p = part.strip().strip(',，、').strip()
            if p and len(p) >= 2:
                keys.add(p.lower())

        # Also add the full string (without parenthetical) as a key
        base = re.sub(r'[（(].*?[）)]', '', cleaned).strip()
        if base and len(base) >= 2:
            keys.add(base.lower())

        return keys

    def _determine_tier(self, scholar: ScholarResult) -> str:
        """Determine scholar tier from their honors and position."""
        text = f"{scholar.honors} {scholar.position}".lower()
        if any(k in text for k in ["院士", "academician", "nae", "nas member", "frs",
                                    "fellow of the royal society", "欧洲科学院"]):
            return "Academician"
        if any(k in text for k in ["ieee fellow", "acm fellow", "acl fellow", "aaai fellow",
                                    "aps fellow", "rsc fellow", "acs fellow",
                                    "ifac fellow", "asme fellow", "aaas fellow",
                                    "iapr fellow", "isca fellow", "incose fellow",
                                    "iet fellow", "aaia fellow"]):
            return "Fellow"
        if any(k in text for k in ["turing", "图灵", "nobel", "诺贝尔", "fields medal",
                                    "国家最高科学技术奖", "国家科技进步", "国家自然科学奖",
                                    "国家技术发明奖", "wolf prize", "沃尔夫奖",
                                    "abel prize", "阿贝尔奖"]):
            return "Major Award Winner"
        if any(k in text for k in ["杰青", "长江", "优青", "万人计划"]):
            return "National Talent (China)"
        if any(k in text for k in ["chief scientist", "首席科学家", "vp of research",
                                    "研究副总裁", "lab director", "实验室主任",
                                    "distinguished scientist"]):
            return "Industry Leader"
        if any(k in text for k in ["校长", "院长", "president", "dean"]):
            return "University Leadership"
        # If we got this far with honors text, it's likely notable
        if scholar.honors and len(scholar.honors) > 5:
            return "Notable Scholar"
        return ""

    async def close(self):
        if self._client:
            await self._client.close()
            self._client = None
