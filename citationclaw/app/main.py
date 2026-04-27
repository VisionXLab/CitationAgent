import asyncio
import shutil
import re
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
import httpx


def _make_openai_client(api_key: str, base_url: str, timeout: float = 60.0):
    """Create an OpenAI client that bypasses system proxy settings."""
    from openai import OpenAI
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        http_client=httpx.Client(trust_env=False, timeout=timeout),
    )

from citationclaw.app.config_manager import ConfigManager, AppConfig, DATA_DIR
from citationclaw.app.task_executor import TaskExecutor
from citationclaw.app.log_manager import LogManager


# ==================== Lifespan ====================
@asynccontextmanager
async def lifespan(app):
    """Application startup/shutdown lifecycle."""
    print("=" * 50)
    print("CitationClaw v2 has been activated.")
    print("=" * 50)
    yield
    print("应用已关闭")


# FastAPI应用
app = FastAPI(title="CitationClaw v2", version="2.0.0", lifespan=lifespan)

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
task_executor = TaskExecutor(log_manager, config_manager)


# ── Helper: task done callback ──────────────────────────────────────────
def _make_task_done_callback(executor: TaskExecutor, lm: LogManager):
    """Create a done_callback that surfaces task exceptions to the UI."""
    def _cb(task: asyncio.Task):
        try:
            exc = task.exception()
            if exc:
                lm.error(f"任务异常终止: {exc}")
                lm.broadcast_event("task_error", {"error": str(exc)})
        except asyncio.CancelledError:
            pass
        finally:
            executor.is_running = False
    return _cb


def _launch_task(coro):
    """Set is_running, create task, attach done_callback. Returns task."""
    task_executor.is_running = True
    task = asyncio.create_task(coro)
    task.add_done_callback(_make_task_done_callback(task_executor, log_manager))
    task_executor.current_task = task
    return task


# ==================== 页面路由 ====================
@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "now": date.today().strftime("%Y-%m-%d")})


# ==================== API路由 ====================
@app.get("/api/config")
async def get_config():
    return config_manager.get().model_dump()


class ConfigUpdate(BaseModel):
    """配置更新模型"""
    scraper_api_keys: list[str]
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    result_folder_prefix: str = ""
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
    scraper_geo_rotate: bool = False
    retry_max_attempts: int = 3
    retry_intervals: str = "5,10,20"
    dc_retry_max_attempts: int = 3
    author_search_prompt1: str = "这是一篇论文。请你根据这个paper_link和paper_title，去搜索查阅这篇论文的作者列表，然后输出每个作者的名字及其对应的单位名称。"
    author_search_prompt2: str = "这是一篇论文及作者列表。请你根据这篇论文、作者名字和作者单位，去搜索该每位作者的个人信息，输出每位作者的谷歌学术累积引用（如有）、重大学术头衔（比如是否IEEE/ACM/ACL等学术Fellow、中国科学院院士、中国工程院院士、国外院士如欧洲科学院院士、诺贝尔奖得主、图灵奖得主，国家杰青、长江学者、优青、在国外著名机构（例如google，deepmind，meta，openai）就业的人士，或在AI领域的国际知名人物），行政职位（如国内外知名大学的校长或院长）。"
    enable_renowned_scholar_filter: bool = True
    renowned_scholar_model: str = "gemini-3-flash-preview-nothinking"
    renowned_scholar_prompt: str = "这是一篇论文的作者列表信息。现在，请你根据这些作者信息，找到那些国内外享誉盛名的学者。对于中国学者，着重找到那些院士级别、校长等重要行政职务的学者。对于海外学者，着重找到那些来自国际著名研究机构如谷歌、微软（google，deepmind，meta，openai），以及有海外院士头衔的学者。若该作者列表里没有这样的重要学者，则输出\"无\"。"
    enable_author_verification: bool = False
    author_verify_model: str = "gemini-3-pro-preview-search"
    author_verify_prompt: str = "这是一份已经整理好的作者学术信息列表。请你对列表中的每一位作者信息进行真实性校验。"
    enable_citing_description: bool = True
    enable_dashboard: bool = True
    service_tier: str = "basic"
    citing_description_scope: str = "all"
    skip_author_search: bool = False
    specified_scholars: str = ""
    dashboard_skip_citing_analysis: bool = False
    dashboard_model: str = "gemini-3-flash-preview-nothinking"
    s2_api_key: str = ""
    # CORE API Key — optional, enables the CORE aggregator PDF source.
    # Not yet wired into the UI, but listed here so that the UI's
    # round-trip save (GET existing config -> merge with form -> POST)
    # doesn't silently drop it from config.json. Without this field the
    # merged POST body would be validated into ConfigUpdate which would
    # strip the key, and the eventual AppConfig save would overwrite the
    # real value with "".
    core_api_key: str = ""
    mineru_api_token: str = ""
    cdp_debug_port: int = 0
    # 2026-04-20: missing here caused the exact same silent-wipe pattern
    # as `core_api_key` (2026-04-19). Pydantic dropped the field from any
    # UI round-trip save, defaulted it to False, and wrote `enable_pdf_llm_search:
    # false` back to disk — so the LLM fallback silently stopped working
    # even though config.json once had it on.
    enable_pdf_llm_search: bool = False
    # Phase 2 登录检查点（2026-04-20）：Phase 2 开始前自动弹出 publisher
    # 登录页并等待用户确认，cookies 通过 runtime/debug_browser_profile 持久化。
    enable_phase2_login_checkpoint: bool = True
    phase2_login_urls: list[str] = [
        "https://ieeexplore.ieee.org/Xplore/home.jsp",
        "https://www.sciencedirect.com/",
        "https://link.springer.com/",
        "https://dl.acm.org/",
        "https://onlinelibrary.wiley.com/",
    ]
    phase2_login_wait_seconds: int = 180
    # 2026-04-20: post-login CDP auth probe. Silent-wipe-protected
    # (default True, no UI widget yet -- see the `enable_pdf_llm_search`
    # pattern from 2026-04-20).
    enable_phase2_login_probe: bool = True
    # 2026-04-20: login checkpoint sentinel TTL (hours). 0 = always prompt.
    phase2_login_stamp_hours: int = 24
    # 2026-04-21: log noise suppression. Deliberately not exposed as a UI
    # widget — user said "通过 config 手动修改去实现". Defaults to INFO
    # (no change vs before).
    log_min_level: str = "INFO"
    api_access_token: str = ""
    api_user_id: str = ""


@app.get("/api/presets")
async def get_presets():
    from citationclaw.app.config_manager import SERVICE_TIER_PRESETS
    return SERVICE_TIER_PRESETS


@app.get("/api/providers")
async def get_providers():
    """Return LLM provider presets for the setup wizard."""
    from citationclaw.config.provider_manager import ProviderManager
    pm = ProviderManager()
    presets = {}
    for name in pm.list_presets():
        info = pm.get_preset(name)
        presets[name] = {
            "name": info.get("name", name),
            "base_url": info.get("base_url", ""),
            "default_model": info.get("default_model", ""),
        }
    return {"presets": presets}


@app.post("/api/config")
async def save_config(config: ConfigUpdate):
    try:
        data = config.model_dump()
        # Debug: log MinerU token save status
        token = data.get("mineru_api_token", "")
        if token:
            print(f"[CONFIG] MinerU token 已保存: {token[:8]}...({len(token)} chars)")

        # ── Sensitive-key preservation ──
        # For API keys that are often set out-of-band (directly in config.json
        # or via an earlier save) but aren't edited on every UI round-trip,
        # empty-string values in the POST body MUST NOT overwrite a real
        # stored value. Without this guard, saving any other UI field would
        # silently wipe these secrets.
        existing = config_manager.get().model_dump()
        for key in (
            "core_api_key",
            "s2_api_key",
            "mineru_api_token",
            "openai_api_key",
            "api_access_token",
            "api_user_id",
            # 2026-04-20: boolean feature flag. "Not data.get(key)" evaluates
            # to True for False/missing, so a UI POST that omits the field or
            # sends False will NOT flip a previously-on setting back off.
            # Flip only happens through an explicit UI widget which (as of
            # 2026-04-20) does not exist yet.
            "enable_pdf_llm_search",
            # Same silent-wipe protection for the Phase 2 login checkpoint
            # (also default-True with no UI widget yet). Guards against a UI
            # save POSTing `False` from a form that never rendered the field.
            "enable_phase2_login_checkpoint",
            # 2026-04-20: post-login auth probe toggle, same pattern.
            "enable_phase2_login_probe",
            # 2026-04-21: observed CDP port silent-wipe. UI form didn't
            # render a cdp_debug_port input, POSTed 0, ConfigUpdate
            # happily overwrote 9222 with 0 -- pipeline then thought
            # CDP was disabled for the rest of the session. `not
            # data.get(key)` is True for 0 so the existing preservation
            # check already works for ints too.
            "cdp_debug_port",
            # 2026-04-21: new log_min_level. UI doesn't render it
            # (deliberately -- feature is "advanced users only" per
            # user request). Protect from "" / missing.
            "log_min_level",
            # 2026-04-21: observed during UI smoke test -- user clicked
            # something that triggered a config save, POST body had
            # empty list / empty strings for these 3, silent-wipe
            # erased the real values. Symptoms:
            #   scraper_api_keys=[] -> ZeroDivisionError "integer
            #     modulo by zero" in PaperURLFinder._next_key
            #     (`self.key_idx % len(self.api_keys)` with len=0)
            #   openai_base_url=""  -> LLM client construction fails
            #   openai_model=""     -> chat.completions.create fails
            # Protect them the same way as other API keys.
            "scraper_api_keys",
            "openai_base_url",
            "openai_model",
        ):
            if not data.get(key) and existing.get(key):
                data[key] = existing[key]

        new_config = AppConfig(**data)
        config_manager.save(new_config)
        return {"status": "success", "message": "配置已保存"}
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": f"配置保存失败: {str(e)}"}
        )


# ── URL validation helper ──────────────────────────────────────────
_SCHOLAR_URL_RE = re.compile(r'^https?://(scholar\.google\.\w+|scholar\.google\.co\.\w+)')


def _validate_scholar_url(url: str) -> str:
    """Validate that the URL looks like a Google Scholar URL."""
    url = url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL 不能为空")
    if not re.match(r'^https?://', url):
        raise HTTPException(status_code=400, detail="URL 必须以 http:// 或 https:// 开头")
    return url


class TaskStartRequest(BaseModel):
    url: str
    output_prefix: str
    resume_page: int = 0


class YearTraverseResponse(BaseModel):
    enable: bool


@app.post("/api/task/start")
async def start_task(request: TaskStartRequest):
    if task_executor.is_running:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "任务正在运行中,请等待完成"}
        )

    url = _validate_scholar_url(request.url)
    config = config_manager.get()

    _launch_task(
        task_executor.execute_stage1_scraping(
            url=url,
            config=config,
            output_prefix=request.output_prefix,
            resume_page=request.resume_page
        )
    )
    return {"status": "success", "message": "阶段1已启动: 开始抓取引用列表"}


@app.post("/api/task/continue")
async def continue_task():
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

    _launch_task(task_executor.execute_stage2_and_3())
    return {"status": "success", "message": "阶段2/3已启动: 开始搜索作者信息"}


@app.post("/api/task/import")
async def import_task(file: UploadFile = File(...)):
    import tempfile

    temp_path = None
    try:
        content = await file.read()
        if len(content) > 100 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="文件过大，限制 100MB")

        with tempfile.NamedTemporaryFile(mode='wb', suffix='.jsonl', delete=False) as temp_file:
            temp_file.write(content)
            temp_path = Path(temp_file.name)

        config = config_manager.get()
        result = await task_executor.import_history(temp_path, config)

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

    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"导入失败: {str(e)}"}
        )
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


class PaperInput(BaseModel):
    title: str
    aliases: List[str] = []


class RunRequest(BaseModel):
    papers: List[PaperInput]
    output_prefix: str = "paper"


@app.get("/api/quota/check")
async def check_quota():
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
    if task_executor.is_running:
        return JSONResponse(status_code=400,
            content={"status": "error", "message": "任务运行中，请等待"})

    groups = [{"title": p.title.strip(), "aliases": [a.strip() for a in p.aliases if a.strip()]}
              for p in request.papers if p.title.strip()]
    if not groups:
        return JSONResponse(status_code=400,
            content={"status": "error", "message": "请输入至少一篇论文题目"})

    config = config_manager.get()
    _launch_task(
        task_executor.execute_for_titles(
            paper_groups=groups,
            config=config,
            output_prefix=request.output_prefix,
        )
    )
    total = sum(1 + len(g["aliases"]) for g in groups)
    return {"status": "success", "message": f"已启动，共 {len(groups)} 篇论文（含 {total} 个搜索标题）"}


class FromCacheRequest(BaseModel):
    paper_title: str
    output_prefix: str = "cached"


@app.post("/api/run/from-cache")
async def run_from_cache(request: FromCacheRequest):
    if task_executor.is_running:
        return JSONResponse(status_code=400,
            content={"status": "error", "message": "任务运行中，请等待"})

    if not request.paper_title.strip():
        return JSONResponse(status_code=400,
            content={"status": "error", "message": "请输入论文标题"})

    config = config_manager.get()
    _launch_task(
        task_executor.build_report_from_cache(
            paper_title=request.paper_title.strip(),
            config=config,
            output_prefix=request.output_prefix or "cached",
        )
    )
    return {"status": "success", "message": f"已启动缓存报告生成: {request.paper_title.strip()}"}


class ScholarProfileRequest(BaseModel):
    profile_url: str


@app.post("/api/scholar/papers")
async def fetch_scholar_papers(request: ScholarProfileRequest):
    url = _validate_scholar_url(request.profile_url)
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
        papers = await scraper.fetch_all_papers(url)
        return {"papers": papers, "total": len(papers)}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500,
            content={"error": f"爬取失败: {str(e)}"})


class ChatReportRequest(BaseModel):
    messages: list
    context: dict = {}


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
        "你是 CitationClaw v2 智能分析助手，专门针对以下这份论文被引画像报告回答问题。",
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


_UI_SYSTEM_PROMPT = """你是 CitationClaw v2 使用助手，帮助用户操作 CitationClaw v2 学术引用分析工具。

## CitationClaw v2 核心功能
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
https://visionxlab.github.io/CitationClaw/guidelines.html
该文档包含完整的安装步骤、API 申请与填写说明、各参数含义及截图示例，是解决配置问题的最佳参考。

请简洁、准确地回答用户关于使用 CitationClaw v2 的问题，不要涉及报告数据内容。"""


class ChatUIRequest(BaseModel):
    messages: list


@app.post("/api/chat/ui")
async def chat_ui(request: ChatUIRequest):
    config = config_manager.get()
    if not config.openai_api_key or not config.openai_base_url:
        return JSONResponse(status_code=400,
                            content={"error": "未配置 LLM API"})

    messages_to_send = [{"role": "system", "content": _UI_SYSTEM_PROMPT}] + [
        {"role": m["role"], "content": m["content"]} for m in request.messages
    ]

    def _stream():
        try:
            client = _make_openai_client(config.openai_api_key,
                                         config.openai_base_url, timeout=60.0)
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
    config = config_manager.get()
    if not config.openai_api_key or not config.openai_base_url:
        return JSONResponse(status_code=400,
                            content={"error": "未配置 LLM API"})

    system_prompt = _build_report_system_prompt(request.context)
    history = [{"role": m["role"], "content": m["content"]} for m in request.messages]
    last_user_msg = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")

    light_model  = config.dashboard_model
    search_model = config.openai_model

    def _stream():
        try:
            client = _make_openai_client(config.openai_api_key,
                                         config.openai_base_url, timeout=90.0)

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
    task_executor.cancel()
    return {"status": "success", "message": "任务取消中..."}


@app.post("/api/task/year-traverse-respond")
async def year_traverse_respond(request: YearTraverseResponse):
    if task_executor._year_traverse_event is None:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "当前无等待确认的年份遍历提示"}
        )
    task_executor._year_traverse_choice = request.enable
    task_executor._year_traverse_event.set()
    return {"status": "success", "enable": request.enable}


@app.post("/api/task/phase2-login-ready")
async def phase2_login_ready():
    """Unblock the Phase 2 login checkpoint.

    Called by the UI modal's 继续 button once the user has finished
    signing in to IEEE / Springer / Elsevier in the auto-launched
    debug browser. A matching server-side `asyncio.Event` in
    `TaskExecutor._prompt_phase2_login` is released; the pipeline then
    continues into metadata + PDF download with fresh publisher cookies.
    """
    if task_executor._phase2_login_event is None:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "当前无等待确认的 Phase 2 登录提示"}
        )
    task_executor._phase2_login_event.set()
    return {"status": "success", "message": "登录已确认，Phase 2 继续"}


class APITestRequest(BaseModel):
    api_key: str
    base_url: str
    model: str
    test_query: str = "请告诉我现在的准确日期和时间（年月日时分秒）。"


@app.post("/api/test_openai")
async def test_openai_api(request: APITestRequest):
    try:
        client = _make_openai_client(request.api_key, request.base_url, timeout=60.0)

        try:
            response_no_web = client.chat.completions.create(
                model=request.model,
                messages=[{"role": "user", "content": request.test_query}],
                temperature=0.1
            )
            result_no_web = response_no_web.choices[0].message.content
        except Exception as e:
            result_no_web = f"错误: {str(e)}"

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

        has_web_search = "错误" not in result_with_web and result_with_web != result_no_web

        return {
            "status": "success",
            "has_web_search": has_web_search,
            "test_results": {
                "without_web_search": result_no_web,
                "with_web_search": result_with_web
            },
            "message": "API连接成功" if has_web_search else "API可用但可能不支持web search"
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


# ── Lightweight pre-test endpoints ───────────────────────────────────────
class PretestRequest(BaseModel):
    api_key: str
    base_url: str
    model: str


@app.post("/api/pretest/search_llm")
async def pretest_search_llm(req: PretestRequest):
    """Quick test: verify Search LLM with web_search_options works."""
    from datetime import datetime
    try:
        client = _make_openai_client(req.api_key, req.base_url, timeout=30.0)
        resp = client.chat.completions.create(
            model=req.model,
            messages=[{"role": "user", "content": "请告诉我现在的准确日期和时间。"}],
            temperature=0.0,
            extra_body={"web_search_options": {}},
        )
        answer = resp.choices[0].message.content or ""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {"status": "success", "message": f"Search LLM 可用 ✓（{now}）", "reply": answer}
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": f"Search LLM 不可用: {e}"})


@app.post("/api/pretest/light_model")
async def pretest_light_model(req: PretestRequest):
    """Quick test: verify lightweight model works."""
    from datetime import datetime
    try:
        client = _make_openai_client(req.api_key, req.base_url, timeout=30.0)
        resp = client.chat.completions.create(
            model=req.model,
            messages=[{"role": "user", "content": "请回复OK两个字母。"}],
            temperature=0.0,
        )
        answer = resp.choices[0].message.content or ""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {"status": "success", "message": f"轻量模型可用 ✓（{now}）", "reply": answer}
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": f"轻量模型不可用: {e}"})



@app.get("/api/task/status")
async def get_task_status():
    return task_executor.get_status()


# ── Results endpoints with absolute path ──────────────────────────────────

@app.get("/api/results/folders")
async def list_result_folders():
    folders = []
    if DATA_DIR.exists():
        for sub in DATA_DIR.iterdir():
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
    for dir_path in [DATA_DIR / "excel", DATA_DIR / "json", DATA_DIR / "jsonl"]:
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
        for dir_path in [DATA_DIR / "excel", DATA_DIR / "json", DATA_DIR / "jsonl"]:
            if dir_path.exists():
                for file in dir_path.iterdir():
                    if file.is_file():
                        add_file(file)

    if folder != "__legacy__":
        if DATA_DIR.exists():
            for sub in DATA_DIR.iterdir():
                if sub.is_dir() and sub.name.startswith("result-"):
                    if folder is None or sub.name == folder:
                        for file in sub.iterdir():
                            if file.is_file():
                                add_file(file)

    results.sort(key=lambda x: x["modified"], reverse=True)
    return results


def _safe_data_path(filepath: str) -> Path:
    """Normalize path and verify it's inside DATA_DIR (prevent traversal)."""
    norm = filepath.replace("\\", "/")
    p = Path(norm)
    resolved = p.resolve()
    try:
        resolved.relative_to(DATA_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="不允许访问该路径")
    return resolved


@app.get("/api/results/view/{filepath:path}")
async def view_result_html(filepath: str):
    p = _safe_data_path(filepath)
    if p.exists() and p.is_file():
        return FileResponse(path=p, media_type="text/html")
    raise HTTPException(status_code=404, detail="文件不存在")


@app.get("/api/results/download/{filepath:path}")
async def download_result(filepath: str):
    p = _safe_data_path(filepath)
    if p.exists() and p.is_file():
        return FileResponse(path=p, filename=p.name, media_type="application/octet-stream")
    # 向下兼容：仅传文件名时在旧目录中查找
    fname = Path(filepath).name
    for dir_path in [DATA_DIR / "excel", DATA_DIR / "json", DATA_DIR / "jsonl"]:
        legacy = dir_path / fname
        if legacy.exists() and legacy.is_file():
            return FileResponse(path=legacy, filename=fname, media_type="application/octet-stream")
    raise HTTPException(status_code=404, detail="文件不存在")


@app.delete("/api/results/folder/{folder_name}")
async def delete_result_folder(folder_name: str):
    if not folder_name.startswith("result-"):
        raise HTTPException(status_code=400, detail="只允许删除 result- 开头的文件夹")
    # Reject any path traversal characters
    if "/" in folder_name or "\\" in folder_name or ".." in folder_name:
        raise HTTPException(status_code=400, detail="文件夹名称包含非法字符")
    folder_path = DATA_DIR / folder_name
    try:
        folder_path.resolve().relative_to(DATA_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="不允许访问该路径")
    if not folder_path.exists() or not folder_path.is_dir():
        raise HTTPException(status_code=404, detail="文件夹不存在")
    shutil.rmtree(folder_path)
    return {"success": True}


# ==================== WebSocket ====================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    log_manager.add_websocket(websocket)

    try:
        await websocket.send_json({
            "type": "history",
            "data": log_manager.get_recent_logs()
        })

        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
            except asyncio.TimeoutError:
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break  # connection lost

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket错误: {e}")
    finally:
        log_manager.remove_websocket(websocket)
