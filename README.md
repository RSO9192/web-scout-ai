# web-scout-ai

![web-scout-ai logo](https://raw.githubusercontent.com/RSO9192/web-scout-ai/main/assets/web-scout-logo.svg)

[![PyPI Version](https://img.shields.io/pypi/v/web-scout-ai)](https://pypi.org/project/web-scout-ai/)
[![Downloads](https://static.pepy.tech/badge/web-scout-ai/month)](https://pepy.tech/project/web-scout-ai)
[![Python Versions](https://img.shields.io/pypi/pyversions/web-scout-ai)](https://pypi.org/project/web-scout-ai/)
[![License](https://img.shields.io/github/license/RSO9192/web-scout-ai)](https://github.com/RSO9192/web-scout-ai/blob/main/LICENSE)

**The web research tool for your agent: cited answers, with the evidence behind every claim.**

Give your agent one tool call. `web-scout-ai` searches the web, reads the pages and documents it finds, follows up when evidence is incomplete, and returns a cited answer together with what it extracted from each source and an account of every URL it could not read.

**On document-heavy research questions, 91% of Web Scout's claims were backed by a page it cites, against 78% for OpenAI web search, with a third fewer unsupported claims per answer** ([evaluation](#evaluation)).

![A sentence from result.synthesis linked to the extract in result.scraped that backs it, above the audit-trail counts for the same call](https://raw.githubusercontent.com/RSO9192/web-scout-ai/main/assets/web-scout-evidence.svg)

```python
from agents import Agent, Runner, function_tool
from web_scout import run_web_research


@function_tool
async def web_research(question: str) -> str:
    """Research a question on the web and return an answer with inline citations."""
    result = await run_web_research(question)
    return result.synthesis


agent = Agent(
    name="Analyst",
    instructions="Use web_research whenever you need facts from web sources.",
    tools=[web_research],
)
result = await Runner.run(agent, "Indonesia biodiversity hotspots: current status and recent trend")
print(result.final_output)
```

The example uses the [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/), which `web-scout-ai` already depends on. `run_web_research()` is a plain async function, so it wraps the same way as a tool in LangChain, pydantic-ai or any other framework.

<details>
<summary><strong>Example output</strong> (excerpt)</summary>

Query: `Indonesia biodiversity hotspots current status and recent trend`

> Indonesia’s two recognized biodiversity hotspots are Sundaland and Wallacea
> [Threatened Biodiversity Hotspots in
> Indonesia](https://www.kew.org/science/our-science/projects/indonesia-biodiversity-hotspot-old).
> The clearest recent national-level indicator—not a hotspot-by-hotspot
> measure—is the Red List Index: it fell from 0.77 in 2018 to 0.75 in 2024;
> Indonesia’s 2025–2029 strategic plan sets a target of 0.76 by 2029 [Deputy
> Minister: Biodiversity Status Documents Become a Compass for Indonesia’s
> Conservation
> Policy](https://www.kehutanan.go.id/pers/wamenhut-dokumen-status-keanekaragaman-hayati-jadi-kompas-kebijakan-konservasi-indonesia).
> The index is described as an aggregate indicator of species extinction risk
> [Deputy Minister: Biodiversity Status Documents Become a Compass for
> Indonesia’s Conservation
> Policy](https://www.kehutanan.go.id/pers/wamenhut-dokumen-status-keanekaragaman-hayati-jadi-kompas-kebijakan-konservasi-indonesia).
>
> For Wallacea, the available detailed profile is dated: its latest
> biodiversity-status data were compiled through November 1, 2013. It recorded
> 560 globally threatened species—308 terrestrial or freshwater and 252
> marine—including 35 critically endangered, 108 endangered, and 417 vulnerable
> species [Wallacea Biodiversity Hotspot, pp. 5, 9–10,
> 20](https://www.cepf.net/resources/calls-for-proposals/ecosystem-profile-technical-summary-1).
> Forest cover was reported as 17.7 million hectares, about half of Wallacea’s
> land area, in 2011 [Wallacea Biodiversity Hotspot, pp. 5, 9–10,
> 20](https://www.cepf.net/resources/calls-for-proposals/ecosystem-profile-technical-summary-1).
> For 2000–2011, annual deforestation rates in Key Biodiversity Areas were 0.09%
> in protected areas, 0.21% in unprotected areas, and 0.29% in partially
> protected areas [Wallacea Biodiversity Hotspot, pp. 5, 9–10,
> 20](https://www.cepf.net/resources/calls-for-proposals/ecosystem-profile-technical-summary-1).
> These figures show documented pressures but are too old to establish
> Wallacea’s current status.

This is a fraction of what that call returned. The full synthesis was 4,804 characters; `result.scraped` also held the extract from each of the 8 sources it read (~6,700 characters)—including a CEPF ecosystem profile, a 2024 national biodiversity status report, Indonesia's Ministry of Forestry and UNEP's country fiche—plus an audit trail of the 3 searches, 3 irrelevant fetches, 1 source error and 17 snippet-only hits that were not used as citations. Results vary with the query, sources, model, and network.

</details>
## Why Web Scout

A hosted web-search tool gives your agent an answer and a list of links. Web Scout also gives it:

- **The evidence, per source.** `result.scraped` holds the query-relevant extract from every page and document it read, with page references for PDFs, so each claim can be checked without fetching the pages again.
- **Citations only to what it read.** A deterministic check rejects citations to invented or snippet-only URLs before the answer is returned.
- **Coverage, not first hits.** It checks whether the evidence answers every part of the question, then follows promising links or runs targeted searches, also in the language of the country you ask about.
- **An account of every URL.** Sources that failed, were blocked, hit a bot wall or turned out to be irrelevant are listed with the reason instead of disappearing.
- **Your own stack.** Choose the models and provider, restrict or exclude domains, and cache sources.

If you only need search links, use a search API directly. Web Scout is a bounded research component: put it inside your agent when the agent needs to read real sources.

## Quick start

```bash
pip install web-scout-ai
web-scout-setup
```

`web-scout-setup` installs the Patchright-managed Chromium browser and its system dependencies. It may request `sudo` for OS-level browser libraries.

The default research models run on Amazon Bedrock Mantle, vision fallback uses Gemini, and open-web search uses Serper:

```bash
export AWS_BEARER_TOKEN_BEDROCK="your-bedrock-api-key"
export GEMINI_API_KEY="your-gemini-api-key"
export SERPER_API_KEY="your-serper-api-key"
```

Exa and keyless [Parallel Search MCP](https://docs.parallel.ai/integrations/mcp/search-mcp) are also supported, and any [LiteLLM](https://docs.litellm.ai/docs/providers) model can replace the defaults; see [Configuration](https://github.com/RSO9192/web-scout-ai/blob/main/docs/configuration.md).

```python
import asyncio

from web_scout import run_web_research


async def main():
    result = await run_web_research("What are the main threats to coral reefs worldwide?")

    print(result.synthesis)

    print("\nSources read:")
    for source in result.scraped:
        print(f"- {source.title or source.url}: {source.url}")

    if result.bot_detected or result.scrape_failed:
        print(f"\nCould not read {len(result.bot_detected) + len(result.scrape_failed)} source(s)")


asyncio.run(main())
```

Restrict discovery to trusted domains, or skip search and start from a page, document, API endpoint or database listing:

```python
result = await run_web_research("Latest evidence on sea-level rise", include_domains=["ipcc.ch", "nasa.gov"])

result = await run_web_research(
    "Extract the recommended adaptation measures and supporting evidence",
    direct_url="https://example.org/report.pdf",
)
```

## Evaluation

Web Scout (default settings) and OpenAI web search (`gpt-6-astra`, high reasoning effort) answered 11 questions, twice each: 8 document-heavy questions whose answers sit inside official documents and data, and 3 simple lookups a search snippet can answer. An independent fetcher downloaded every page each answer cites, and a Gemini judge split each answer into claims and checked them against those pages. Only the final answers were scored; Web Scout's per-source extracts come on top.

| Web Scout / OpenAI web search | Document-heavy (8) | Simple lookups (3) |
| --- | --- | --- |
| Evidence returned with the answer | **Extract from every source read / links only** | **Extract from every source read / links only** |
| Verified claims: cited and backed by the cited pages | **91% / 78%** | 100% / 100% |
| Unsupported or contradicted claims per answer | **0.7 / 1.1** | 0.0 / 0.0 |
| Claims with a citation | **99% / 87%** | 100% / 100% |
| Completeness (1–5) | 4.4 / 4.3 | 5.0 / 5.0 |

Run on 2026-10-27 with v1.13.0. Per-question results, scoring rules and limitations are in [docs/evaluation.md](https://github.com/RSO9192/web-scout-ai/blob/main/docs/evaluation.md); reproduce with `python eval/compare_search.py`.

## The return value is an audit trail

`run_web_research()` returns a typed `WebResearchResult`:

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

| Field | Meaning |
| --- | --- |
| `synthesis` | Final answer with inline Markdown citations |
| `scraped` | Sources successfully read and extracted; `content` contains the query-relevant evidence |
| `scrape_failed` | Extraction attempts that failed for an unclassified reason |
| `blocked_by_policy` | Sources skipped because their domain is in `exclude_domains` |
| `source_http_error` | Source-side HTTP or network failures |
| `scraped_irrelevant` | Pages fetched successfully but not useful for the query |
| `bot_detected` | Sources that still returned a bot-protection wall |
| `snippet_only` | Search results discovered but not opened; snippets are never valid citation targets |
| `queries` | Every search query executed, its result count, domain restrictions, country, and language |

`UrlEntry` contains `url`, `title`, and `content`. `SearchQuery` contains `query`,
`num_results_returned`, `domains_restricted`, `country_code`, and `language_code`.

The synthesizer is instructed to use scraped evidence only. A deterministic final check rejects citations to invented or snippet-only URLs and retries the synthesis with feedback.

## How it works

```text
question
   │
   ├─ generate targeted searches ─ search in parallel ─ select diverse URLs
   │                                                     │
   │                                                     ▼
   │      static HTML ────────┐                    fetch in parallel
   │      JS / bot challenge ─┤                          │
   │      documents ──────────┼─ route + extract ◀───────┘
   │      JSON / images ──────┘          │
   │                                    ▼
   └─ follow-up search ◀── evaluate evidence coverage
                                        │ sufficient
                                        ▼
                              grounded synthesis
                                        │
                                        ▼
                               citation validation
```

| Input encountered | What the pipeline does |
| --- | --- |
| Static HTML | Uses a fast HTTP fetch and converts the page to clean Markdown |
| JS-heavy pages and SPA shells | Escalates to a stealth Chromium browser and can interact with page controls |
| Cloudflare or similar bot challenges | Retries with Scrapling's stealth browser and Cloudflare challenge handling |
| PDF, DOCX, PPTX, XLSX | Downloads and converts the document with Docling |
| Scanned PDFs, charts, maps, images | Uses the configured vision model when text extraction is insufficient |
| JSON endpoints | Converts structured payloads into readable evidence |
| List, index, and database pages | Ranks detail links, follows them, and can take one pagination hop |
| Thin or incomplete evidence | Scrapes promising backlog URLs or generates targeted follow-up searches |

The browser fallback can pass many JS-gated and Cloudflare-protected pages, but it is not a guarantee: sites can still block automation. Those URLs are reported in `result.bot_detected` instead of disappearing or being presented as evidence. Use the package only where you have permission and in accordance with the source site's terms.

## Main options

| Option | Default | What it does |
| --- | --- | --- |
| `research_depth` | `"standard"` | `"deep"` runs more searches and reads more sources |
| `include_domains` | `None` | Restricts discovery to these domains |
| `exclude_domains` | `None` | Blocks domains; `RECOMMENDED_EXCLUDE_DOMAINS` is a curated list |
| `direct_url` | `None` | Skips search and starts from this URL |
| `search_backend` | `"serper"` | `"exa"` or `"parallel"` (keyless) |
| `models` | GPT-6 Luna, Gemini vision | LLM per stage, using LiteLLM model IDs |
| `coverage_criteria` | `None` | Extra conditions the evidence must meet |
| `extractor_guidance` | `None` | Refines what each source extractor keeps |

Every option, the single-URL `fetch()` helper, source caching and resource cleanup are documented in [Configuration](https://github.com/RSO9192/web-scout-ai/blob/main/docs/configuration.md). See the [performance report](https://github.com/RSO9192/web-scout-ai/blob/main/docs/performance.md) for measurements and limits.

## Requirements

- Python 3.13
- An API key for the configured LLM provider
- A Serper or Exa API key for search mode (Parallel needs none)
- Chromium setup for rendered pages, interactive sites, and browser fallbacks

## Contributing

The main extension point is [`SearchBackend`](https://github.com/RSO9192/web-scout-ai/blob/main/src/web_scout/search_backends.py). New backends should implement the async `search()` contract and return normalized results (title, url, snippet).

Bug reports and focused pull requests are welcome at [github.com/RSO9192/web-scout-ai](https://github.com/RSO9192/web-scout-ai).

## License

[MIT](https://github.com/RSO9192/web-scout-ai/blob/main/LICENSE)
