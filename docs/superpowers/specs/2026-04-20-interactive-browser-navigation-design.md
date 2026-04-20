# Interactive Browser Navigation — Design Spec

**Date:** 2026-04-20  
**Status:** Approved, ready for implementation

## Summary

Add click-based interactive navigation to the content extractor sub-agent so it can reveal data hidden behind tabs, buttons, and "load more" controls on JS-heavy data portals. Triggered automatically as a fallback when passive scraping returns thin content.

---

## Goals

- Allow the research agent to click buttons, tabs, and interactive controls on pages that hide content behind UI interactions.
- Keep the change minimal: no new public API, no new dependencies, no changes outside `tools.py`.
- Use the Element Reference Pattern (pre-numbered elements, LLM picks by index) to avoid hallucinated selectors.

## Non-Goals

- Form filling (text inputs, date pickers) — out of scope.
- Multi-step wizards, login flows, CAPTCHA solving — out of scope.
- Persistent browser sessions across multiple `scrape_and_extract` calls — each call gets its own isolated session.

---

## Architecture

All changes are confined to `src/web_scout/tools.py`, specifically inside `_build_extractor_agent`.

No changes to:
- `scraping.py` (stays LLM-free)
- `agent.py` (pipeline unchanged)
- Public API (`run_web_research` signature unchanged)
- Default models (`content_extractor` model handles the new tools without upgrade)

---

## New Tools on the Extractor Sub-Agent

Two new `@function_tool` closures are added alongside `raw_scrape` and `scrape_linked_document`.

### `list_interactive_elements()`

- Launches a Playwright Chromium browser (headless, stealth UA) for the pre-locked URL.
- Navigates to the URL and waits for network idle.
- Evaluates a JS snippet that queries: `button`, `[role="tab"]`, `[role="button"]`, `select`, and `a` elements matching load-more/expand patterns.
- Filters to visible + enabled elements only (`offsetParent !== null`, `!disabled`).
- Returns a numbered plain-text list:
  ```
  Interactive elements on page:
  [1] tab: "Overview"
  [2] tab: "Data by Year"
  [3] button: "Load more results"
  [4] select: "Select year"
  ```
- The Playwright browser and page are stored in a mutable closure list `[browser, page]` shared with `click_element`.

### `click_element(index: int)`

- Re-queries the interactive element list fresh at click time (handles DOM changes since `list_interactive_elements` was called).
- If the element at `index` is no longer present, returns: `"Element [n] no longer visible — call list_interactive_elements() again to get the updated list."`
- Clicks the element, waits up to 3 seconds for network idle.
- Re-extracts page content as markdown using crawl4ai's `DefaultMarkdownGenerator` on the current page HTML.
- Returns the updated markdown content, or an error string on Playwright failure.
- Enforces a **hard limit of 5 clicks** via a counter in the closure. On the 6th call returns: `"INTERACTION LIMIT REACHED: 5 clicks used. Synthesize from content gathered so far."`
- If content after click is still under 500 chars, appends: `"Content still thin after click — consider clicking a different element."`

---

## Session Lifecycle

- The Playwright browser is launched **lazily** on the first call to `list_interactive_elements()`.
- It is stored in `_browser_holder = [None]` and `_page_holder = [None]` closure lists.
- After `Runner.run(extractor_agent, ...)` completes (or raises), `create_scrape_and_extract_tool` closes the browser in a `try/finally` block: `if _browser_holder[0]: await _browser_holder[0].close()`.
- No browser is opened if the extractor never needs interaction (rich `raw_scrape` result).

---

## Trigger Condition

The extractor sub-agent decides whether to use interaction. The instructions are extended with a new **Step 1b** between the existing Step 1 (fetch) and Step 2 (linked document check):

```
## Step 1b — Handle thin content with interaction
If raw_scrape returned fewer than 500 characters of meaningful content
AND the page is not a document (PDF/DOCX):

1. Call list_interactive_elements() to see what is clickable.
2. If the list contains tabs, buttons, or controls likely to reveal data
   relevant to the research query, call click_element(n) for the most
   promising one.
3. Use the updated content. You may click up to 5 elements total.
4. If content remains thin after clicking, proceed with what you have.

Do NOT call list_interactive_elements() if raw_scrape already returned
rich content — interaction is a fallback, not a default.
```

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Click limit exceeded (>5) | Hard stop message returned, LLM synthesizes what it has |
| Stale DOM element (index gone) | Tool returns descriptive message; LLM can re-list |
| Thin content after click | Tool appends note; LLM can try another element |
| Playwright exception (timeout, crash) | Exception caught, error string returned; falls back to original `raw_scrape` content |
| `list_interactive_elements` called when `raw_scrape` already rich | Instructions prevent this; no runtime guard needed |

---

## Files Changed

| File | Change |
|---|---|
| `src/web_scout/tools.py` | Add `list_interactive_elements` and `click_element` closures in `_build_extractor_agent`; add session cleanup in `create_scrape_and_extract_tool`; extend `_EXTRACTOR_INSTRUCTIONS` with Step 1b |

---

## Testing Notes

- Test with a known JS portal that hides data behind tabs (e.g. a FAO data portal page).
- Confirm: no interaction attempted when `raw_scrape` returns rich content.
- Confirm: click limit enforced at 5.
- Confirm: browser closed after extractor finishes (no dangling processes).
- Confirm: existing scraping behaviour unchanged for static HTML, PDF, JSON paths.
