# `web-scout-ai`

![web-scout-ai logo](assets/web-scout-logo.svg)

[![PyPI Version](https://img.shields.io/pypi/v/web-scout-ai)](https://pypi.org/project/web-scout-ai/)
[![PyPI Downloads per Month](https://img.shields.io/pypi/dm/web-scout-ai)](https://pypi.org/project/web-scout-ai/)
[![Python Versions](https://img.shields.io/pypi/pyversions/web-scout-ai)](https://pypi.org/project/web-scout-ai/)
[![License](https://img.shields.io/github/license/RSO9192/web-scout-ai)](LICENSE)

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

Built-in web search tools in frameworks like the OpenAI Agents SDK return snippets — short excerpts from search results that the model has to reason from. They don't read the actual pages.

`web-scout-ai` goes deeper: it scrapes, converts, and extracts relevant content from real pages — static HTML, JS-rendered sites, PDFs, DOCX, and JSON endpoints. You also control exactly which sources get scraped, how deep the pipeline goes, and what counts as good enough coverage before synthesis.

No Tavily + crawl4ai + custom glue code. No open-ended agent you cannot control in production.

---

## Three Real Use Cases

### 1. Climate and policy evidence retrieval

Query institutional sources and get a cited synthesis — not just links.

```python
result = await run_web_research(
    "drought impact on smallholder farmers in sub-Saharan Africa",
    include_domains=["fao.org", "ipcc.ch", "worldbank.org"],
)
```

### 2. Rapid literature scanning

Point it at a report library or database page. It detects list pages, follows item links, and reads the actual documents.

```python
result = await run_web_research(
    "sustainable land management technologies",
    direct_url="https://wocat.net/en/database/list/?type=technology&country=ke",
)
```

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
        models={"web_researcher": "openai/gpt-4o-mini", "content_extractor": "openai/gpt-4o-mini"},
        search_backend="serper",
    )
    print(result.synthesis)
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
    blocked_by_policy: list[UrlEntry]
    source_http_error: list[UrlEntry]
    scraped_irrelevant: list[UrlEntry]
    bot_detected: list[UrlEntry]
    snippet_only: list[UrlEntry]
    queries: list[SearchQuery]
```

- `synthesis`: final grounded answer with inline source citations
- `scraped`: URLs successfully read, with extracted relevant content
- `scrape_failed`: URLs attempted but could not be scraped
- `blocked_by_policy`: URLs skipped because they match the built-in block policy
- `source_http_error`: URLs that failed because the source returned HTTP/network errors
- `scraped_irrelevant`: URLs that were fetched successfully but did not contain relevant content
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

## How It Works

See the maintained flow doc: `[docs/pipeline-flow.md](docs/pipeline-flow.md)`

1. Generate targeted search queries.
2. Search the web with Serper.
3. Triage the best URLs across result sets.
4. Scrape and extract relevant content in parallel.
5. After each non-final search iteration, run the coverage evaluator to decide whether the evidence actually answers the question.
6. If coverage is still weak, either reuse promising backlog URLs or run follow-up searches.
7. Produce a grounded synthesis with inline citations.
8. Run a deterministic citation check before returning.

### Research Modes

```python
# 1) Open web research
await run_web_research(query="...", models=models, search_backend="serper")

# 2) Domain-restricted research
await run_web_research(query="...", models=models, include_domains=["iucn.org", "wwf.org"])

# 3) Direct URL extraction (skip search)
await run_web_research(query="...", models=models, direct_url="https://example.org/report.pdf")

# 4) Direct URL list-page deepening
await run_web_research(query="...", models=models, direct_url="https://wocat.net/en/database/list/?type=technology&country=ke")
```

If the URL is a list, index, or database page, the pipeline detects it, collects relevant item links, follows them, and takes one pagination hop when present.

### How URL Outcomes Are Classified

| What happened                                        | Result bucket        | Meaning                                   |
| ---------------------------------------------------- | -------------------- | ----------------------------------------- |
| Scrape and extraction succeeded                      | `scraped`            | The URL produced usable extracted content |
| Search result was seen but never scraped             | `snippet_only`       | Only the search snippet is kept           |
| URL matched a blocked domain policy                  | `blocked_by_policy`  | Skipped before normal extraction          |
| Source returned HTTP/network errors                  | `source_http_error`  | The source failed, not the package logic  |
| Bot protection or anti-automation page detected      | `bot_detected`       | The URL was reachable but blocked         |
| Page loaded but content was not useful for the query | `scraped_irrelevant` | Fetch succeeded, relevance failed         |
| Extraction failed for other reasons                  | `scrape_failed`      | Generic scrape or extraction failure      |

### Follow-Up Rules

| Situation                                                     | What the pipeline does next                                                           |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `direct_url` is a list / index / database page                | Extract ranked detail links, allow one next-page hop, then scrape selected follow-ups |
| `direct_url` is a document                                    | Do not fan out into site chrome or navigation pages                                   |
| Search mode completes a non-final iteration                   | Run coverage evaluation to decide whether current evidence is sufficient              |
| Search mode has weak coverage but promising snippet-only URLs | Scrape backlog URLs before running new searches                                       |
| Search mode has weak coverage and backlog looks weak          | Generate follow-up search queries                                                     |
| Domain-restricted mode finds a hub page                       | Deepen within the same domain before broadening search                                |

---

## Search Backends

```python
await run_web_research(query=..., models=..., search_backend="serper")
```

- `serper`: Google-quality results with rich metadata (date, rank, People Also Ask, Knowledge Graph). Requires `SERPER_API_KEY` — Serper is generous with free-tier limits.

Additional backends can be added by the community — see `SearchBackend` in `[search_backends.py](src/web_scout/search_backends.py)`.

---

## Research Depth

```python
# Standard (default): usually up to ~10 sources
await run_web_research(query=..., models=..., research_depth="standard")

# Deep: usually up to ~28 sources
await run_web_research(query=..., models=..., research_depth="deep")
```

| Parameter                    | Standard | Deep |
| ---------------------------- | -------- | ---- |
| Max iterations               | 2        | 3    |
| Search queries (first round) | 3        | 5    |
| Search queries (follow-up)   | 2        | 4    |
| URLs scraped (first round)   | 6        | 12   |
| URLs scraped (follow-up)     | 4        | 8    |
| Hub deepening cap            | 10       | 15   |

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

### Domain Control

```python
# Restrict discovery to selected domains
await run_web_research(query=..., models=..., include_domains=["fao.org", "ipcc.ch"])

# Re-allow domains that are blocked by default
await run_web_research(query=..., models=..., allowed_domains=["reddit.com"])
```

By default, the scraper blocks common social and video platforms. `allowed_domains` lets you opt specific domains back in.

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

- Full logo: `[assets/web-scout-logo.svg](assets/web-scout-logo.svg)`
- Square logo mark (avatar-safe): `[assets/web-scout-logo-mark.svg](assets/web-scout-logo-mark.svg)`
- Social card preview: `[assets/web-scout-social-card.svg](assets/web-scout-social-card.svg)`

## License

MIT
