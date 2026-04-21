# SPA Fragment & Form-Contamination Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect when `raw_scrape` returns a SPA fragment URL or form-contaminated content and append structured signals so the extractor LLM calls `list_interactive_elements` to find the real data.

**Architecture:** Two pure detection functions (`_has_fragment`, `_is_form_contaminated`) are added to `tools.py`. The `raw_scrape` closure is extended to append signal strings when either condition fires. The extractor instructions gain a second trigger block that reacts to these signals. No changes to `_validate_url`, `_scrape_url`, or the public API.

**Tech Stack:** Python 3.13, pytest-asyncio, unittest.mock (already in use in the project).

---

## File Map

| Action | File | What changes |
|---|---|---|
| Modify | `src/web_scout/tools.py` | Add `_has_fragment`, `_is_form_contaminated`; extend `raw_scrape`; update `_EXTRACTOR_INSTRUCTIONS` |
| Create | `tests/test_spa_form_detection.py` | All new tests for this feature |

---

### Task 1: `_has_fragment` — pure URL fragment detector

**Files:**
- Modify: `src/web_scout/tools.py` (add function before `_build_extractor_agent` at line 424)
- Test: `tests/test_spa_form_detection.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_spa_form_detection.py`:

```python
"""Tests for SPA fragment and form-contamination detection in raw_scrape."""

from web_scout.tools import _has_fragment


def test_has_fragment_detects_hash_fragment():
    assert _has_fragment("https://www.fao.org/faostat/en/#data/QCL") is True


def test_has_fragment_false_for_plain_url():
    assert _has_fragment("https://fao.org/fishery/en") is False


def test_has_fragment_false_for_empty_fragment():
    # URL ending with # but no fragment content
    assert _has_fragment("https://fao.org/page#") is False


def test_has_fragment_false_for_query_string_only():
    assert _has_fragment("https://fao.org/search?q=fish") is False


def test_has_fragment_detects_spa_anchor():
    assert _has_fragment("https://example.com/app#/dashboard/stats") is True
```

- [ ] **Step 2: Run test — confirm it fails**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest tests/test_spa_form_detection.py -v
```

Expected: `ImportError: cannot import name '_has_fragment' from 'web_scout.tools'`

- [ ] **Step 3: Implement `_has_fragment` in `tools.py`**

Add immediately before the `_build_extractor_agent` function (line 424), after the existing module-level constants:

```python
def _has_fragment(url: str) -> bool:
    """True if the URL contains a non-empty #fragment (SPA client-side routing)."""
    from urllib.parse import urlparse
    return bool(urlparse(url).fragment)
```

- [ ] **Step 4: Run test — confirm it passes**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest tests/test_spa_form_detection.py::test_has_fragment_detects_hash_fragment tests/test_spa_form_detection.py::test_has_fragment_false_for_plain_url tests/test_spa_form_detection.py::test_has_fragment_false_for_empty_fragment tests/test_spa_form_detection.py::test_has_fragment_false_for_query_string_only tests/test_spa_form_detection.py::test_has_fragment_detects_spa_anchor -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/web_scout/tools.py tests/test_spa_form_detection.py
git commit -m "feat: add _has_fragment detector for SPA URLs"
```

---

### Task 2: `_is_form_contaminated` — survey/nav-only content detector

**Files:**
- Modify: `src/web_scout/tools.py` (add function after `_has_fragment`)
- Test: `tests/test_spa_form_detection.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_spa_form_detection.py`:

```python
from web_scout.tools import _is_form_contaminated


def test_is_form_contaminated_detects_strongly_agree_repetition():
    content = (
        "National Statistical Institutes\n"
        "* Strongly Agree\n"
        "* Strongly Agree\n"
        "* Strongly Agree\n"
        "Kindly provide details on your response.\n"
    )
    assert _is_form_contaminated(content) is True


def test_is_form_contaminated_detects_kindly_provide_repetition():
    content = (
        "Please rate our service.\n"
        "Kindly provide your feedback.\n"
        "Kindly provide details.\n"
        "Thank you.\n"
    )
    assert _is_form_contaminated(content) is True


def test_is_form_contaminated_false_for_normal_article():
    content = (
        "Fish production increased by 3% in 2023 according to FAO data.\n"
        "The report covers 180 countries and includes aquaculture statistics.\n"
        "Global capture fisheries reached 91 million tonnes.\n"
        "Aquaculture contributed an additional 88 million tonnes.\n"
    ) * 5
    assert _is_form_contaminated(content) is False


def test_is_form_contaminated_detects_nav_only_bullet_page():
    # >20 lines, >75% bullet points — navigation dump
    lines = ["* " + f"Nav link {i}" for i in range(25)]
    content = "\n".join(lines)
    assert _is_form_contaminated(content) is True


def test_is_form_contaminated_false_for_mixed_content_with_bullets():
    # Some bullets mixed with prose — below 75% threshold
    lines = (
        ["Fish production rose in 2023."] * 8
        + ["* " + f"Related link {i}" for i in range(5)]
        + ["The data covers 180 countries and includes aquaculture."] * 7
    )
    content = "\n".join(lines)
    assert _is_form_contaminated(content) is False


def test_is_form_contaminated_false_for_short_survey_content():
    # Under 20 lines — not enough data to apply bullet-ratio rule
    lines = ["* " + f"Bullet {i}" for i in range(10)]
    content = "\n".join(lines)
    # No repeated survey tokens either
    assert _is_form_contaminated(content) is False
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest tests/test_spa_form_detection.py -k "form_contaminated" -v
```

Expected: `ImportError: cannot import name '_is_form_contaminated' from 'web_scout.tools'`

- [ ] **Step 3: Implement `_is_form_contaminated` in `tools.py`**

Add immediately after `_has_fragment`:

```python
_FORM_TOKENS = (
    "strongly agree", "strongly disagree", "please rate",
    "kindly provide", "please provide", "select an option",
)

def _is_form_contaminated(content: str) -> bool:
    """True if content is dominated by survey/form patterns despite char count > 500.

    Triggers on either:
    - A survey token appearing 2+ times (case-insensitive).
    - 20+ lines where >75% are bullet-point lines (nav-only dump).
    """
    lower = content.lower()
    if any(lower.count(tok) >= 2 for tok in _FORM_TOKENS):
        return True
    lines = [l for l in content.splitlines() if l.strip()]
    if len(lines) >= 20:
        bullet_lines = sum(1 for l in lines if l.strip().startswith(("* ", "- ")))
        if bullet_lines / len(lines) > 0.75:
            return True
    return False
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest tests/test_spa_form_detection.py -k "form_contaminated" -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/web_scout/tools.py tests/test_spa_form_detection.py
git commit -m "feat: add _is_form_contaminated detector for survey/nav-only content"
```

---

### Task 3: Signal injection in `raw_scrape`

**Files:**
- Modify: `src/web_scout/tools.py` (`raw_scrape` closure inside `_build_extractor_agent`, lines 440–455)
- Test: `tests/test_spa_form_detection.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_spa_form_detection.py`:

```python
from unittest.mock import AsyncMock, patch
import pytest
from agents.tool import ToolContext
from web_scout.tools import _build_extractor_agent

_THIN = 500  # _THIN_CONTENT_CHARS


def _make_ctx():
    return ToolContext(
        context=None, tool_name="raw_scrape",
        tool_call_id="test-id", tool_arguments="{}",
    )


def _make_agent(url="https://example.org/portal"):
    return _build_extractor_agent(model="dummy", query="fish production statistics",
                                   url=url, wait_for=None)


def _fake_scrape_result(content: str, title: str = "Test Page", error=None):
    """Return a mock for scraping._scrape_url that yields fixed values."""
    async def _mock(*args, **kwargs):
        return content, title, error
    return _mock


@pytest.mark.asyncio
async def test_raw_scrape_appends_spa_signal_for_fragment_url():
    """Fragment URL → SPA signal appended to output."""
    agent, cleanup = _make_agent(url="https://fao.org/faostat/en/#data/QCL")
    tool = next(t for t in agent.tools if getattr(t, "name", None) == "raw_scrape")
    rich_content = "Fish data content. " * 40  # well above 500 chars

    with patch("web_scout.tools._build_extractor_agent.__code__",
               agent.tools[0].__dict__):  # no-op — we patch _scrape_url below
        pass

    with patch("web_scout.scraping.scrape_url", _fake_scrape_result(rich_content)):
        result = await tool.on_invoke_tool(_make_ctx(), "{}")

    assert "[SPA: URL fragment detected" in result
    await cleanup()


@pytest.mark.asyncio
async def test_raw_scrape_appends_form_signal_for_survey_content():
    """Survey content > 500 chars → form signal appended to output."""
    agent, cleanup = _make_agent(url="https://fao.org/faostat/en/")
    tool = next(t for t in agent.tools if getattr(t, "name", None) == "raw_scrape")
    survey_content = (
        "National Statistical Institutes\n"
        "* Strongly Agree\n" * 5
        + "Some nav content. " * 30
    )
    assert len(survey_content) >= 500

    with patch("web_scout.scraping.scrape_url", _fake_scrape_result(survey_content)):
        result = await tool.on_invoke_tool(_make_ctx(), "{}")

    assert "[Form/survey content detected" in result
    await cleanup()


@pytest.mark.asyncio
async def test_raw_scrape_appends_both_signals_for_faostat_like_url():
    """Fragment URL + survey content → both signals appear."""
    agent, cleanup = _make_agent(url="https://fao.org/faostat/en/#data/QCL")
    tool = next(t for t in agent.tools if getattr(t, "name", None) == "raw_scrape")
    survey_content = (
        "Crops and livestock products\n"
        "* Strongly Agree\n" * 5
        + "More content. " * 30
    )

    with patch("web_scout.scraping.scrape_url", _fake_scrape_result(survey_content)):
        result = await tool.on_invoke_tool(_make_ctx(), "{}")

    assert "[SPA: URL fragment detected" in result
    assert "[Form/survey content detected" in result
    await cleanup()


@pytest.mark.asyncio
async def test_raw_scrape_no_signal_for_normal_rich_content():
    """Normal rich content with no fragment → no signals appended."""
    agent, cleanup = _make_agent(url="https://fao.org/fishery/en")
    tool = next(t for t in agent.tools if getattr(t, "name", None) == "raw_scrape")
    normal_content = (
        "Fish production increased by 3% in 2023 according to FAO. "
        "Aquaculture reached 88 million tonnes globally. "
    ) * 40

    with patch("web_scout.scraping.scrape_url", _fake_scrape_result(normal_content)):
        result = await tool.on_invoke_tool(_make_ctx(), "{}")

    assert "[SPA:" not in result
    assert "[Form/survey" not in result
    await cleanup()


@pytest.mark.asyncio
async def test_raw_scrape_no_form_signal_when_content_under_500_chars():
    """Form detection is skipped when content < 500 chars (already thin)."""
    agent, cleanup = _make_agent(url="https://fao.org/fishery/en")
    tool = next(t for t in agent.tools if getattr(t, "name", None) == "raw_scrape")
    thin_survey = "* Strongly Agree\n" * 3  # repeated token but < 500 chars

    with patch("web_scout.scraping.scrape_url", _fake_scrape_result(thin_survey)):
        result = await tool.on_invoke_tool(_make_ctx(), "{}")

    assert "[Form/survey content detected" not in result
    await cleanup()
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest tests/test_spa_form_detection.py -k "raw_scrape" -v
```

Expected: 5 failures — signals not yet in output.

- [ ] **Step 3: Extend `raw_scrape` in `tools.py`**

Replace the current `raw_scrape` body (lines 440–455) with:

```python
    @function_tool
    async def raw_scrape() -> str:
        """Fetch and return the full content of the pre-set URL.

        The URL is determined by the outer research task — no argument needed.
        Validates the URL first (skips dead links, empty pages, binary files).
        Works with static HTML, JS-rendered pages, JSON endpoints, images,
        PDFs, DOCX, PPTX, and XLSX.
        """
        content, title, error = await _scrape_url(url, wait_for, query=query, vision_model=vision_model, allowed_domains=allowed_domains, max_pdf_pages=max_pdf_pages, max_content_chars=max_content_chars)
        if error:
            return f"[Scrape failed: {error}]"
        if not content.strip():
            return "[Page returned empty content]"
        header = f"# {title}\nSource: {url}\n\n" if title else f"Source: {url}\n\n"

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

- [ ] **Step 4: Run tests — confirm they pass**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest tests/test_spa_form_detection.py -k "raw_scrape" -v
```

Expected: 5 passed.

- [ ] **Step 5: Run full suite — confirm no regressions**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest --tb=short -q
```

Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add src/web_scout/tools.py tests/test_spa_form_detection.py
git commit -m "feat: inject SPA and form-contamination signals into raw_scrape output"
```

---

### Task 4: Update extractor instructions

**Files:**
- Modify: `src/web_scout/tools.py` (`_EXTRACTOR_INSTRUCTIONS` string, around lines 357–370)
- Test: `tests/test_spa_form_detection.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_spa_form_detection.py`:

```python
from web_scout.tools import _EXTRACTOR_INSTRUCTIONS


def test_extractor_instructions_mention_spa_signal():
    """Instructions must tell the LLM to react to the SPA signal string."""
    assert "[SPA: URL fragment detected" in _EXTRACTOR_INSTRUCTIONS


def test_extractor_instructions_mention_form_signal():
    """Instructions must tell the LLM to react to the form signal string."""
    assert "[Form/survey content detected" in _EXTRACTOR_INSTRUCTIONS
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest tests/test_spa_form_detection.py::test_extractor_instructions_mention_spa_signal tests/test_spa_form_detection.py::test_extractor_instructions_mention_form_signal -v
```

Expected: 2 failures — strings not yet in instructions.

- [ ] **Step 3: Update `_EXTRACTOR_INSTRUCTIONS` in `tools.py`**

Replace the existing Step 1b block (lines 357–370):

```python
## Step 1b — Handle thin content or low-quality content with interaction
If raw_scrape returned fewer than 500 characters of meaningful content
AND the page is not a document (PDF/DOCX/PPTX/XLSX):

1. Call list_interactive_elements() to see what is clickable on the page.
2. If the list contains tabs, buttons, or controls likely to reveal data
   relevant to the research query, call click_element(n) for the most
   promising element.
3. Use the updated content. You may call click_element up to 5 times total.
4. If content remains thin after clicking all promising elements, proceed
   with what you have.

Also call list_interactive_elements() if raw_scrape returned a message containing:
- "[SPA: URL fragment detected" — the page uses client-side routing; the visible
  content may be the wrong tab or view. Look for tabs, dropdowns, or section
  selectors that navigate to the target data.
- "[Form/survey content detected" — the page loaded a feedback widget instead of
  data. Look for data tabs, dropdowns, or navigation controls that reveal the
  actual content.
In both cases, click the most promising element and use the updated content.

Do NOT call list_interactive_elements() if raw_scrape already returned
rich content with no signals — interaction is a fallback, not a default.
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest tests/test_spa_form_detection.py -v
```

Expected: all tests in the file pass.

- [ ] **Step 5: Run full suite — confirm no regressions**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest --tb=short -q
```

Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add src/web_scout/tools.py tests/test_spa_form_detection.py
git commit -m "feat: extend extractor instructions to react to SPA and form signals"
```

---

### Task 5: Live smoke test against FAOSTAT

**Files:** None modified — this is a verification step only.

- [ ] **Step 1: Run live probe against the FAOSTAT URL**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent bash -c '
set -a && source /Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/report/.env && set +a
python -c "
import asyncio
from web_scout.scraping import scrape_url
from web_scout.tools import _has_fragment, _is_form_contaminated

async def probe():
    url = \"https://www.fao.org/faostat/en/#data/QCL\"
    content, title, error = await scrape_url(url, query=\"crop production statistics\")
    print(f\"Fragment detected: {_has_fragment(url)}\")
    print(f\"Form contaminated: {_is_form_contaminated(content) if len(content) >= 500 else False} (len={len(content)})\")
    print(f\"Error: {error!r}\")
    print(f\"Content preview: {content[:200]!r}\")

asyncio.run(probe())
" 2>&1 | grep -v "LiteLLM\|litellm\|DEBUG\|crawl4ai\|docling\|httpx\|httpcore\|asyncio\|aiohttp\|playwright"
'
```

Expected output:
```
Fragment detected: True
Form contaminated: True (len=4400)
Error: None
Content preview: '...'
```

Both detectors firing confirms the fix is end-to-end correct for the FAOSTAT case. The extractor LLM will now receive both signal strings appended to the content and will call `list_interactive_elements` instead of treating the survey noise as data.

- [ ] **Step 2: Run full suite one final time with API keys loaded**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent bash -c '
set -a && source /Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/report/.env && set +a
pytest --tb=short -q
'
```

Expected: all tests pass (including live scraping tests).
