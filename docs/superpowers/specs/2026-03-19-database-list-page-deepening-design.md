# Design: Database / List-Page Deepening

**Date:** 2026-03-19
**Status:** Approved

## Problem

When a user restricts research to a domain like `wocat.net` or passes a direct URL such as
`https://wocat.net/en/database/list/?type=technology&country=ke`, the pipeline fails to extract
useful results because:

1. **Direct URL mode** — the list page is JS-rendered and paginated. The pipeline scrapes the
   first render but only follows up to 3 same-domain links, missing most item detail pages.
2. **Domain-restricted search mode** — search engines surface individual technology pages or,
   occasionally, the list/index page itself. When a list page is found, the existing deepening
   logic (capped at 3 links, only triggers when `count_scraped < 2`) is too conservative.
3. **No pagination support** — neither mode follows "next page" links, so items beyond the
   first rendered page are never discovered.
4. **No list-page awareness** — the pipeline treats all pages identically; there is no concept
   of a "hub" page whose primary value is the item links it contains, not its prose.

## Goals

- Handle database/list/index pages correctly in both `direct_url` and `domain+query` modes.
- Scrape the **top N most query-relevant** item detail pages from a list, not all of them.
- Support **one hop of pagination** to discover items beyond the first rendered page.
- Require **no new public API parameters** — existing callers are unaffected.
- Use the **existing vision fallback** infrastructure for pages where text extraction is thin.

## Non-Goals

- Full exhaustive pagination (all pages of a database). One hop is sufficient and keeps the
  pipeline deterministic.
- Automatic construction of filtered database URLs from a domain + query. Users who know the
  exact filtered URL use `direct_url`; others rely on domain-restricted search.
- Changes to the public `run_web_research` signature.

## Approach

Approach 2 of three considered: *List-page detection + tiered deepening*. Chosen because it
handles the target use cases without over-engineering (Approach 3: full database exploration
mode) or being too passive (Approach 1: just raising caps).

---

## Design

### Files changed

| File | Nature of change |
|---|---|
| `src/web_scout/tools.py` | Extractor output: new `page_type` field; `relevant_links` cap 5→15; updated instructions |
| `src/web_scout/agent.py` | Hub-aware deepening in both `direct_url` and domain-restricted modes; `_find_next_page_url` helper |

No new files. No changes to `scraping.py`, `models.py`, `search_backends.py`, or `utils.py`.

---

### Section 1: Extractor changes (`tools.py`)

#### 1a. New `page_type` field on `_ExtractorOutput`

```python
page_type: Literal["list", "content"] = Field(
    default="content",
    description=(
        'Set to "list" if this page is a database view, search results page, '
        'index, or any page whose primary purpose is listing many items with links '
        'to detail pages. Set to "content" for articles, reports, and detail pages.'
    ),
)
```

The extractor LLM classifies the page in the **same inference call** as content extraction —
zero extra cost or latency.

**Vision path:** When the page returns thin text (the existing vision fallback case) and a
`vision_fallback` model is configured, the screenshot is passed to the same structured output
schema, so `page_type` is populated from the visual layout instead of text. No new vision calls
are added; this piggybacks on the existing fallback.

#### 1b. Increase `relevant_links` cap 5 → 15

```python
relevant_links: List[str] = Field(
    default_factory=list,
    description=(
        "Up to 15 absolute URLs found in the page that are highly likely to contain "
        "additional specific information for the research query. "
        "If page_type is 'list', treat each visible item's detail-page link as a candidate "
        "and rank by relevance to the query. Return up to 15."
    ),
)
```

#### 1c. Extractor instructions addition

One paragraph appended to `_EXTRACTOR_INSTRUCTIONS`:

> If the page is a list/database/search-results view (page_type = "list"), your primary job
> is to identify and rank the item links, not to extract prose. Return up to 15 item URLs in
> `relevant_links`, ordered by likely relevance to the research query.

#### 1d. Serialization

The `final_output` builder in `create_scrape_and_extract_tool` gains:
- `links[:5]` → `links[:15]`
- A `**Page type: list**` marker line when `page_type == "list"`, so `agent.py` can parse it
  without importing extractor internals.

---

### Section 2: `_find_next_page_url` helper (`agent.py`)

A small pure function defined once, used by both pipeline modes:

```python
def _find_next_page_url(content: str, base_url: str) -> Optional[str]:
```

- Scans markdown links in `content` for text matching: `"Next"`, `"Next page"`, `"›"`, `"»"`,
  or bare sequential page numbers (`"2"` when page 1 is implied).
- Validates the result URL shares the same domain as `base_url`.
- Returns at most one URL. One hop only — keeps the pipeline deterministic.

---

### Section 3: Direct URL deepening (`agent.py`)

**Current logic (simplified):**
```
scrape direct_url
→ if "Relevant Links found on page" in output:
    collect same-domain links → follow up to 3
```

**New logic:**
```
scrape direct_url → read page_type from output marker

if page_type == "list":
    candidates = relevant_links from output (up to 15)

    next_page = _find_next_page_url(content, direct_url)
    if next_page and not already scraped:
        scrape next_page → add its relevant_links to candidates (deduplicated)

    depth_cap = 10 (standard) or 15 (deep)
    scrape top min(len(candidates), depth_cap) in parallel

else:  # "content" — existing behaviour unchanged
    collect same-domain links → follow up to 3
```

Depth caps by preset:

| Preset | Hub deepening cap | Non-hub cap (unchanged) |
|---|---|---|
| standard | 10 | 3 |
| deep | 15 | 3 |

---

### Section 4: Domain-restricted deepening (`agent.py`)

**Current logic (simplified):**
```
after each iteration:
  if count_scraped < 2:
    collect relevant_links → follow up to 3 same-domain links
```

**New logic:**
```
after each iteration:
  hub_pages = [entry for scraped_this_iteration if page_type == "list"]

  if hub_pages:
    candidates = relevant_links from all hub pages (deduplicated, same-domain)

    for each hub:
      next_page = _find_next_page_url(hub.content, hub.url)
      if next_page and not already scraped:
        scrape next_page → add its relevant_links to candidates

    depth_cap = 10 (standard) or 15 (deep)
    scrape top min(len(candidates), depth_cap) not yet scraped

  elif count_scraped < 2:
    # existing fallback behaviour unchanged
    collect relevant_links → follow up to 3
```

Key difference: **deepening now fires on hub detection regardless of `count_scraped`**. A
successfully-scraped list page (count_scraped ≥ 2) previously caused deepening to be skipped;
it now triggers a full hub deepening pass.

---

## Data flow summary

```
User: direct_url=wocat_list OR include_domains=["wocat.net"] + query

         scrape page
              │
     page_type == "list"?
        /            \
      yes             no
       │               │
  collect up to    existing logic
  15 relevant      (follow up to 3
  links (LLM       same-domain links)
  ranked)
       │
  one-hop pagination?
  (find + scrape next page,
   add its links to candidates)
       │
  scrape top N detail pages
  in parallel (N=10 std / 15 deep)
       │
  synthesise across all scraped content
```

---

## Error handling

- If next-page scrape fails, it is silently skipped — candidates from the first page are used.
- If `page_type` marker is absent from extractor output (e.g. old-format output), the pipeline
  falls back to the existing `count_scraped < 2` deepening logic unchanged.
- All link-following respects the existing `ResearchTracker` deduplication — a URL already
  scraped or failed is never retried.
- Depth caps are hard limits; the semaphore (`max_concurrent=3`) controls parallelism as before.

---

## Testing notes

Manual test case:
```python
result = await run_web_research(
    query="sustainable land management technologies in Kenya",
    models=DEFAULT_WEB_RESEARCH_MODELS,
    direct_url="https://wocat.net/en/database/list/?type=technology&country=ke",
)
# Expect: multiple individual technology detail pages in result.scraped
# Expect: synthesis covers several distinct SLM technologies
```

Domain-restricted test case:
```python
result = await run_web_research(
    query="sustainable land management technologies in Kenya",
    models=DEFAULT_WEB_RESEARCH_MODELS,
    include_domains=["wocat.net"],
)
# Expect: if search surfaces the list page, hub deepening fires
# Expect: individual technology pages scraped, not just the list page
```
