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

- a search API (Serper)
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
        query="Kenya interannual variability and long-term trends in precipitation — current status and recent trend",
        models={
            "web_researcher": "gemini/gemini-3-flash-preview",
            "content_extractor": "gemini/gemini-3-flash-preview",
        },
        search_backend="serper",
    )
    print(result.synthesis)
    print(f"\n{len(result.scraped)} sources read, avg {sum(len(s.content) for s in result.scraped) // len(result.scraped):,} chars/source")

asyncio.run(main())
```

**Real output** (from an actual run — sources, numbers, and dates are live from the web):

```text
Precipitation in Kenya is characterized by extreme interannual variability and
distinct seasonal trends that have shifted significantly in recent decades.
The country's climate is dominated by a bimodal rainfall pattern consisting of
the 'long rains' (March–May, MAM) and 'short rains' (October–December, OND).

Long-Term Precipitation Trends
Historically, the two main rainy seasons have exhibited opposing trends:

• Long Rains (MAM): Between 1985 and 2010, a consistent drying trend was
  observed, attributed to a shortening of the season through delayed onset and
  earlier cessation. However, this trend has shown signs of recovery since 2018
  due to extremely wet seasons in 2018, 2020, and 2024.

• Short Rains (OND): A consistent wetting trend has been recorded from 1983 to
  2021, with seasonal rainfall increasing by approximately 1.44 to 2.36 mm per
  year. Projections suggest the short rains may deliver more total rainfall than
  the long rains by 2030–2040.

Current Status (2024)
The year 2024 exemplified the current state of extreme variability:

• MAM 2024: Recorded as one of the wettest seasons on record for several
  stations, including Nairobi and Central Kenya. Ndakaini station recorded a
  seasonal high of 1,355.5 mm. Many areas received 111% to over 200% of their
  long-term mean, resulting in widespread flooding and crop destruction.

• OND 2024: In sharp contrast, the short rains were generally below average,
  receiving only 26–75% of normal rainfall in the Northeast and Turkana
  regions. This poor performance led to a deterioration in food security, with
  2.15 million people facing food insecurity by early 2025.

Interannual Variability and Drivers
Rainfall variability has increased substantially since 2013, marked by more
frequent and intense extremes. Primary drivers include the Indian Ocean Dipole
(IOD) — positive IOD phases can lead to rainfall totals 2–3 times the
long-term mean — and ENSO, though the coherence between ENSO and Kenyan
rainfall has diminished since 2013, suggesting other regional factors are
becoming more influential.

4 sources read, avg 2,701 chars/source
```

**Sources actually scraped:**

- [Observations of enhanced rainfall variability in Kenya, East Africa (1981–2021)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11153539/) — PMC / Scientific Reports
- [Drivers and impacts of Eastern African rainfall variability](https://www.icpac.net/documents/829/s43017-023-00397-x_1.pdf) — ICPAC / Nature Reviews
- [State of the Climate Kenya 2024](https://meteo.go.ke/documents/1353/State_of_the_Climate_Kenya_2024_v1.pdf) — Kenya Meteorological Department (PDF)
- [State of the Climate Report Kenya 2024](https://www.sei.org/publications/state-climate-report-kenya-2024/) — Stockholm Environment Institute

---

## Quick Start

### Install

```bash
pip install web-scout-ai
web-scout-setup   # installs Chromium for JS-rendered pages
```

### First run

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
        search_backend="serper",
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
    search_backend="serper",
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
    search_backend="serper",
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
2. Search the web with Serper.
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
 +- Search web (Serper)
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
await run_web_research(query=..., models=..., search_backend="serper")
```

- `serper`: Google-quality results with rich metadata (date, rank, People Also Ask, Knowledge Graph). Requires `SERPER_API_KEY` — Serper is generous with free-tier limits.

Additional backends can be added by the community — see `SearchBackend` in [`search_backends.py`](src/web_scout/search_backends.py).

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
# Search backend
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
        search_backend="serper",
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
- `SERPER_API_KEY` for the Serper search backend (generous free tier)

## Brand Assets

- Full logo: [`assets/web-scout-logo.svg`](assets/web-scout-logo.svg)
- Square logo mark (avatar-safe): [`assets/web-scout-logo-mark.svg`](assets/web-scout-logo-mark.svg)
- Social card preview: [`assets/web-scout-social-card.svg`](assets/web-scout-social-card.svg)

## License

MIT
