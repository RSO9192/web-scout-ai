# Design: Pipeline Speed Optimisation

**Date:** 2026-04-21
**Status:** Approved

## Problem

Profiling a standard-depth open-web query revealed three distinct bottlenecks:

| Bottleneck | Root cause | Observed cost |
|---|---|---|
| Duplicate `scrape_linked_document` calls | No shared cache — each parallel extractor agent independently re-downloads the same linked document | 9/17 scrape calls were duplicates (e.g. SOFIA 2024 report fetched ×5) |
| Low extractor concurrency | `max_concurrent=3` — 10 URLs processed in batches of 3 | Forces ≥4 sequential LLM batches even when more parallelism is safe |
| Unnecessary second iteration | Coverage evaluator always runs between iterations even when iteration 1 already produced enough sources | 4 extra extractor LLM calls + 6.8s coverage eval when iteration 1 scraped 6 sources |

**Total observed:** 249s wall-clock for a standard query (sum of LLM calls: 535s).
**Target:** 30–45s for HTML-heavy queries; 80–100s for queries that hit slow PDFs (hard floor without per-URL timeout).

## Chosen approach

Three targeted changes with no public API changes and no architectural restructuring.

## Components

### 1. Shared document scrape cache — `tools.py`

A `dict[str, str]` created once per pipeline run in `create_scrape_and_extract_tool` and passed into every `_build_extractor_agent` call.

Inside the `scrape_linked_document` closure, check the cache before any network or LLM work:

```python
norm = ResearchTracker._normalize_url(document_url)
if norm in _doc_cache:
    return _doc_cache[norm]
# ... existing _validate_url + _scrape_url logic ...
_doc_cache[norm] = result_string
return result_string
```

**What it prevents:** The same linked document (e.g. the SOFIA 2024 report linked from multiple search results) being re-validated and re-scraped by every parallel extractor agent that encounters it.

**What it does not change:** The extractor agent LLM call per primary URL — those remain independent and are not deduplicated. The cache only covers the secondary `scrape_linked_document` tool.

**Thread safety:** The cache dict is accessed from `asyncio` coroutines in the same event loop — no locks needed (Python's GIL and asyncio's cooperative scheduling make single-threaded dict access safe here).

### 2. Increase `max_concurrent` default 3 → 6 — `tools.py`

Change the default in `create_scrape_and_extract_tool`:

```python
def create_scrape_and_extract_tool(
    ...
    max_concurrent: int = 6,   # was 3
    ...
):
```

No call-site changes needed. All 6 iteration-1 URLs now process in a single parallel batch; wall time = max(individual extraction times) rather than ceil(6/3) = 2 sequential batches.

For HTML-heavy queries (5–15s per extraction) this roughly halves iteration-1 time. For queries that hit slow PDFs the gain is smaller but still real for the non-bottleneck URLs.

### 3. Skip coverage evaluator if ≥ 4 sources scraped — `agent.py`

Inside the per-iteration coverage evaluation block (which runs when `iteration < max_iterations - 1`), add an early-exit check before the evaluator LLM call:

```python
scraped_entries = tracker.build_result_groups()["scraped"]
if len(scraped_entries) >= 4:
    logger.info(
        "[pipeline] %d sources scraped after iteration %d — skipping coverage evaluation",
        len(scraped_entries), iteration + 1,
    )
    break
```

**Effect:** When iteration 1 produces ≥ 4 successfully scraped sources, the pipeline skips the coverage evaluator LLM call, skips iteration 2 entirely, and goes straight to synthesis.

**Threshold rationale:** 4 is conservative — enough evidence for a solid synthesis, low enough that niche domain-restricted queries (which often scrape only 1–2 sources per iteration) still get a second iteration.

**What is unchanged:** The "0 successful scrapes" fallback path that forces another iteration regardless — that remains in place.

## Data flow

```
create_scrape_and_extract_tool(max_concurrent=6)
    _doc_cache = {}  ← shared across all extractor agents this run

    for each URL in batch (up to 6 concurrent):
        _build_extractor_agent(..., doc_cache=_doc_cache)
            raw_scrape()           ← primary URL, unchanged
            scrape_linked_document(url):
                check _doc_cache[norm(url)]  ← cache hit? return immediately
                else: scrape → store in _doc_cache → return

    after iteration 1:
        if len(scraped) >= 4:  ← new early exit
            break → synthesis
        else:
            run coverage evaluator → iteration 2 (unchanged)
```

## What does NOT change

- `_validate_url` routing logic
- `_scrape_url` internals
- `scrape_and_extract` public interface
- `run_web_research` signature
- Per-URL extraction logic and LLM calls
- Hub detection, deepening, pagination
- Domain-restricted and direct-URL pipeline paths

## Testing

| Test | Type | File |
|---|---|---|
| `scrape_linked_document` returns cached content on second call | unit (mocked) | `test_pipeline_speed.py` |
| Cache is shared across parallel extractor agents | unit (mocked) | `test_pipeline_speed.py` |
| `max_concurrent` default is 6 | unit | `test_pipeline_speed.py` |
| Pipeline skips coverage eval and iteration 2 when iter1 scraped ≥ 4 sources | unit (mocked) | `test_pipeline_speed.py` |
| Pipeline still runs iteration 2 when iter1 scraped < 4 sources | unit (mocked) | `test_pipeline_speed.py` |
| Full suite: no regressions | integration | all existing tests |

## Expected improvement

| Query type | Before | After |
|---|---|---|
| HTML-heavy (5–15s/URL) | 250s | 35–55s |
| Mixed HTML + PDF | 250s | 80–110s |
| Domain-restricted (few URLs) | 177s | 60–90s |
