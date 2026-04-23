# Pipeline Speed Optimisation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce standard-depth pipeline wall-clock time from ~250s to 35–55s for HTML-heavy queries by eliminating duplicate document fetches, increasing extractor concurrency, and skipping unnecessary second iterations.

**Architecture:** Three independent changes to `tools.py` and `agent.py`: (1) a shared `_doc_cache` dict passed into every `_build_extractor_agent` call so `scrape_linked_document` never re-fetches the same URL twice in a run; (2) `max_concurrent` default raised 3 → 6 so iteration-1 URLs all run in parallel; (3) an early-exit guard that breaks out of the iteration loop when ≥ 4 sources are already scraped, skipping the coverage-evaluator LLM call and the entire second iteration.

**Tech Stack:** Python 3.13, asyncio, pytest-asyncio, unittest.mock (all already in use).

---

## File Map

| Action | File | What changes |
| --- | --- | --- |
| Modify | `src/web_scout/tools.py` | Add `_doc_cache` param to `_build_extractor_agent`; wire cache in `scrape_linked_document`; create cache in `create_scrape_and_extract_tool`; raise `max_concurrent` default to 6 |
| Modify | `src/web_scout/agent.py` | Add early-exit guard before coverage-evaluator LLM call |
| Create | `tests/test_pipeline_speed.py` | All new tests for this feature |

---

### Task 1: Shared document cache in `scrape_linked_document`

**Files:**

- Modify: `src/web_scout/tools.py` lines 462, 510–537, 928
- Test: `tests/test_pipeline_speed.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pipeline_speed.py`:

```python
"""Tests for pipeline speed optimisations."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from agents.tool import ToolContext
from web_scout.tools import _build_extractor_agent, create_scrape_and_extract_tool


def _make_ctx(tool_name="scrape_linked_document"):
    return ToolContext(
        context=None, tool_name=tool_name,
        tool_call_id="test-id", tool_arguments="{}",
    )


def _find_tool(agent, name):
    for t in agent.tools:
        if getattr(t, "name", None) == name:
            return t
    raise AssertionError(f"Tool '{name}' not found")


# ---------------------------------------------------------------------------
# Task 1: shared document cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_linked_document_uses_cache_on_second_call():
    """scrape_linked_document fetches the document once; second call returns cached."""
    doc_cache: dict = {}
    agent, cleanup = _build_extractor_agent(
        model="dummy", query="fish production", url="https://example.org/page",
        wait_for=None, doc_cache=doc_cache,
    )
    tool = _find_tool(agent, "scrape_linked_document")

    doc_content = "Full report content about fish production. " * 30

    call_count = 0
    async def _fake_scrape_doc(url, **kwargs):
        nonlocal call_count
        call_count += 1
        return doc_content, "Fish Report 2024", None

    with (
        patch("web_scout.scraping._validate_url", AsyncMock(return_value=("SCRAPE_DOC", "document-by-url"))),
        patch("web_scout.scraping._scrape_document", _fake_scrape_doc),
    ):
        result1 = await tool.on_invoke_tool(_make_ctx(), '{"document_url": "https://fao.org/report.pdf"}')
        result2 = await tool.on_invoke_tool(_make_ctx(), '{"document_url": "https://fao.org/report.pdf"}')

    assert call_count == 1, "Document should be fetched only once"
    assert result1 == result2
    assert "fish production" in result1.lower() or "Fish Report" in result1

    await cleanup()


@pytest.mark.asyncio
async def test_scrape_linked_document_cache_shared_across_agents():
    """Two extractor agents sharing the same cache fetch a common doc only once."""
    doc_cache: dict = {}

    agent1, cleanup1 = _build_extractor_agent(
        model="dummy", query="fish production", url="https://example.org/page1",
        wait_for=None, doc_cache=doc_cache,
    )
    agent2, cleanup2 = _build_extractor_agent(
        model="dummy", query="fish production", url="https://example.org/page2",
        wait_for=None, doc_cache=doc_cache,
    )

    tool1 = _find_tool(agent1, "scrape_linked_document")
    tool2 = _find_tool(agent2, "scrape_linked_document")

    doc_content = "SOFIA 2024 report content. " * 30
    call_count = 0

    async def _fake_scrape_doc(url, **kwargs):
        nonlocal call_count
        call_count += 1
        return doc_content, "SOFIA 2024", None

    with (
        patch("web_scout.scraping._validate_url", AsyncMock(return_value=("SCRAPE_DOC", "document-by-url"))),
        patch("web_scout.scraping._scrape_document", _fake_scrape_doc),
    ):
        result1 = await tool1.on_invoke_tool(_make_ctx(), '{"document_url": "https://fao.org/sofia.pdf"}')
        result2 = await tool2.on_invoke_tool(_make_ctx(), '{"document_url": "https://fao.org/sofia.pdf"}')

    assert call_count == 1, "Two agents sharing cache must fetch the document only once"
    assert result1 == result2

    await cleanup1()
    await cleanup2()


@pytest.mark.asyncio
async def test_scrape_linked_document_no_cache_by_default():
    """_build_extractor_agent with no doc_cache still works (backward compatible)."""
    agent, cleanup = _build_extractor_agent(
        model="dummy", query="fish", url="https://example.org/page",
        wait_for=None,
    )
    tool = _find_tool(agent, "scrape_linked_document")

    async def _fake_scrape_doc(url, **kwargs):
        return "content " * 40, "Doc", None

    with (
        patch("web_scout.scraping._validate_url", AsyncMock(return_value=("SCRAPE_DOC", "ok"))),
        patch("web_scout.scraping._scrape_document", _fake_scrape_doc),
    ):
        result = await tool.on_invoke_tool(_make_ctx(), '{"document_url": "https://fao.org/doc.pdf"}')

    assert "content" in result
    await cleanup()
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest tests/test_pipeline_speed.py -k "cache" -v
```

Expected: 3 failures — `_build_extractor_agent` does not accept `doc_cache` parameter yet.

- [ ] **Step 3: Add `doc_cache` parameter to `_build_extractor_agent` signature**

In `src/web_scout/tools.py`, change the `_build_extractor_agent` signature at line 462:

```python
def _build_extractor_agent(model: Any, query: str, url: str, wait_for: Optional[str], vision_model: Optional[str] = None, allowed_domains: Optional[frozenset] = None, max_pdf_pages: int = 50, max_content_chars: int = 30_000, doc_cache: Optional[dict] = None) -> tuple:
```

- [ ] **Step 4: Wire the cache inside `scrape_linked_document`**

In `src/web_scout/tools.py`, replace the `scrape_linked_document` body (lines 510–537) with:

```python
    @function_tool
    async def scrape_linked_document(document_url: str) -> str:
        """Fetch and return the text content of a primary source document linked from the page.

        Use this when the page you scraped is a metadata or catalogue record
        (e.g. a FAOLEX law entry, a library repository page, a UN treaty record)
        that links to the actual primary document (a law text, full report, treaty PDF, etc.).

        Only call this for the single most important primary document — not for
        supplementary annexes, navigation links, or secondary references.
        Only accepts links that validate as real document resources, including
        extensionless download URLs that return document content-types.

        Args:
            document_url: Absolute URL of the primary source document to fetch.
        """
        norm = ResearchTracker._normalize_url(document_url)
        if doc_cache is not None and norm in doc_cache:
            return doc_cache[norm]

        verdict, detail = await _validate_url(document_url, allowed_domains=allowed_domains)
        if verdict != _SCRAPE_DOC:
            return (
                "[scrape_linked_document rejected: URL does not look like a primary "
                f"document ({detail}): {document_url}]"
            )
        content, title, error = await _scrape_document(document_url, query=query, vision_model=vision_model, max_pdf_pages=max_pdf_pages)
        if error:
            return f"[Document scrape failed: {error}]"
        if not content.strip():
            return "[Document returned empty content]"
        header = f"# {title}\nSource: {document_url}\n\n" if title else f"Source: {document_url}\n\n"
        result = header + content
        if doc_cache is not None:
            doc_cache[norm] = result
        return result
```

- [ ] **Step 5: Create and pass `_doc_cache` in `create_scrape_and_extract_tool`**

In `src/web_scout/tools.py`, find the line that creates the semaphore (line 859). Add `_doc_cache` immediately after:

```python
    semaphore = asyncio.Semaphore(max_concurrent)
    _doc_cache: dict = {}
    in_flight: Dict[str, asyncio.Future[str]] = {}
```

Then update the `_build_extractor_agent` call at line 928 to pass `doc_cache=_doc_cache`:

```python
                extractor_agent, extractor_cleanup = _build_extractor_agent(
                    extractor_model, query, url, _wait_for,
                    vision_model=vision_model,
                    allowed_domains=allowed_domains,
                    max_pdf_pages=max_pdf_pages,
                    max_content_chars=max_content_chars,
                    doc_cache=_doc_cache,
                )
```

- [ ] **Step 6: Run tests — confirm they pass**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest tests/test_pipeline_speed.py -k "cache" -v
```

Expected: 3 passed.

- [ ] **Step 7: Run full suite — confirm no regressions**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest --tb=short -q
```

Expected: all passing.

- [ ] **Step 8: Commit**

```bash
git add src/web_scout/tools.py tests/test_pipeline_speed.py
git commit -m "perf: add shared doc cache to scrape_linked_document to eliminate duplicate fetches"
```

---

### Task 2: Raise `max_concurrent` default 3 → 6

**Files:**

- Modify: `src/web_scout/tools.py` line 842
- Test: `tests/test_pipeline_speed.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline_speed.py`:

```python
# ---------------------------------------------------------------------------
# Task 2: max_concurrent default
# ---------------------------------------------------------------------------

import inspect
from web_scout.tools import create_scrape_and_extract_tool


def test_create_scrape_and_extract_tool_default_concurrency_is_6():
    """max_concurrent default must be 6 (not 3)."""
    sig = inspect.signature(create_scrape_and_extract_tool)
    default = sig.parameters["max_concurrent"].default
    assert default == 6, f"Expected max_concurrent default=6, got {default}"
```

- [ ] **Step 2: Run test — confirm it fails**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest tests/test_pipeline_speed.py::test_create_scrape_and_extract_tool_default_concurrency_is_6 -v
```

Expected: FAIL — `AssertionError: Expected max_concurrent default=6, got 3`

- [ ] **Step 3: Change the default in `tools.py`**

In `src/web_scout/tools.py` at line 842, change:

```python
    max_concurrent: int = 6,
```

(was `3`)

- [ ] **Step 4: Run test — confirm it passes**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest tests/test_pipeline_speed.py::test_create_scrape_and_extract_tool_default_concurrency_is_6 -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite — confirm no regressions**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest --tb=short -q
```

Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add src/web_scout/tools.py tests/test_pipeline_speed.py
git commit -m "perf: raise max_concurrent default from 3 to 6 for faster parallel extraction"
```

---

### Task 3: Early exit when ≥ 4 sources scraped after iteration 1

**Files:**

- Modify: `src/web_scout/agent.py` lines 1024–1030
- Test: `tests/test_pipeline_speed.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline_speed.py`:

```python
# ---------------------------------------------------------------------------
# Task 3: early exit from coverage evaluator
# ---------------------------------------------------------------------------

from web_scout.agent import run_web_research, DEFAULT_WEB_RESEARCH_MODELS
from web_scout.models import WebResearchResultRaw


def _patch_scrape_tool_with_sources(monkeypatch, n_sources: int):
    """Replace create_scrape_and_extract_tool with one that records n_sources in tracker."""
    import web_scout.agent as _agent_mod
    from web_scout.tools import ResearchTracker
    from web_scout.models import UrlEntry

    async def _fake_scrape(url: str) -> str:
        return f"Scraped content for {url}. " * 40

    original_create = _agent_mod.create_scrape_and_extract_tool

    def _fake_create(**kwargs):
        tracker: ResearchTracker = kwargs.get("tracker")

        async def _scrape_and_record(url: str, wait_for=None) -> str:
            content = f"Scraped content for {url}. " * 40
            if tracker is not None:
                norm = tracker._normalize_url(url)
                tracker._urls[norm] = UrlEntry(url=url, content=content, title="Test")
                tracker._actions[norm] = "scraped"
            return content

        return _scrape_and_record

    monkeypatch.setattr(_agent_mod, "create_scrape_and_extract_tool", _fake_create)


@pytest.mark.asyncio
async def test_pipeline_skips_coverage_eval_when_4_sources_scraped(monkeypatch):
    """Coverage evaluator must not be called when iteration 1 scraped ≥ 4 sources."""
    import web_scout.agent as _agent_mod

    _patch_scrape_tool_with_sources(monkeypatch, n_sources=4)

    evaluator_calls = []

    async def _fake_runner_run(agent_obj, prompt, **kwargs):
        agent_name = getattr(agent_obj, "name", "")
        if agent_name == "coverage_evaluator":
            evaluator_calls.append(prompt)
        return type("R", (), {"final_output_as": lambda self, t: WebResearchResultRaw(synthesis="ok")})()

    monkeypatch.setattr(_agent_mod.Runner, "run", _fake_runner_run)
    # Patch SerperBackend so the pipeline doesn't need a real API key
    from web_scout.search_backends import SearchResponse, SearchResult
    monkeypatch.setattr(
        "web_scout.agent.SerperBackend",
        lambda key: _FakeSerperBackend(),
    )
    monkeypatch.setenv("SERPER_API_KEY", "test-key")

    await run_web_research(
        query="fish production",
        models={"web_researcher": "dummy", "content_extractor": "dummy"},
        search_backend="serper",
    )

    assert evaluator_calls == [], (
        f"Coverage evaluator should not be called when ≥4 sources scraped, "
        f"but was called {len(evaluator_calls)} time(s)"
    )


@pytest.mark.asyncio
async def test_pipeline_runs_coverage_eval_when_fewer_than_4_sources(monkeypatch):
    """Coverage evaluator must still run when iteration 1 scraped < 4 sources."""
    import web_scout.agent as _agent_mod

    _patch_scrape_tool_with_sources(monkeypatch, n_sources=2)

    evaluator_calls = []

    async def _fake_runner_run(agent_obj, prompt, **kwargs):
        agent_name = getattr(agent_obj, "name", "")
        if agent_name == "coverage_evaluator":
            evaluator_calls.append(prompt)
            from web_scout.agent import CoverageEvaluation
            return type("R", (), {
                "final_output_as": lambda self, t: CoverageEvaluation(
                    fully_answered=True, gaps="", promising_unscraped_urls=[], needs_new_searches=False
                )
            })()
        return type("R", (), {"final_output_as": lambda self, t: WebResearchResultRaw(synthesis="ok")})()

    monkeypatch.setattr(_agent_mod.Runner, "run", _fake_runner_run)
    monkeypatch.setattr(
        "web_scout.agent.SerperBackend",
        lambda key: _FakeSerperBackend(),
    )
    monkeypatch.setenv("SERPER_API_KEY", "test-key")

    await run_web_research(
        query="fish production",
        models={"web_researcher": "dummy", "content_extractor": "dummy"},
        search_backend="serper",
    )

    assert len(evaluator_calls) >= 1, "Coverage evaluator must run when < 4 sources scraped"


class _FakeSerperBackend:
    """Minimal Serper stub that returns a handful of fake results."""
    async def search(self, query, max_results=10, include_domains=None):
        from web_scout.search_backends import SearchResponse, SearchResult
        results = [
            SearchResult(title=f"Result {i}", url=f"https://example.org/result-{i}", snippet="data")
            for i in range(min(max_results, 4))
        ]
        return SearchResponse(results=results, related_searches=[])
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest tests/test_pipeline_speed.py -k "coverage_eval" -v
```

Expected: both tests fail — the early exit does not exist yet.

- [ ] **Step 3: Add early-exit guard in `agent.py`**

In `src/web_scout/agent.py`, find the coverage evaluation block starting at line 1024. Add the early-exit check immediately after the `if not scraped_entries:` block (after line 1030):

```python
            # 2g. Evaluate Coverage
            if iteration < depth["max_iterations"] - 1:
                scraped_entries = tracker.build_result_groups()["scraped"]
                if not scraped_entries:
                    logger.info("[pipeline] 0 successful scrapes, doing another iteration")
                    missing_info = "Everything, no successful scrapes yet."
                    needs_new_searches = True
                    continue

                if len(scraped_entries) >= 4:
                    logger.info(
                        "[pipeline] %d sources scraped after iteration %d — skipping coverage evaluation",
                        len(scraped_entries), iteration + 1,
                    )
                    break

                eval_prompt = f"Research Query: {query}\n\nScraped Content:\n"
                # ... rest of existing coverage evaluation code unchanged ...
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest tests/test_pipeline_speed.py -k "coverage_eval" -v
```

Expected: 2 passed.

- [ ] **Step 5: Run full suite — confirm no regressions**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent pytest --tb=short -q
```

Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add src/web_scout/agent.py tests/test_pipeline_speed.py
git commit -m "perf: skip coverage evaluator when iteration 1 already scraped >=4 sources"
```

---

### Task 4: Live timing verification

**Files:** None modified — verification only.

- [ ] **Step 1: Run the timing probe**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent bash -c '
set -a && source /Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/report/.env && set +a
cd /Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai
PYTHONPATH=src python /tmp/timing_probe.py 2>&1 | grep -v "Loading weights\|LiteLLM\|litellm\|crawl4ai\|docling\|httpx\|httpcore\|playwright\|asyncio\|aiohttp\|WARNING\|INFO\|DEBUG"
'
```

The timing probe at `/tmp/timing_probe.py` already exists from the profiling session. If it has been lost, recreate it with:

```python
"""Profile where time goes in a single open-web research run."""
import asyncio, time, logging, os, sys
sys.path.insert(0, 'src')

from dotenv import load_dotenv
load_dotenv('/Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/report/.env')

from web_scout import scraping as _scraping
_orig_scrape = _scraping.scrape_url
_scrape_times = []
async def _timed_scrape(url, *a, **kw):
    t = time.perf_counter()
    r = await _orig_scrape(url, *a, **kw)
    _scrape_times.append((url[:60], time.perf_counter() - t, len(r[0])))
    return r
_scraping.scrape_url = _timed_scrape

from agents import Runner as _Runner
_llm_times = []
_orig_run = _Runner.run
async def _timed_run(agent, prompt, *a, **kw):
    t = time.perf_counter()
    r = await _orig_run(agent, prompt, *a, **kw)
    _llm_times.append((getattr(agent, 'name', '?'), time.perf_counter() - t))
    return r
_Runner.run = _timed_run

from web_scout.agent import run_web_research, DEFAULT_WEB_RESEARCH_MODELS
logging.basicConfig(level=logging.WARNING)

async def main():
    t0 = time.perf_counter()
    result = await run_web_research(
        query="global fish capture production trends 2022 statistics",
        models=DEFAULT_WEB_RESEARCH_MODELS,
        search_backend="serper",
        research_depth="standard",
    )
    total = time.perf_counter() - t0
    print(f"\nTOTAL: {total:.1f}s")
    print(f"\nSCRAPE TIMES ({len(_scrape_times)} calls):")
    for url, t, chars in sorted(_scrape_times, key=lambda x: -x[1]):
        print(f"  {t:5.1f}s  {chars:6d}ch  {url}")
    print(f"\nLLM TIMES ({len(_llm_times)} calls):")
    for name, t in _llm_times:
        print(f"  {t:5.1f}s  {name}")
    print(f"\nSources scraped: {len(result.scraped)}, failed: {len(result.scrape_failed)}")

asyncio.run(main())
```

**Expected improvements vs baseline (249s):**

- Duplicate `scrape_linked_document` calls: eliminated (call count drops, same result)
- `content_extractor` LLM call count: reduced (no iteration 2 if ≥4 sources)
- Wall-clock total: 35–80s depending on whether slow PDFs are in the result set

- [ ] **Step 2: Run quality probe to confirm output quality is preserved**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent bash -c '
set -a && source /Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/report/.env && set +a
cd /Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai
PYTHONPATH=src python tests/quality_probe.py 2>&1
'
```

Expected: average score ≥ 7.5/10 (same quality as pre-optimisation, faster delivery).
