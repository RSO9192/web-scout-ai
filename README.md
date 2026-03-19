<p align="center">
  <img src="assets/web-scout-logo.svg" alt="web-scout-ai logo" width="900" />
</p>

<p align="center">
  <a href="https://pypi.org/project/web-scout-ai/"><img src="https://img.shields.io/pypi/v/web-scout-ai?style=for-the-badge&logo=pypi&logoColor=white" alt="PyPI Version"></a>
  <a href="https://pypi.org/project/web-scout-ai/"><img src="https://img.shields.io/pypi/dm/web-scout-ai?style=for-the-badge&logo=pypi&logoColor=white&label=PyPI%20downloads%2Fmonth" alt="PyPI Downloads per Month"></a>
  <a href="https://pypi.org/project/web-scout-ai/"><img src="https://img.shields.io/pypi/pyversions/web-scout-ai?style=for-the-badge&logo=python&logoColor=white" alt="Python Versions"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-0f172a?style=for-the-badge" alt="License"></a>
</p>

<p align="center">
  <strong>The missing middle ground between basic search APIs and heavyweight deep-research agents.</strong><br />
  Ask one question, get real pages/documents, and receive a grounded synthesis with sources.
</p>

## Why web-scout-ai

Most tools force a tradeoff:
- Fast search APIs return shallow snippets.
- Deep research agents can be slow, expensive, and hard to control.

`web-scout-ai` gives you a deterministic async pipeline that usually finishes in 15-40 seconds:
- Generate targeted search queries
- Search and rank URLs
- Scrape web pages and real documents
- Evaluate coverage and fill gaps
- Return a synthesized answer with source content

## What Makes It Different

### 1) It reads the source pages, not only snippets
Instead of returning 200-character search previews, it extracts substantial query-relevant content from each source.

### 2) It handles real research documents
Built-in support for:
- Static HTML
- JS-rendered SPAs via Playwright
- PDFs, DOCX, PPTX, XLSX via `docling`
- Scanned PDFs via a vision-model fallback

### 3) It closes the loop automatically
Search -> Scrape -> Evaluate -> Iterate -> Synthesize.
If coverage is incomplete, it first checks the existing backlog of collected results before issuing new searches.

### 4) It fits agent workflows
One async function (`run_web_research`), typed output (`WebResearchResult`), and provider flexibility through LiteLLM.

## Installation

```bash
pip install web-scout-ai
web-scout-setup
```

`web-scout-setup` installs Chromium for JS-rendered pages.

## Quick Start

```python
import asyncio
from web_scout import run_web_research

async def main():
    result = await run_web_research(
        query="What are the main threats to coral reefs worldwide?",
        models={
            "web_researcher": "gemini/gemini-2.0-flash",
            "content_extractor": "gemini/gemini-2.0-flash",
        },
    )

    print(result.synthesis)

    for source in result.scraped:
        print(f"- {source.title}: {source.url}")

asyncio.run(main())
```

## Configuration

### Models

All model ids follow [LiteLLM provider naming](https://docs.litellm.ai/docs/providers):

```python
models = {
    # Required
    "web_researcher": "openai/gpt-4o",
    "content_extractor": "gemini/gemini-2.0-flash",

    # Optional overrides (default: web_researcher)
    "query_generator": "anthropic/claude-sonnet-4-20250514",
    "coverage_evaluator": "openai/gpt-4o-mini",
    "synthesiser": "anthropic/claude-sonnet-4-20250514",

    # Optional scanned-PDF / empty-page fallback
    "vision_fallback": "gemini/gemini-2.0-flash",
}
```

### Environment Variables

```bash
# Search backend
export SERPER_API_KEY="..."          # optional; use DuckDuckGo without a key

# LLM providers (set only what you use)
export OPENAI_API_KEY="..."
export ANTHROPIC_API_KEY="..."
export GEMINI_API_KEY="..."
export MISTRAL_API_KEY="..."
export GROQ_API_KEY="..."
```

### Research Modes

```python
# 1) Open web research (default)
await run_web_research(query="latest IPCC findings on sea level rise", models=models)

# 2) Domain-restricted research
await run_web_research(
    query="endemic species conservation programs",
    models=models,
    include_domains=["iucn.org", "wwf.org"],
)

# 3) Direct URL extraction (skip search)
await run_web_research(
    query="key findings from this report",
    models=models,
    direct_url="https://example.org/biodiversity-report.pdf",
)

# 4) Direct URL with list-page (hub) deepening
await run_web_research(
    query="sustainable land management technologies in Kenya",
    models=models,
    direct_url="https://wocat.net/en/database/list/?type=technology&country=ke",
)
```

### Search Backends

```python
# Default: Serper (requires SERPER_API_KEY)
await run_web_research(query=..., models=..., search_backend="serper")

# Free: DuckDuckGo (no key)
await run_web_research(query=..., models=..., search_backend="duckduckgo")
```

### Research Depth

```python
# Standard (default): usually up to ~10 sources
await run_web_research(query=..., models=..., research_depth="standard")

# Deep: usually up to ~28 sources
await run_web_research(query=..., models=..., research_depth="deep")
```

| Parameter | Standard | Deep |
| --- | --- | --- |
| Max iterations | 2 | 3 |
| Search queries (first round) | 3 | 5 |
| Search queries (follow-up) | 2 | 4 |
| URLs scraped (first round) | 6 | 12 |
| URLs scraped (follow-up) | 4 | 8 |

### Domain Expertise Hint

```python
await run_web_research(
    query="red list status of Panthera tigris subspecies",
    models=models,
    domain_expertise="conservation biology and IUCN Red List assessments",
)
```

## Pipeline At A Glance

Editable diagram: [`pipeline-diagram.excalidraw`](pipeline-diagram.excalidraw)

```
Query
 │
 ├─ Generate search queries (LLM)
 ├─ Search web (Serper or DuckDuckGo)
 ├─ Select best URLs
 ├─ Scrape & extract in parallel
 │   ├─ Static HTML
 │   ├─ JS/SPA via Playwright
 │   ├─ PDF/DOCX/PPTX/XLSX via docling
 │   └─ Scanned PDFs via vision fallback
 ├─ Evaluate coverage (LLM)
 │   ├─ Scrape promising backlog URLs
 │   └─ Or generate targeted follow-up queries
 ├─ Synthesize findings (LLM)
 │
 └─ WebResearchResult
```

## Use As An Agent Tool

```python
from agents import Agent, function_tool
from web_scout import run_web_research

@function_tool
async def research(query: str) -> str:
    result = await run_web_research(
        query=query,
        models={
            "web_researcher": "gemini/gemini-2.0-flash",
            "content_extractor": "gemini/gemini-2.0-flash",
        },
        search_backend="duckduckgo",
    )
    sources = "\n".join(f"- {s.url}" for s in result.scraped)
    return f"{result.synthesis}\n\nSources:\n{sources}"

agent = Agent(
    name="researcher",
    model="gpt-4o",
    tools=[research],
    instructions="Use research tool to answer with up-to-date web sources.",
)
```

## Output Schema

```python
class WebResearchResult(BaseModel):
    synthesis: str
    scraped: list[UrlEntry]
    scrape_failed: list[UrlEntry]
    snippet_only: list[UrlEntry]
    queries: list[SearchQuery]
```

`UrlEntry` contains `url`, `title`, and `content`.
`SearchQuery` contains `query`, `num_results_returned`, and `domains_restricted`.

## Requirements

- Python `>=3.10`
- API key for at least one supported LLM provider
- Optional `SERPER_API_KEY` (or use DuckDuckGo)

## License

MIT
