# web-scout-ai

![web-scout-ai logo](assets/web-scout-logo.svg)

[![PyPI Version](https://img.shields.io/pypi/v/web-scout-ai)](https://pypi.org/project/web-scout-ai/)
[![PyPI Downloads per Month](https://img.shields.io/pypi/dm/web-scout-ai)](https://pypi.org/project/web-scout-ai/)
[![Python Versions](https://img.shields.io/pypi/pyversions/web-scout-ai)](https://pypi.org/project/web-scout-ai/)
[![License](https://img.shields.io/github/license/RSO9192/web-scout-ai)](LICENSE)

Give it a question or a URL. Get back a cited answer, the extracted evidence behind it, and an audit trail of every source the pipeline could—or could not—read.

```python
from web_scout import run_web_research

result = await run_web_research(
    "What are the main climate risks for Kenya's agricultural sector?"
)

print(result.synthesis)
print(f"Read {len(result.scraped)} sources across {len(result.queries)} searches")
```

<details open>
<summary><strong>Example output</strong> (abridged from a recorded project run)</summary>

> Kenya's agricultural sector is highly exposed because it is approximately
> 98% rain-fed and most farmers lack irrigation. The evidence identifies five
> recurring risks:
>
> - **Heat stress:** temperatures have risen by about 0.21°C per decade since
>   the 1960s and are projected to rise further by 2050.
> - **Less predictable rainfall:** regional drying and greater variability
>   disrupt planting and crop growth.
> - **More frequent droughts and floods:** poor rainy seasons have occurred
>   every 2–3 years since 1999, compared with every 5–6 years previously.
> - **Water insecurity:** glacier retreat and repeated dry periods threaten
>   agricultural water supplies.
> - **Crop and livestock losses:** heat and water stress reduce suitable
>   production areas and increase food-security risk.
>
> Sources: [World Bank climate profile](https://climateknowledgeportal.worldbank.org/sites/default/files/country-profiles/15724-WB_Kenya%20Country%20Profile-WEB.pdf), [Kenya Climate Smart Agriculture Strategy](https://www.adaptation-undp.org/sites/default/files/resources/kenya_climate_smart_agriculture_strategy.pdf), [ILRI](https://www.ilri.org/news/climate-change-africa-what-will-it-mean-agriculture-and-food-security), and four more sources.

That run took 95.2 seconds, executed 3 targeted searches, read 7 sources—including 3 PDFs—and returned a 4,471-character synthesis. Results and timing vary with the query, sources, model, and network.

</details>

## What it actually does

`web-scout-ai` is an async Python research pipeline, not a search-result wrapper. It searches, opens the selected sources, routes each response to the right extractor, evaluates whether the collected evidence is sufficient, and synthesizes only from sources it successfully read.

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

## Quick start

### 1. Install

```bash
pip install web-scout-ai
web-scout-setup
```

`web-scout-setup` installs the Patchright-managed Chromium browser and its system dependencies. It may request `sudo` for OS-level browser libraries.

### 2. Configure keys

The default models use Gemini and open-web discovery uses Serper:

```bash
export GEMINI_API_KEY="your-gemini-api-key"
export SERPER_API_KEY="your-serper-api-key"
```

Direct-URL mode does not use Serper, so it only needs the API key for your configured model provider.

### 3. Run research

```python
import asyncio

from web_scout import run_web_research


async def main():
    result = await run_web_research(
        query="What are the main threats to coral reefs worldwide?",
        cache=True,
    )

    print(result.synthesis)

    print("\nSources read:")
    for source in result.scraped:
        print(f"- {source.title or source.url}: {source.url}")

    if result.bot_detected or result.scrape_failed:
        print(
            f"\nCould not read "
            f"{len(result.bot_detected) + len(result.scrape_failed)} source(s)"
        )


asyncio.run(main())
```

## Three ways to use it

### Open-web research

Generate several searches, read the strongest results in parallel, evaluate coverage, and search again if important evidence is missing.

```python
result = await run_web_research(
    query="What is driving the global adaptation finance gap?",
)
```

### Domain-restricted research

Keep discovery and hub deepening focused on authoritative domains.

```python
result = await run_web_research(
    query="Latest evidence on sea-level rise",
    include_domains=["ipcc.ch", "nasa.gov"],
)
```

### Direct URL extraction

Skip search and start from a page, document, API endpoint, image, or database listing.

```python
result = await run_web_research(
    query="Extract the recommended adaptation measures and supporting evidence",
    direct_url="https://example.org/report.pdf",
)
```

For a document, the pipeline reads that document without wandering into site navigation. For a list or database page, it can rank and follow relevant records and their linked primary documents.

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
| `blocked_by_policy` | Sources skipped by the built-in domain policy |
| `source_http_error` | Source-side HTTP or network failures |
| `scraped_irrelevant` | Pages fetched successfully but not useful for the query |
| `bot_detected` | Sources that still returned a bot-protection wall |
| `snippet_only` | Search results discovered but not opened; snippets are never valid citation targets |
| `queries` | Every search query executed, its result count, and domain restrictions |

`UrlEntry` contains `url`, `title`, and `content`. `SearchQuery` contains `query`, `num_results_returned`, and `domains_restricted`.

The synthesizer is instructed to use scraped evidence only. A deterministic final check rejects citations to invented or snippet-only URLs and retries the synthesis with feedback.

## Research depth

```python
# Faster default
await run_web_research(query="...", research_depth="standard")

# More searches and sources, with a stricter coverage threshold
await run_web_research(query="...", research_depth="deep")
```

| Budget | Standard | Deep |
| --- | ---: | ---: |
| Maximum search iterations | 2 | 3 |
| Initial search queries | 3 | 5 |
| Follow-up search queries | 2 | 4 |
| URLs selected in the first round | 6 | 12 |
| URLs selected in a follow-up round | 4 | 8 |
| Hub/detail-page cap | 10 | 15 |

The coverage evaluator can stop early when the evidence already answers the question. You can add your own acceptance conditions with `coverage_criteria`:

```python
result = await run_web_research(
    query="Compare national methane policies",
    coverage_criteria="Include at least one primary government source per country.",
)
```

## Custom extractor guidance

Use `extractor_guidance` to refine what each per-source extractor keeps and how it
organizes evidence. The guidance applies only to extraction; it does not alter search
query generation, coverage evaluation, or final synthesis.

```python
result = await run_web_research(
    query="Tunisia agricultural trends",
    extractor_guidance="""
Country of interest: Tunisia

- Keep findings explicitly about Tunisia.
- Also keep regional findings whose stated scope includes Tunisia, even when the
  source does not name Tunisia; label them with the source's scope, such as
  "North Africa:".
- Discard purely global findings and findings about geographies that do not
  include Tunisia.
""",
)
```

Guidance augments rather than replaces WebScout's extractor instructions. The base
contract always takes precedence for source grounding, tool constraints, output
fields, page-type handling, and the exact no-evidence sentinel.

## Configuration

The defaults use `gemini/gemini-3-flash-preview`. Model IDs follow [LiteLLM provider naming](https://docs.litellm.ai/docs/providers), so the research and extraction stages can use different providers or models.

```python
models = {
    # Used as the fallback for query generation, coverage, and synthesis
    "web_researcher": "openai/gpt-4o-mini",

    # Reads and extracts individual sources
    "content_extractor": "gemini/gemini-2.0-flash",

    # Optional stage-specific choices
    "query_generator": "openai/gpt-4o-mini",
    "coverage_evaluator": "openai/gpt-4o-mini",
    "synthesiser": "openai/gpt-4o-mini",
    "followup_selector": "openai/gpt-4o-mini",
    "vision_fallback": "gemini/gemini-2.0-flash",
}

result = await run_web_research(query="...", models=models)
```

Provider credentials are read from their standard environment variables, such as `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, or AWS credentials for Bedrock.

### Public API

```python
result = await run_web_research(
    query="latest IPCC findings on sea-level rise",
    models=None,                       # optional; Gemini defaults
    search_backend="serper",          # currently supported search backend
    research_depth="standard",        # "standard", "deep", or a custom dict
    include_domains=["ipcc.ch"],       # optional discovery restriction
    direct_url=None,                   # optional; skips search when set
    domain_expertise="climate science",  # optional prompt context
    allowed_domains=None,              # opt blocked domains back in
    max_pdf_pages=50,                  # pages converted from each PDF
    max_content_chars=30_000,          # characters passed to the extractor per source
    cache=False,                       # process-local source cache
    coverage_criteria=None,            # extra evidence requirements
    extractor_guidance=None,           # optional per-source extraction guidance
)
```

### Domain policy

Common social, video, and consistently paywalled platforms are blocked by default so they do not consume the scrape budget. You can opt a domain back in explicitly:

```python
result = await run_web_research(
    query="...",
    allowed_domains=["reddit.com"],
)
```

### Source caching

With `cache=True`, successful raw source artifacts are reused by later `run_web_research()` calls in the same Python process. Pages and documents are not fetched or converted again, but query-specific extraction and synthesis still run each time.

The cache is in-memory only. Failed scrapes, final answers, query-specific summaries, and click-driven browser sessions are not cached.

## Pipeline, in one view

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

For the maintained control-flow diagrams and exact routing rules, see [docs/pipeline-flow.md](docs/pipeline-flow.md).

## Where it fits

Use `web-scout-ai` when your application needs the contents of real pages and documents—not only search snippets—and you want the source successes and failures returned as structured data.

It is intentionally a bounded research component. If you only need search links, use a search API directly. If you need an open-ended autonomous research process with human checkpoints, put this package inside a broader agent workflow.

## Requirements

- Python 3.10–3.13
- An API key for the configured LLM provider
- A Serper API key for search mode
- Chromium setup for rendered pages, interactive sites, and browser fallbacks

## Contributing

The main extension point is [`SearchBackend`](src/web_scout/search_backends.py). New backends should implement the async `search()` contract and return normalized results and related searches.

Bug reports and focused pull requests are welcome at [github.com/RSO9192/web-scout-ai](https://github.com/RSO9192/web-scout-ai).

## License

[MIT](LICENSE)
