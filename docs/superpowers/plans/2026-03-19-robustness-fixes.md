# Robustness Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four real robustness gaps found in code review: pagination silently failing on relative/www URLs, fragile `include_domains` input handling, UTM tracking params defeating URL deduplication, and hardcoded blocked domains that can't be overridden.

**Architecture:** Each fix is isolated to its natural layer — `agent.py` for pipeline logic, `tools.py` for URL normalization, `scraping.py` for domain blocking — with a new `tests/test_url_utils.py` for cross-cutting URL helper tests. No new public API parameters except `allowed_domains` on `run_web_research`, `create_scrape_and_extract_tool`, and `scrape_url`.

**Tech Stack:** Python 3.10+, pytest + pytest-asyncio, urllib.parse (stdlib only, no new deps)

---

## File Map

| File | What changes |
|---|---|
| `src/web_scout/agent.py` | Fix `_find_next_page_url` (relative links + www. norm); add `_normalize_domain` helper; apply at API entry; add `allowed_domains` param |
| `src/web_scout/tools.py` | Strip tracking params in `_normalize_url`; add `allowed_domains` to `create_scrape_and_extract_tool` and `_build_extractor_agent` |
| `src/web_scout/scraping.py` | Make `_is_blocked_domain` / `_validate_url` / `scrape_url` accept `allowed_domains` |
| `tests/test_hub_detection.py` | Append new `_find_next_page_url` tests (relative links, www. match) |
| `tests/test_url_utils.py` | New file: tests for `_normalize_domain`, `_normalize_url` (UTM), `_is_blocked_domain` (allow list) |

---

## Task 1: Fix `_find_next_page_url` — relative links and `www.` normalization

**Files:**
- Modify: `src/web_scout/agent.py` (function `_find_next_page_url`, around line 179; add `urljoin` to urllib.parse import at line 29)
- Modify: `tests/test_hub_detection.py` (append tests)

### Background

`_find_next_page_url` currently uses a regex that only matches `https?://` absolute URLs. Relative links like `[Next](/en/database/list/?page=2)` are silently ignored. It also compares raw netlocs, so `wocat.net` vs `www.wocat.net` are treated as cross-domain and the pagination hop is skipped.

### Current code (lines 179–195 of `agent.py`)

```python
def _find_next_page_url(content: str, base_url: str) -> Optional[str]:
    base_netloc = urlparse(base_url).netloc.lower()

    for match in _re.finditer(r'\[([^\]]*)\]\((https?://[^\s\)]+)\)', content):
        link_text = match.group(1).strip().lower()
        href = match.group(2)
        if link_text in _NEXT_PAGE_TOKENS:
            href_netloc = urlparse(href).netloc.lower()
            if href_netloc == base_netloc:
                return href
    return None
```

- [ ] **Step 1: Add failing tests for relative links and www. mismatch**

Append to `tests/test_hub_detection.py`:

```python
def test_find_next_page_url_relative_link():
    """Relative [Next](/page/2) should resolve against base_url and match."""
    content = "Results [Next](/en/database/list/?page=2) here"
    result = _find_next_page_url(content, "https://wocat.net/en/database/list/")
    assert result == "https://wocat.net/en/database/list/?page=2"


def test_find_next_page_url_www_prefix_match():
    """www.example.com and example.com should be treated as the same domain."""
    content = "[Next](https://www.wocat.net/page/2)"
    assert _find_next_page_url(content, "https://wocat.net/page/1") == "https://www.wocat.net/page/2"


def test_find_next_page_url_anchor_not_matched():
    """Fragment-only links (#section) must not be matched."""
    content = "[Next](#section2)"
    assert _find_next_page_url(content, "https://wocat.net/page/1") is None


def test_find_next_page_url_mailto_not_matched():
    """mailto: links must not be matched."""
    content = "[Next](mailto:next@example.com)"
    assert _find_next_page_url(content, "https://wocat.net/page/1") is None
```

- [ ] **Step 2: Run to confirm they fail**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent python -m pytest tests/test_hub_detection.py -k "relative_link or www_prefix or anchor_not or mailto_not" -v
```

Expected: 2 FAIL (relative and www cases), 2 PASS (anchor and mailto are already excluded by the absolute-URL regex).

- [ ] **Step 3: Fix `_find_next_page_url` in `agent.py`**

First, add `urljoin` to the existing `urllib.parse` import at line 29:

```python
from urllib.parse import urlparse, urljoin
```

Then replace the function body (keep the docstring, replace everything else):

```python
def _find_next_page_url(content: str, base_url: str) -> Optional[str]:
    """Scan markdown content for a 'next page' link on the same domain as base_url.

    Matches link text (case-insensitive) against: 'next', 'next page', '›', '»'.
    Handles both absolute (https://...) and relative (/path) hrefs.
    Normalizes www. prefix when comparing domains.
    Bare digits are intentionally excluded (too fragile).
    Returns the first matching same-domain URL, or None.
    """
    base_netloc = urlparse(base_url).netloc.lower().removeprefix("www.")

    # Expanded regex: match any href, not just https?:// absolute URLs
    for match in _re.finditer(r'\[([^\]]*)\]\(([^\s\)\#][^\s\)]*)\)', content):
        link_text = match.group(1).strip().lower()
        href_raw = match.group(2)

        # Skip non-navigable schemes
        if href_raw.startswith(("mailto:", "javascript:", "tel:", "data:")):
            continue

        # Resolve relative hrefs against base_url; strip fragment before domain check
        href = urljoin(base_url, href_raw)
        href = href.split("#")[0]  # drop fragment (e.g. /page/2#top → /page/2)
        if not href:
            continue

        if link_text in _NEXT_PAGE_TOKENS:
            href_netloc = urlparse(href).netloc.lower().removeprefix("www.")
            if href_netloc == base_netloc:
                return href
    return None
```

Note: `str.removeprefix` is Python 3.9+, which is fine given `python >= 3.10` in `pyproject.toml`.

- [ ] **Step 4: Run tests to confirm they pass**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent python -m pytest tests/test_hub_detection.py -v
```

Expected: all 15 tests pass (11 existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/web_scout/agent.py tests/test_hub_detection.py
git commit -m "fix: _find_next_page_url handles relative links and www. normalization"
```

---

## Task 2: Normalize `include_domains` at the API boundary

**Files:**
- Modify: `src/web_scout/agent.py` (add `_normalize_domain` helper near top of pipeline section; apply in `run_web_research`)
- Create: `tests/test_url_utils.py`

### Background

If a caller passes `include_domains=["https://wocat.net/en/"]`, the pipeline silently fails because all netloc comparisons use the raw string `"https://wocat.net/en/"` instead of `"wocat.net"`. A single normalizer applied once at entry prevents this everywhere.

- [ ] **Step 1: Write failing tests**

Create `tests/test_url_utils.py`:

```python
"""Unit tests for URL/domain utility helpers."""
from web_scout.agent import _normalize_domain


def test_normalize_domain_plain():
    assert _normalize_domain("wocat.net") == "wocat.net"


def test_normalize_domain_strips_scheme():
    assert _normalize_domain("https://wocat.net") == "wocat.net"


def test_normalize_domain_strips_www():
    assert _normalize_domain("www.wocat.net") == "wocat.net"


def test_normalize_domain_strips_scheme_and_www():
    assert _normalize_domain("https://www.wocat.net/en/database/") == "wocat.net"


def test_normalize_domain_strips_path():
    assert _normalize_domain("wocat.net/en/database") == "wocat.net"


def test_normalize_domain_trailing_slash():
    assert _normalize_domain("wocat.net/") == "wocat.net"


def test_normalize_domain_uppercase():
    assert _normalize_domain("WOCAT.NET") == "wocat.net"
```

- [ ] **Step 2: Run to confirm they fail**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent python -m pytest tests/test_url_utils.py -v
```

Expected: `FAILED` — `cannot import name '_normalize_domain' from 'web_scout.agent'`.

- [ ] **Step 3: Add `_normalize_domain` to `agent.py`**

Add the function immediately after `_find_next_page_url` (around line 196):

```python
def _normalize_domain(d: str) -> str:
    """Strip scheme, www., path, and trailing whitespace from a domain string.

    Ensures include_domains entries like 'https://www.wocat.net/en/' are treated
    identically to 'wocat.net'.
    """
    d = d.strip().lower()
    if "://" in d:
        d = urlparse(d).netloc
    # Strip any path component
    d = d.split("/")[0]
    # Strip port (keep domain only)
    d = d.split(":")[0]
    return d.removeprefix("www.")
```

- [ ] **Step 4: Apply normalizer in `run_web_research`**

Inside `run_web_research`, immediately after the depth preset check (around line 244), add:

```python
    # Normalize include_domains: strip scheme, www., paths so callers can pass
    # "https://wocat.net/en/" or "www.wocat.net" and get correct results.
    if include_domains:
        include_domains = [_normalize_domain(d) for d in include_domains]
```

- [ ] **Step 5: Run tests**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent python -m pytest tests/test_url_utils.py tests/test_hub_detection.py -v
```

Expected: all 22 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/web_scout/agent.py tests/test_url_utils.py
git commit -m "fix: normalize include_domains at API entry to strip scheme/www/path"
```

---

## Task 3: Strip UTM tracking params in `_normalize_url`

**Files:**
- Modify: `src/web_scout/tools.py` (add `_TRACKING_PARAMS` constant near top; update `_normalize_url`; add `parse_qsl, urlencode` to urllib.parse import at line 22)
- Modify: `tests/test_url_utils.py` (append tests)

### Background

`ResearchTracker._normalize_url` currently preserves the full query string. URLs like `https://example.com/page?utm_source=google` and `https://example.com/page` are treated as different keys, wasting scrape budget.

### Current code (tools.py lines 68–74)

```python
@staticmethod
def _normalize_url(url: str) -> str:
    p = urlparse(url)
    scheme = "https" if p.scheme in ("http", "https") else p.scheme
    return urlunparse(
        (scheme, p.netloc.lower(), p.path.rstrip("/"), p.params, p.query, "")
    )
```

- [ ] **Step 1: Write failing tests**

Append to `tests/test_url_utils.py`:

```python
from web_scout.tools import ResearchTracker


def test_normalize_url_strips_utm_source():
    base = "https://example.com/page"
    with_utm = "https://example.com/page?utm_source=google"
    assert ResearchTracker._normalize_url(with_utm) == ResearchTracker._normalize_url(base)


def test_normalize_url_strips_multiple_tracking_params():
    url = "https://example.com/page?utm_source=google&utm_medium=email&utm_campaign=spring"
    assert ResearchTracker._normalize_url(url) == "https://example.com/page"


def test_normalize_url_preserves_non_tracking_params():
    url = "https://wocat.net/en/database/list/?type=technology&country=ke"
    normalized = ResearchTracker._normalize_url(url)
    assert "type=technology" in normalized
    assert "country=ke" in normalized


def test_normalize_url_strips_fbclid():
    url = "https://example.com/article?fbclid=IwAR123"
    assert ResearchTracker._normalize_url(url) == "https://example.com/article"


def test_normalize_url_mixed_tracking_and_real_params():
    url = "https://example.com/search?q=test&utm_source=google&page=2"
    normalized = ResearchTracker._normalize_url(url)
    assert "q=test" in normalized
    assert "page=2" in normalized
    assert "utm_source" not in normalized


def test_normalize_url_http_to_https():
    assert ResearchTracker._normalize_url("http://example.com/page") == "https://example.com/page"


def test_normalize_url_trailing_slash_stripped():
    assert ResearchTracker._normalize_url("https://example.com/page/") == "https://example.com/page"
```

- [ ] **Step 2: Run to confirm they fail**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent python -m pytest tests/test_url_utils.py -k "utm or fbclid or tracking or mixed_tracking" -v
```

Expected: FAIL — UTM params are currently preserved.

- [ ] **Step 3: Add `_TRACKING_PARAMS` and update `_normalize_url` in `tools.py`**

Add `parse_qsl, urlencode` to the existing `urllib.parse` import (currently line 22):

```python
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
```

Add the constant near the top of the file, after the existing `_ACTION_RANK` dict (around line 51):

```python
_TRACKING_PARAMS: frozenset = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_source_platform",
    "fbclid", "gclid", "msclkid",
    "mc_cid", "mc_eid",
    "_ga", "ref",
})
```

Replace `_normalize_url` body:

```python
@staticmethod
def _normalize_url(url: str) -> str:
    p = urlparse(url)
    scheme = "https" if p.scheme in ("http", "https") else p.scheme
    if p.query:
        filtered = [
            (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
            if k.lower() not in _TRACKING_PARAMS
        ]
        query = urlencode(filtered)
    else:
        query = ""
    return urlunparse(
        (scheme, p.netloc.lower(), p.path.rstrip("/"), p.params, query, "")
    )
```

- [ ] **Step 4: Run all tests**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent python -m pytest tests/test_url_utils.py tests/test_hub_detection.py -v
```

Expected: all 29 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/web_scout/tools.py tests/test_url_utils.py
git commit -m "fix: strip UTM and tracking params in _normalize_url"
```

---

## Task 4: Configurable blocked domains

**Files:**
- Modify: `src/web_scout/scraping.py` (`_is_blocked_domain`, `_validate_url`, `scrape_url`)
- Modify: `src/web_scout/tools.py` (`_build_extractor_agent`, `create_scrape_and_extract_tool`)
- Modify: `src/web_scout/agent.py` (`run_web_research` signature + docstring + `create_scrape_and_extract_tool` call)
- Modify: `tests/test_url_utils.py` (append tests)

### Background

`_BLOCKED_DOMAINS` in `scraping.py` is a module-level frozenset that blocks `reddit.com`, `twitter.com`, etc. unconditionally. A caller doing social-media research has no way to allow them. The fix adds an optional `allowed_domains: Optional[frozenset] = None` parameter threaded through the call chain. The default block list stays intact; passing `allowed_domains={"reddit.com"}` subtracts those from the effective block set.

### Call chain (top to bottom)

```
run_web_research(allowed_domains: Optional[List[str]])
  → frozenset conversion
  → create_scrape_and_extract_tool(allowed_domains: Optional[frozenset])
    → _build_extractor_agent(allowed_domains: Optional[frozenset])
      → raw_scrape closure captures allowed_domains
        → scrape_url(url, ..., allowed_domains: Optional[frozenset])
          → _validate_url(url, allowed_domains: Optional[frozenset])
            → _is_blocked_domain(url, allowed_domains: Optional[frozenset])
```

- [ ] **Step 1: Write failing tests**

Append to `tests/test_url_utils.py`:

```python
from web_scout.scraping import _is_blocked_domain


def test_is_blocked_domain_reddit_blocked_by_default():
    assert _is_blocked_domain("https://reddit.com/r/MachineLearning") is True


def test_is_blocked_domain_reddit_allowed_when_in_allowed_set():
    allowed = frozenset({"reddit.com"})
    assert _is_blocked_domain("https://reddit.com/r/MachineLearning", allowed_domains=allowed) is False


def test_is_blocked_domain_unrelated_domain_not_blocked():
    assert _is_blocked_domain("https://wocat.net/en/database/") is False


def test_is_blocked_domain_subdomain_blocked():
    # m.youtube.com is a subdomain of youtube.com and should be blocked
    assert _is_blocked_domain("https://m.youtube.com/watch?v=abc") is True


def test_is_blocked_domain_www_reddit_blocked_by_default():
    assert _is_blocked_domain("https://www.reddit.com/r/science") is True


def test_is_blocked_domain_allowed_set_empty_uses_full_blocklist():
    assert _is_blocked_domain("https://twitter.com/user", allowed_domains=frozenset()) is True
```

- [ ] **Step 2: Run to confirm they fail**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent python -m pytest tests/test_url_utils.py -k "blocked_domain" -v
```

Expected: `test_is_blocked_domain_reddit_allowed_when_in_allowed_set` FAILS — function doesn't accept `allowed_domains` yet.

- [ ] **Step 3: Update `_is_blocked_domain` in `scraping.py`**

```python
def _is_blocked_domain(url: str, allowed_domains: Optional[frozenset] = None) -> bool:
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    effective_blocked = _BLOCKED_DOMAINS
    if allowed_domains:
        effective_blocked = _BLOCKED_DOMAINS - allowed_domains
    return any(netloc == d or netloc.endswith("." + d) for d in effective_blocked)
```

`Optional` is already imported at the top of `scraping.py`.

- [ ] **Step 4: Thread `allowed_domains` through `_validate_url` and `scrape_url` in `scraping.py`**

Update `_validate_url` signature and the `_is_blocked_domain` call inside it:

```python
async def _validate_url(url: str, allowed_domains: Optional[frozenset] = None) -> Tuple[str, str]:
    if _is_blocked_domain(url, allowed_domains=allowed_domains):
        return _SKIP, "blocked domain"
    # ... rest of function unchanged
```

Update `scrape_url` signature, its docstring Args section, and the `_validate_url` call inside it:

```python
async def scrape_url(
    url: str,
    wait_for: Optional[str] = None,
    query: str = "",
    vision_model: Optional[str] = None,
    allowed_domains: Optional[frozenset] = None,
) -> Tuple[str, str, Optional[str]]:
    """Scrape a URL and return clean markdown content.
    ...existing docstring...
        allowed_domains: Frozenset of domain strings (e.g. ``frozenset({"reddit.com"})``)
            to remove from the default blocked-domain list. ``None`` uses the full block list.
    ...rest of docstring...
    """
    # ...
    verdict, detail = await _validate_url(url, allowed_domains=allowed_domains)
    # ... rest of function unchanged
```

- [ ] **Step 5: Run tests to confirm scraping.py changes pass**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent python -m pytest tests/test_url_utils.py -k "blocked_domain" -v
```

Expected: all 6 blocked_domain tests pass.

- [ ] **Step 6: Thread `allowed_domains` through `tools.py`**

Update `_build_extractor_agent` signature:

```python
def _build_extractor_agent(model: Any, query: str, url: str, wait_for: Optional[str], vision_model: Optional[str] = None, allowed_domains: Optional[frozenset] = None) -> Agent:
```

Inside, update the `raw_scrape` closure to pass it to `_scrape_url`:

```python
    @function_tool
    async def raw_scrape() -> str:
        content, title, error = await _scrape_url(url, wait_for, query=query, vision_model=vision_model, allowed_domains=allowed_domains)
        # ... rest unchanged
```

Update `create_scrape_and_extract_tool` signature:

```python
def create_scrape_and_extract_tool(
    extractor_model: Any,
    tracker: Optional[ResearchTracker] = None,
    query: str = "",
    max_concurrent: int = 3,
    vision_model: Optional[str] = None,
    allowed_domains: Optional[frozenset] = None,
):
```

Inside, pass it to `_build_extractor_agent`:

```python
        extractor_agent = _build_extractor_agent(extractor_model, query, url, _wait_for, vision_model=vision_model, allowed_domains=allowed_domains)
```

- [ ] **Step 7: Add `allowed_domains` to `run_web_research` in `agent.py`**

Add the parameter to the function signature:

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
) -> WebResearchResult:
```

Add to the docstring Args section:

```
        allowed_domains: Domains to remove from the default block list (e.g.
            ``["reddit.com"]`` to allow Reddit pages). The block list covers
            social media and video platforms by default. Pass ``None`` (default)
            to use the full block list unchanged.
```

After the depth preset check and `include_domains` normalization block, add:

```python
    # Convert allowed_domains to frozenset, applying same normalization as include_domains
    # so callers can pass "www.reddit.com" or "https://reddit.com" and get correct behaviour.
    _allowed = frozenset(_normalize_domain(d) for d in allowed_domains) if allowed_domains else None
```

Update the `create_scrape_and_extract_tool` call:

```python
    scrape_tool = create_scrape_and_extract_tool(
        extractor_model=extractor_model,
        tracker=tracker,
        query=query,
        vision_model=vision_model,
        allowed_domains=_allowed,
    )
```

- [ ] **Step 8: Run all tests**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent python -m pytest tests/test_url_utils.py tests/test_hub_detection.py -v
```

Expected: all 35 tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/web_scout/scraping.py src/web_scout/tools.py src/web_scout/agent.py tests/test_url_utils.py
git commit -m "feat: add allowed_domains param to allow overriding default blocked-domain list"
```

---

## Task 5: Broaden test coverage for pre-existing URL utilities

**Files:**
- Modify: `tests/test_url_utils.py` (append)

### Background

The existing tests cover the new helpers added in Tasks 1–4. This task adds tests for pre-existing behaviours that lack any coverage: `_normalize_url` edge cases, `_is_blocked_domain` for subdomains and http vs https, and `_find_next_page_url` for more realistic content strings.

- [ ] **Step 1: Append additional tests to `tests/test_url_utils.py`**

```python
# --- Additional _normalize_url coverage ---

def test_normalize_url_preserves_empty_query():
    url = "https://example.com/page?"
    # Trailing ? should not leave a hanging separator
    normalized = ResearchTracker._normalize_url(url)
    assert normalized == "https://example.com/page"


def test_normalize_url_idempotent():
    url = "https://example.com/page?type=tech&country=ke"
    assert ResearchTracker._normalize_url(url) == ResearchTracker._normalize_url(
        ResearchTracker._normalize_url(url)
    )


def test_normalize_url_fragment_stripped():
    # urlunparse already omits fragment (last component is "")
    url = "https://example.com/page"
    assert "#" not in ResearchTracker._normalize_url(url)


# --- Additional _is_blocked_domain coverage ---

def test_is_blocked_domain_linkedin_blocked():
    assert _is_blocked_domain("https://linkedin.com/in/someone") is True


def test_is_blocked_domain_allowed_set_only_removes_specified():
    allowed = frozenset({"reddit.com"})
    # twitter.com is still blocked even when reddit is allowed
    assert _is_blocked_domain("https://twitter.com/user", allowed_domains=allowed) is True
    assert _is_blocked_domain("https://reddit.com/r/foo", allowed_domains=allowed) is False


# --- Additional _find_next_page_url realistic content ---

def test_find_next_page_url_in_real_markdown_table():
    content = (
        "| Technology | Country |\n"
        "| Agroforestry | KE |\n"
        "\n"
        "Page 1 of 3 — [Next](https://wocat.net/en/database/list/?page=2&type=technology)\n"
    )
    result = _find_next_page_url(content, "https://wocat.net/en/database/list/")
    assert result == "https://wocat.net/en/database/list/?page=2&type=technology"


def test_find_next_page_url_ignores_other_links_with_same_domain():
    content = (
        "[Home](https://wocat.net/) "
        "[About](https://wocat.net/about) "
        "[Next](https://wocat.net/list/?page=2)"
    )
    result = _find_next_page_url(content, "https://wocat.net/list/")
    assert result == "https://wocat.net/list/?page=2"
```

- [ ] **Step 2: Run to confirm all pass**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent python -m pytest tests/test_url_utils.py tests/test_hub_detection.py -v
```

Expected: all pass (the new tests should pass immediately — they test existing or already-fixed behaviour).

- [ ] **Step 3: Commit**

```bash
git add tests/test_url_utils.py
git commit -m "test: broaden URL utility coverage for normalize_url, is_blocked_domain, find_next_page_url"
```

---

## Final verification

Run the full suite one last time:

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent python -m pytest tests/ -v
```

Expected: all tests in both `test_hub_detection.py` and `test_url_utils.py` pass.
