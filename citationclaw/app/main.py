import asyncio
from pathlib import Path
from typing import List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from citationclaw.app.config_manager import ConfigManager, AppConfig
from citationclaw.app.task_executor import TaskExecutor
from citationclaw.app.log_manager import LogManager


# FastAPI应用
app = FastAPI(title="论文被引画像智能体", version="1.0.0")

# 静态文件和模板（使用包内路径，兼容 pip install 和本地开发）
_PKG_DIR = Path(__file__).parent.parent
app.mount("/static", StaticFiles(directory=str(_PKG_DIR / "static")), name="static")
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
    author_search_prompt2: str = "这是一篇论文及作者列表。请你根据这篇论文、作者名字和作者单位，去搜索该每位作者的个人信息，输出每位作者的谷歌学术累积引用（如有）、重大学术头衔（比如是否IEEE/ACM/ACL等学术Fellow、中国科学院院士、中国工程院院士、国外院士如欧洲科学院院士、诺贝尔奖得主、图灵奖得主，国家杰青、长江学者、优青、在国外著名机构（例如google，deepmind，meta，openai）就业的人士，或在AI领域的国际知名人物），行政职位（如国内外知名大学的校长或院长）。"
    enable_renowned_scholar_filter: bool = True
    renowned_scholar_model: str = "gemini-3-flash-preview-nothinking"
    renowned_scholar_prompt: str = "这是一篇论文的作者列表信息。现在，请你根据这些作者信息，找到那些国内外享誉盛名的学者。对于中国学者，着重找到那些院士级别、校长等重要行政职务的学者。对于海外学者，着重找到那些来自国际著名研究机构如谷歌、微软（google，deepmind，meta，openai），以及有海外院士头衔的学者。若该作者列表里没有这样的重要学者，则输出\"无\"。"
    enable_author_verification: bool = False
    author_verify_model: str = "gemini-3-pro-preview-search"
    author_verify_prompt: str = "这是一份已经整理好的作者学术信息列表。请你对列表中的每一位作者信息进行真实性校验。"
    enable_citing_description: bool = True
    enable_dashboard: bool = True
    service_tier: str = "full"
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


@app.get("/api/results/list")
async def list_results():
    """列出所有结果文件"""
    results = []

    for dir_path in [Path("data/excel"), Path("data/json"), Path("data/jsonl")]:
        if dir_path.exists():
            for file in dir_path.iterdir():
                if file.is_file():
                    results.append({
                        "name": file.name,
                        "size": file.stat().st_size,
                        "type": file.suffix,
                        "path": str(file),
                        "modified": file.stat().st_mtime
                    })

    # 按修改时间倒序排列
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
    print("论文被引画像智能体启动中...")
    print("=" * 50)


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时"""
    print("应用已关闭")
