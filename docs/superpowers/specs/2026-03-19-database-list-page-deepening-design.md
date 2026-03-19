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

## Non-Goals

- Full exhaustive pagination (all pages of a database). One hop is sufficient and keeps the
  pipeline deterministic.
- Automatic construction of filtered database URLs from a domain + query. Users who know the
  exact filtered URL use `direct_url`; others rely on domain-restricted search.
- Changes to the public `run_web_research` signature.
- Direct vision-model classification of page type (vision results feed into the extractor as
  text; the extractor LLM decides `page_type` from that text — no structural change needed).

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
| `src/web_scout/agent.py` | Hub-aware deepening in both `direct_url` and domain-restricted modes; `_find_next_page_url` helper; `_DEPTH_PRESETS` hub cap keys |

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
zero extra cost or latency. The classification is always based on the text returned by
`raw_scrape()`. When the vision fallback fires (thin text → `_scrape_via_vision` →
plain-text extraction), that plain text is what `raw_scrape()` returns to the extractor agent;
the extractor LLM then decides `page_type` from that text. This is the existing two-layer
architecture: `scraping.py` always returns `(content: str, title: str, error: Optional[str])`;
structured classification happens only in the extractor agent layer (`tools.py`). No change to
the vision path is required.

#### 1b. Increase `relevant_links` cap 5 → 15

Update the `_ExtractorOutput.relevant_links` field description from "Up to 5" to "Up to 15":

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

#### 1c. Extractor instructions update

In `_EXTRACTOR_INSTRUCTIONS`, step 4 currently reads "include up to 5 of them in
`relevant_links`". Change to "include up to 15 of them in `relevant_links`".

Additionally append one paragraph after the existing instructions:

> If the page is a list/database/search-results view (page_type = "list"), your primary job
> is to identify and rank the item links, not to extract prose. Return up to 15 item URLs in
> `relevant_links`, ordered by likely relevance to the research query.

#### 1d. Serialization — `page_type` marker and links slice

In `create_scrape_and_extract_tool`, the `final_output` string builder (currently at the end
of `scrape_and_extract`) gains two changes:

1. **Links slice**: `links[:5]` → `links[:15]`
2. **Page-type marker**: append a line `**Page type: list**` when `output.page_type == "list"`:

```python
if output.page_type == "list":
    final_output += "\n**Page type: list**"
if links:
    final_output += "\n\n**Relevant Links found on page:**\n" + "\n".join(
        f"- {lnk}" for lnk in links[:15]
    )
```

The marker must appear **before** the links block so `agent.py` can detect hub status without
scanning the full link list.

**Parsing in `agent.py`** uses a simple substring check (same pattern as the existing links
parser):

```python
is_hub = "**Page type: list**" in content
```

This is consistent with how the current code checks `"**Relevant Links found on page:**" in content`.

---

### Section 2: `_DEPTH_PRESETS` update (`agent.py`)

Add a `hub_deepening_cap` key to both presets:

```python
_DEPTH_PRESETS = {
    "standard": {
        "max_iterations": 2,
        "queries_first": 3,
        "queries_followup": 2,
        "urls_first": 6,
        "urls_followup": 4,
        "hub_deepening_cap": 10,   # new
    },
    "deep": {
        "max_iterations": 3,
        "queries_first": 5,
        "queries_followup": 4,
        "urls_first": 12,
        "urls_followup": 8,
        "hub_deepening_cap": 15,   # new
    },
}
```

All deepening code references `depth["hub_deepening_cap"]` — no magic numbers.

---

### Section 3: `_find_next_page_url` helper (`agent.py`)

A small pure function defined once near the top of `agent.py` (alongside the existing
`_judge_synthesis`), used by both pipeline modes:

```python
def _find_next_page_url(content: str, base_url: str) -> Optional[str]:
```

**Matching logic** — scan all markdown links in `content` using the existing regex pattern
`r'\[([^\]]*)\]\((https?://[^\s\)]+)\)'`. Match the link text (stripped, case-insensitive)
against this fixed set of tokens only:

- `"next"`, `"next page"`, `"›"`, `"»"`

Bare digit matching (e.g. `"2"`) is intentionally **excluded** — too fragile, would match
footnote links, table references, etc.

For each matched link:

- Parse the href with `urlparse`
- Verify `netloc` matches that of `base_url` (same domain)
- Return the first valid match, or `None`

This function is pure (no I/O) and is easily unit-tested independently.

---

### Section 4: Direct URL deepening (`agent.py`)

**Current logic (simplified):**
```
scrape direct_url
→ if "Relevant Links found on page" in output:
    collect same-domain links → follow up to 3
```

**New logic:**
```
content = await scrape_tool(direct_url)

is_hub = "**Page type: list**" in content

if is_hub:
    # Collect candidate links from the hub page
    candidates = []
    for line in content.split("\n"):
        if line.startswith("- http") or ("- [" in line and "http" in line):
            url = _extract_url_from_line(line)   # existing line-parsing logic
            if url:
                candidates.append(url)

    # One-hop pagination — scrape next page via scrape_tool (records in tracker)
    next_page = _find_next_page_url(content, direct_url)
    if next_page:
        next_content = await scrape_tool(next_page)
        for line in next_content.split("\n"):
            if line.startswith("- http") or ("- [" in line and "http" in line):
                url = _extract_url_from_line(line)
                if url and url not in candidates:
                    candidates.append(url)

    # Scrape top N in parallel (tracker dedup prevents double-scraping)
    hub_cap = depth["hub_deepening_cap"]
    tasks = [scrape_tool(u) for u in candidates[:hub_cap]]
    await asyncio.gather(*tasks)

else:
    # Existing behaviour unchanged — same-domain links, up to 3
    ...
```

`_extract_url_from_line` is the existing inline parsing logic already present in the direct-URL
deepening block (lines 259-262 of current `agent.py`) — no change needed, just named for
clarity.

Because next-page scraping goes through `scrape_tool`, the tracker (`record_scrape`,
`record_scrape_failure`, `record_bot_detection`) is updated automatically, ensuring the
next-page URL is deduplicated in later iterations.

---

### Section 5: Domain-restricted deepening (`agent.py`)

**Tracking `(url, content)` pairs per iteration**

In the current code, `urls_to_scrape` and `extracted_contents` are parallel lists produced in
the same iteration:

```python
tasks = [scrape_tool(url) for url in urls_to_scrape]
extracted_contents = await asyncio.gather(*tasks)
```

Zip them to form per-iteration pairs:

```python
iter_results = list(zip(urls_to_scrape, extracted_contents))
# iter_results: List[Tuple[str, str]]  — (url, content_string)
```

**Current deepening logic (simplified):**
```
if count_scraped < 2:
    collect relevant_links → follow up to 3 same-domain links
```

**New logic:**
```python
hub_results = [(url, c) for url, c in iter_results if "**Page type: list**" in c]

if hub_results:
    candidates = []

    for hub_url, hub_content in hub_results:
        for line in hub_content.split("\n"):
            if line.startswith("- http") or ("- [" in line and "http" in line):
                url = _extract_url_from_line(line)
                parsed_netloc = urlparse(url).netloc.lower() if url else ""
                if url and any(
                    parsed_netloc == d.lower() or parsed_netloc.endswith("." + d.lower())
                    for d in include_domains
                ):
                    if tracker._normalize_url(url) not in tracker._actions:
                        if url not in candidates:
                            candidates.append(url)

        # One-hop pagination per hub (via scrape_tool → recorded in tracker)
        next_page = _find_next_page_url(hub_content, hub_url)
        if next_page and tracker._normalize_url(next_page) not in tracker._actions:
            next_content = await scrape_tool(next_page)
            for line in next_content.split("\n"):
                if line.startswith("- http") or ("- [" in line and "http" in line):
                    url = _extract_url_from_line(line)
                    parsed_netloc = urlparse(url).netloc.lower() if url else ""
                    if url and any(
                        parsed_netloc == d.lower() or parsed_netloc.endswith("." + d.lower())
                        for d in include_domains
                    ) and tracker._normalize_url(url) not in tracker._actions:
                        if url not in candidates:
                            candidates.append(url)

    hub_cap = depth["hub_deepening_cap"]
    hub_tasks = [scrape_tool(u) for u in candidates[:hub_cap]]
    await asyncio.gather(*hub_tasks)

elif count_scraped < 2:
    # Existing fallback behaviour unchanged
    ...
```

Key difference from before: **deepening fires on hub detection regardless of `count_scraped`**.
A successfully-scraped list page (count_scraped ≥ 2) previously caused deepening to be skipped;
it now triggers a full hub deepening pass.

---

## Data flow summary

```
User: direct_url=wocat_list OR include_domains=["wocat.net"] + query

         scrape page
              │
   "**Page type: list**" in output?
        /            \
      yes             no
       │               │
  collect candidate  existing logic
  links (up to 15   (follow up to 3
  from relevant_    same-domain links)
  links block)
       │
  one-hop pagination?
  (_find_next_page_url → scrape via scrape_tool
   → add its links to candidates)
       │
  scrape top hub_deepening_cap detail pages
  in parallel (N=10 std / 15 deep)
       │
  synthesise across all scraped content
```

---

## Error handling

- If next-page scrape fails, it is silently skipped — candidates from the first page are used.
  The failure is recorded in the tracker via the normal `scrape_tool` path.
- If the `**Page type: list**` marker is absent from extractor output (e.g. older model
  behaviour, or extractor sub-agent crashes before writing the marker), `is_hub` evaluates
  to `False` and the pipeline falls back to the existing `count_scraped < 2` deepening logic
  unchanged.
- All link-following respects the existing `ResearchTracker` deduplication via
  `tracker._actions` — a URL already scraped or attempted is never retried.
- Depth caps come from `_DEPTH_PRESETS["hub_deepening_cap"]`; the semaphore
  (`max_concurrent=3`) controls parallelism as before.
- Domain filtering in Section 5 uses the same `netloc` comparison already present in the
  existing deepening block — no new validation logic is introduced.

---

## Testing notes

### Integration tests

**Direct URL (hub):**
```python
result = await run_web_research(
    query="sustainable land management technologies in Kenya",
    models=DEFAULT_WEB_RESEARCH_MODELS,
    direct_url="https://wocat.net/en/database/list/?type=technology&country=ke",
)
# Expect: multiple individual technology detail pages in result.scraped
# Expect: synthesis covers several distinct SLM technologies
# Expect: result.scraped count > 3 (more than the old cap of 3)
```

**Domain-restricted (hub surfaced by search):**
```python
result = await run_web_research(
    query="sustainable land management technologies in Kenya",
    models=DEFAULT_WEB_RESEARCH_MODELS,
    include_domains=["wocat.net"],
)
# Expect: if search surfaces the list page, hub deepening fires
# Expect: individual technology pages in result.scraped, not just the list page
```

### Unit tests for `_find_next_page_url`

```python
# Positive cases
assert _find_next_page_url(
    "... [Next](https://wocat.net/page/2) ...", "https://wocat.net/page/1"
) == "https://wocat.net/page/2"

assert _find_next_page_url(
    "... [›](https://wocat.net/list/?page=2) ...", "https://wocat.net/list/"
) == "https://wocat.net/list/?page=2"

# Negative: cross-domain link should not match
assert _find_next_page_url(
    "... [Next](https://other.org/page/2) ...", "https://wocat.net/page/1"
) is None

# Negative: bare digit should not match (fragile heuristic excluded)
assert _find_next_page_url(
    "... [2](https://wocat.net/page/2) ...", "https://wocat.net/page/1"
) is None

# Negative: no pagination link in content
assert _find_next_page_url(
    "Some content with no next link.", "https://wocat.net/page/1"
) is None
```
