# Database List-Page Deepening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the pipeline correctly handle database/list pages (like WOCAT's filtered technology list) by detecting hub pages, following up to 15 relevant item links, and supporting one-hop pagination — in both `direct_url` and domain-restricted modes.

**Architecture:** The extractor sub-agent gains a new `page_type` field ("list" or "content") classified in the same LLM call as content extraction. When a hub page is detected, the pipeline collects up to 15 LLM-ranked item links (instead of 3 random same-domain links), optionally follows one "next page" link to gather more candidates, then scrapes the top N in parallel.

**Tech Stack:** Python 3.10+, Pydantic v2, openai-agents SDK, pytest + pytest-asyncio

---

## File Map

| File | Role | Change type |
|---|---|---|
| `src/web_scout/tools.py` | Extractor output model + instructions + serialization | Modify |
| `src/web_scout/agent.py` | Pipeline deepening logic + `_find_next_page_url` helper + depth presets | Modify |
| `tests/test_hub_detection.py` | Unit tests for `_find_next_page_url` and `_ExtractorOutput.page_type` | Create |

No other files change. Public API (`run_web_research` signature) is unchanged.

---

## Task 1: `_ExtractorOutput` — add `page_type` field and increase `relevant_links` cap

**Files:**
- Modify: `src/web_scout/tools.py` (lines 163–187: `_ExtractorOutput` class; lines 190–212: `_EXTRACTOR_INSTRUCTIONS`; lines 551–554: `final_output` builder)
- Create: `tests/test_hub_detection.py`

### Background

`_ExtractorOutput` is the Pydantic model returned by the content extractor sub-agent (defined in `tools.py`). It currently has three fields: `title`, `relevant_content`, and `relevant_links` (capped at 5). The `final_output` string builder in `scrape_and_extract` serializes these fields into a markdown string that `agent.py` parses line-by-line.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hub_detection.py`:

```python
"""Unit tests for hub-page detection helpers."""
import pytest
from web_scout.tools import _ExtractorOutput


def test_page_type_default_is_content():
    out = _ExtractorOutput(relevant_content="Some article text.")
    assert out.page_type == "content"


def test_page_type_list_accepted():
    out = _ExtractorOutput(relevant_content="List page.", page_type="list")
    assert out.page_type == "list"


def test_relevant_links_accepts_up_to_15():
    links = [f"https://example.com/{i}" for i in range(15)]
    out = _ExtractorOutput(relevant_content="x", relevant_links=links)
    assert len(out.relevant_links) == 15
```

- [ ] **Step 2: Run to confirm they fail**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_hub_detection.py::test_page_type_default_is_content tests/test_hub_detection.py::test_page_type_list_accepted -v
```

Expected: `FAILED` — `_ExtractorOutput` has no `page_type` attribute yet.

- [ ] **Step 3: Add `page_type` to `_ExtractorOutput` in `tools.py`**

At the top of `tools.py`, add `Literal` to the typing imports:
```python
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional
```

In `_ExtractorOutput` (after `relevant_content`, before `relevant_links`), add:
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

Update `relevant_links` field description — change "Up to 5" to "Up to 15" and add the list-page instruction:
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

- [ ] **Step 4: Run tests to confirm they pass**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_hub_detection.py -v
```

Expected: all 3 pass.

- [ ] **Step 5: Update `_EXTRACTOR_INSTRUCTIONS`**

In `_EXTRACTOR_INSTRUCTIONS` (around line 204), change:
```python
"   Ensure they are absolute URLs (starting with http/https).\n"
"5. Return a highly informative ``relevant_content`` of up to 5,000 characters.\n"
```
to:
```python
"   Ensure they are absolute URLs (starting with http/https).\n"
"5. Return a highly informative ``relevant_content`` of up to 5,000 characters.\n"
"\n"
"If the page is a list/database/search-results view (page_type = \"list\"), your primary job\n"
"is to identify and rank the item links, not to extract prose. Return up to 15 item URLs in\n"
"``relevant_links``, ordered by likely relevance to the research query.\n"
```

Also in step 4 of the instructions, change "include up to 5 of them" to "include up to 15 of them".

- [ ] **Step 6: Update the `final_output` builder**

In `create_scrape_and_extract_tool`, locate the `final_output` builder (currently around line 551). It currently reads:
```python
header = f"# {title}\nSource: {url}\n\n" if title else f"Source: {url}\n\n"
final_output = header + content
if links:
    final_output += "\n\n**Relevant Links found on page:**\n" + "\n".join(f"- {lnk}" for lnk in links[:5])

if tracker is not None:
    count_scraped = len(tracker.build_result_groups()["scraped"])
    if count_scraped < 2:
        final_output += (
            "\n\n⚠ REMINDER: You MUST successfully scrape AT LEAST 2 high-quality sources "
            "before synthesising and finishing. You currently have "
            f"{count_scraped} successful scrape(s)."
        )

return final_output
```

Replace the `header`/`final_output`/`if links:` portion **only** (preserve the `⚠ REMINDER` block and `return` unchanged):
```python
header = f"# {title}\nSource: {url}\n\n" if title else f"Source: {url}\n\n"
final_output = header + content
if output.page_type == "list":
    final_output += "\n**Page type: list**"
if links:
    final_output += "\n\n**Relevant Links found on page:**\n" + "\n".join(f"- {lnk}" for lnk in links[:15])

# ⚠ REMINDER block and return statement below are UNCHANGED — do not remove them
```

Note: the `**Page type: list**` marker appears **before** the links block.

- [ ] **Step 7: Run full test suite to confirm nothing is broken**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_hub_detection.py -v
```

Expected: all 3 pass.

- [ ] **Step 8: Commit**

```bash
git add src/web_scout/tools.py tests/test_hub_detection.py
git commit -m "feat: add page_type field to _ExtractorOutput and increase relevant_links cap to 15"
```

---

## Task 2: `_find_next_page_url` helper + unit tests

**Files:**
- Modify: `src/web_scout/agent.py` (add helper near `_judge_synthesis` at line 137)
- Modify: `tests/test_hub_detection.py` (add unit tests)

### Background

`_find_next_page_url` is a pure function that scans a markdown content string for a "next page" link (by link text), validates it's on the same domain as the base URL, and returns it. It is used by both direct-URL and domain-restricted deepening. The function has no side effects and does not make HTTP requests — it only parses strings.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hub_detection.py`:

```python
from web_scout.agent import _find_next_page_url


def test_find_next_page_url_next_token():
    content = "Some content [Next](https://wocat.net/page/2) more content"
    assert _find_next_page_url(content, "https://wocat.net/page/1") == "https://wocat.net/page/2"


def test_find_next_page_url_right_arrow():
    content = "[›](https://wocat.net/list/?page=2)"
    assert _find_next_page_url(content, "https://wocat.net/list/") == "https://wocat.net/list/?page=2"


def test_find_next_page_url_double_right_arrow():
    content = "[»](https://wocat.net/list/?page=2)"
    assert _find_next_page_url(content, "https://wocat.net/list/") == "https://wocat.net/list/?page=2"


def test_find_next_page_url_case_insensitive():
    content = "[NEXT PAGE](https://wocat.net/page/2)"
    assert _find_next_page_url(content, "https://wocat.net/page/1") == "https://wocat.net/page/2"


def test_find_next_page_url_cross_domain_rejected():
    content = "[Next](https://other.org/page/2)"
    assert _find_next_page_url(content, "https://wocat.net/page/1") is None


def test_find_next_page_url_bare_digit_not_matched():
    content = "[2](https://wocat.net/page/2)"
    assert _find_next_page_url(content, "https://wocat.net/page/1") is None


def test_find_next_page_url_no_pagination_link():
    assert _find_next_page_url("Some content with no next link.", "https://wocat.net/page/1") is None


def test_find_next_page_url_empty_content():
    assert _find_next_page_url("", "https://wocat.net/page/1") is None
```

- [ ] **Step 2: Run to confirm they fail**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_hub_detection.py -k "find_next_page" -v
```

Expected: `FAILED` — `cannot import name '_find_next_page_url' from 'web_scout.agent'`.

- [ ] **Step 3: Implement `_find_next_page_url` in `agent.py`**

Add the following imports at the top of `agent.py` if not already present (they are — `re` is imported as `_re`, `urlparse` is imported):
```python
# _re and urlparse already imported at top of agent.py
```

Add the function directly after `_judge_synthesis` (around line 170):

```python
def _find_next_page_url(content: str, base_url: str) -> Optional[str]:
    """Scan markdown content for a 'next page' link on the same domain as base_url.

    Matches link text (case-insensitive) against: 'next', 'next page', '›', '»'.
    Bare digits are intentionally excluded (too fragile).
    Returns the first matching same-domain URL, or None.
    """
    _NEXT_TOKENS = {"next", "next page", "›", "»"}
    base_netloc = urlparse(base_url).netloc.lower()

    for match in _re.finditer(r'\[([^\]]*)\]\((https?://[^\s\)]+)\)', content):
        link_text = match.group(1).strip().lower()
        href = match.group(2)
        if link_text in _NEXT_TOKENS:
            href_netloc = urlparse(href).netloc.lower()
            if href_netloc == base_netloc:
                return href
    return None
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_hub_detection.py -k "find_next_page" -v
```

Expected: all 8 pass.

- [ ] **Step 5: Run full test suite**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_hub_detection.py -v
```

Expected: all 11 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/web_scout/agent.py tests/test_hub_detection.py
git commit -m "feat: add _find_next_page_url helper with unit tests"
```

---

## Task 3: `_DEPTH_PRESETS` update + direct-URL hub deepening

**Files:**
- Modify: `src/web_scout/agent.py` (lines 113–128: `_DEPTH_PRESETS`; lines 246–279: direct-URL deepening block)

### Background

`_DEPTH_PRESETS` is a module-level dict in `agent.py` with `"standard"` and `"deep"` keys. The `depth` variable is resolved from this dict at the start of `run_web_research` and passed implicitly via closure to the rest of the function.

The direct-URL deepening block currently starts at line 256 (`if direct_url:`) and follows up to 3 same-domain links found in the `**Relevant Links found on page:**` block. This task replaces that block with hub-aware logic.

- [ ] **Step 1: Add `hub_deepening_cap` to `_DEPTH_PRESETS`**

Locate `_DEPTH_PRESETS` at line 113. Add `"hub_deepening_cap"` to both presets:

```python
_DEPTH_PRESETS = {
    "standard": {
        "max_iterations": 2,
        "queries_first": 3,
        "queries_followup": 2,
        "urls_first": 6,
        "urls_followup": 4,
        "hub_deepening_cap": 10,
    },
    "deep": {
        "max_iterations": 3,
        "queries_first": 5,
        "queries_followup": 4,
        "urls_first": 12,
        "urls_followup": 8,
        "hub_deepening_cap": 15,
    },
}
```

- [ ] **Step 2: Replace the direct-URL deepening block**

Locate the direct-URL deepening block (lines ~256–279). It currently reads:

```python
        # Deepen if relevant links are found
        links_to_deepen = []
        if "**Relevant Links found on page:**" in content:
            for line in content.split("\n"):
                if line.startswith("- http") or (line.startswith("- [") and "http" in line):
                    l = line.split("](", 1)[-1].split(")", 1)[0] if "](" in line else line.replace("- ", "").strip()
                    links_to_deepen.append(l)

        if links_to_deepen:
            direct_domain = urlparse(direct_url).netloc.lower()
            if direct_domain.startswith("www."):
                direct_domain = direct_domain[4:]

            same_domain_links = []
            if direct_domain:
                for l in links_to_deepen:
                    link_domain = urlparse(l).netloc.lower()
                    if link_domain == direct_domain or link_domain.endswith("." + direct_domain):
                        same_domain_links.append(l)

            same_domain_links = same_domain_links[:3]
            if same_domain_links:
                logger.info("[pipeline] deepening on %d links from direct URL", len(same_domain_links))
                tasks = [scrape_tool(link) for link in same_domain_links]
                await asyncio.gather(*tasks)
```

Replace entirely with:

```python
        # Deepen if relevant links are found
        is_hub = "**Page type: list**" in content

        if is_hub:
            # Hub page: collect LLM-ranked item links, one-hop pagination
            candidates = []
            if "**Relevant Links found on page:**" in content:
                for line in content.split("\n"):
                    if line.startswith("- http") or (line.startswith("- [") and "http" in line):
                        l = line.split("](", 1)[-1].split(")", 1)[0] if "](" in line else line.replace("- ", "").strip()
                        if l:
                            candidates.append(l)

            next_page = _find_next_page_url(content, direct_url)
            if next_page:
                logger.info("[pipeline] hub pagination: scraping next page %s", next_page)
                next_content = await scrape_tool(next_page)
                for line in next_content.split("\n"):
                    if line.startswith("- http") or (line.startswith("- [") and "http" in line):
                        l = line.split("](", 1)[-1].split(")", 1)[0] if "](" in line else line.replace("- ", "").strip()
                        if l and l not in candidates:
                            candidates.append(l)

            hub_cap = depth["hub_deepening_cap"]
            if candidates:
                logger.info("[pipeline] hub deepening on %d candidate links (cap=%d)", len(candidates), hub_cap)
                tasks = [scrape_tool(link) for link in candidates[:hub_cap]]
                await asyncio.gather(*tasks)

        else:
            # Non-hub: existing behaviour — follow up to 3 same-domain links
            links_to_deepen = []
            if "**Relevant Links found on page:**" in content:
                for line in content.split("\n"):
                    if line.startswith("- http") or (line.startswith("- [") and "http" in line):
                        l = line.split("](", 1)[-1].split(")", 1)[0] if "](" in line else line.replace("- ", "").strip()
                        links_to_deepen.append(l)

            if links_to_deepen:
                direct_domain = urlparse(direct_url).netloc.lower()
                if direct_domain.startswith("www."):
                    direct_domain = direct_domain[4:]

                same_domain_links = []
                if direct_domain:
                    for l in links_to_deepen:
                        link_domain = urlparse(l).netloc.lower()
                        if link_domain == direct_domain or link_domain.endswith("." + direct_domain):
                            same_domain_links.append(l)

                same_domain_links = same_domain_links[:3]
                if same_domain_links:
                    logger.info("[pipeline] deepening on %d links from direct URL", len(same_domain_links))
                    tasks = [scrape_tool(link) for link in same_domain_links]
                    await asyncio.gather(*tasks)
```

- [ ] **Step 3: Run existing tests to confirm nothing is broken**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_hub_detection.py -v
```

Expected: all 11 pass.

- [ ] **Step 4: Commit**

```bash
git add src/web_scout/agent.py
git commit -m "feat: hub-aware deepening for direct_url mode with one-hop pagination"
```

---

## Task 4: Domain-restricted hub deepening

**Files:**
- Modify: `src/web_scout/agent.py` (lines ~388–421: domain-restricted deepening block inside `if include_domains:`)

### Background

The domain-restricted deepening block fires after each search iteration at line ~398 (`if include_domains:`). It currently only deepens when `count_scraped < 2`. This task replaces that with hub-aware logic that fires whenever a hub page is detected in the current iteration's results, regardless of `count_scraped`.

The key change is building `iter_results` by zipping `urls_to_scrape` with `extracted_contents` — both are already parallel lists computed in the same iteration.

- [ ] **Step 1: Build `iter_results` after each parallel scrape**

There are **four** places in the search-mode loop where `extracted_contents` is set. Add `iter_results` immediately after each one:

**a) Evaluator-backlog path — has URLs (around line 325):**

```python
tasks = [scrape_tool(url) for url in urls_to_scrape]
extracted_contents = await asyncio.gather(*tasks)
iter_results = list(zip(urls_to_scrape, extracted_contents))  # ADD THIS
```

**b) Evaluator-backlog path — no promising URLs (around line 327):**

```python
logger.info("[pipeline] no promising backlog URLs to scrape")
extracted_contents = []
iter_results = []  # ADD THIS
```

**c) Normal scrape path — has URLs (around line 393):**

```python
tasks = [scrape_tool(url) for url in urls_to_scrape]
extracted_contents = await asyncio.gather(*tasks)
iter_results = list(zip(urls_to_scrape, extracted_contents))  # ADD THIS
```

**d) Normal scrape path — no new URLs (around line 396):**

```python
logger.info("[pipeline] no new URLs to scrape")
extracted_contents = []
iter_results = []  # ADD THIS (prevents NameError when include_domains is set)
```

Without `iter_results = []` in the empty-list branches (b and d), the `if include_domains:` deepening block will raise `NameError: name 'iter_results' is not defined` when `urls_to_scrape` is empty.

- [ ] **Step 2: Replace the domain-restricted deepening block**

Locate the `if include_domains:` deepening block (around line 398). It currently reads:

```python
            # 2f. Deepen (Domain Restricted Mode)
            if include_domains:
                count_scraped = len(tracker.build_result_groups()["scraped"])
                if count_scraped < 2 and extracted_contents:
                    links_to_deepen = []
                    for content in extracted_contents:
                        if "**Relevant Links found on page:**" in content:
                            for line in content.split("\n"):
                                if line.startswith("- http") or (line.startswith("- [") and "http" in line):
                                    l = line.split("](", 1)[-1].split(")", 1)[0] if "](" in line else line.replace("- ", "").strip()
                                    parsed_netloc = urlparse(l).netloc.lower()
                                    if any(parsed_netloc == d.lower() or parsed_netloc.endswith("." + d.lower()) for d in include_domains):
                                        if tracker._normalize_url(l) not in tracker._actions:
                                            if l not in links_to_deepen:
                                                links_to_deepen.append(l)

                    if links_to_deepen:
                        deep_links = links_to_deepen[:3]
                        logger.info("[pipeline] domain restricted deepening on %d links: %s", len(deep_links), deep_links)
                        deep_tasks = [scrape_tool(link) for link in deep_links]
                        await asyncio.gather(*deep_tasks)
```

Replace entirely with:

```python
            # 2f. Deepen (Domain Restricted Mode)
            if include_domains:
                count_scraped = len(tracker.build_result_groups()["scraped"])

                # Detect hub pages in this iteration's results
                hub_results = [
                    (url, c) for url, c in iter_results
                    if "**Page type: list**" in c
                ]

                if hub_results:
                    # Hub deepening: collect LLM-ranked item links from all hub pages
                    candidates = []
                    for hub_url, hub_content in hub_results:
                        if "**Relevant Links found on page:**" in hub_content:
                            for line in hub_content.split("\n"):
                                if line.startswith("- http") or (line.startswith("- [") and "http" in line):
                                    l = line.split("](", 1)[-1].split(")", 1)[0] if "](" in line else line.replace("- ", "").strip()
                                    if l:
                                        parsed_netloc = urlparse(l).netloc.lower()
                                        if any(parsed_netloc == d.lower() or parsed_netloc.endswith("." + d.lower()) for d in include_domains):
                                            if tracker._normalize_url(l) not in tracker._actions:
                                                if l not in candidates:
                                                    candidates.append(l)

                        # One-hop pagination per hub
                        next_page = _find_next_page_url(hub_content, hub_url)
                        if next_page and tracker._normalize_url(next_page) not in tracker._actions:
                            logger.info("[pipeline] hub pagination (domain mode): %s", next_page)
                            next_content = await scrape_tool(next_page)
                            for line in next_content.split("\n"):
                                if line.startswith("- http") or (line.startswith("- [") and "http" in line):
                                    l = line.split("](", 1)[-1].split(")", 1)[0] if "](" in line else line.replace("- ", "").strip()
                                    if l:
                                        parsed_netloc = urlparse(l).netloc.lower()
                                        if any(parsed_netloc == d.lower() or parsed_netloc.endswith("." + d.lower()) for d in include_domains):
                                            if tracker._normalize_url(l) not in tracker._actions:
                                                if l not in candidates:
                                                    candidates.append(l)

                    hub_cap = depth["hub_deepening_cap"]
                    if candidates:
                        logger.info("[pipeline] hub deepening (domain mode) on %d candidates (cap=%d)", len(candidates), hub_cap)
                        deep_tasks = [scrape_tool(link) for link in candidates[:hub_cap]]
                        await asyncio.gather(*deep_tasks)

                elif count_scraped < 2 and extracted_contents:
                    # Existing fallback: no hub detected, thin coverage — follow up to 3 links
                    links_to_deepen = []
                    for content in extracted_contents:
                        if "**Relevant Links found on page:**" in content:
                            for line in content.split("\n"):
                                if line.startswith("- http") or (line.startswith("- [") and "http" in line):
                                    l = line.split("](", 1)[-1].split(")", 1)[0] if "](" in line else line.replace("- ", "").strip()
                                    parsed_netloc = urlparse(l).netloc.lower()
                                    if any(parsed_netloc == d.lower() or parsed_netloc.endswith("." + d.lower()) for d in include_domains):
                                        if tracker._normalize_url(l) not in tracker._actions:
                                            if l not in links_to_deepen:
                                                links_to_deepen.append(l)

                    if links_to_deepen:
                        deep_links = links_to_deepen[:3]
                        logger.info("[pipeline] domain restricted deepening on %d links: %s", len(deep_links), deep_links)
                        deep_tasks = [scrape_tool(link) for link in deep_links]
                        await asyncio.gather(*deep_tasks)
```

- [ ] **Step 3: Run unit tests**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_hub_detection.py -v
```

Expected: all 11 pass.

- [ ] **Step 4: Commit**

```bash
git add src/web_scout/agent.py
git commit -m "feat: hub-aware deepening for domain-restricted mode with one-hop pagination"
```

---

## Task 5: Smoke test

**Files:**
- Read: `src/web_scout/agent.py` (verify `iter_results` is defined in ALL branches where `extracted_contents` is set)

### Background

`iter_results` must be defined before the `if include_domains:` deepening block in every execution path. There are two paths that produce `extracted_contents`:
1. Normal search path (lines ~390–394)
2. Evaluator-backlog path (lines ~323–325)

Both must zip to `iter_results`. This step verifies that.

- [ ] **Step 1: Audit all `extracted_contents` assignments**

Read `src/web_scout/agent.py` and confirm that every location where `extracted_contents = await asyncio.gather(...)` is followed immediately by `iter_results = list(zip(urls_to_scrape, extracted_contents))`.

Check: if `urls_to_scrape` is empty in the backlog path (no promising URLs), `extracted_contents = []` is set on line ~327. In this case `iter_results = []` (zip of empty lists). Confirm this assignment is also present after the empty-list fallback.

- [ ] **Step 2: Run all tests one final time**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_hub_detection.py -v
```

Expected: all 11 pass.

- [ ] **Step 3: Final commit**

```bash
git add src/web_scout/agent.py
git commit -m "fix: ensure iter_results is defined in all extracted_contents branches"
```

---

## Verification

Run the WOCAT direct-URL scenario manually:

```python
# In a Python script or notebook, with .env loaded:
import asyncio
from dotenv import load_dotenv
from web_scout.agent import run_web_research, DEFAULT_WEB_RESEARCH_MODELS

load_dotenv()

async def test():
    result = await run_web_research(
        query="sustainable land management technologies in Kenya",
        models=DEFAULT_WEB_RESEARCH_MODELS,
        direct_url="https://wocat.net/en/database/list/?type=technology&country=ke",
        search_backend="serper",
    )
    print(f"Scraped: {len(result.scraped)} pages")
    for s in result.scraped:
        print(f"  - {s.title or s.url}")
    print("\nSynthesis preview:")
    print(result.synthesis[:500])

asyncio.run(test())
```

Success criteria:
- `result.scraped` contains more than 3 entries (old cap was 3)
- Scraped entries are individual technology detail pages (not the list page itself)
- Synthesis mentions multiple distinct SLM technologies found in Kenya
