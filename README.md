<div align="right">

English | [中文](./README.zh-CN.md)

</div>

<div align="center">
  <img src="docs/assets/logo.png" width="60%" alt="CitationClaw Logo"><br>

# CitationClaw: A Lightweight Engine for Discovering Scientific Impact through Citations

让每一次引用都成为可解释的影响力  
<em>Turning Every Citation into Explainable Impact</em>

[![Homepage](https://img.shields.io/badge/Homepage-CitationClaw-blue)](https://visionxlab.github.io/CitationClaw/)
[![PyPI](https://img.shields.io/pypi/v/citationclaw?logo=pypi&logoColor=white&color=0073b7)](https://pypi.org/project/citationclaw/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/citationclaw)](https://pypi.org/project/citationclaw/)
[![Visitors](https://visitor-badge.laobi.icu/badge?page_id=VisionXLab.CitationClaw)](https://github.com/VisionXLab/CitationClaw)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](https://github.com/VisionXLab/CitationClaw/pulls)
[![Issues](https://img.shields.io/github/issues/VisionXLab/CitationClaw)](https://github.com/VisionXLab/CitationClaw/issues)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)
![LLM](https://img.shields.io/badge/LLM-OpenAI%20Compatible-412991?logo=openai&logoColor=white)
![ScraperAPI](https://img.shields.io/badge/Crawler-ScraperAPI-FF6B35)
[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey)](https://creativecommons.org/licenses/by-nc/4.0/)

**Turn Every Citation into Explainable Impact.**  
Input paper titles (or import from Google Scholar profiles), and generate a full citation portrait report in minutes.

</div>

> ## 🚀 Contribute with PRs
> CitationClaw is community-driven and PR-friendly.
>
> - Open an issue: <https://github.com/VisionXLab/CitationClaw/issues>
> - Submit a PR: <https://github.com/VisionXLab/CitationClaw/pulls>
> - Good first tasks: docs, UI polish, skill metadata, retry robustness

## 📢 News

- **2026-03-18**: Released **beta v1.0.8** — UI improvements: default to Basic tier, maintenance notice for Advanced/Full, startup modal with usage notices, V-API Key labeling; fix SCOPE section showing incorrect citing paper count in multi-paper mode.
- **2026-03-16**: Released **beta v1.0.7** — Reliability hardening: parallel quota-storm prevention (early-exit guards + 2h gather timeout), and empty/corrupt JSONL guards across all pipeline phases to ensure the pipeline always runs to completion.
- **2026-03-15**: Released **beta v1.0.6** — English README as default, Chinese switch at top, and usage flow linked to Guidelines Quick Start.
- **2026-03-14**: Released **v1.0.5** — AI assistant widgets for UI/report pages and reliability fixes.
- **2026-03-14**: Released **v1.0.4** — improved UI and introduced Basic/Advanced/Full service tiers.
- **2026-03-12**: Released **v1.0** — first public release.

## Key Features

- 🧠 **Five-Phase Citation Pipeline**: crawl -> author intelligence -> export -> citing description -> dashboard.
- 🎯 **Renowned Scholar Focus**: auto-identifies high-impact scholars and generates dedicated outputs.
- ⚡ **Tiered Analysis Modes**: Basic / Advanced / Full for speed-cost-depth tradeoff.
- 🔁 **Resumable + Cache-Aware**: supports resume-by-page, author cache, and citing-description cache.
- 📊 **Shareable HTML Report**: standalone dashboard file, no extra server needed for viewing.
- 🧩 **Skills Runtime Inside**: keeps five-phase logic while moving execution to modular skills.

## 🏗️ Architecture

CitationClaw keeps deterministic business phases while using a skills-style runtime for orchestration.

```text
UI/REST/WebSocket
      │
      ▼
TaskExecutor (Orchestrator)
      │
      ▼
Skills Runtime
  ├─ phase1_citation_fetch
  ├─ phase2_author_intel
  ├─ phase3_export
  ├─ phase4_citation_desc
  └─ phase5_report_generate
```

More details: [Technical Report](https://visionxlab.github.io/CitationClaw/technical-report.html)

## Table of Contents

- [News](#-news)
- [Key Features](#key-features)
- [Architecture](#️-architecture)
- [Install](#-install)
- [Quick Start](#-quick-start)
- [Configuration Highlights](#️-configuration-highlights)
- [Project Structure](#-project-structure)
- [Outputs](#-outputs)
- [Contribute & Roadmap](#-contribute--roadmap)
- [Community](#-community)
- [Star History](#-star-history)
- [Disclaimer](#️-disclaimer)

## 📦 Install

Requires **Python 3.10+** (Python 3.12 recommended).

### Install from PyPI (recommended)

```bash
pip install citationclaw
citationclaw                  # default: 127.0.0.1:8000
citationclaw --port 8080      # custom port
```

### Install from source

```bash
git clone https://github.com/VisionXLab/CitationClaw.git
cd CitationClaw
pip install -r requirements.txt
python start.py               # default: 127.0.0.1:8000
python start.py --port 8080
```

## 🚀 Quick Start

For first-time users, follow the complete guide with screenshots:

- [Quick Start (online)](https://visionxlab.github.io/CitationClaw/guidelines.html#installation)
- [Quick Start (local file)](./docs/guidelines.html#installation)

## ⚙️ Configuration Highlights

- **Required keys**:
  - `ScraperAPI Key(s)` for Google Scholar crawling
  - `OpenAI-compatible API Key` for LLM-based analysis
- **Recommended search model**:
  - Keep `gemini-3-flash-preview-search` for search-capable stages
- **Service tiers**:
  - `Basic`: lower cost and faster for first runs
  - `Advanced`: citing descriptions for renowned-scholar papers only
  - `Full`: citing descriptions for all citing papers
- **For papers with >1000 citations**:
  - Enable year traverse mode

## 📁 Project Structure

```text
citationclaw/
├── app/                 # FastAPI app, task orchestration, config, logs
├── core/                # scraping / search / export / dashboard engines
├── skills/              # skills runtime and five phase skills
├── static/              # frontend assets
├── templates/           # Jinja2 pages
docs/                    # docs and demos
test/                    # tests
```

## 📤 Outputs

Each run creates a timestamped folder under `data/result-{timestamp}/`, usually including:

- `paper_results.xlsx`
- `paper_results_all_renowned_scholar.xlsx`
- `paper_results_top-tier_scholar.xlsx`
- `paper_results_with_citing_desc.xlsx`
- `paper_results.json`
- `paper_dashboard.html`

## 🤝 Contribute & Roadmap

PRs are welcome and appreciated.

Suggested directions:

- richer skill metadata and registry conventions
- stronger retry and network-failure resilience
- dashboard readability and UX improvement
- tests for pipeline contracts and compatibility
- provider/model compatibility presets

Useful links:

- Issues: <https://github.com/VisionXLab/CitationClaw/issues>
- Pull Requests: <https://github.com/VisionXLab/CitationClaw/pulls>
- Guidelines: <https://visionxlab.github.io/CitationClaw/guidelines.html>

## 🌍 Community

- Product update: [减论 reduct.cn](https://www.reduct.cn/)
- User group (CN):

<div align="center">
  <img src="docs/assets/group.jpg" width="200" alt="User Group QR">
</div>

## ⭐ Star History

<div align="center">

[![Star History Chart](https://api.star-history.com/svg?repos=VisionXLab/CitationClaw&type=Date)](https://star-history.com/#VisionXLab/CitationClaw&Date)

</div>

