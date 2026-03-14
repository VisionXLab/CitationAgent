<div align="right">

English | [中文](./README.zh-CN.md)

</div>

<div align="center">
  <img src="docs/assets/icon.png" width="110" alt="CitationClaw Logo"><br>

# CitationClaw — Paper Citation Portrait Analysis 🦞

Turn Every Citation into Explainable Impact  
<em>让每一次引用都成为可解释的影响力</em>

[![Homepage](https://img.shields.io/badge/Homepage-CitationClaw-blue)](https://visionxlab.github.io/CitationClaw/)
[![PyPI](https://img.shields.io/pypi/v/citationclaw?logo=pypi&logoColor=white&color=0073b7)](https://pypi.org/project/citationclaw/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/citationclaw)](https://pypi.org/project/citationclaw/)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)
![LLM](https://img.shields.io/badge/LLM-OpenAI%20Compatible-412991?logo=openai&logoColor=white)
![ScraperAPI](https://img.shields.io/badge/Crawler-ScraperAPI-FF6B35)
[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey)](https://creativecommons.org/licenses/by-nc/4.0/)

**Input paper titles (or import from Google Scholar profile), and generate a full citation portrait report in one click.**

</div>

---

## What is CitationClaw?

CitationClaw crawls citing papers from Google Scholar, analyzes author backgrounds (including renowned scholars), and generates a standalone interactive HTML report with:

- keyword cloud
- citation trend and prediction
- country / institution distribution
- renowned scholar portrait
- citing sentence analysis (optional by service tier)
- LLM-generated summary insights

---

## Quick Links

| Link | Description |
|---|---|
| [📘 Guidelines](https://visionxlab.github.io/CitationClaw/guidelines.html) | Full documentation (installation, quick start, config, outputs, troubleshooting) |
| [⚡ Quick Start (First-time users)](https://visionxlab.github.io/CitationClaw/guidelines.html#installation) | Recommended first-run path with screenshots |
| [📊 Demo Report 1](https://visionxlab.github.io/CitationClaw/demo1.html) | Real report output example |
| [📊 Demo Report 2](https://visionxlab.github.io/CitationClaw/demo2.html) | Another real report example |
| [📖 User Story](https://visionxlab.github.io/CitationClaw/use-report.html) | End-to-end usage write-up |
| [🔧 Technical Report](https://visionxlab.github.io/CitationClaw/technical-report.html) | Architecture and implementation details |

---

## Installation

Requires **Python 3.10+** (Python 3.12 recommended).

### Option 1: Install from PyPI (Recommended)

```bash
pip install citationclaw
citationclaw                  # default: 127.0.0.1:8000
citationclaw --port 8080      # custom port
```

### Option 2: Run from Source

```bash
git clone https://github.com/VisionXLab/CitationClaw.git
cd CitationClaw
pip install -r requirements.txt
python start.py               # default: 127.0.0.1:8000
python start.py --port 8080
```

---

## Usage Flow

Please follow the first-time Quick Start in Guidelines:

- [Quick Start (online)](https://visionxlab.github.io/CitationClaw/guidelines.html#installation)
- [Quick Start (local file)](./docs/guidelines.html#installation)

---

## Service Tiers

- **Basic**: scholar search + renowned scholar filtering (lower cost, faster)
- **Advanced**: adds citing sentence search only for renowned scholars
- **Full**: citing sentence search for all citing papers

---

## Outputs

Each run creates a timestamped result folder under `data/result-{timestamp}/`, typically including:

- `paper_results.xlsx`
- `paper_results_all_renowned_scholar.xlsx`
- `paper_results_top-tier_scholar.xlsx`
- `paper_results_with_citing_desc.xlsx`
- `paper_results.json`
- `paper_dashboard.html`

---

## Changelog

| Date | Version | Notes |
|---|---|---|
| 2026-03-15 | v1.0.6 | Added default English README with Chinese switch, moved Chinese content to `README.zh-CN.md`, and linked usage flow to Guidelines Quick Start |
| 2026-03-14 | v1.0.5 | Added AI assistant widgets for UI/report pages, fixed report assistant button issue, visual improvements |
| 2026-03-14 | v1.0.4 | Improved UI and introduced basic/advanced/full service tiers |
| 2026-03-12 | v1.0 | First public release |

---

## Community

- Product update: [减论 reduct.cn](https://www.reduct.cn/)
- User group (CN):

<div align="center">
  <img src="docs/assets/group.jpg" width="200" alt="User Group QR">
</div>

---

## Disclaimer

This project is for academic research and personal learning. Please comply with Google Scholar terms and local regulations. Avoid abusive large-scale crawling. The authors are not responsible for consequences caused by misuse.
