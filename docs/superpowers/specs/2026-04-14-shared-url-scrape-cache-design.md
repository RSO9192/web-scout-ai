# Shared URL Scrape Cache for `run_web_research`

**Date:** 2026-04-14
**Status:** Approved

## Context

When a caller (e.g. `run_figures_web_search_pipeline`) makes multiple successive calls to
`run_web_research` with related queries, the same URLs can be scraped and extracted more than
once. Each scrape is expensive: it invokes an extractor sub-agent, consumes tokens, and adds
latency. A shared cache dict passed across calls eliminates the redundant work.

## Goal

Add an optional `url_cache: dict[str, str] | None = None` parameter to `run_web_research`.
When a caller provides a shared dict, any URL already scraped in a previous call is served
from the cache instead of being re-scraped. The default is `None`, preserving all existing
behaviour.

## Public API Change

```python
async def run_web_research(
    query: str,
    models: Dict[str, str],
    include_domains: Optional[List[str]] = None,
    direct_url: Optional[str] = None,
    search_backend: str = "serper",
    domain_expertise: Optional[str] = None,
    research_depth: str = "standard",
    allowed_domains: Optional[List[str]] = None,
    max_pdf_pages: int = 50,
    url_cache: dict[str, str] | None = None,   # ← new
) -> WebResearchResult:
```

Existing callers passing positional or keyword arguments are unaffected.

## New Helper: `_apply_url_cache`

A private async function placed just above `run_web_research`:

```python
async def _apply_url_cache(
    urls: list[str],
    scrape_fn,
    tracker: ResearchTracker,
    url_cache: dict[str, str] | None,
) -> tuple[list[str], list[tuple[str, str]]]:
```

### Behaviour

1. **Cache disabled** (`url_cache is None`): scrape all URLs normally via `_gather_scrapes`,
   return `(extracted_contents, list(zip(urls, extracted_contents)))`.

2. **Cache enabled**: split `urls` into hits and misses while preserving original order via
   index tracking.

   - **Hits** (normalized URL already in `url_cache`):
     - Log: `logger.info("[pipeline] url cache hit: %s", url)`
     - Call `tracker.record_scrape(url, "", cached_content)` to register the URL as scraped
       in the tracker (title left empty — any title from a prior search snippet is preserved).
     - Record the cached content at its original position.

   - **Misses** (normalized URL not in `url_cache`):
     - Scrape concurrently via `_gather_scrapes`.
     - After scraping, for each miss where `tracker._actions.get(norm) == "scraped"`,
       store the full `scrape_fn` return string in `url_cache[norm]`.
     - Using the tracker's action state as the success predicate avoids re-implementing
       failure-string heuristics and stays correct if the scrape tool's error messages change.

3. Reassemble `extracted_contents` in original order, build
   `iter_results = list(zip(urls, extracted_contents))`, return both.

### Cache key

`ResearchTracker._normalize_url(url)` — same normalization already used by the tracker
(strips tracking params, lowercases netloc, strips trailing slash, normalizes scheme to https).

### Cache value

The full string returned by `scrape_fn` (i.e. `scrape_and_extract`). This includes the
`# Title\nSource: url\n\n` header and, for hub pages, the `**Page type: list**` marker.
Storing the full return value means cache hits feed into `iter_results` identically to live
scrapes, including correct hub detection.

### Concurrency / locking

No locking required. All callers share the same asyncio event loop. CPython dict reads and
writes do not context-switch mid-operation, so plain dict access is safe.

## Call Sites Modified in `run_web_research`

Two locations in `agent.py` replace the existing `if urls_to_scrape: ... _gather_scrapes`
block with a single call:

```python
extracted_contents, iter_results = await _apply_url_cache(
    urls_to_scrape, scrape_tool, tracker, url_cache
)
```

**Site 1 — Evaluator-backlog path** (`if iteration > 0 and not needs_new_searches:`):
replaces the `if urls_to_scrape:` block that runs when the coverage evaluator selected
promising unscraped backlog URLs.

**Site 2 — Normal search path** (inside the `else:` branch after query generation and URL
triage): replaces the `if urls_to_scrape:` block that runs after interleaved URL triage.

All deepening paths (`deep_tasks` passed to `_gather_scrapes`) are **not** modified — the
minimal-and-safe scope decision keeps the change localized.

## What Is Not Changed

- `create_scrape_and_extract_tool` and `ResearchTracker` — no changes.
- Direct URL mode — no changes.
- All hub deepening and domain-restricted deepening paths — no changes.
- `_gather_scrapes` — no changes.
- All other parameters and pipeline logic — no changes.

## Caller Usage Example

```python
url_cache: dict[str, str] = {}

for query in figure_queries:
    result = await run_web_research(
        query=query,
        models=models,
        url_cache=url_cache,   # shared across calls
    )
```

## Testing

- Unit test: `_apply_url_cache` with a pre-populated cache dict — assert that hits skip
  `scrape_fn`, that `tracker.record_scrape` is called for hits, and that results are
  reassembled in the correct order.
- Unit test: `url_cache=None` path — assert behaviour is identical to the existing pipeline.
- Integration / probe test: run two queries that share at least one URL; confirm the second
  call logs cache hits and produces fewer `scrape_count` increments.
