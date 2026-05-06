"""Compare latest CitationClaw PDF-download results against a baseline run.

Inputs (read-only):
  - data/result-20260419_174759/paper_results.json      (latest run, V2)
  - test_data/林铮老师论文被引分析/A highly efficient model to study the semantics of salient object detection/test_results.xlsx  (baseline)

Output:
  - data/result-20260419_174759/pdf_download_comparison.xlsx  (5 sheets)

This script does NOT modify any existing code or data.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
NEW_JSON = REPO / "data" / "result-20260419_174759" / "paper_results.json"
BASELINE_XLSX = (
    REPO
    / "test_data"
    / "林铮老师论文被引分析"
    / "A highly efficient model to study the semantics of salient object detection"
    / "test_results.xlsx"
)
OUT_XLSX = REPO / "data" / "result-20260419_174759" / "pdf_download_comparison.xlsx"


# ────────────────────────────── helpers ──────────────────────────────

def norm_title(t) -> str:
    if t is None:
        return ""
    return str(t).strip().lower()


def extract_domain(paper_link: str, pdf_url: str) -> str:
    for url in (paper_link, pdf_url):
        if not url or not isinstance(url, str):
            continue
        try:
            d = urlparse(url).netloc.lower()
            if d:
                return d.removeprefix("www.")
        except Exception:
            continue
    return ""


_DOMAIN_CATEGORY = [
    ("ieeexplore.ieee.org", "IEEE"),
    ("ieee.org", "IEEE"),
    ("dl.acm.org", "ACM"),
    ("acm.org", "ACM"),
    ("link.springer.com", "Springer"),
    ("springer.com", "Springer"),
    ("springeropen.com", "Springer"),
    ("sciencedirect.com", "Elsevier"),
    ("elsevier.com", "Elsevier"),
    ("onlinelibrary.wiley.com", "Wiley"),
    ("wiley.com", "Wiley"),
    ("openaccess.thecvf.com", "CVF"),
    ("thecvf.com", "CVF"),
    ("arxiv.org", "arXiv"),
    ("tandfonline.com", "TaylorFrancis"),
    ("sagepub.com", "SAGE"),
    ("spiedigitallibrary.org", "SPIE"),
    ("drive.google.com", "GoogleDrive"),
    ("mdpi.com", "MDPI"),
    ("nature.com", "Nature"),
    ("openreview.net", "OpenReview"),
    ("aclanthology.org", "ACL"),
    ("proceedings.mlr.press", "PMLR"),
    ("jmlr.org", "JMLR"),
    ("neurips.cc", "NeurIPS"),
    ("openaccess.com", "OpenAccess"),
    ("aaai.org", "AAAI"),
    ("ijcai.org", "IJCAI"),
    ("isprs.org", "ISPRS"),
    ("frontiersin.org", "Frontiers"),
    ("semanticscholar.org", "SemanticScholar"),
]


def categorize(domain: str) -> str:
    if not domain:
        return "Unknown"
    for needle, label in _DOMAIN_CATEGORY:
        if needle in domain:
            return label
    return "Other"


_REASON_BY_CATEGORY = {
    "IEEE": "IEEE 需 CDP 登录会话；若当前运行未建立 CDP 浏览器会话或会话 cookie 失效，120s 认证超时后 fallback 链也不命中",
    "ACM": "ACM Digital Library 需订阅；无专用 CDP handler，依赖 Sci-Hub / Unpaywall 兜底；DOI 未被收录即失败",
    "Springer": "Springer 需订阅；无专用 CDP handler，依赖 Sci-Hub / Unpaywall 兜底",
    "Elsevier": "Elsevier 需 CDP 登录会话；Cloudflare Turnstile 未通过或 pdfDownload 元数据解析失败",
    "Wiley": "Wiley 需订阅；无专用 CDP handler，依赖 Sci-Hub / Unpaywall 兜底",
    "CVF": "CVF openaccess 通常可直链下载；URL 变体改写未命中（如新年份路径结构变化）或 S2/OA 未记录",
    "arXiv": "arXiv ID 不存在 / 版本错配 / 网络瞬时失败；CDP/ScraperAPI 对 arxiv 无特殊兜底",
    "TaylorFrancis": "Taylor & Francis 需订阅；无专用 CDP handler，依赖 Sci-Hub / Unpaywall 兜底",
    "SAGE": "SAGE 需订阅；无专用 CDP handler，依赖 Sci-Hub / Unpaywall 兜底",
    "SPIE": "SPIE Digital Library 需订阅；无专用 CDP handler，依赖 Sci-Hub / Unpaywall 兜底",
    "GoogleDrive": "Google Drive 个人分享链；可能需要登录、被撤回或分享受限",
    "MDPI": "MDPI 开放获取；URL 变体 '/pdf' 改写未命中或服务端临时错误",
    "Wiley": "Wiley 需订阅；依赖 Sci-Hub / Unpaywall 兜底",
    "OpenReview": "OpenReview URL 改写未命中或论文为隐私/已撤回",
    "Nature": "Nature 需订阅；依赖 Sci-Hub / Unpaywall 兜底",
    "Other": "未在已知域名映射中命中，可能是小众期刊站点或临时网络故障",
    "Unknown": "无法解析域名（Paper_Link 与 pdf_url 均为空）",
}


# ────────────────────────────── load ──────────────────────────────

def load_new_run() -> pd.DataFrame:
    with NEW_JSON.open("r", encoding="utf-8") as f:
        data = json.load(f)
    rows = []
    for p in data:
        rows.append({
            "Paper_Title": p.get("Paper_Title") or "",
            "Paper_Year": p.get("Paper_Year"),
            "Venue": p.get("Venue") or "",
            "Paper_Link": p.get("Paper_Link") or "",
            "pdf_url": p.get("pdf_url") or "",
            "PDF_Download": bool(p.get("PDF_Download")),
            "PDF_Path": p.get("PDF_Path") or "",
            "Data_Sources": p.get("Data_Sources") or "",
        })
    df = pd.DataFrame(rows)
    df["domain"] = [extract_domain(r.Paper_Link, r.pdf_url) for r in df.itertuples()]
    df["category"] = df["domain"].map(categorize)
    df["title_norm"] = df["Paper_Title"].map(norm_title)
    return df


def load_baseline() -> pd.DataFrame:
    df = pd.read_excel(BASELINE_XLSX)
    df = df.rename(columns=str)
    df["Paper_Title"] = df["Paper_Title"].fillna("").astype(str)
    df["Paper_Link"] = df["Paper_Link"].fillna("").astype(str)
    df["pdf_url"] = df["pdf_url"].fillna("").astype(str)
    df["PDF_Download"] = df["PDF_Download"].fillna(False).astype(bool)
    df["PDF_Path"] = df["PDF_Path"].fillna("").astype(str)
    df["domain"] = [extract_domain(r.Paper_Link, r.pdf_url) for r in df.itertuples()]
    df["category"] = df["domain"].map(categorize)
    df["title_norm"] = df["Paper_Title"].map(norm_title)
    return df


# ────────────────────────────── sheets ──────────────────────────────

def build_summary(new_df: pd.DataFrame, base_df: pd.DataFrame) -> pd.DataFrame:
    new_total = len(new_df)
    new_ok = int(new_df["PDF_Download"].sum())
    base_total = len(base_df)
    base_ok = int(base_df["PDF_Download"].sum())
    new_rate = new_ok / new_total * 100 if new_total else 0
    base_rate = base_ok / base_total * 100 if base_total else 0

    # title-matched transitions
    new_idx = new_df.set_index("title_norm")["PDF_Download"]
    base_idx = base_df.set_index("title_norm")["PDF_Download"]
    common = sorted(set(new_idx.index) & set(base_idx.index) - {""})
    trans = Counter()
    for t in common:
        b = bool(base_idx.loc[t]) if not isinstance(base_idx.loc[t], pd.Series) else bool(base_idx.loc[t].iloc[0])
        n = bool(new_idx.loc[t]) if not isinstance(new_idx.loc[t], pd.Series) else bool(new_idx.loc[t].iloc[0])
        key = f"{'succ' if b else 'fail'}→{'succ' if n else 'fail'}"
        trans[key] += 1

    rows = [
        {"Metric": "总施引论文数", "Baseline": base_total, "New_Run": new_total, "Delta": new_total - base_total},
        {"Metric": "PDF 下载成功", "Baseline": base_ok, "New_Run": new_ok, "Delta": new_ok - base_ok},
        {"Metric": "PDF 下载失败", "Baseline": base_total - base_ok, "New_Run": new_total - new_ok, "Delta": (new_total - new_ok) - (base_total - base_ok)},
        {"Metric": "成功率 (%)", "Baseline": round(base_rate, 1), "New_Run": round(new_rate, 1), "Delta": round(new_rate - base_rate, 1)},
        {"Metric": "相对提升倍数 (new/base)", "Baseline": 1.0, "New_Run": round(new_rate / base_rate, 2) if base_rate else "∞", "Delta": ""},
        {"Metric": "—— 以下为标题对齐交集 ——", "Baseline": "", "New_Run": "", "Delta": ""},
        {"Metric": "对齐交集大小", "Baseline": len(base_df), "New_Run": len(new_df), "Delta": len(common)},
        {"Metric": "基线失败 → 新成功（提升）", "Baseline": "", "New_Run": "", "Delta": trans.get("fail→succ", 0)},
        {"Metric": "基线成功 → 新仍成功", "Baseline": "", "New_Run": "", "Delta": trans.get("succ→succ", 0)},
        {"Metric": "基线成功 → 新失败（回归）", "Baseline": "", "New_Run": "", "Delta": trans.get("succ→fail", 0)},
        {"Metric": "基线失败 → 新仍失败", "Baseline": "", "New_Run": "", "Delta": trans.get("fail→fail", 0)},
    ]
    notes = [
        {"Metric": "", "Baseline": "", "New_Run": "", "Delta": ""},
        {"Metric": "说明", "Baseline": "基线 122 vs 新 95 的差额来自 Scholar 分页与去重策略差异，不是下载环节本身", "New_Run": "", "Delta": ""},
        {"Metric": "说明", "Baseline": "失败原因为按域名 + pdf_downloader.py 流水线逻辑推断；代码本身对失败只返回 None，无显式 error 字段", "New_Run": "", "Delta": ""},
    ]
    return pd.DataFrame(rows + notes)


def build_failures(new_df: pd.DataFrame, base_df: pd.DataFrame) -> pd.DataFrame:
    base_idx = base_df.set_index("title_norm")["PDF_Download"].to_dict()
    fails = new_df[~new_df["PDF_Download"]].copy()
    fails["inferred_category"] = fails["category"]
    fails["likely_reason"] = fails["category"].map(lambda c: _REASON_BY_CATEGORY.get(c, _REASON_BY_CATEGORY["Other"]))
    in_base = []
    base_status = []
    for tn in fails["title_norm"]:
        if tn and tn in base_idx:
            in_base.append(True)
            base_status.append("baseline_success" if bool(base_idx[tn]) else "baseline_fail")
        else:
            in_base.append(False)
            base_status.append("not_in_baseline")
    fails["in_baseline"] = in_base
    fails["baseline_status"] = base_status
    cols = [
        "Paper_Title", "Paper_Year", "Venue", "Paper_Link", "pdf_url",
        "domain", "inferred_category", "likely_reason",
        "in_baseline", "baseline_status",
    ]
    out = fails[cols].reset_index(drop=True)
    out.insert(0, "#", range(1, len(out) + 1))
    return out


def build_domain_breakdown(new_df: pd.DataFrame, base_df: pd.DataFrame) -> pd.DataFrame:
    def agg(df):
        g = df.groupby("category").agg(
            total=("PDF_Download", "size"),
            success=("PDF_Download", "sum"),
        )
        g["success"] = g["success"].astype(int)
        g["rate"] = (g["success"] / g["total"] * 100).round(1)
        return g

    n = agg(new_df)
    b = agg(base_df)
    cats = sorted(set(n.index) | set(b.index))
    rows = []
    for c in cats:
        bt = int(b.loc[c, "total"]) if c in b.index else 0
        bs = int(b.loc[c, "success"]) if c in b.index else 0
        br = float(b.loc[c, "rate"]) if c in b.index else 0.0
        nt = int(n.loc[c, "total"]) if c in n.index else 0
        ns = int(n.loc[c, "success"]) if c in n.index else 0
        nr = float(n.loc[c, "rate"]) if c in n.index else 0.0
        rows.append({
            "Category": c,
            "Baseline_Total": bt,
            "Baseline_Success": bs,
            "Baseline_Rate_%": br,
            "NewRun_Total": nt,
            "NewRun_Success": ns,
            "NewRun_Rate_%": nr,
            "Rate_Delta_pp": round(nr - br, 1),
            "Success_Delta": ns - bs,
        })
    df = pd.DataFrame(rows)
    df = df.sort_values(by=["NewRun_Total", "Rate_Delta_pp"], ascending=[False, False]).reset_index(drop=True)
    return df


def build_title_matched_diff(new_df: pd.DataFrame, base_df: pd.DataFrame) -> pd.DataFrame:
    base_map = {tn: row for tn, row in base_df.set_index("title_norm").to_dict("index").items() if tn}
    rows = []
    for r in new_df.itertuples():
        tn = r.title_norm
        if not tn or tn not in base_map:
            continue
        b = base_map[tn]
        b_ok = bool(b["PDF_Download"])
        n_ok = bool(r.PDF_Download)
        if b_ok == n_ok:
            transition = "succ→succ" if b_ok else "fail→fail"
        elif not b_ok and n_ok:
            transition = "fail→success (↑)"
        else:
            transition = "success→fail (↓ regression)"
        rows.append({
            "transition": transition,
            "Paper_Title": r.Paper_Title,
            "category": r.category,
            "domain": r.domain,
            "baseline_PDF_Download": b_ok,
            "new_PDF_Download": n_ok,
            "Paper_Link": r.Paper_Link,
        })
    df = pd.DataFrame(rows)
    order = {"success→fail (↓ regression)": 0, "fail→success (↑)": 1, "fail→fail": 2, "succ→succ": 3}
    df["_ord"] = df["transition"].map(order)
    df = df.sort_values(by=["_ord", "Paper_Title"]).drop(columns="_ord").reset_index(drop=True)
    return df


def build_improvements() -> pd.DataFrame:
    rows = [
        ("跨平台 Chrome cookie 自动检测",
         "旧版硬编码 macOS 路径；V2 支持 Win(LOCALAPPDATA) / macOS / Linux，并自动挑选 IEEE cookie 数最多的 profile 作为机构登录身份",
         "IEEE / Elsevier / Springer / Wiley"),
        ("CDP-IEEE handler（新增）",
         "通过 WebSocket 驱动本地 Edge/Chrome 的远程调试端口，提取 arnumber → 导航 stamp.jsp → 120s 认证超时内等待下载；附 getPDF.jsp URL 兜底",
         "IEEE"),
        ("CDP-Elsevier handler（新增）",
         "pdfDownload 元数据正则解析 + Cloudflare Turnstile 状态检测 + 卡住自动刷新 + 内嵌 viewer 的 S3 签名 URL 页内抓取",
         "Elsevier / ScienceDirect"),
        ("ScraperAPI + LLM 兜底（新增）",
         "ScraperAPI 渲染 JS → 规则抽 PDF 链接失败时，调 LLM 分析 HTML 定位 'Download PDF' 按钮的 href",
         "Springer / Wiley / 小众期刊"),
        ("批量并发下载",
         "asyncio semaphore=10，每篇 8 分钟 hard timeout（最近提交 a654fcc），一个卡死的条目不再阻塞整批次",
         "全体（吞吐量）"),
        ("瞬时错误自动重试",
         "RemoteDisconnected / http.client 异常不再直接失败，进入下一阶段继续尝试",
         "全体（稳健性）"),
        ("URL 变体智能改写扩展",
         "覆盖 OpenReview pdf?→/forum 修正、CVF /papers/ 路径、MDPI /pdf 追加、Springer /content/pdf/ 重写、IEEE stamp.jsp、ScienceDirect pdfft 参数",
         "CVF / OpenReview / MDPI / Springer / IEEE / Elsevier"),
        ("Unpaywall v2 API 接入",
         "在 Sci-Hub / 出版商 fallback 之后新增合法 OA 源查询",
         "所有 DOI 已登记 OA 版本的论文"),
        ("S2 API + OpenAlex 双源并行",
         "除了 S2 外新增 OpenAlex openAccessPdf；两者合并去重选最新版",
         "arXiv 预印本 + OA 期刊"),
        ("统一字段访问器",
         "_paper_title()/_paper_link()/_paper_pdf_url()/_paper_oa_pdf_url()/_paper_gs_pdf_link() 等封装，兼容 Scholar / S2 / OpenAlex 字段命名差异",
         "全体（减少空字段失败）"),
    ]
    return pd.DataFrame(rows, columns=["Improvement", "Description", "Expected_Beneficiary_Category"])


# ────────────────────────────── main ──────────────────────────────

def main():
    if not NEW_JSON.exists():
        sys.exit(f"Missing: {NEW_JSON}")
    if not BASELINE_XLSX.exists():
        sys.exit(f"Missing: {BASELINE_XLSX}")

    new_df = load_new_run()
    base_df = load_baseline()

    summary = build_summary(new_df, base_df)
    failures = build_failures(new_df, base_df)
    domain = build_domain_breakdown(new_df, base_df)
    diff = build_title_matched_diff(new_df, base_df)
    improvements = build_improvements()

    OUT_XLSX.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as xw:
        summary.to_excel(xw, sheet_name="Summary", index=False)
        failures.to_excel(xw, sheet_name="New_Run_Failures", index=False)
        domain.to_excel(xw, sheet_name="Domain_Breakdown", index=False)
        diff.to_excel(xw, sheet_name="Title_Matched_Diff", index=False)
        improvements.to_excel(xw, sheet_name="V2_Improvements", index=False)

    print(f"Wrote: {OUT_XLSX}")
    print(f"  Summary rows:          {len(summary)}")
    print(f"  New_Run_Failures rows: {len(failures)}")
    print(f"  Domain_Breakdown rows: {len(domain)}")
    print(f"  Title_Matched_Diff:    {len(diff)}")
    print(f"  V2_Improvements rows:  {len(improvements)}")


if __name__ == "__main__":
    main()
