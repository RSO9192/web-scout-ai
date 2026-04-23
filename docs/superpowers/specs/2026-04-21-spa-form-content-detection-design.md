# Design: SPA Fragment & Form-Contamination Detection

**Date:** 2026-04-21
**Status:** Approved

## Problem

JS data portals (e.g. FAOSTAT `https://www.fao.org/faostat/en/#data/QCL`) cause two
compounding failures:

1. **SPA fragment routing** — the `#fragment` is client-side JS routing. The scraper
   loads the base page instead of the target data tab, so the wrong view is returned.

2. **Form contamination** — the page loads a satisfaction survey widget ("Strongly Agree"
   × 5) alongside navigation, inflating char count to ~4 400. Because content exceeds the
   500-char thin-content threshold, `list_interactive_elements` is never triggered, and
   the extractor treats the survey noise as the actual page content.

## Chosen Approach: Signal injection in `raw_scrape` (Option C)

Two lightweight checks are added inside the `raw_scrape` closure. When triggered they
append a structured signal string to the returned content. The extractor instructions are
extended to react to these signals by calling `list_interactive_elements`.

No changes are made to `_validate_url`, `_scrape_url`, the public API, or any other tool.

## Components

### 1. `_has_fragment(url: str) -> bool` — `tools.py`

Pure function. Returns `True` if `urlparse(url).fragment` is non-empty.

```
https://fao.org/faostat/en/#data/QCL  →  True
https://fao.org/fishery/en            →  False
```

### 2. `_is_form_contaminated(content: str) -> bool` — `tools.py`

Pure function. Returns `True` if either:

- Any survey token appears ≥ 2 times (case-insensitive):
  `"strongly agree"`, `"strongly disagree"`, `"please rate"`,
  `"kindly provide"`, `"please provide"`, `"select an option"`
- OR content has ≥ 20 lines and > 75% are bullet-point lines
  (starts with `"* "` or `"- "`), indicating nav-only pages.

Only called when `len(content) >= _THIN_CONTENT_CHARS` (500) to avoid
double-signalling already-thin pages.

### 3. Signal injection in `raw_scrape` — `tools.py`

After `_scrape_url` returns, before the closure returns to the LLM:

```python
signals = []
if _has_fragment(url):
    signals.append(
        "[SPA: URL fragment detected — current content may be the wrong "
        "tab/view. Call list_interactive_elements to find the data section.]"
    )
if len(content) >= _THIN_CONTENT_CHARS and _is_form_contaminated(content):
    signals.append(
        "[Form/survey content detected — actual data is likely behind "
        "interactive elements. Call list_interactive_elements.]"
    )
if signals:
    return header + content + "\n\n" + "\n".join(signals)
return header + content
```

Both signals can fire simultaneously (FAOSTAT triggers both).

### 4. Extractor instructions update — `tools.py`

The existing thin-content trigger block is extended with a second condition:

```
Also call list_interactive_elements() if raw_scrape returned a message containing:
- "[SPA: URL fragment detected" — the page uses client-side routing; the visible
  content may be the wrong tab or view. Look for tabs or section selectors.
- "[Form/survey content detected" — the page loaded a feedback widget instead of
  data. Look for data tabs, dropdowns, or navigation controls.
```

All existing interaction rules (5-click limit, stale-index handling,
thin-content-after-click warning) remain unchanged.

## Data Flow

```
raw_scrape(url)
    └─ _scrape_url(url, ...)  →  (content, title, error)
            │
            ├─ error?  →  return error string  (unchanged)
            │
            ├─ _has_fragment(url)?  →  append SPA signal
            │
            ├─ len(content) >= 500 and _is_form_contaminated(content)?
            │       →  append Form signal
            │
            └─ return header + content [+ signals]
                        │
                        ▼
              Extractor LLM sees signal
                        │
                        ▼
              Calls list_interactive_elements()
                        │
                        ▼
              Clicks relevant tab/button (≤ 5 clicks)
                        │
                        ▼
              Returns actual data content
```

## Error Handling

- Both detection functions are pure with no I/O — they cannot raise.
- If `_scrape_url` itself errors, the existing error path fires before any detection.
- If signals appear but `list_interactive_elements` finds no elements, the extractor
  proceeds with available content (existing fallback behaviour).

## Testing

| Test | Type | File |
|---|---|---|
| `_has_fragment` with/without fragment | unit | `test_spa_form_detection.py` |
| `_is_form_contaminated` with survey, nav-only, normal content | unit | `test_spa_form_detection.py` |
| `raw_scrape` appends SPA signal for fragment URL | unit (mocked scrape) | `test_spa_form_detection.py` |
| `raw_scrape` appends form signal for survey content > 500 chars | unit (mocked scrape) | `test_spa_form_detection.py` |
| `raw_scrape` appends both signals for FAOSTAT-like URL | unit (mocked scrape) | `test_spa_form_detection.py` |
| `raw_scrape` does NOT append form signal when content < 500 chars | unit | `test_spa_form_detection.py` |
| `raw_scrape` does NOT append form signal for clean content | unit | `test_spa_form_detection.py` |
| Extractor instructions mention SPA and form signal strings | unit | `test_spa_form_detection.py` |

## What Does NOT Change

- `_validate_url` routing logic
- `_scrape_url` internals
- `list_interactive_elements` and `click_element` tools
- Public API (`run_web_research`, `scrape_url`)
- Existing thin-content trigger (< 500 chars)
- Click limit (5), stale-index handling, thin-content-after-click warning
