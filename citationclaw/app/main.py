import asyncio
import shutil
from pathlib import Path
from typing import List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from citationclaw.app.config_manager import ConfigManager, AppConfig
from citationclaw.app.task_executor import TaskExecutor
from citationclaw.app.log_manager import LogManager


# FastAPI应用
app = FastAPI(title="CitationClaw", version="1.0.0")

# 静态文件和模板（使用包内路径，兼容 pip install 和本地开发）
_PKG_DIR = Path(__file__).parent.parent
_ROOT_DIR = _PKG_DIR.parent
app.mount("/static", StaticFiles(directory=str(_PKG_DIR / "static")), name="static")
if (_ROOT_DIR / "docs" / "assets").exists():
    app.mount("/docs-assets", StaticFiles(directory=str(_ROOT_DIR / "docs" / "assets")), name="docs-assets")
templates = Jinja2Templates(directory=str(_PKG_DIR / "templates"))

# 全局对象
config_manager = ConfigManager()
log_manager = LogManager()
task_executor = TaskExecutor(log_manager)


# ==================== 页面路由 ====================
@app.get("/")
async def index(request: Request):
    """首页"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/config")
async def config_page(request: Request):
    """配置页面"""
    return templates.TemplateResponse("config.html", {
        "request": request,
        "captured_url": ""
    })


@app.get("/task")
async def task_page(request: Request):
    """任务执行页面"""
    return templates.TemplateResponse("task.html", {"request": request})


@app.get("/results")
async def results_page(request: Request):
    """结果页面"""
    return templates.TemplateResponse("results.html", {"request": request})


# ==================== API路由 ====================
@app.get("/api/config")
async def get_config():
    """获取配置"""
    return config_manager.get().model_dump()


class ConfigUpdate(BaseModel):
    """配置更新模型"""
    scraper_api_keys: list[str]
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    default_output_prefix: str = "paper"
    sleep_between_pages: int = 10
    sleep_between_authors: float = 0.5
    parallel_author_search: int = 10
    resume_page_count: int = 0
    enable_year_traverse: bool = False
    debug_mode: bool = False
    test_mode: bool = False
    scraper_premium: bool = False
    scraper_ultra_premium: bool = False
    scraper_session: bool = False
    scholar_no_filter: bool = False
    retry_max_attempts: int = 3
    retry_intervals: str = "5,10,20"
    dc_retry_max_attempts: int = 3
    author_search_prompt1: str = "这是一篇论文。请你根据这个paper_link和paper_title，去搜索查阅这篇论文的作者列表，然后输出每个作者的名字及其对应的单位名称。"
    author_search_prompt2: str = """这是一篇论文及作者列表。请你根据这篇论文、作者名字和作者单位，去搜索该每位作者的个人信息，输出每位作者的以下信息：

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
    enable_renowned_scholar_filter: bool = True
    renowned_scholar_model: str = "gemini-3-flash-preview-nothinking"
    renowned_scholar_prompt: str = """以上是一篇论文的作者列表信息。
### 任务指南：
1. **严格的高影响力判定 (is_high_impact)**：只保留以下类别的学者（满足任一条件）：
   - **国际顶级奖项得主**：诺贝尔奖、图灵奖、菲尔兹奖、阿贝尔奖、沃尔夫奖、克拉福德奖、奈望林纳奖/IMU Abacus Medal、哥德尔奖、ACM Prize in Computing、IEEE Medal of Honor、科学突破奖、拉斯克奖、邵逸夫奖等
   - **院士头衔**：中科院院士、工程院院士、各国国家科学院/工程院院士、欧洲科学院院士、英国皇家学会会士等
   - **知名学会Fellow**：IEEE Fellow、ACM Fellow、ACL Fellow、AAAI Fellow、AAAS Fellow、SIAM Fellow、APS Fellow等
   **必须排除（不保留）**：
   - 国内院士以下的头衔：杰青、长江学者、优青、万人计划、青千等
   - 公司/企业任职：Google/DeepMind/OpenAI/Meta AI/Microsoft Research等企业员工
   - 普通大学教授、普通研究员
   除此之外，其他人员一律不保留。

2. **无重量级作者**：若作者信息明确说明无重量级作者，或所有作者都不符合上述严格标准，只需要输出'无任何重量级学者'。

3. **有重量级作者**：若存在符合条件的学者，只输出这些顶级学者，进一步总结每位重量级作者的元信息，包括姓名、机构、国家、职务、荣誉称号。每位重量级作者之间用 $$$分隔符$$$ 来隔开，输出格式参考如下：

（输出格式参考）：
$$$分隔符$$$
重量级作者1
姓名
机构（当前最新任职单位）
国家
职务（教授/研究员等学术职称）
荣誉称号（院士/Fellow/顶级奖项得主，必须包含具体奖项名称如'图灵奖得主2018'、'菲尔兹奖得主2014'等）
$$$分隔符$$$
重量级作者2
姓名
机构（当前最新任职单位）
国家
职务（教授/研究员等学术职称）
荣誉称号（院士/Fellow/顶级奖项得主）

直至所有的重量级作者都被记录下来。记住，无需任何前言后记。"""
    enable_author_verification: bool = False
    author_verify_model: str = "gemini-3-pro-preview-search"
    author_verify_prompt: str = """这是一份已经整理好的作者学术信息列表。请你对列表中的每一位作者信息进行真实性校验。你需要执行以下任务：
1. 针对每位作者，核查其姓名、所属单位、谷歌学术引用量、学术头衔、行政职位是否真实存在。
2. 必须通过可靠公开来源进行核验，包括但不限于：
   - 学术数据库：Google Scholar、DBLP、ORCID、ResearchGate、Web of Science
   - 官方奖项网站：诺贝尔奖官网(nobelprize.org)、图灵奖官网(acm.org/turing-award)、菲尔兹奖官网(mathunion.org)、阿贝尔奖官网(abelprize.no)、沃尔夫奖官网(wolffund.org.il)、克拉福德奖官网(crafoordprize.se)、科学突破奖官网(breakthroughprize.org)、邵逸夫奖官网(shawprize.org)
   - 学会官方：IEEE Fellow Directory、ACM Awards、ACL Awards、AAAI Fellows
   - 官方机构：中国科学院官网、中国工程院官网、各国科学院官网
   - 学术主页：大学官网主页、ResearchGate
3. 对每条信息分别标注核验结果，格式为：
   - 正确（Verified）：可被权威来源明确证实。
   - 存疑（Uncertain）：存在部分证据但不充分或信息冲突。
   - 错误（Incorrect）：无法找到可信来源或存在明显错误。
4. 若发现错误或存疑，请给出修正后的准确信息（若能确定）。
5. 对每条核验内容，必须给出对应的来源链接或来源名称。
6. 最终输出结构化结果，包括：作者姓名、原始信息、核验结论、修正信息（如有）、核验来源。
7. 若无法找到任何可信来源，请明确说明"未检索到可信来源支持该信息"，禁止基于推测补充信息。"""
    enable_citing_description: bool = True
    enable_dashboard: bool = True
    service_tier: str = "full"
    min_citations_filter: int = 0
    citing_description_scope: str = "all"
    skip_author_search: bool = False
    specified_scholars: str = ""
    dashboard_skip_citing_analysis: bool = False
    dashboard_model: str = "gemini-3-flash-preview-nothinking"
    api_access_token: str = ""
    api_user_id: str = ""


@app.get("/api/presets")
async def get_presets():
    from citationclaw.app.config_manager import SERVICE_TIER_PRESETS
    return SERVICE_TIER_PRESETS


@app.post("/api/config")
async def save_config(config: ConfigUpdate):
    """保存配置"""
    try:
        new_config = AppConfig(**config.model_dump())
        config_manager.save(new_config)
        return {"status": "success", "message": "配置已保存"}
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": f"配置保存失败: {str(e)}"}
        )


class TaskStartRequest(BaseModel):
    """任务启动请求模型"""
    url: str
    output_prefix: str
    resume_page: int = 0


class YearTraverseResponse(BaseModel):
    enable: bool


@app.post("/api/task/start")
async def start_task(request: TaskStartRequest):
    """启动任务（仅阶段1：抓取引用列表）"""
    if task_executor.is_running:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "任务正在运行中,请等待完成"}
        )

    config = config_manager.get()

    # 创建后台任务（仅执行阶段1）
    task_executor.current_task = asyncio.create_task(
        task_executor.execute_stage1_scraping(
            url=request.url,
            config=config,
            output_prefix=request.output_prefix,
            resume_page=request.resume_page
        )
    )

    return {"status": "success", "message": "阶段1已启动: 开始抓取引用列表"}


@app.post("/api/task/continue")
async def continue_task():
    """继续任务（阶段2和3：搜索作者信息 + 导出）"""
    if task_executor.is_running:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "任务正在运行中,请等待完成"}
        )

    if not task_executor.stage1_result:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "未找到阶段1的结果，请先执行阶段1"}
        )

    # 创建后台任务（执行阶段2和3）
    task_executor.current_task = asyncio.create_task(
        task_executor.execute_stage2_and_3()
    )

    return {"status": "success", "message": "阶段2/3已启动: 开始搜索作者信息"}


@app.post("/api/task/import")
async def import_task(file: UploadFile = File(...)):
    """导入历史抓取记录"""
    import tempfile

    try:
        # 创建临时文件
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.jsonl', delete=False) as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_path = Path(temp_file.name)

        # 执行导入
        config = config_manager.get()
        result = await task_executor.import_history(temp_path, config)

        # 删除临时文件
        temp_path.unlink()

        if result["success"]:
            return {
                "status": "success",
                "file_name": result["file_name"],
                "paper_count": result["paper_count"],
                "file_prefix": result["file_prefix"]
            }
        else:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": result["message"]}
            )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"导入失败: {str(e)}"}
        )


class PaperInput(BaseModel):
    title: str
    aliases: List[str] = []


class RunRequest(BaseModel):
    papers: List[PaperInput]   # 每篇论文：正式标题 + 曾用名列表
    output_prefix: str = "paper"


@app.get("/api/quota/check")
async def check_quota():
    """预检查 LLM API 余额"""
    config = config_manager.get()
    if not config.api_access_token or not config.api_user_id:
        return {"configured": False, "message": "未配置系统令牌或用户ID"}
    from citationclaw.app.cost_tracker import CostTracker
    ct = CostTracker()
    result = await ct.query_llm_quota(config.openai_base_url, config.api_access_token, config.api_user_id)
    if result:
        remaining = result["quota"] / 500_000
        return {
            "configured": True,
            "remaining": round(remaining, 2),
            "remaining_rmb": round(remaining * 2, 2),
        }
    return {"configured": True, "error": "查询失败，令牌可能无效"}


@app.post("/api/run")
async def run_pipeline(request: RunRequest):
    """全自动流水线：论文题目 → 引用URL → 爬取 → 作者搜索 → 导出"""
    if task_executor.is_running:
        return JSONResponse(status_code=400,
            content={"status": "error", "message": "任务运行中，请等待"})

    groups = [{"title": p.title.strip(), "aliases": [a.strip() for a in p.aliases if a.strip()]}
              for p in request.papers if p.title.strip()]
    if not groups:
        return JSONResponse(status_code=400,
            content={"status": "error", "message": "请输入至少一篇论文题目"})

    config = config_manager.get()
    task_executor.current_task = asyncio.create_task(
        task_executor.execute_for_titles(
            paper_groups=groups,
            config=config,
            output_prefix=request.output_prefix,
        )
    )
    total = sum(1 + len(g["aliases"]) for g in groups)
    return {"status": "success", "message": f"已启动，共 {len(groups)} 篇论文（含 {total} 个搜索标题）"}


class ScholarProfileRequest(BaseModel):
    profile_url: str


@app.post("/api/scholar/papers")
async def fetch_scholar_papers(request: ScholarProfileRequest):
    """爬取 Google Scholar 用户主页的论文列表"""
    config = config_manager.get()
    if not config.scraper_api_keys:
        return JSONResponse(status_code=400,
            content={"error": "未配置 ScraperAPI 密钥，请先在配置页设置"})

    from citationclaw.core.scholar_profile_scraper import ScholarProfileScraper
    scraper = ScholarProfileScraper(
        api_keys=config.scraper_api_keys,
        log_callback=print,
        retry_max_attempts=config.retry_max_attempts,
        retry_intervals=config.retry_intervals,
    )
    try:
        papers = await asyncio.to_thread(scraper.fetch_all_papers, request.profile_url)
        return {"papers": papers, "total": len(papers)}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500,
            content={"error": f"爬取失败: {str(e)}"})


class ChatReportRequest(BaseModel):
    """报告智能问答请求"""
    messages: list          # [{"role": "user"|"assistant", "content": "..."}]
    context: dict = {}      # 报告上下文（由 HTML 中嵌入的数据传入）


def _build_report_system_prompt(ctx: dict) -> str:
    targets   = ctx.get("target_papers", [])
    stats     = ctx.get("stats", {})
    scholars  = ctx.get("scholars", [])
    keywords  = ctx.get("keywords", [])
    top_p     = ctx.get("top_papers", [])
    insights  = ctx.get("insights", [])
    ctypes    = ctx.get("citation_types", [])
    cpos      = ctx.get("citation_positions", [])
    findings  = ctx.get("key_findings", [])
    year_dist = ctx.get("year_dist", {})

    def fmt_list(items, fn, limit=20):
        return "\n".join(f"  - {fn(x)}" for x in items[:limit]) or "  （无数据）"

    parts = [
        "你是 CitationClaw🦞 智能分析助手，专门针对以下这份论文被引画像报告回答问题。",
        "请基于报告数据作答，语言简洁专业，必要时引用具体数字。",
        "若问题超出报告数据范围，请如实说明。",
        "",
        "## 目标论文",
        fmt_list(targets, lambda t: t),
        "",
        "## 核心统计",
        f"  - 引用论文总数：{stats.get('total', 'N/A')}",
        f"  - 知名学者数量：{stats.get('scholars', 'N/A')}",
        f"  - 院士/Fellow 数量：{stats.get('fellows', 'N/A')}",
        f"  - 覆盖国家/地区：{stats.get('countries', 'N/A')}",
        f"  - 最高单篇被引量：{stats.get('max_cit', 'N/A')}",
        "",
        "## 知名学者（前30位）",
        fmt_list(scholars, lambda s: f"{s.get('name','')} | {s.get('level','')} | {s.get('country','')}", 30),
        "",
        "## 研究关键词",
        "  " + "、".join(k.get("keyword", "") for k in keywords[:25]),
        "",
        "## 高影响力施引论文（Top 20）",
        fmt_list(top_p, lambda p:
            f"{p.get('title','')} ({p.get('year','')}, 被引{p.get('citations','')}次, {p.get('country','')})", 20),
        "",
        "## 年份分布",
        "  " + "  ".join(f"{y}:{n}" for y, n in sorted(year_dist.items())),
        "",
        "## 引用类型分布",
        fmt_list(ctypes, lambda c: f"{c.get('type','')} {c.get('count','')}篇"),
        "",
        "## 引用位置分布",
        fmt_list(cpos, lambda p: f"{p.get('position','')} {p.get('count','')}篇"),
        "",
        "## AI 关键发现",
        fmt_list(findings, lambda f: f),
        "",
        "## 数据洞察",
        fmt_list(insights, lambda i: f"{i.get('title','')}: {i.get('body','')}"),
    ]
    return "\n".join(parts)


_UI_SYSTEM_PROMPT = """你是 CitationClaw🦞 使用助手，帮助用户操作 CitationClaw 学术引用分析工具。

## CitationClaw 核心功能
- 输入论文题目（或 Google Scholar 主页 URL）→ 自动爬取所有施引文献
- 识别院士/Fellow 等知名学者，生成可视化 HTML 画像报告
- 支持多篇论文批量分析、断点续爬、年份遍历模式（突破1000篇限制）

## 关键配置
- **ScraperAPI Key**：用于爬取 Google Scholar（免费账户有1000积分试用）
- **LLM API Key + Base URL**：推荐 V-API，Search Model 必须支持实时 web search
- **分析层级**：基础版（仅统计）/ 进阶版（院士才查引用原句）/ 全面版（所有施引文献查引用原句）

## 常见问题
- 请求失败/积分不足 → 检查 ScraperAPI Key 余额，建议配置3个以上轮换
- LLM 编造学者信息 → Search Model 必须具备实时 web search 能力
- 引用超过1000篇 → 开启年份遍历模式
- 任务中断 → 设置 resume_page_count 为中断页码重新启动

## 配置指引
如果用户询问如何配置 API、如何快速开始或遇到配置相关问题，请主动引导用户查阅官方配置指引文档：
👉 https://visionxlab.github.io/CitationClaw/guidelines.html
该文档包含完整的安装步骤、API 申请与填写说明、各参数含义及截图示例，是解决配置问题的最佳参考。

请简洁、准确地回答用户关于使用 CitationClaw 的问题，不要涉及报告数据内容。"""


class ChatUIRequest(BaseModel):
    """前端UI助手请求"""
    messages: list  # [{"role": "user"|"assistant", "content": "..."}]


@app.post("/api/chat/ui")
async def chat_ui(request: ChatUIRequest):
    """前端操作引导助手（轻量级，流式返回）"""
    config = config_manager.get()
    if not config.openai_api_key or not config.openai_base_url:
        return JSONResponse(status_code=400,
                            content={"error": "未配置 LLM API"})

    messages_to_send = [{"role": "system", "content": _UI_SYSTEM_PROMPT}] + [
        {"role": m["role"], "content": m["content"]} for m in request.messages
    ]

    def _stream():
        try:
            from openai import OpenAI
            client = OpenAI(api_key=config.openai_api_key,
                            base_url=config.openai_base_url, timeout=60.0)
            stream = client.chat.completions.create(
                model=config.dashboard_model,
                messages=messages_to_send,
                stream=True, max_tokens=800,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            yield f"\n\n[错误：{e}]"

    return StreamingResponse(_stream(), media_type="text/plain; charset=utf-8")


@app.post("/api/chat/report")
async def chat_report(request: ChatReportRequest):
    """报告智能问答：轻量级为主，自动识别需联网搜索的问题并切换模型（流式返回）

    流式协议：
      - 普通回答：直接流式文本
      - 需要搜索：先发送 "__SEARCHING__\\n"，再流式发送最终答案
    """
    config = config_manager.get()
    if not config.openai_api_key or not config.openai_base_url:
        return JSONResponse(status_code=400,
                            content={"error": "未配置 LLM API"})

    system_prompt = _build_report_system_prompt(request.context)
    history = [{"role": m["role"], "content": m["content"]} for m in request.messages]
    last_user_msg = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")

    light_model  = config.dashboard_model   # nothinking 轻量
    search_model = config.openai_model      # 带 web search 能力的模型

    def _stream():
        try:
            from openai import OpenAI
            client = OpenAI(api_key=config.openai_api_key,
                            base_url=config.openai_base_url, timeout=90.0)

            # ── Step 1: 分类器 —— 判断是否需要联网搜索 ──────────────────────
            needs_search = False
            if search_model and search_model != light_model:
                try:
                    cls_resp = client.chat.completions.create(
                        model=light_model,
                        messages=[
                            {"role": "system",
                             "content": "判断用户问题是否需要联网搜索才能回答（报告中未包含的实时信息、最新研究动态等）。只回答 Y 或 N，不要其他内容。"},
                            {"role": "user", "content": last_user_msg},
                        ],
                        stream=False, max_tokens=3,
                    )
                    verdict = (cls_resp.choices[0].message.content or "").strip().upper()
                    needs_search = verdict.startswith("Y")
                except Exception:
                    needs_search = False

            if needs_search:
                # ── Step 2a: 搜索模型获取原始答案 ────────────────────────────
                yield "__SEARCHING__\n"
                try:
                    search_resp = client.chat.completions.create(
                        model=search_model,
                        messages=[{"role": "system", "content": system_prompt}] + history,
                        stream=False, max_tokens=2000,
                    )
                    raw_answer = search_resp.choices[0].message.content or ""
                except Exception as e:
                    raw_answer = f"（搜索失败：{e}）"

                # ── Step 2b: 轻量模型整理搜索结果（流式） ───────────────────
                summarize_msgs = [
                    {"role": "system", "content": system_prompt},
                    *history[:-1],
                    {"role": "user",
                     "content": (f"用户问：{last_user_msg}\n\n"
                                 f"联网搜索结果如下，请结合报告数据，用简洁专业的语言综合作答：\n\n{raw_answer}")},
                ]
                stream = client.chat.completions.create(
                    model=light_model,
                    messages=summarize_msgs,
                    stream=True, max_tokens=1200,
                )
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content

            else:
                # ── Step 2b: 轻量模型直接回答（流式） ───────────────────────
                stream = client.chat.completions.create(
                    model=light_model,
                    messages=[{"role": "system", "content": system_prompt}] + history,
                    stream=True, max_tokens=1500,
                )
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content

        except Exception as e:
            yield f"\n\n[错误：{e}]"

    return StreamingResponse(_stream(), media_type="text/plain; charset=utf-8")


@app.post("/api/task/cancel")
async def cancel_task():
    """取消任务"""
    task_executor.cancel()
    return {"status": "success", "message": "任务取消中..."}


@app.post("/api/task/year-traverse-respond")
async def year_traverse_respond(request: YearTraverseResponse):
    """接收用户对年份遍历提示的响应"""
    if task_executor._year_traverse_event is None:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "当前无等待确认的年份遍历提示"}
        )
    task_executor._year_traverse_choice = request.enable
    task_executor._year_traverse_event.set()
    return {"status": "success", "enable": request.enable}


class APITestRequest(BaseModel):
    """API测试请求模型"""
    api_key: str
    base_url: str
    model: str
    test_query: str = "请告诉我现在的准确日期和时间（年月日时分秒）。"


@app.post("/api/test_openai")
async def test_openai_api(request: APITestRequest):
    """测试OpenAI API是否支持web search"""
    try:
        from openai import OpenAI

        # 创建客户端
        client = OpenAI(
            api_key=request.api_key,
            base_url=request.base_url,
            timeout=60.0
        )

        # 不带web_search_options的测试
        try:
            response_no_web = client.chat.completions.create(
                model=request.model,
                messages=[{"role": "user", "content": request.test_query}],
                temperature=0.1
            )
            result_no_web = response_no_web.choices[0].message.content
        except Exception as e:
            result_no_web = f"错误: {str(e)}"

        # 带web_search_options的测试
        try:
            response_with_web = client.chat.completions.create(
                model=request.model,
                messages=[{"role": "user", "content": request.test_query}],
                temperature=0.1,
                extra_body={"web_search_options": {}}
            )
            result_with_web = response_with_web.choices[0].message.content
        except Exception as e:
            result_with_web = f"错误: {str(e)}"

        # 判断是否支持web search
        has_web_search = "错误" not in result_with_web and result_with_web != result_no_web

        return {
            "status": "success",
            "has_web_search": has_web_search,
            "test_results": {
                "without_web_search": result_no_web,
                "with_web_search": result_with_web
            },
            "message": "✅ API连接成功" if has_web_search else "⚠️ API可用但可能不支持web search"
        }

    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": f"API测试失败: {str(e)}",
                "has_web_search": False
            }
        )


@app.get("/api/task/status")
async def get_task_status():
    """获取任务状态"""
    return task_executor.get_status()


@app.get("/api/results/folders")
async def list_result_folders():
    """列出所有结果文件夹"""
    folders = []
    data_dir = Path("data")
    if data_dir.exists():
        for sub in data_dir.iterdir():
            if not (sub.is_dir() and sub.name.startswith("result-")):
                continue
            files = [f for f in sub.iterdir() if f.is_file()]
            folders.append({
                "name": sub.name,
                "display_name": sub.name,
                "file_count": len(files),
                "modified": max((f.stat().st_mtime for f in files), default=sub.stat().st_mtime),
                "size": sum(f.stat().st_size for f in files),
            })

    # 旧版扁平目录
    legacy_files = []
    for dir_path in [Path("data/excel"), Path("data/json"), Path("data/jsonl")]:
        if dir_path.exists():
            legacy_files.extend([f for f in dir_path.iterdir() if f.is_file()])
    if legacy_files:
        folders.append({
            "name": "__legacy__",
            "display_name": "旧版结果文件",
            "file_count": len(legacy_files),
            "modified": max(f.stat().st_mtime for f in legacy_files),
            "size": sum(f.stat().st_size for f in legacy_files),
        })

    folders.sort(key=lambda x: x["modified"], reverse=True)
    return folders


@app.get("/api/results/list")
async def list_results(folder: str = None):
    """列出结果文件；传 folder 参数时只返回该文件夹内的文件"""
    results = []

    def add_file(file: Path):
        results.append({
            "name": file.name,
            "size": file.stat().st_size,
            "type": file.suffix,
            "path": str(file),
            "modified": file.stat().st_mtime
        })

    if folder == "__legacy__" or (folder is None):
        for dir_path in [Path("data/excel"), Path("data/json"), Path("data/jsonl")]:
            if dir_path.exists():
                for file in dir_path.iterdir():
                    if file.is_file():
                        add_file(file)

    if folder != "__legacy__":
        data_dir = Path("data")
        if data_dir.exists():
            for sub in data_dir.iterdir():
                if sub.is_dir() and sub.name.startswith("result-"):
                    if folder is None or sub.name == folder:
                        for file in sub.iterdir():
                            if file.is_file():
                                add_file(file)

    results.sort(key=lambda x: x["modified"], reverse=True)
    return results


def _safe_data_path(filepath: str) -> Path:
    """规范化路径并验证在 data/ 目录下（防路径穿越）"""
    norm = filepath.replace("\\", "/")
    p = Path(norm)
    try:
        p.resolve().relative_to(Path("data").resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="不允许访问该路径")
    return p


@app.get("/api/results/view/{filepath:path}")
async def view_result_html(filepath: str):
    """在浏览器内查看 HTML 报告（返回 text/html）"""
    p = _safe_data_path(filepath)
    if p.exists() and p.is_file():
        return FileResponse(path=p, media_type="text/html")
    raise HTTPException(status_code=404, detail="文件不存在")


@app.get("/api/results/download/{filepath:path}")
async def download_result(filepath: str):
    """下载结果文件（支持完整相对路径或仅文件名向下兼容）"""
    p = _safe_data_path(filepath)
    # 直接路径命中
    if p.exists() and p.is_file():
        return FileResponse(path=p, filename=p.name, media_type="application/octet-stream")
    # 向下兼容：仅传文件名时在旧目录中查找
    fname = p.name
    for dir_path in [Path("data/excel"), Path("data/json"), Path("data/jsonl")]:
        legacy = dir_path / fname
        if legacy.exists() and legacy.is_file():
            return FileResponse(path=legacy, filename=fname, media_type="application/octet-stream")
    raise HTTPException(status_code=404, detail="文件不存在")


@app.delete("/api/results/folder/{folder_name}")
async def delete_result_folder(folder_name: str):
    """删除 data/result-{timestamp} 文件夹"""
    if not folder_name.startswith("result-"):
        raise HTTPException(status_code=400, detail="只允许删除 result- 开头的文件夹")
    folder_path = Path("data") / folder_name
    try:
        folder_path.resolve().relative_to(Path("data").resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="不允许访问该路径")
    if not folder_path.exists() or not folder_path.is_dir():
        raise HTTPException(status_code=404, detail="文件夹不存在")
    shutil.rmtree(folder_path)
    return {"success": True}


# ==================== WebSocket ====================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket端点,用于实时日志和进度推送"""
    await websocket.accept()
    log_manager.add_websocket(websocket)

    try:
        # 发送历史日志
        await websocket.send_json({
            "type": "history",
            "data": log_manager.get_recent_logs()
        })

        # 保持连接,接收客户端消息(可用于心跳检测)
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
                # 可以处理客户端消息(例如心跳)
            except asyncio.TimeoutError:
                # 发送心跳保持连接
                await websocket.send_json({"type": "ping"})

    except WebSocketDisconnect:
        log_manager.remove_websocket(websocket)
    except Exception as e:
        print(f"WebSocket错误: {e}")
        log_manager.remove_websocket(websocket)


# ==================== 应用启动和关闭事件 ====================
@app.on_event("startup")
async def startup_event():
    """应用启动时"""
    print("=" * 50)
    print("CitationClaw has been activated.")
    print("=" * 50)


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时"""
    print("应用已关闭")
