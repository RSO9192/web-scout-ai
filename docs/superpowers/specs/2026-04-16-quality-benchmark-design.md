# Quality Benchmark Design

**Date:** 2026-04-16  
**Status:** Approved

## Goal

Build a `tests/quality_benchmark.py` script that runs a mixed query set through web-scout-ai and OpenAI web search side-by-side, scores both with an LLM judge, and produces a rich diagnostic report. The primary purpose is to identify where web-scout-ai loses quality — whether due to URL scrape failures, thin content extraction, or synthesis drift — so that targeted improvements can be made.

## Query Set

8 queries: 4 FAO/ESS-domain, 4 general deep-research.

**FAO/ESS-domain:**
1. "Kenya interannual variability and long-term trends in precipitation — current status and recent trend"
2. "Global food insecurity trends 2022–2024 — FAO State of Food Security and Nutrition report key findings"
3. "Ethiopia crop production statistics 2023 — cereals area harvested and yield data"
4. "FAOSTAT deforestation and forest area change Sub-Saharan Africa 2000–2023"

**General deep-research:**
5. "What are the projected sea level rise impacts on Venice specifically, including flood frequency projections and MOSE barrier effectiveness under different IPCC scenarios?"
6. "What specific Total Allowable Catch quotas has ICCAT set for Eastern Atlantic and Mediterranean bluefin tuna 2022–2026?"
7. "What is the current deforestation rate in the Brazilian Cerrado, main commodity drivers, and specific IBAMA enforcement actions in the last two years?"
8. "Latest IPCC AR6 findings on food system vulnerability to climate change — specific regional projections and adaptation options"

Queries 5–7 repeat the existing benchmark set to provide a direct performance baseline.

## Tool Configuration

**web-scout-ai:**
- Backend: `serper`
- Research depth: `standard`
- Models: default (`gemini/gemini-3-flash-preview` for all roles, as defined in `agent.py`)
- `.env` path: `/Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/report/.env`

**OpenAI comparison:**
- Model: `gpt-5.4-mini`
- Tool: `WebSearchTool(search_context_size="high")`
- System prompt: strict no-fabrication, inline citations, per-source extraction (same as existing benchmark)
- Structured output: `OpenAIWebSearchOutput` with `synthesis` + `sources` list

**LLM judge:**
- Model: `gpt-5.4-mini`
- Same strict judge prompt as existing `benchmark.py`
- Scores 1–5 on three dimensions: URL Relevance, Tailored Comprehensiveness, Synthesis Quality

## Metrics

**Per tool per query (summary table):**
- Scraped count, Failed count, Bot-blocked count (web-scout-ai only)
- Elapsed seconds
- URL Relevance / Tailored Comprehensiveness / Synthesis Quality (1–5)
- Overall score (mean of three dimensions)

**Per query detail block (web-scout-ai only — OpenAI internals not inspectable):**
- Scrape success breakdown: `N scraped / N failed / N bot-blocked / N http-error / N blocked-by-policy / N irrelevant out of N attempted`
- Failed URLs table: URL + error message for every non-scraped entry
- Source content previews: title, URL, first 250 chars of extract for each scraped source
- Judge rationale text (2–3 sentences per dimension, both tools)
- Full synthesis text (both tools)

## Report Structure

Two output files per run, saved to `tests/benchmark_results/`:
- `quality_benchmark_YYYYMMDD_HHMMSS.md` — human-readable Markdown
- `quality_benchmark_YYYYMMDD_HHMMSS.json` — full structured data including complete source content

**Markdown layout:**
```
# Quality Benchmark
Date / config header

## Summary Table
(one row per tool per query)

---

## Query 1: <text>

### web-scout-ai
Scrape breakdown: N scraped / N failed / ...
#### Failed URLs
| URL | Error |
...
#### Source Previews
- [Title](url): <250 char preview>
...
#### Scores & Rationales
...
#### Synthesis
...

### OpenAI (gpt-5.4-mini)
#### Scores & Rationales
...
#### Synthesis
...

---
(repeat for each query)
```

## Implementation

Single new file: `tests/quality_benchmark.py`.

**Structure:**
- Config block at top (queries, models, backend, output dir) — easy to edit for future runs
- `run_web_scout(query) -> ToolResult` — calls `run_web_research()`, extracts all URL groups from `WebResearchResult` (`scraped`, `scrape_failed`, `blocked_by_policy`, `source_http_error`, `scraped_irrelevant`, `bot_detected`)
- `run_openai_websearch(query) -> ToolResult` — `gpt-5.4-mini` agent with `WebSearchTool`
- `evaluate_result(result) -> Evaluation` — LLM-as-judge using `gpt-5.4-mini`
- `build_report(results, queries) -> str` — Markdown report builder
- `main()` — sequential query loop, concurrent tool execution per query, saves outputs

**Execution model:**
- Queries run sequentially (one at a time) — keeps logs readable, avoids API saturation
- Both tools run concurrently within each query (`asyncio.gather`)
- Both evaluations run concurrently after each query pair completes

**No changes to the web-scout-ai library are required.** All needed data is already exposed via `WebResearchResult` fields.

## What This Enables

After running the benchmark, the report will show:
- Which queries produce low scores and why (URL relevance failure vs. thin extraction vs. synthesis drift)
- Which domains are consistently failing (bot-blocked, HTTP errors, policy-blocked)
- Whether scrape failure rate correlates with lower scores
- How web-scout-ai compares to OpenAI web search on FAO vs. general queries

These findings directly guide targeted improvements to the scraping routing, blocked-domain list, failure fallbacks, and extraction quality.
