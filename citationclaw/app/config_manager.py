import json
from pathlib import Path
from typing import List
from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    """应用配置模型"""
    # ScraperAPI配置
    scraper_api_keys: List[str] = Field(
        default_factory=list,
        description="ScraperAPI的API Keys列表"
    )

    # OpenAI兼容API配置
    openai_api_key: str = Field(default="", description="OpenAI兼容的API Key")
    openai_base_url: str = Field(default="https://api.gpt.ge/v1/", description="API Base URL")
    openai_model: str = Field(default="gemini-3-flash-preview-search", description="模型名称")

    # 任务配置
    default_output_prefix: str = Field(default="paper", description="默认输出文件前缀")
    sleep_between_pages: int = Field(default=10, description="翻页间隔（秒）")
    sleep_between_authors: float = Field(default=0.5, description="搜索作者间隔（秒）")
    parallel_author_search: int = Field(default=10, description="并行作者搜索数量(1=串行, >1=并行)")

    # 断点续爬
    resume_page_count: int = Field(default=0, description="从第几页继续")

    # 按年份遍历（绕过Google Scholar 1000条限制）
    enable_year_traverse: bool = Field(default=False, description="是否启用按年份遍历模式")

    # 调试模式
    debug_mode: bool = Field(default=False, description="是否启用调试模式（输出详细日志和HTML）")

    # 测试模式
    test_mode: bool = Field(default=False, description="测试模式：跳过真实API调用，使用test/mock_author_info.jsonl中的伪造数据")

    # ScraperAPI高级选项
    scraper_premium: bool = Field(default=False, description="启用ScraperAPI Premium代理")
    scraper_ultra_premium: bool = Field(default=False, description="启用ScraperAPI Ultra Premium代理")
    scraper_session: bool = Field(default=False, description="启用ScraperAPI会话保持（同一代理IP翻页）")
    scholar_no_filter: bool = Field(default=False, description="Google Scholar链接追加&filter=0（显示全部结果不过滤）")
    scraper_geo_rotate: bool = Field(default=False, description="数据中心重试时自动切换国家代码（需Business Plan及以上）")

    # 重试配置
    retry_max_attempts: int = Field(default=3, description="HTTP/登录页错误的最大重试次数")
    retry_intervals: str = Field(default="5,10,20",
                                 description="重试间隔（秒），逗号分隔。如 '10' 表示固定10秒，'5,10,20' 表示依次等待5/10/20秒")
    dc_retry_max_attempts: int = Field(default=5, description="数据中心不一致时的最大重试次数（每次自动切换国家代码）")

    # 作者搜索Prompt配置
    author_search_prompt1: str = Field(
        default="这是一篇论文。请你根据这个paper_link和paper_title，去搜索查阅这篇论文的作者列表，然后输出每个作者的名字及其对应的单位名称。",
        description="搜索作者列表及单位的Prompt"
    )
    author_search_prompt2: str = Field(
        default="""这是一篇论文及作者列表。请你根据这篇论文、作者名字和作者单位，去搜索该每位作者的个人信息，输出每位作者的以下信息：

1. **谷歌学术累积引用**（如有）

2. **重大学术头衔**（严格限定以下类别，其他一概不关注）：
   - **国际顶级奖项得主**：诺贝尔奖（Nobel Prize）、图灵奖（Turing Award）、菲尔兹奖（Fields Medal）、阿贝尔奖（Abel Prize）、沃尔夫奖（Wolf Prize）、克拉福德奖（Crafoord Prize）、奈望林纳奖（Nevanlinna Prize/IMU Abacus Medal）、哥德尔奖（Gödel Prize）、ACM Prize in Computing、IEEE Medal of Honor、富兰克林奖章（Franklin Medal）、科学突破奖（Breakthrough Prize）、拉斯克奖（Lasker Award）、邵逸夫奖（Shaw Prize）等
   - **院士头衔**：中国科学院院士、中国工程院院士、国外院士（如欧洲科学院院士、美国国家科学院院士、美国国家工程院院士、美国艺术与科学院院士、英国皇家学会院士/会士、德国科学院院士、法国科学院院士、瑞典皇家科学院院士等）
   - **学会Fellow**：IEEE Fellow、ACM Fellow、ACL Fellow、AAAI Fellow、AAAS Fellow、SIAM Fellow、APS Fellow、AMS Fellow等

3. **学术机构任职**：国内外顶尖高校（如清华、北大、MIT、Stanford、CMU、Berkeley、Caltech、Harvard、Princeton、Oxford、Cambridge等）或国家级研究机构的教授、研究员职称

**注意**：
- 不搜索杰青、长江学者、优青、万人计划等国内院士以下的头衔
- 不关注公司/企业任职（如Google、DeepMind、OpenAI、Meta AI、Microsoft Research等）
- 只关注纯学术界的院士/Fellow级别和顶级奖项得主""",
        description="搜索作者详细信息的Prompt"
    )

    # 二次筛选大佬配置
    enable_renowned_scholar_filter: bool = Field(default=True, description="是否启用二次筛选重要学者")
    renowned_scholar_model: str = Field(default="gemini-3-flash-preview-nothinking",
                                        description="二次筛选使用的模型（cheaper model）")
    renowned_scholar_prompt: str = Field(
        default=(
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

            "直至所有的重量级作者都被记录下来。记住，无需任何前言后记。"),
        description="二次筛选重要学者的Prompt"
    )

    # 作者信息校验配置
    enable_author_verification: bool = Field(default=False, description="是否启用作者信息真实性校验")
    author_verify_model: str = Field(default="gemini-3-pro-preview-search", description="作者信息校验使用的模型")
    author_verify_prompt: str = Field(
        default=(
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
        ),
        description="作者信息校验的Prompt"
    )

    # 引用描述搜索配置
    enable_citing_description: bool = Field(default=True, description="是否搜索引用描述（Phase 4）")
    enable_dashboard: bool = Field(default=True, description="是否生成 HTML 画像报告（Phase 5）")

    # 引用阈值过滤（Phase 1 后）
    min_citations_filter: int = Field(default=0, description="施引论文引用数阈值：0=不过滤，否则只处理引用数≥此值的论文")

    # 服务分层
    service_tier: str = Field(default="full", description="服务层级预置: full/advanced/basic")
    citing_description_scope: str = Field(default="all",
        description="Phase 4 引用描述搜索范围: all=全部, renowned_only=仅院士/Fellow, specified_only=仅指定学者")
    skip_author_search: bool = Field(default=False, description="是否跳过 Phase 2+3（作者搜索和导出）")
    specified_scholars: str = Field(default="", description="指定学者名单，逗号分隔")
    dashboard_skip_citing_analysis: bool = Field(default=False, description="Dashboard 是否跳过引用描述分析部分")

    dashboard_model: str = Field(default="gemini-3-flash-preview-nothinking",
                                 description="画像报告 LLM 分析使用的模型")

    # 费用追踪配置
    api_access_token: str = Field(default="", description="API中转站系统令牌（用于查询额度，在个人中心获取）")
    api_user_id: str = Field(default="", description="API中转站用户数字ID（在个人中心查看）")


class ConfigManager:
    def __init__(self, config_path: str = "config.json"):
        self.config_path = Path(config_path)
        self.config = self._load()

    def _load(self) -> AppConfig:
        """加载配置"""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return AppConfig(**data)
            except Exception as e:
                print(f"加载配置失败: {e}, 使用默认配置")
                return AppConfig()
        return AppConfig()

    def save(self, config: AppConfig):
        """保存配置"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(config.model_dump(), f, ensure_ascii=False, indent=2)
        self.config = config

    def get(self) -> AppConfig:
        """获取配置"""
        return self.config

    def update(self, **kwargs):
        """更新配置"""
        updated_data = self.config.model_dump()
        updated_data.update(kwargs)
        new_config = AppConfig(**updated_data)
        self.save(new_config)


SERVICE_TIER_PRESETS = {
    "basic": {
        "label": "基础服务 (Basic)",
        "description": "搜索知名学者 + 筛选院士/Fellow（不分析引文描述）",
        "switches": {
            "enable_renowned_scholar_filter": True,
            "enable_citing_description": False,
            "citing_description_scope": "all",
            "skip_author_search": False,
            "specified_scholars": "",
            "enable_dashboard": True,
            "dashboard_skip_citing_analysis": True,
        }
    },
    "advanced": {
        "label": "进阶服务 (Advanced)",
        "description": "搜索知名学者 + 筛选院士/Fellow + 仅搜索大佬论文的引用描述",
        "switches": {
            "enable_renowned_scholar_filter": True,
            "enable_citing_description": True,
            "citing_description_scope": "renowned_only",
            "skip_author_search": False,
            "specified_scholars": "",
            "enable_dashboard": True,
            "dashboard_skip_citing_analysis": False,
        }
    },
    "full": {
        "label": "全面服务 (Full)",
        "description": "搜索知名学者 + 筛选院士/Fellow + 逐篇搜索引用描述",
        "switches": {
            "enable_renowned_scholar_filter": True,
            "enable_citing_description": True,
            "citing_description_scope": "all",
            "skip_author_search": False,
            "specified_scholars": "",
            "enable_dashboard": True,
            "dashboard_skip_citing_analysis": False,
        }
    },
}
