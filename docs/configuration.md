# Configuration

Reference for every `run_web_research()` option and the supporting helpers. For installation and a first run, see the [README](../README.md).

## Search backends

Serper is the default search backend (`SERPER_API_KEY`).

To use the Exa search backend instead (`search_backend="exa"`):

```bash
export EXA_API_KEY="your-exa-api-key"
```

To use [Parallel Search MCP](https://docs.parallel.ai/integrations/mcp/search-mcp)
for free web search without a search-provider account or API key, select
`search_backend="parallel"`:

```python
result = await run_web_research(
    "What are the main threats to coral reefs worldwide?",
    search_backend="parallel",
)
```

Parallel uses anonymous Fast-mode search over Streamable HTTP. No additional
package or search key is needed; keep the keys for your configured research
models. Anonymous access has usage limits, and search failures are handled by
the existing pipeline. Domain filters are enforced locally on returned URLs;
domain and country/language hints also guide the search, but do not guarantee
that it will find enough matching results. Source reading still uses the
existing scraping pipeline. Serper remains the default.

Direct-URL mode does not use a search backend, so it only needs the API key for your configured model provider.

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

## Models

Pass `models` to choose the LLM for each stage. Model IDs follow [LiteLLM provider naming](https://docs.litellm.ai/docs/providers), so each stage can use a different provider. Any key you omit keeps its default. These are the defaults:

```python
models = {
    # Query generation, search-result ranking, coverage evaluation and synthesis,
    # unless overridden below
    "web_researcher": "bedrock_mantle/openai.gpt-6-luna",
    # Reads and extracts each source
    "content_extractor": "bedrock_mantle/openai.gpt-6-luna",
    # Chooses which follow-up links to open
    "followup_selector": "bedrock_mantle/openai.gpt-6-luna",
    # Describes scanned pages, charts, maps and images
    "vision_fallback": "gemini/gemini-3.7-flash",
    # Optional per-stage overrides; each falls back to "web_researcher"
    # "query_generator": "...",
    # "search_result_selector": "...",  # ranks search results before scraping (unused when Jev is on)
    # "coverage_evaluator": "...",
    # "synthesiser": "...",
}

result = await run_web_research(query="...", models=models)
```

For example, to keep the defaults but extract sources with Gemini:

```python
result = await run_web_research(
    query="...",
    models={"content_extractor": "gemini/gemini-3.7-flash"},
)
```

Provider credentials are read from their standard environment variables, such as `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, or AWS credentials for Bedrock.

## All options

```python
result = await run_web_research(
    query="latest IPCC findings on sea-level rise",
    models=None,  # optional; GPT-6 Luna defaults, Gemini vision
    search_backend="serper",  # "serper" (default), "exa", or "parallel" (keyless MCP)
    research_depth="standard",  # "standard", "deep", or a custom dict
    include_domains=["ipcc.ch"],  # optional discovery restriction
    direct_url=None,  # optional; skips search when set
    domain_expertise="climate science",  # optional prompt context
    exclude_domains=None,  # optional blocklist; nothing is blocked
    # by default. See "Domain policy" below.
    max_pdf_pages=50,  # pages converted from each PDF
    max_content_chars=30_000,  # characters passed to the extractor per source
    verify_pdf_claims=False,  # extra claim-support check on PDF extracts
    cache=False,  # accepted; URL and PDF caches stay on
    cache_storage=None,  # Prefect block, e.g. a GCS bucket
    refresh_pdf_cache=False,  # recompute cached PDF parses
    refresh_url_cache=False,  # recompute cached URL fetches
    coverage_criteria=None,  # extra evidence requirements
    extractor_guidance=None,  # optional per-source extraction guidance
)
```

For country-specific questions, the query planner identifies the canonical country
name and automatically combines English queries with queries in the country's principal
publication language. Web Scout resolves the country name through its internal ISO 3166-1
registry before any code reaches a search backend. Serper maps the validated code and
planned language to its country and language options; Exa maps the country to
`userLocation` and relies on the translated query text for language targeting.
Both initial and gap-directed follow-up searches use the same multilingual policy.
The search-result selector also treats requested geography as part of relevance,
favoring sources with substantive place-specific coverage without hard-coding any
country or excluding stronger international evidence. It runs on the
`search_result_selector` model (default: `web_researcher`), or on Jev when
`DISABLE_JEV=false`, and never sees URLs from `exclude_domains`. If the GPT call fails
it falls back to backend rank order. Localization is fully automatic;
there are no public country or language parameters. Final syntheses are always written
in English, including when evidence was retrieved from non-English sources.

## Jev classification (optional)

Five steps make yes/no judgments rather than generate text: search-result selection, crawler link selection, follow-up link selection, search coverage evaluation, and (when `verify_pdf_claims=True`) PDF claim verification. By default they use the GPT models above. Set `DISABLE_JEV=false` to run all five on TypeSafe Jev instead:

```bash
export DISABLE_JEV=false
export TYPESAFE_API_KEY="your-typesafe-api-key"
```

| `DISABLE_JEV` | Classification steps use | Needs `TYPESAFE_API_KEY` |
| --- | --- | --- |
| unset or `true` (default) | GPT models from `models` | No |
| `false` | Jev | Yes; `run_web_research()` raises `ClassificationError` without it |

The value is case-insensitive. Query generation, extraction, vision and synthesis always use your configured models. Jev failures raise errors; there is no automatic GPT fallback.

## Fetching a single URL

Fetch one URL and get a `FetchResult`. This uses the same Scrapling fetcher as the research pipeline (fast HTTP, then a stealth browser when the page looks blocked or JavaScript-rendered):

```python
from web_scout import PDF_MAGIC_BYTES, fetch

result = await fetch("https://example.org/report.pdf")
page = await fetch("https://example.org/article", wait_for="article")
```

`result.body` is the raw response bytes for binary payloads such as PDFs and images. HTML and other text responses leave `body` as `None` and put the decoded page in `result.html_content`. Failures do not raise: `result.error` is set when the URL is rejected or the fetch fails, and `result.status` is outside the 2xx range for HTTP errors. Pass `exclude_domains` to skip hosts before any network call.

`PDF_MAGIC_BYTES` is `b"%PDF"`, the four-byte header every PDF file starts with. Check the body against it when you need to know whether the fetch returned a PDF. That is more reliable than `result.content_type`, which download URLs often report as `application/octet-stream`:

```python
is_pdf = bool(result.body and result.body.startswith(PDF_MAGIC_BYTES))
if is_pdf:
    pdf_bytes = result.body
```

`fetch_pdf` is a thin wrapper around `fetch` that does this check and returns the PDF bytes. It raises `RuntimeError` when the fetch fails or the body does not start with `PDF_MAGIC_BYTES`:

```python
from web_scout import fetch_pdf

pdf_bytes = await fetch_pdf("https://openknowledge.fao.org/bitstreams/c1caede2-ea98-46b0-b663-4cae429e05d3/download")
```

## Domain policy

Nothing is blocked by default: `exclude_domains=None` means every domain is eligible for search and scraping. To block domains, pass a list explicitly. A curated list of platforms that waste the scrape budget (social/video sites with no scrapeable text, bot-blocked search engines, hard-paywalled publishers) is exported as `RECOMMENDED_EXCLUDE_DOMAINS`:

```python
from web_scout import run_web_research, RECOMMENDED_EXCLUDE_DOMAINS

# Opt in to the curated list
result = await run_web_research(
    query="...",
    exclude_domains=RECOMMENDED_EXCLUDE_DOMAINS,
)

# Curated list plus your own additions
result = await run_web_research(
    query="...",
    exclude_domains=[*RECOMMENDED_EXCLUDE_DOMAINS, "statista.com"],
)
```

Exclusions are applied natively by search backends that support them (Exa's `excludeDomains`); backends without native support (Serper) rely on the post-search URL filter, which always runs. Contradictions fail fast: a domain appearing in both `include_domains` and `exclude_domains`, or a `direct_url` whose host is excluded, raises `ValueError`.

## Source caching

URL fetches and PDF parses are cached with Prefect. A second request for the same URL reuses the stored page or document. A second request for the same PDF bytes reuses the parse even when the URL is different. Query-specific extraction and synthesis still run each time.

By default the cache is on disk at `~/.cache/web-scout` and survives a new process. Pass `cache_storage` to store both caches in a caller-supplied Prefect block, such as a GCS bucket:

```python
from prefect_gcp.cloud_storage import GcsBucket

result = await run_web_research(
    query="...",
    cache_storage=GcsBucket(bucket="my-cache"),
    refresh_pdf_cache=False,
    refresh_url_cache=False,
)
```

`refresh_pdf_cache=True` and `refresh_url_cache=True` recompute those entries. `cache=True` is still accepted and does not turn the cache off. Failed scrapes, final answers, query-specific summaries, and click-driven browser sessions are not cached. Concurrent workers wait rather than fetching or parsing the same URL or PDF at the same time.

## Resource reuse

CPU PDF pipelines initialize on the first document and are reused. Browser and HTTP resources are reused on the same event loop; after requests finish, call `await web_scout.scraping.close_resources()` at application shutdown. See the [performance report](performance.md) for measurements, limits, benchmark commands and compatibility details.

## Orchestrator link selection

`Orchestrator` uses `DefaultCrawler`, which selects links from already-parsed content with the same selectors as the research pipeline, so `DISABLE_JEV` applies to it too. With GPT it uses the default `followup_selector` model unless you pass a custom `llm_config`.

`Crawl4AICrawler` remains an alias for `DefaultCrawler` to preserve existing imports. `llm_config=None` requests heuristic-only selection. Config objects with `provider`, `api_token` and `base_url` are accepted for custom GPT connections. No Crawl4AI package, browser or setup is required.
