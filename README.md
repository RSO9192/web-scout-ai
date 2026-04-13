# `web-scout-ai`

![web-scout-ai logo](assets/web-scout-logo.svg)

[![PyPI Version](https://img.shields.io/pypi/v/web-scout-ai?style=for-the-badge&logo=pypi&logoColor=white)](https://pypi.org/project/web-scout-ai/)
[![PyPI Downloads per Month](https://img.shields.io/pypi/dm/web-scout-ai?style=for-the-badge&logo=pypi&logoColor=white&label=PyPI%20downloads%2Fmonth)](https://pypi.org/project/web-scout-ai/)
[![Python Versions](https://img.shields.io/pypi/pyversions/web-scout-ai?style=for-the-badge&logo=python&logoColor=white)](https://pypi.org/project/web-scout-ai/)
[![License](https://img.shields.io/badge/license-MIT-0f172a?style=for-the-badge)](LICENSE)

**Grounded web research for agents and apps.**  
One async call to discover sources, read real pages and documents, close coverage gaps, and return a cited synthesis.

## Why This Exists

Most web tools stop too early.

- Search APIs give you snippets and links, not enough context to answer reliably.
- Single-page scrapers can read one URL well, but they do not know what to read next.
- Full deep-research agents often produce good work, but they can be slower, more expensive, and harder to control in production flows.

`web-scout-ai` is the middle path: a deterministic research pipeline that is stronger than search-only tooling and much lighter than open-ended research agents.

## What It Actually Does

`web-scout-ai` does not just search and summarize. It runs a full research loop:

1. Generate targeted search queries.
2. Search the web with Serper or DuckDuckGo.
3. Triage the best URLs across result sets.
4. Scrape and extract relevant content in parallel.
5. Evaluate whether the evidence actually answers the question.
6. Reuse promising backlog URLs or run follow-up searches if coverage is still weak.
7. Produce a grounded synthesis with inline citations.
8. Run a deterministic citation check before returning the final answer.

That gives you a practical balance of depth, speed, and control in one function: `run_web_research(...)`.

## Why It Feels Different

### It reads sources, not snippets

Each selected URL is scraped and converted into a substantial query-relevant extract before synthesis.

### It handles messy real-world content

- Static HTML via fast HTTP
- JS-rendered pages via Playwright
- JSON endpoints via structured extraction
- Image URLs via optional vision extraction
- PDF, DOCX, PPTX, XLSX via `docling`, including extensionless download URLs detected from response headers
- Bot-protected PDFs (e.g. Akamai) via Playwright browser download fallback
- Short metadata/catalogue pages retained for extractor inspection instead of being dropped as thin pages
- Scanned PDFs and empty JS pages via optional vision fallback

### It can deepen automatically

If a direct URL is actually a list or database page, `web-scout-ai` can detect that, follow relevant item links, and even take one pagination hop.

### It is easy to plug into agents

You get one async entry point, typed output, provider flexibility via LiteLLM, and no framework lock-in.

## Quick Start

```bash
pip install web-scout-ai
web-scout-setup
```

`web-scout-setup` installs the Chromium browser required for JS-rendered pages.

## First Run

This example uses DuckDuckGo so it works without a search API key.

```python
import asyncio
from web_scout import run_web_research

async def main():
    result = await run_web_research(
        query="What are the main threats to coral reefs worldwide?",
        models={
            "web_researcher": "openai/gpt-5.4-mini",
            "content_extractor": "gemini/gemini-3-flash-preview",
        },
        search_backend="duckduckgo",
    )

    print(result.synthesis)
    print("\nSources:")
    for source in result.scraped:
        print(f"- {source.title or source.url}: {source.url}")

asyncio.run(main())
```

## What You Get Back

```python
class WebResearchResult(BaseModel):
    synthesis: str
    scraped: list[UrlEntry]
    scrape_failed: list[UrlEntry]
    bot_detected: list[UrlEntry]
    snippet_only: list[UrlEntry]
    queries: list[SearchQuery]
```

- `synthesis`: final grounded answer with inline source citations
- `scraped`: URLs successfully scraped, with extracted relevant content
- `scrape_failed`: URLs that were attempted but could not be scraped
- `bot_detected`: URLs blocked by bot protection
- `snippet_only`: search results kept only as snippets
- `queries`: all search queries executed during the run

`UrlEntry` contains `url`, `title`, and `content`.
`SearchQuery` contains `query`, `num_results_returned`, and `domains_restricted`.

## API At A Glance

```python
result = await run_web_research(
    query="latest IPCC findings on sea level rise",
    models={
        "web_researcher": "openai/gpt-5.4-mini",
        "content_extractor": "gemini/gemini-3-flash-preview",
    },
    search_backend="duckduckgo",         # or "serper"
    research_depth="standard",           # or "deep"
    include_domains=["ipcc.ch"],         # optional
    direct_url=None,                     # optional
    domain_expertise="climate science",  # optional
    allowed_domains=None,                # optional
    max_pdf_pages=50,                    # optional, default 50
)
```

## Research Modes

```python
# 1) Open web research
await run_web_research(
    query="latest IPCC findings on sea level rise",
    models=models,
    search_backend="duckduckgo",
)

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

# 4) Direct URL list-page deepening
await run_web_research(
    query="sustainable land management technologies in Kenya",
    models=models,
    direct_url="https://wocat.net/en/database/list/?type=technology&country=ke",
)
```

### Direct URL mode is more than single-page extraction

If the URL is a list, index, or database page, the pipeline can:

- detect that it is a hub page
- collect the most relevant item links
- follow up to a depth-dependent cap of those links
- take one "next page" hop when pagination is present

This is especially useful for catalog pages, result listings, and structured report libraries.

## Search Backends

```python
# Default: Serper (requires SERPER_API_KEY)
await run_web_research(query=..., models=..., search_backend="serper")

# Free: DuckDuckGo (no API key)
await run_web_research(query=..., models=..., search_backend="duckduckgo")
```

- `serper`: Google-quality results with richer metadata
- `duckduckgo`: zero-config and free, ideal for quick starts and lightweight usage

## Research Depth

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
| Hub deepening cap | 10 | 15 |

## Configuration

### Models

Model IDs follow [LiteLLM provider naming](https://docs.litellm.ai/docs/providers):

```python
models = {
    # Required
    "web_researcher": "openai/gpt-5.4-mini",
    "content_extractor": "gemini/gemini-3-flash-preview",

    # Optional step-specific overrides (default: web_researcher)
    "query_generator": "openai/gpt-5.4-mini",
    "coverage_evaluator": "openai/gpt-5.4-mini",
    "synthesiser": "openai/gpt-5.4-mini",

    # Optional fallback for scanned PDFs, image URLs, or empty JS pages
    "vision_fallback": "gemini/gemini-3-flash-preview",
}
```

### Environment Variables

```bash
# Search backend (optional if using DuckDuckGo)
export SERPER_API_KEY="..."

# LLM providers (set what you use)
export OPENAI_API_KEY="..."
export ANTHROPIC_API_KEY="..."
export GEMINI_API_KEY="..."
export MISTRAL_API_KEY="..."
export GROQ_API_KEY="..."
```

### Domain Control

```python
# Restrict discovery to selected domains
await run_web_research(
    query=...,
    models=...,
    include_domains=["fao.org", "ipcc.ch"],
)

# Re-allow domains that are blocked by default
await run_web_research(
    query=...,
    models=...,
    allowed_domains=["reddit.com"],
)
```

By default, the scraper blocks common social and video platforms. `allowed_domains` lets you opt specific domains back in when they are genuinely useful for the task.

## Pipeline Overview

Editable diagram: [`pipeline-diagram.excalidraw`](pipeline-diagram.excalidraw)

```text
Query
 |
 +- Generate search queries (LLM)
 +- Search web (Serper or DuckDuckGo)
 +- Select best URLs across result sets
 +- Scrape and extract in parallel
 |   +- Static HTML
 |   +- JS/SPA via Playwright
 |   +- JSON endpoints via structured extraction
 |   +- Image URLs via vision extraction
 |   +- PDF/DOCX/PPTX/XLSX via docling
 |   +- Extensionless document downloads via content-type/content-disposition sniffing
 |   +- Bot-protected PDFs via Playwright download fallback
 |   +- Short metadata pages retained for linked-document follow-up
 |   +- Scanned PDFs via vision fallback
 +- Evaluate coverage (LLM)
 |   +- Reuse promising backlog URLs
 |   +- Or generate targeted follow-up searches
 +- Synthesize findings with citations (LLM)
 +- Run deterministic citation checks
 |
 +- WebResearchResult
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
            "web_researcher": "openai/gpt-5.4-mini",
            "content_extractor": "gemini/gemini-3-flash-preview",
        },
        search_backend="duckduckgo",
    )
    sources = "\n".join(f"- {s.url}" for s in result.scraped)
    return f"{result.synthesis}\n\nSources:\n{sources}"

agent = Agent(
    name="researcher",
    model="gpt-5.4-mini",
    tools=[research],
    instructions="Use the research tool to answer with up-to-date web sources.",
)
```

## Where It Fits Best

`web-scout-ai` is a strong fit when you need:

- up-to-date answers grounded in real web sources
- multi-source synthesis without building a full deep-research stack
- a reusable research tool inside an agent workflow
- better handling of report libraries, list pages, and mixed web/document sources

It is probably not the right tool if you only need simple search snippets or if you want a fully autonomous long-form research agent that decides everything itself.

## Brand Assets

- Full logo: [`assets/web-scout-logo.svg`](assets/web-scout-logo.svg)
- Square logo mark (avatar-safe): [`assets/web-scout-logo-mark.svg`](assets/web-scout-logo-mark.svg)
- Social card preview: [`assets/web-scout-social-card.svg`](assets/web-scout-social-card.svg)

## Requirements

- Python `>=3.10`
- API key for at least one supported LLM provider
- Optional `SERPER_API_KEY` if you want the Serper backend

## License

MIT
