#!/usr/bin/env python3
"""
core/dashboard_test.py  ——  独立运行 Phase 5（DashboardGenerator）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
无需从头爬取，直接指定已有的 Excel 文件即可生成画像报告 HTML。

用法一：只指定主 Excel，其余全部自动推断 / 读 config.json
  python core/dashboard_test.py data/result-20260225/paper_results.xlsx

用法二：完整指定所有路径和参数
  python core/dashboard_test.py data/result-20260225/paper_results.xlsx \\
      --renowned-all data/result-20260225/paper_results_all_renowned_scholar.xlsx \\
      --renowned-top data/result-20260225/paper_results_top-tier_scholar.xlsx \\
      --output       my_report.html \\
      --api-key      sk-xxxx \\
      --base-url     https://api.example.com/v1/ \\
      --model        gemini-3-flash-preview-nothinking

用法三：不带任何参数，脚本会交互式询问
  python core/dashboard_test.py

说明：
  主 Excel 可以是普通结果文件（*_results.xlsx）或带引用描述的文件
  （*_results_with_citing_desc.xlsx）。著名学者/顶尖学者文件未指定时，
  脚本会在同目录下按命名规范自动搜索；若不存在则报告中对应部分留空。
  API 配置未指定时优先读取项目根目录的 config.json。
"""

import argparse
import sys
from pathlib import Path

# ── 确保能导入项目根目录下的模块 ────────────────────────────────────────
_HERE = Path(__file__).parent.resolve()
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    """从 config.json 读取 API 配置，失败时返回空字典。"""
    try:
        from citationclaw.app.config_manager import ConfigManager
        cm = ConfigManager(str(_ROOT / "config.json"))
        cfg = cm.get()
        return {
            "api_key":  cfg.openai_api_key,
            "base_url": cfg.openai_base_url,
            "model":    cfg.dashboard_model,
        }
    except Exception as e:
        print(f"[提示] 无法加载 config.json: {e}")
        return {"api_key": "", "base_url": "", "model": ""}


def _find_companion(base: Path, suffix: str) -> Path:
    """
    在 base 所在目录中寻找伴随文件。
    先尝试 base.stem + suffix + ".xlsx"，再搜索 *{suffix}.xlsx。
    """
    # 优先：把 suffix 拼到 stem 后面
    candidate = base.parent / (base.stem + suffix + ".xlsx")
    if candidate.exists():
        return candidate
    # 次选：目录中任意匹配项
    matches = sorted(base.parent.glob(f"*{suffix}.xlsx"))
    if matches:
        return matches[0]
    return candidate   # 不存在也返回（generate() 内部能容错）


def _prompt(label: str, default: str = "") -> str:
    """交互式输入，带默认值提示。"""
    hint = f" [{default}]" if default else ""
    val = input(f"{label}{hint}: ").strip().strip('"').strip("'")
    return val or default


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="直接生成画像报告 HTML（Phase 5），无需从头爬取",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "excel",
        nargs="?",
        help="主 Excel 文件路径（_results.xlsx 或 _with_citing_desc.xlsx）",
    )
    parser.add_argument(
        "--renowned-all", metavar="PATH",
        help="著名学者 Excel（_all_renowned_scholar.xlsx）；缺省则自动推断",
    )
    parser.add_argument(
        "--renowned-top", metavar="PATH",
        help="顶尖学者 Excel（_top-tier_scholar.xlsx）；缺省则自动推断",
    )
    parser.add_argument(
        "--output", "-o", metavar="PATH",
        help="输出 HTML 路径；缺省则在同目录下以原文件名 + _dashboard.html 命名",
    )
    parser.add_argument("--api-key",  metavar="KEY",  help="OpenAI 兼容 API Key（缺省读 config.json）")
    parser.add_argument("--base-url", metavar="URL",  help="API Base URL（缺省读 config.json）")
    parser.add_argument("--model",    metavar="NAME", help="LLM 模型名（缺省读 config.json）")
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="跳过确认提示，直接执行",
    )

    args = parser.parse_args()

    print("\n━━━  Citation Dashboard Generator  ━━━\n")

    # ── 1. 主 Excel ───────────────────────────────────────────────────────
    if args.excel:
        excel_path = Path(args.excel).resolve()
    else:
        raw = _prompt("主 Excel 文件路径")
        if not raw:
            print("[错误] 必须提供主 Excel 文件路径。", file=sys.stderr)
            sys.exit(1)
        excel_path = Path(raw).resolve()

    if not excel_path.exists():
        print(f"[错误] 文件不存在: {excel_path}", file=sys.stderr)
        sys.exit(1)

    # ── 2. 伴随学者文件（自动推断或手动指定） ─────────────────────────────
    if args.renowned_all:
        renowned_all = Path(args.renowned_all).resolve()
    else:
        renowned_all = _find_companion(excel_path, "_all_renowned_scholar")

    if args.renowned_top:
        renowned_top = Path(args.renowned_top).resolve()
    else:
        renowned_top = _find_companion(excel_path, "_top-tier_scholar")

    # ── 3. 输出路径 ───────────────────────────────────────────────────────
    if args.output:
        output_html = Path(args.output).resolve()
    else:
        output_html = excel_path.parent / (excel_path.stem + "_dashboard.html")

    # ── 4. API 配置 ───────────────────────────────────────────────────────
    cfg = _load_config()
    api_key  = args.api_key  or cfg.get("api_key", "")
    base_url = args.base_url or cfg.get("base_url", "")
    model    = args.model    or cfg.get("model", "")

    if not api_key:
        api_key = _prompt("OpenAI 兼容 API Key")
    if not base_url:
        base_url = _prompt("API Base URL", "https://api.gpt.ge/v1/")
    if not model:
        model = _prompt("LLM 模型名", "gemini-3-flash-preview-nothinking")

    # ── 5. 打印计划 ───────────────────────────────────────────────────────
    def _flag(p: Path) -> str:
        return "✓" if p.exists() else "✗ (不存在，对应部分将留空)"

    print("─── 文件配置 ────────────────────────────────────────────────")
    print(f"  主数据    : {excel_path}")
    print(f"  著名学者  : {renowned_all}  {_flag(renowned_all)}")
    print(f"  顶尖学者  : {renowned_top}  {_flag(renowned_top)}")
    print(f"  输出 HTML : {output_html}")
    print()
    print("─── API 配置 ─────────────────────────────────────────────────")
    print(f"  Base URL  : {base_url}")
    print(f"  Model     : {model}")
    masked = ("*" * min(6, len(api_key)) + "..." + api_key[-4:]) if len(api_key) > 4 else "[未设置]"
    print(f"  API Key   : {masked}")
    print()

    if not args.yes:
        confirm = input("确认开始生成？[Y/n] ").strip().lower()
        if confirm and confirm not in ("y", "yes"):
            print("已取消。")
            sys.exit(0)

    print()

    # ── 6. download_filenames（用于报告内的下载链接） ─────────────────────
    download_filenames = {
        "excel":        excel_path.name,
        "all_renowned": renowned_all.name if renowned_all.exists() else "",
        "top_renowned": renowned_top.name if renowned_top.exists() else "",
    }

    # ── 7. 调用 DashboardGenerator ────────────────────────────────────────
    from citationclaw.core.dashboard_generator import DashboardGenerator

    def log(msg: str):
        print(f"  {msg}")

    gen = DashboardGenerator(
        api_key=api_key,
        base_url=base_url,
        model=model,
        log_callback=log,
    )

    print("▶ 启动 Phase 5 …\n")
    try:
        out = gen.generate(
            citing_desc_excel=excel_path,
            renowned_all_xlsx=renowned_all,
            renowned_top_xlsx=renowned_top,
            output_html=output_html,
            download_filenames=download_filenames,
        )
        print(f"\n{'─'*60}")
        print(f"  ✅  报告已生成：{out}")
        print(f"{'─'*60}\n")
    except Exception as e:
        import traceback
        print(f"\n[错误] 生成失败: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
