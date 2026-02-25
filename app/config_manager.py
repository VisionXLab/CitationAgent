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
    parallel_author_search: int = Field(default=5, description="并行作者搜索数量(1=串行, >1=并行)")

    # 断点续爬
    resume_page_count: int = Field(default=0, description="从第几页继续")

    # 按年份遍历（绕过Google Scholar 1000条限制）
    enable_year_traverse: bool = Field(default=False, description="是否启用按年份遍历模式")

    # 调试模式
    debug_mode: bool = Field(default=False, description="是否启用调试模式（输出详细日志和HTML）")

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
        default="这是一篇论文及作者列表。请你根据这篇论文、作者名字和作者单位，去搜索该每位作者的个人信息，输出每位作者的谷歌学术累积引用（如有）、重大学术头衔（比如是否IEEE/ACM/ACL等学术Fellow、中国科学院院士、中国工程院院士、国外院士如欧洲科学院院士、诺贝尔奖得主、图灵奖得主，国家杰青、长江学者、优青，或在AI领域的国际知名人物），行政职位（如国内外知名大学的校长或院长）。",
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
            "1. **高影响力判定 (is_high_impact)**：学术影响力大（院士、知名学会Fellow、国家杰青/长江/优青等）或企业界大佬（首席科学家、VP、负责人）。除此之外，其他级别的学者或教授一律不保留。\n"
            "2. **无重量级作者**：若作者信息明确说明无重量级作者，只需要输出'无任何重量级学者'。\n\n"
            "3. **有重量级作者**：若有重量级作者，只输出那些顶级大佬级别的学者，进一步总结每位重量级作者的元信息，包括姓名、机构、国家、职务、荣誉称号。每位重量级作者之间用 $$$分隔符$$$ 来隔开，输出格式参考如下：\n\n"
            "（输出格式参考）：\n"
            "$$$分隔符$$$\n"
            "重量级作者1\n"
            "姓名\n"
            "机构（当前最新任职单位）\n"
            "国家\n"
            "职务（在行政单位或著名研究机构的职务或职称）\n"
            "荣誉称号（所获得的学术头衔或国际重量级头衔）\n"
            "$$$分隔符$$$\n"
            "重量级作者2\n"
            "姓名\n"
            "机构（当前最新任职单位）\n"
            "国家\n"
            "职务（在行政单位或著名研究机构的职务或职称）\n"
            "荣誉称号（所获得的学术头衔或国际重量级头衔）\n"

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
            "2. 必须通过可靠公开来源进行核验（如Google Scholar、大学官网主页、DBLP、ORCID、ResearchGate、IEEE/ACM/ACL官方Fellow名单、科学院官网、诺奖或图灵奖官网等）。\n"
            "3. 对每条信息分别标注核验结果，格式为：\n"
            "   - 正确（Verified）：可被权威来源明确证实。\n"
            "   - 存疑（Uncertain）：存在部分证据但不充分或信息冲突。\n"
            "   - 错误（Incorrect）：无法找到可信来源或存在明显错误。\n"
            "4. 若发现错误或存疑，请给出修正后的准确信息（若能确定）。\n"
            "5. 对每条核验内容，必须给出对应的来源链接或来源名称。\n"
            "6. 最终输出结构化结构，包括：作者姓名、原始信息、核验结论、修正信息（如有）、核验来源。\n"
            "7. 若无法找到任何可信来源，请明确说明\"未检索到可信来源支持该信息\"，禁止基于推测补充信息。"
        ),
        description="作者信息校验的Prompt"
    )

    # 引用描述搜索配置
    enable_citing_description: bool = Field(default=True, description="是否搜索引用描述（Phase 4）")
    enable_dashboard: bool = Field(default=True, description="是否生成 HTML 画像报告（Phase 5）")
    dashboard_model: str = Field(default="gemini-3-flash-preview-nothinking",
                                 description="画像报告 LLM 分析使用的模型")


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
