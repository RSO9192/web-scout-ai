# Interactive Browser Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new tools (`list_interactive_elements`, `click_element`) to the content extractor sub-agent so it can click tabs, buttons, and "load more" controls on JS-heavy data portals when passive scraping returns thin content.

**Architecture:** Both tools live in `src/web_scout/tools.py` inside `_build_extractor_agent`. A shared Playwright session (browser + page) is opened lazily on the first `list_interactive_elements` call and closed after the extractor finishes via a cleanup coroutine returned alongside the agent. `_build_extractor_agent` now returns `(Agent, cleanup_coro)`. The extractor instructions gain a new Step 1b that triggers interaction only when `raw_scrape` returns fewer than 500 characters.

**Tech Stack:** Python 3.10+, Playwright (already installed via crawl4ai), pytest-asyncio, unittest.mock

---

## File Map

| File | Change |
|---|---|
| `src/web_scout/tools.py` | Add JS constants, two new tools, cleanup coroutine, updated `_build_extractor_agent` signature, updated `_EXTRACTOR_INSTRUCTIONS`, updated call site in `create_scrape_and_extract_tool` |
| `tests/test_interactive_tools.py` | New — unit tests for both tools |
| `tests/test_scrape_tool_dedupe.py` | Update monkeypatches broken by `_build_extractor_agent` returning a tuple |

---

## Task 1: Update existing monkeypatch and write failing tests

**Files:**
- Modify: `tests/test_scrape_tool_dedupe.py`
- Create: `tests/test_interactive_tools.py`

- [ ] **Step 1.1: Fix broken monkeypatches in `test_scrape_tool_dedupe.py`**

`_build_extractor_agent` will now return `(Agent, cleanup_coro)`. The existing tests mock it as `lambda *args, **kwargs: object()` which will break the unpack. Update both usages:

In `test_scrape_tool_reuses_inflight_request`:
```python
async def _no_cleanup():
    pass

monkeypatch.setattr(
    tools,
    "_build_extractor_agent",
    lambda *args, **kwargs: (object(), _no_cleanup),
)
```

In `test_scrape_tool_does_not_retry_bot_detected`:
```python
async def _no_cleanup():
    pass

monkeypatch.setattr(
    tools,
    "_build_extractor_agent",
    lambda *args, **kwargs: (object(), _no_cleanup),
)
```

- [ ] **Step 1.2: Verify existing tests still pass (they should — logic unchanged)**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent \
  pytest tests/test_scrape_tool_dedupe.py -v
```

Expected: all tests PASS (no logic changed, only monkeypatch updated).

- [ ] **Step 1.3: Write failing tests for `list_interactive_elements`**

Create `tests/test_interactive_tools.py`:

```python
"""Unit tests for interactive browser navigation tools."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web_scout.tools import _build_extractor_agent


def _make_extractor_agent(url="https://example.org/portal", query="fish production"):
    """Build extractor agent with test fixtures; returns (agent, cleanup)."""
    return _build_extractor_agent(
        model="dummy",
        query=query,
        url=url,
        wait_for=None,
    )


def _find_tool(agent, name: str):
    """Locate a tool on an agent by function name."""
    for tool in agent.tools:
        fn = getattr(tool, "on_invoke_tool", None) or getattr(tool, "fn", None)
        if fn and fn.__name__ == name:
            return tool
        # openai-agents wraps tools; try the name attribute
        if getattr(tool, "name", None) == name:
            return tool
    raise AssertionError(f"Tool '{name}' not found on agent. Available: {[getattr(t, 'name', t) for t in agent.tools]}")


# ---------------------------------------------------------------------------
# list_interactive_elements
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_interactive_elements_returns_numbered_list():
    """Happy path: Playwright page returns two elements; tool formats them."""
    agent, cleanup = _make_extractor_agent()
    tool = _find_tool(agent, "list_interactive_elements")

    fake_elements = [
        {"tag": "tab", "text": "Data by Year"},
        {"tag": "button", "text": "Load more results"},
    ]

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=fake_elements)

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        result = await tool.on_invoke_tool(None, "{}")

    assert "[1] tab: \"Data by Year\"" in result
    assert "[2] button: \"Load more results\"" in result
    assert "Interactive elements on page:" in result

    await cleanup()


@pytest.mark.asyncio
async def test_list_interactive_elements_no_elements():
    """Page with no interactive elements returns informative message."""
    agent, cleanup = _make_extractor_agent()
    tool = _find_tool(agent, "list_interactive_elements")

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=[])

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        result = await tool.on_invoke_tool(None, "{}")

    assert "No interactive elements found" in result

    await cleanup()


@pytest.mark.asyncio
async def test_list_interactive_elements_playwright_error():
    """Playwright launch failure returns a readable error string."""
    agent, cleanup = _make_extractor_agent()
    tool = _find_tool(agent, "list_interactive_elements")

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(side_effect=RuntimeError("browser crashed"))
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        result = await tool.on_invoke_tool(None, "{}")

    assert "list_interactive_elements failed" in result
    assert "browser crashed" in result

    await cleanup()


# ---------------------------------------------------------------------------
# click_element
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_click_element_returns_page_content():
    """After a successful click, tool returns updated page text."""
    agent, cleanup = _make_extractor_agent()

    fake_elements = [{"tag": "tab", "text": "Data by Year"}]

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(side_effect=[fake_elements, True])
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.inner_text = AsyncMock(return_value="Year 2023: 1,200 tonnes\nYear 2022: 1,100 tonnes\n" * 30)

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    list_tool = _find_tool(agent, "list_interactive_elements")
    click_tool = _find_tool(agent, "click_element")

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        await list_tool.on_invoke_tool(None, "{}")
        result = await click_tool.on_invoke_tool(None, '{"index": 1}')

    assert "2023" in result
    assert "tonnes" in result

    await cleanup()


@pytest.mark.asyncio
async def test_click_element_enforces_limit():
    """click_element refuses after 5 clicks."""
    agent, cleanup = _make_extractor_agent()

    fake_elements = [{"tag": "tab", "text": "Tab A"}]

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    # evaluate: first call from list_interactive_elements, then alternating for each click
    mock_page.evaluate = AsyncMock(return_value=fake_elements)
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.inner_text = AsyncMock(return_value="content " * 200)

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    list_tool = _find_tool(agent, "list_interactive_elements")
    click_tool = _find_tool(agent, "click_element")

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        await list_tool.on_invoke_tool(None, "{}")
        for _ in range(5):
            await click_tool.on_invoke_tool(None, '{"index": 1}')
        result = await click_tool.on_invoke_tool(None, '{"index": 1}')

    assert "INTERACTION LIMIT REACHED" in result

    await cleanup()


@pytest.mark.asyncio
async def test_click_element_stale_index():
    """click_element returns descriptive message when index exceeds element count."""
    agent, cleanup = _make_extractor_agent()

    fake_elements = [{"tag": "tab", "text": "Tab A"}]

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    # evaluate returns False (element not found) for the click call
    mock_page.evaluate = AsyncMock(side_effect=[fake_elements, False])
    mock_page.wait_for_load_state = AsyncMock()

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    list_tool = _find_tool(agent, "list_interactive_elements")
    click_tool = _find_tool(agent, "click_element")

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        await list_tool.on_invoke_tool(None, "{}")
        result = await click_tool.on_invoke_tool(None, '{"index": 99}')

    assert "no longer visible" in result or "not found" in result.lower()

    await cleanup()


@pytest.mark.asyncio
async def test_click_element_thin_content_warning():
    """click_element appends warning when post-click content is under 500 chars."""
    agent, cleanup = _make_extractor_agent()

    fake_elements = [{"tag": "tab", "text": "Tab A"}]

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(side_effect=[fake_elements, True])
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.inner_text = AsyncMock(return_value="short content")  # under 500 chars

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_context)
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    list_tool = _find_tool(agent, "list_interactive_elements")
    click_tool = _find_tool(agent, "click_element")

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        await list_tool.on_invoke_tool(None, "{}")
        result = await click_tool.on_invoke_tool(None, '{"index": 1}')

    assert "Content still thin" in result

    await cleanup()


@pytest.mark.asyncio
async def test_click_element_called_without_session_raises():
    """click_element without a prior list_interactive_elements call returns error."""
    agent, cleanup = _make_extractor_agent()
    click_tool = _find_tool(agent, "click_element")

    result = await click_tool.on_invoke_tool(None, '{"index": 1}')

    assert "call list_interactive_elements" in result.lower()

    await cleanup()


@pytest.mark.asyncio
async def test_cleanup_closes_browser():
    """cleanup() closes the Playwright browser when it was opened."""
    agent, cleanup = _make_extractor_agent()

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=[{"tag": "tab", "text": "X"}])

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    list_tool = _find_tool(agent, "list_interactive_elements")

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        await list_tool.on_invoke_tool(None, "{}")
        await cleanup()

    mock_browser.close.assert_called_once()
    mock_pw_cm.__aexit__.assert_called_once()
```

- [ ] **Step 1.4: Run tests to confirm they all FAIL (implementation not yet written)**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent \
  pytest tests/test_interactive_tools.py -v 2>&1 | head -40
```

Expected: errors like `AttributeError: 'Agent' object has no attribute 'tools'` or import errors — confirming the tests are wired to code that doesn't exist yet.

---

## Task 2: Add JS constants and module-level import

**Files:**
- Modify: `src/web_scout/tools.py`

- [ ] **Step 2.1: Add `async_playwright` import and JS constants near the top of `tools.py`**

After the existing imports (around line 25), add:

```python
from playwright.async_api import async_playwright
```

After the `_RETRY_DELAYS` constant (around line 38), add:

```python
_THIN_CONTENT_CHARS = 500
_MAX_INTERACTIVE_CLICKS = 5

# JavaScript that queries all visible interactive elements on a page.
# Returns a list of {tag, text} objects in a stable, deduplicated order.
_GET_ELEMENTS_JS = """
(() => {
    const all = [];
    const seen = new Set();
    document.querySelectorAll('button, [role="tab"], [role="button"], select').forEach(el => {
        if (el.offsetParent === null || el.disabled) return;
        const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
        if (!text) return;
        const key = text.slice(0, 60);
        if (seen.has(key)) return;
        seen.add(key);
        all.push({tag: el.getAttribute('role') || el.tagName.toLowerCase(), text: key});
    });
    document.querySelectorAll('a').forEach(el => {
        if (el.offsetParent === null) return;
        const text = (el.innerText || '').trim();
        if (!/load more|show more|show all|expand|next|view all/i.test(text)) return;
        const key = text.slice(0, 60);
        if (seen.has(key)) return;
        seen.add(key);
        all.push({tag: 'a', text: key});
    });
    return all;
})()
"""

# JavaScript that re-queries interactive elements and clicks the one at `index` (1-based).
# Returns true if clicked, false if index is out of range.
_CLICK_ELEMENT_JS = """
(index) => {
    const all = [];
    const seen = new Set();
    document.querySelectorAll('button, [role="tab"], [role="button"], select').forEach(el => {
        if (el.offsetParent === null || el.disabled) return;
        const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
        if (!text) return;
        const key = text.slice(0, 60);
        if (seen.has(key)) return;
        seen.add(key);
        all.push(el);
    });
    document.querySelectorAll('a').forEach(el => {
        if (el.offsetParent === null) return;
        const text = (el.innerText || '').trim();
        if (!/load more|show more|show all|expand|next|view all/i.test(text)) return;
        const key = text.slice(0, 60);
        if (seen.has(key)) return;
        seen.add(key);
        all.push(el);
    });
    const target = all[index - 1];
    if (!target) return false;
    target.click();
    return true;
}
"""
```

- [ ] **Step 2.2: Verify the import and constants load without error**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent \
  python -c "from web_scout.tools import _GET_ELEMENTS_JS, _CLICK_ELEMENT_JS, _MAX_INTERACTIVE_CLICKS; print('OK')"
```

Expected: `OK`

---

## Task 3: Implement `list_interactive_elements` and `click_element` tools in `_build_extractor_agent`

**Files:**
- Modify: `src/web_scout/tools.py`

- [ ] **Step 3.1: Change `_build_extractor_agent` to return `(Agent, cleanup_coro)` and add the two new tools**

Find `_build_extractor_agent` (currently ends with `return Agent(...)`). Replace the body after the existing `scrape_linked_document` tool definition with the following. The existing `raw_scrape` and `scrape_linked_document` tools remain unchanged above this insertion point.

Add after `scrape_linked_document` and before the `return Agent(...)` line:

```python
    # --- interactive browser session ---
    _browser_holder: list = [None]   # [playwright Browser | None]
    _pw_holder: list = [None]        # [AsyncPlaywrightContextManager | None]
    _page_holder: list = [None]      # [playwright Page | None]
    _click_count: list = [0]

    @function_tool
    async def list_interactive_elements() -> str:
        """List all visible, clickable elements on the page as a numbered list.

        Opens a Playwright browser for the pre-set URL and returns all visible
        buttons, tabs, dropdowns, and load-more links with numeric indices.
        Call click_element(n) to interact with a specific element.

        Only call this when raw_scrape returned thin content (under 500 chars).
        Do NOT call this if raw_scrape already returned rich content.
        """
        try:
            if _browser_holder[0] is None:
                pw_cm = async_playwright()
                pw = await pw_cm.__aenter__()
                _pw_holder[0] = pw_cm
                browser = await pw.chromium.launch(headless=True)
                _browser_holder[0] = browser
                ctx = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                )
                page = await ctx.new_page()
                await page.goto(url, wait_until="networkidle", timeout=30_000)
                _page_holder[0] = page

            elements = await _page_holder[0].evaluate(_GET_ELEMENTS_JS)
            if not elements:
                return "No interactive elements found on this page."

            lines = ["Interactive elements on page:"]
            for i, el in enumerate(elements, 1):
                lines.append(f'[{i}] {el["tag"]}: "{el["text"]}"')
            return "\n".join(lines)

        except Exception as e:
            return f"[list_interactive_elements failed: {e}]"

    @function_tool
    async def click_element(index: int) -> str:
        """Click an interactive element by its index from list_interactive_elements.

        Re-queries the page for the element (handles DOM changes), clicks it,
        waits for the page to settle, and returns the updated page content.
        Maximum 5 clicks per page.

        Args:
            index: 1-based index of the element to click, as returned by
                list_interactive_elements.
        """
        if _page_holder[0] is None:
            return (
                "[click_element error: no browser session open. "
                "Call list_interactive_elements() first.]"
            )

        if _click_count[0] >= _MAX_INTERACTIVE_CLICKS:
            return (
                f"INTERACTION LIMIT REACHED: {_MAX_INTERACTIVE_CLICKS} clicks used. "
                "Synthesize from content gathered so far."
            )

        try:
            clicked = await _page_holder[0].evaluate(_CLICK_ELEMENT_JS, index)
            if not clicked:
                return (
                    f"Element [{index}] no longer visible after previous interaction — "
                    "call list_interactive_elements() again to get the updated list."
                )

            _click_count[0] += 1

            try:
                await _page_holder[0].wait_for_load_state("networkidle", timeout=3_000)
            except Exception:
                pass  # timeout is acceptable — page may not trigger a network event

            content = await _page_holder[0].inner_text("body")
            result = content.strip()

            if len(result) < _THIN_CONTENT_CHARS:
                result += (
                    f"\n\n[Content still thin after click ({len(result)} chars) — "
                    "consider clicking a different element.]"
                )

            return result

        except Exception as e:
            return f"[click_element failed: {e}]"
```

Then replace `return Agent(...)` with:

```python
    agent = Agent(
        name="content_extractor",
        model=model,
        tools=[raw_scrape, scrape_linked_document, list_interactive_elements, click_element],
        output_type=_ExtractorOutput,
        model_settings=ModelSettings(),
        instructions=_EXTRACTOR_INSTRUCTIONS,
    )

    async def cleanup() -> None:
        if _browser_holder[0] is not None:
            try:
                await _browser_holder[0].close()
            except Exception:
                pass
        if _pw_holder[0] is not None:
            try:
                await _pw_holder[0].__aexit__(None, None, None)
            except Exception:
                pass

    return agent, cleanup
```

- [ ] **Step 3.2: Run the new tests to see how many pass**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent \
  pytest tests/test_interactive_tools.py -v 2>&1 | tail -20
```

Expected: most tests pass; any failures likely relate to how `on_invoke_tool` is called or tool naming — debug and fix before proceeding.

Note: the openai-agents SDK wraps `@function_tool` decorated functions. If `on_invoke_tool` is not the right method, check the SDK's tool object API. The tool's underlying callable may be accessible via `tool.fn` or `tool.__wrapped__`. Adjust `_find_tool` in the test file accordingly.

---

## Task 4: Update `create_scrape_and_extract_tool` to unpack `(agent, cleanup)` and call cleanup

**Files:**
- Modify: `src/web_scout/tools.py`

- [ ] **Step 4.1: Update the call site in `create_scrape_and_extract_tool`**

Find this block inside `scrape_and_extract` (inside `create_scrape_and_extract_tool`):

```python
                # Build a fresh extractor agent per call with url locked in the closure
                extractor_agent = _build_extractor_agent(extractor_model, query, url, _wait_for, vision_model=vision_model, allowed_domains=allowed_domains, max_pdf_pages=max_pdf_pages, max_content_chars=max_content_chars)

                input_text = (
                    f"Research query: {query}\n"
                    f"URL: {url}\n\n"
                    f"Call raw_scrape() to fetch the page, then extract relevant content."
                )

                try:
                    result = await _run_with_retry(extractor_agent, input_text, max_turns=30)
```

Replace with:

```python
                # Build a fresh extractor agent per call with url locked in the closure
                extractor_agent, _browser_cleanup = _build_extractor_agent(extractor_model, query, url, _wait_for, vision_model=vision_model, allowed_domains=allowed_domains, max_pdf_pages=max_pdf_pages, max_content_chars=max_content_chars)

                input_text = (
                    f"Research query: {query}\n"
                    f"URL: {url}\n\n"
                    f"Call raw_scrape() to fetch the page, then extract relevant content."
                )

                try:
                    result = await _run_with_retry(extractor_agent, input_text, max_turns=30)
```

Then find the `except Exception as e:` block that handles the sub-agent failure and add a `finally` that calls cleanup. The full block should look like:

```python
                try:
                    result = await _run_with_retry(extractor_agent, input_text, max_turns=30)
                    output = result.final_output_as(_ExtractorOutput)
                except Exception as e:
                    logger.error("[extract] sub-agent failed for %s: %s", url, e)
                    if tracker is not None:
                        tracker.record_scrape_failure(url, str(e))
                    final_output = f"Failed to extract content from {url}: {e}"
                    future.set_result(final_output)
                    return final_output
                finally:
                    await _browser_cleanup()
```

- [ ] **Step 4.2: Run all scrape tool tests to verify nothing is broken**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent \
  pytest tests/test_scrape_tool_dedupe.py tests/test_interactive_tools.py -v
```

Expected: all tests PASS.

---

## Task 5: Extend `_EXTRACTOR_INSTRUCTIONS` with Step 1b

**Files:**
- Modify: `src/web_scout/tools.py`

- [ ] **Step 5.1: Add Step 1b to `_EXTRACTOR_INSTRUCTIONS`**

Find `_EXTRACTOR_INSTRUCTIONS` (the multi-line string). Find the line:

```
## Step 2 — Check for a primary source document
```

Insert the following block immediately before it:

```
## Step 1b — Handle thin content with interaction
If raw_scrape returned fewer than 500 characters of meaningful content
AND the page is not a document (PDF/DOCX/PPTX/XLSX):

1. Call list_interactive_elements() to see what is clickable on the page.
2. If the list contains tabs, buttons, or controls likely to reveal data
   relevant to the research query, call click_element(n) for the most
   promising element.
3. Use the updated content. You may call click_element up to 5 times total.
4. If content remains thin after clicking all promising elements, proceed
   with what you have.

Do NOT call list_interactive_elements() if raw_scrape already returned
rich content — interaction is a fallback, not a default.

```

- [ ] **Step 5.2: Verify the instruction string is syntactically valid Python**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent \
  python -c "from web_scout.tools import _EXTRACTOR_INSTRUCTIONS; print(len(_EXTRACTOR_INSTRUCTIONS), 'chars OK')"
```

Expected: prints a char count with `OK`.

---

## Task 6: Run full test suite and commit

**Files:** none

- [ ] **Step 6.1: Run all unit tests**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent \
  pytest tests/test_scrape_tool_dedupe.py tests/test_interactive_tools.py \
         tests/test_scraping_routing.py tests/test_url_utils.py \
         tests/test_hub_detection.py tests/test_followup_reranker.py -v
```

Expected: all tests PASS.

- [ ] **Step 6.2: Commit**

```bash
git add src/web_scout/tools.py \
        tests/test_interactive_tools.py \
        tests/test_scrape_tool_dedupe.py \
        docs/superpowers/specs/2026-04-20-interactive-browser-navigation-design.md \
        docs/superpowers/plans/2026-04-20-interactive-browser-navigation.md
git commit -m "feat: add interactive browser navigation to content extractor

Adds list_interactive_elements() and click_element() tools to the
extractor sub-agent. When raw_scrape returns thin content (<500 chars),
the extractor can now click tabs, buttons, and load-more controls to
reveal hidden data on JS-heavy portals (e.g. FAO data portals, national
statistics sites). Uses the Element Reference Pattern: pre-numbers all
visible DOM elements so the LLM picks by index, avoiding hallucinated
selectors. Max 5 clicks per page. No new dependencies (Playwright already
installed via crawl4ai). No public API changes."
```
