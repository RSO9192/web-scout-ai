# `web-scout-ai`

![web-scout-ai logo](assets/web-scout-logo.svg)

[![PyPI Version](https://img.shields.io/pypi/v/web-scout-ai?style=for-the-badge&logo=pypi&logoColor=white)](https://pypi.org/project/web-scout-ai/)
[![PyPI Downloads per Month](https://img.shields.io/pypi/dm/web-scout-ai?style=for-the-badge&logo=pypi&logoColor=white&label=PyPI%20downloads%2Fmonth)](https://pypi.org/project/web-scout-ai/)
[![Python Versions](https://img.shields.io/pypi/pyversions/web-scout-ai?style=for-the-badge&logo=python&logoColor=white)](https://pypi.org/project/web-scout-ai/)
[![License](https://img.shields.io/badge/license-MIT-0f172a?style=for-the-badge)](LICENSE)

**AI-powered web research in one async call.**

```bash
pip install web-scout-ai
web-scout-setup
```

```python
from web_scout import run_web_research

result = await run_web_research("climate risk for agriculture in Kenya")
print(result.synthesis)
```

---

## What Problem It Solves

Building a reliable research pipeline requires gluing together:

- a search API (Serper / DuckDuckGo)
- a scraper that handles HTML, JS pages, PDFs, DOCX
- a coverage evaluator to know when you have enough sources
- a synthesizer that cites actual content

`web-scout-ai` is all of that in one call. No Tavily + crawl4ai + custom glue code. No open-ended agent that you cannot control in production.

---

## Three Real Use Cases

### 1. Climate and policy evidence retrieval

Query institutional sources (IPCC, FAO, World Bank) and get a cited synthesis — not just links.

```python
result = await run_web_research(
    "drought impact on smallholder farmers in sub-Saharan Africa",
    include_domains=["fao.org", "ipcc.ch", "worldbank.org"],
)
```

### 2. Agent pipelines

Drop it in as a tool. One function, typed output, no framework lock-in.

```python
@function_tool
async def research(query: str) -> str:
    result = await run_web_research(query, models=models)
    return result.synthesis
```

### 3. Rapid literature scanning

Point it at a report library or database page. It detects list pages, follows item links, and reads the actual documents.

```python
result = await run_web_research(
    "sustainable land management technologies",
    direct_url="https://wocat.net/en/database/list/?type=technology&country=ke",
)
```

---

## Why It Feels Different

**Designed for agents, not humans.** One async entry point, typed output, LiteLLM provider flexibility. Works inside pipelines with no sidechannels.

**Returns structured + clean content.** Every source is scraped and converted into a query-relevant extract before synthesis. You get cited prose, not a list of links.

**Works on the full web.** Static HTML, JS-rendered pages via Playwright, PDFs and DOCX via `docling`, JSON endpoints, even bot-protected files via browser download fallback.

**Knows when to go deeper.** If a URL is a list or database page, the pipeline detects it, follows item links, and takes a pagination hop. If coverage is still weak after the first round, it generates follow-up queries automatically.

---

## Killer Demo

```python
import asyncio
from web_scout import run_web_research

async def main():
    result = await run_web_research(
        query="climate risks for agriculture in Kenya",
        models={
            "web_researcher": "openai/gpt-4o-mini",
            "content_extractor": "gemini/gemini-2.0-flash",
        },
        research_depth="deep",
        include_domains=["fao.org", "ipcc.ch", "worldbank.org"],
    )
    print(result.synthesis)
    print(f"\n{len(result.scraped)} sources read.")

asyncio.run(main())
```

**Sample output:**

```text
Kenya's agricultural sector faces compounding climate risks documented across
major institutional sources:

**Drought and rainfall variability** — The frequency of drought events has
increased significantly since the 1990s, with rainfall becoming less
predictable across maize-growing regions. IPCC AR6 projects a further 10–20%
reduction in seasonal rainfall in the drylands by 2050 [IPCC, 2021].

**Yield losses** — FAO estimates that climate-related shocks already reduce
national cereal production by up to 30% in drought years, disproportionately
affecting smallholder farmers who account for 75% of food production
[FAO, 2023].

**Heat stress on livestock** — Rising temperatures are increasing livestock
mortality rates and reducing milk production in pastoral communities, affecting
livelihoods for approximately 3 million Kenyans [World Bank, 2022].

**Adaptation gaps** — Less than 15% of Kenyan smallholders have access to
climate-smart agriculture services or drought-resistant seed varieties
[FAO, 2023].

14 sources read.
```

---

## Quick Start

### Install

```bash
pip install web-scout-ai
web-scout-setup   # installs Chromium for JS-rendered pages
```

### First run (no API key needed)

```python
import asyncio
from web_scout import run_web_research

async def main():
    result = await run_web_research(
        query="What are the main threats to coral reefs worldwide?",
        models={
            "web_researcher": "openai/gpt-4o-mini",
            "content_extractor": "gemini/gemini-2.0-flash",
        },
        search_backend="duckduckgo",
    )
    print(result.synthesis)
    print("\nSources:")
    for source in result.scraped:
        print(f"- {source.title or source.url}: {source.url}")

asyncio.run(main())
```

---

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
- `scraped`: URLs successfully read, with extracted relevant content
- `scrape_failed`: URLs attempted but could not be scraped
- `bot_detected`: URLs blocked by bot protection
- `snippet_only`: search results kept only as snippets
- `queries`: all search queries executed during the run

`UrlEntry` contains `url`, `title`, and `content`.
`SearchQuery` contains `query`, `num_results_returned`, and `domains_restricted`.

---

## API At A Glance

```python
result = await run_web_research(
    query="latest IPCC findings on sea level rise",
    models={
        "web_researcher": "openai/gpt-4o-mini",
        "content_extractor": "gemini/gemini-2.0-flash",
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

---

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

Especially useful for catalog pages, result listings, and structured report libraries.

---

## What It Actually Does (Pipeline)

1. Generate targeted search queries.
2. Search the web with Serper or DuckDuckGo.
3. Triage the best URLs across result sets.
4. Scrape and extract relevant content in parallel.
5. Evaluate whether the evidence actually answers the question.
6. Reuse promising backlog URLs or run follow-up searches if coverage is still weak.
7. Produce a grounded synthesis with inline citations.
8. Run a deterministic citation check before returning.

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
 |   +- Extensionless document downloads via content-type sniffing
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

---

## Search Backends

```python
# Default: Serper (requires SERPER_API_KEY)
await run_web_research(query=..., models=..., search_backend="serper")

# Free: DuckDuckGo (no API key)
await run_web_research(query=..., models=..., search_backend="duckduckgo")
```

- `serper`: Google-quality results with richer metadata
- `duckduckgo`: zero-config and free, ideal for quick starts and lightweight usage

---

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

---

## Configuration

### Models

Model IDs follow [LiteLLM provider naming](https://docs.litellm.ai/docs/providers):

```python
models = {
    # Required
    "web_researcher": "openai/gpt-4o-mini",
    "content_extractor": "gemini/gemini-2.0-flash",

    # Optional step-specific overrides (default: web_researcher)
    "query_generator": "openai/gpt-4o-mini",
    "coverage_evaluator": "openai/gpt-4o-mini",
    "synthesiser": "openai/gpt-4o-mini",

    # Optional fallback for scanned PDFs, image URLs, or empty JS pages
    "vision_fallback": "gemini/gemini-2.0-flash",
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

---

## Use As An Agent Tool

```python
from agents import Agent, function_tool
from web_scout import run_web_research

@function_tool
async def research(query: str) -> str:
    result = await run_web_research(
        query=query,
        models={
            "web_researcher": "openai/gpt-4o-mini",
            "content_extractor": "gemini/gemini-2.0-flash",
        },
        search_backend="duckduckgo",
    )
    sources = "\n".join(f"- {s.url}" for s in result.scraped)
    return f"{result.synthesis}\n\nSources:\n{sources}"

agent = Agent(
    name="researcher",
    model="gpt-4o-mini",
    tools=[research],
    instructions="Use the research tool to answer with up-to-date web sources.",
)
```

---

## Where It Fits Best

`web-scout-ai` is a strong fit when you need:

- up-to-date answers grounded in real web sources
- multi-source synthesis without building a full deep-research stack
- a reusable research tool inside an agent workflow
- better handling of report libraries, list pages, and mixed web/document sources

It is probably not the right tool if you only need simple search snippets or if you want a fully autonomous long-form research agent that decides everything itself.

---

## Requirements

- Python `>=3.10`
- API key for at least one supported LLM provider
- Optional `SERPER_API_KEY` if you want the Serper backend

## Brand Assets

- Full logo: [`assets/web-scout-logo.svg`](assets/web-scout-logo.svg)
- Square logo mark (avatar-safe): [`assets/web-scout-logo-mark.svg`](assets/web-scout-logo-mark.svg)
- Social card preview: [`assets/web-scout-social-card.svg`](assets/web-scout-social-card.svg)

## License

MIT
