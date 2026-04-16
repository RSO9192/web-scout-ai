# Quality Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three quality problems identified by the benchmark: synthesis hallucination when scraped content is thin, scientific publishers blocked that should be accessible, and no failure context passed to the synthesiser.

**Architecture:** Two independent changes to the library source (`src/web_scout/`): (1) strengthen `SYNTHESISER_INSTRUCTIONS` and enrich the synthesis prompt with failure context in `agent.py`; (2) remove open-access and abstract-available scientific publishers from `_BLOCKED_DOMAINS` in `scraping.py`. Both changes are tested with unit tests running against `src/` via `PYTHONPATH=src`.

**Tech Stack:** Python, pytest; no new dependencies.

---

## Background

The benchmark run (`tests/benchmark_results/quality_benchmark_20260416_122713.md`) shows:
- Synthesis quality scored 1–2/5 on 6/8 queries despite 3–4/5 URL relevance and comprehensiveness. Root cause: `gemini-3-flash-preview` pads thin extracts with training-data facts.
- Ethiopia query: 1 scraped source (ends at 2022), synthesiser hallucinated 2023 yield figures not in any extract.
- Venice, Kenya, FAOSTAT: 2–4 policy-blocked URLs per query from `nature.com`, `researchgate.net`, `frontiersin.org`, `mdpi.com` — all open-access or abstract-available.

The installed package (`gee_llm` env, v1.0.3) is what the benchmark runs against. Unit tests can run against `src/` using `PYTHONPATH=src`. After changes are verified, the user installs via `conda run -p /path/to/env pip install -e .` or re-runs the benchmark with `PYTHONPATH=src python tests/quality_benchmark.py`.

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `src/web_scout/agent.py` | Modify | `SYNTHESISER_INSTRUCTIONS` (stronger no-hallucination rules); `synth_prompt` builder (adds failure context + thin-coverage warning) |
| `src/web_scout/scraping.py` | Modify | Remove 6 open-access / abstract-available domains from `_BLOCKED_DOMAINS` |
| `tests/test_synthesis_grounding.py` | Create | Unit tests for instruction content and synth_prompt enrichment |
| `tests/test_scraping_routing.py` | Modify | Add assertions that previously-blocked domains are now unblocked |

---

### Task 1: Strengthen synthesis grounding

**Files:**
- Modify: `src/web_scout/agent.py:76-88` (SYNTHESISER_INSTRUCTIONS)
- Modify: `src/web_scout/agent.py:1004-1046` (synth_prompt builder)
- Create: `tests/test_synthesis_grounding.py`

- [ ] **Step 1: Write failing tests first**

Create `tests/test_synthesis_grounding.py`:

```python
"""Tests for synthesis grounding — instruction content and prompt enrichment."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from web_scout.agent import SYNTHESISER_INSTRUCTIONS, _build_synth_prompt
from web_scout.models import UrlEntry


# ---------------------------------------------------------------------------
# SYNTHESISER_INSTRUCTIONS content
# ---------------------------------------------------------------------------

def test_instructions_forbid_training_data():
    """Must explicitly say no training data."""
    assert "training" in SYNTHESISER_INSTRUCTIONS.lower()
    assert "not" in SYNTHESISER_INSTRUCTIONS.lower()


def test_instructions_require_gap_reporting():
    """Must tell the model to report gaps rather than fill them."""
    lower = SYNTHESISER_INSTRUCTIONS.lower()
    assert "gap" in lower or "not found" in lower or "did not contain" in lower


def test_instructions_mention_thin_coverage():
    """Must warn about thin coverage."""
    lower = SYNTHESISER_INSTRUCTIONS.lower()
    assert "thin" in lower or "few source" in lower or "limited" in lower


# ---------------------------------------------------------------------------
# _build_synth_prompt enrichment
# ---------------------------------------------------------------------------

def _make_entry(url: str, content: str = "") -> UrlEntry:
    return UrlEntry(url=url, content=content)


def test_synth_prompt_includes_source_count():
    """Prompt must state how many sources were scraped."""
    scraped = [_make_entry("https://a.com", "content a")]
    prompt = _build_synth_prompt(
        query="test query",
        scraped=scraped,
        snippet_only=[],
        bot_detected=[],
        blocked_by_policy=[],
        scrape_failed=[],
        source_http_error=[],
        domain_expertise=None,
    )
    assert "1" in prompt  # source count


def test_synth_prompt_includes_failure_context_when_failures_exist():
    """Prompt must list failed/blocked URLs when present."""
    scraped = [_make_entry("https://a.com", "content")]
    bot = [_make_entry("https://bot.com", "[bot detection: blocked]")]
    blocked = [_make_entry("https://nature.com/paper", "[blocked by policy]")]
    prompt = _build_synth_prompt(
        query="test",
        scraped=scraped,
        snippet_only=[],
        bot_detected=bot,
        blocked_by_policy=blocked,
        scrape_failed=[],
        source_http_error=[],
        domain_expertise=None,
    )
    assert "bot.com" in prompt
    assert "nature.com" in prompt
    assert "could not" in prompt.lower() or "not be accessed" in prompt.lower()


def test_synth_prompt_thin_coverage_warning_when_few_sources():
    """Prompt must include a thin-coverage warning when fewer than 3 sources scraped."""
    scraped = [_make_entry("https://a.com", "content")]
    prompt = _build_synth_prompt(
        query="test",
        scraped=scraped,
        snippet_only=[],
        bot_detected=[],
        blocked_by_policy=[],
        scrape_failed=[],
        source_http_error=[],
        domain_expertise=None,
    )
    lower = prompt.lower()
    assert "thin" in lower or "only 1" in lower or "limited" in lower


def test_synth_prompt_no_thin_warning_when_enough_sources():
    """No thin-coverage warning when 3+ sources scraped."""
    scraped = [
        _make_entry("https://a.com", "content a"),
        _make_entry("https://b.com", "content b"),
        _make_entry("https://c.com", "content c"),
    ]
    prompt = _build_synth_prompt(
        query="test",
        scraped=scraped,
        snippet_only=[],
        bot_detected=[],
        blocked_by_policy=[],
        scrape_failed=[],
        source_http_error=[],
        domain_expertise=None,
    )
    assert "thin" not in prompt.lower()


def test_synth_prompt_no_failure_section_when_no_failures():
    """When everything scraped fine, no failure section in prompt."""
    scraped = [_make_entry("https://a.com", "content")]
    prompt = _build_synth_prompt(
        query="test",
        scraped=scraped,
        snippet_only=[],
        bot_detected=[],
        blocked_by_policy=[],
        scrape_failed=[],
        source_http_error=[],
        domain_expertise=None,
    )
    assert "could not be accessed" not in prompt.lower()
    assert "bot-blocked" not in prompt.lower()
```

- [ ] **Step 2: Run tests — expect ImportError (function not yet defined)**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent \
  python -m pytest tests/test_synthesis_grounding.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name '_build_synth_prompt' from 'web_scout.agent'`

- [ ] **Step 3: Replace SYNTHESISER_INSTRUCTIONS in `src/web_scout/agent.py`**

Find and replace the `SYNTHESISER_INSTRUCTIONS` block at line 76–88:

```python
SYNTHESISER_INSTRUCTIONS = """\
You are a web research synthesiser. Your job is to read the extracted contents
from various web pages and produce a coherent narrative ``synthesis`` answering the query.

## Absolute rules — no exceptions

**NO TRAINING DATA.** Every specific fact, number, statistic, name, date, quota,
rate, or decision in your synthesis MUST be explicitly present in one of the provided
scraped sources. Do NOT recall, infer, or approximate from your own training knowledge.
This rule applies even when you are confident you know the answer from prior knowledge.

**REPORT GAPS, DO NOT FILL THEM.** When the sources do not contain a specific piece of
information the query asks for, write: "The available sources did not contain [missing item]."
Do not substitute related data, use approximate figures, or blend in background knowledge.
A synthesis that honestly reports gaps is more valuable than one that fills them silently.

**THIN COVERAGE.** If very few sources were scraped (the count appears in your prompt),
do not compensate with broader knowledge. Synthesize only what the sources contain and
explicitly state that coverage is limited.

## Format rules

- **Citation:** use inline markdown [Source Title](URL) after every factual claim.
  Every factual statement must be attributed to at least one source from your prompt.
- Lead with what was found; address the query directly.
- If sources contradict each other, note the contradiction explicitly.
- Do NOT cite URLs that appear in the "SOURCES THAT COULD NOT BE ACCESSED" section —
  those were never scraped and their content is unknown.
"""
```

- [ ] **Step 4: Extract synth_prompt builder into `_build_synth_prompt` in `src/web_scout/agent.py`**

Add this function just before the `run_web_research` function (around line 539):

```python
def _build_synth_prompt(
    query: str,
    scraped: list,
    snippet_only: list,
    bot_detected: list,
    blocked_by_policy: list,
    scrape_failed: list,
    source_http_error: list,
    domain_expertise: Optional[str],
) -> str:
    """Build the synthesis prompt from scraped content and failure context.

    Includes:
    - Scraped source JSON
    - Snippet-only JSON
    - Failure context (failed/blocked URLs) so the synthesiser knows what's missing
    - Source count and thin-coverage warning when fewer than 3 sources scraped
    """
    import json as _json

    scraped_json = [
        {"url": e.url, "title": e.title or e.url, "content": e.content}
        for e in scraped
    ]
    snippet_json = [
        {"url": e.url, "title": e.title or e.url, "snippet": e.content}
        for e in snippet_only
        if e.content
    ]

    prompt = f"Research Query: {query}\n\n"
    if domain_expertise:
        prompt += f"Domain Expertise: {domain_expertise}\n\n"

    # Source count and thin-coverage warning
    n = len(scraped)
    prompt += f"You have {n} successfully scraped source(s) to work with.\n"
    if n < 3:
        prompt += (
            f"⚠ THIN COVERAGE: Only {n} source(s) available. "
            "Synthesize ONLY what these sources contain. "
            "Explicitly state any data the query asks for that is NOT in these sources. "
            "Do NOT fill gaps from training knowledge.\n"
        )
    prompt += "\n"

    # Failure context
    failure_lines: list[str] = []
    for e in bot_detected:
        failure_lines.append(f"  - {e.url} [bot-blocked: content could not be read]")
    for e in blocked_by_policy:
        domain = urlparse(e.url).netloc.lower()
        failure_lines.append(f"  - {domain} [policy-blocked: not attempted]")
    for e in scrape_failed + source_http_error:
        failure_lines.append(f"  - {e.url} [failed: {(e.content or '')[:80]}]")
    if failure_lines:
        prompt += (
            "SOURCES THAT COULD NOT BE ACCESSED "
            "— do NOT cite these, do not assume what they contain:\n"
            + "\n".join(failure_lines[:10])
            + "\n\n"
        )

    # Scraped and snippet content
    if not scraped and not snippet_json:
        prompt += "(No sources were found. You must state that no evidence was found.)\n"
    else:
        if scraped_json:
            prompt += f"Scraped sources (full extracts):\n{_json.dumps(scraped_json, indent=2)}\n\n"
        if snippet_json:
            prompt += f"Additional sources (search snippets only):\n{_json.dumps(snippet_json, indent=2)}\n\n"

    prompt += "Provide the 'synthesis' of the findings directly answering the query.\n"
    return prompt
```

- [ ] **Step 5: Replace the inline synth_prompt block in `run_web_research` with a call to `_build_synth_prompt`**

Find the block starting at line ~1014 (`import json as _json` through `synth_prompt += "Provide the 'synthesis'..."`) and replace it with:

```python
    synth_prompt = _build_synth_prompt(
        query=query,
        scraped=scraped,
        snippet_only=snippet_only,
        bot_detected=bot_detected,
        blocked_by_policy=blocked_by_policy,
        scrape_failed=scrape_failed,
        source_http_error=source_http_error,
        domain_expertise=domain_expertise,
    )
```

The block being replaced is:
```python
    import json as _json

    scraped_json = [
        {"url": entry.url, "title": entry.title or entry.url, "content": entry.content}
        for entry in scraped
    ]
    snippet_json = [
        {"url": entry.url, "title": entry.title or entry.url, "snippet": entry.content}
        for entry in snippet_only
        if entry.content
    ]

    synth_prompt = f"Research Query: {query}\n\n"
    if domain_expertise:
        synth_prompt += f"Domain Expertise: {domain_expertise}\n\n"

    if not scraped and not snippet_json:
        synth_prompt += "(No sources were found. You must state that no evidence was found.)\n"
    else:
        if scraped_json:
            synth_prompt += f"Scraped sources (full extracts):\n{_json.dumps(scraped_json, indent=2)}\n\n"
        if snippet_json:
            synth_prompt += f"Additional sources (search snippets only):\n{_json.dumps(snippet_json, indent=2)}\n\n"

    synth_prompt += "Provide the 'synthesis' of the findings directly answering the query.\n"
```

- [ ] **Step 6: Run tests — expect all 8 to pass**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent \
  PYTHONPATH=src python -m pytest tests/test_synthesis_grounding.py -v
```

Expected output:
```
PASSED tests/test_synthesis_grounding.py::test_instructions_forbid_training_data
PASSED tests/test_synthesis_grounding.py::test_instructions_require_gap_reporting
PASSED tests/test_synthesis_grounding.py::test_instructions_mention_thin_coverage
PASSED tests/test_synthesis_grounding.py::test_synth_prompt_includes_source_count
PASSED tests/test_synthesis_grounding.py::test_synth_prompt_includes_failure_context_when_failures_exist
PASSED tests/test_synthesis_grounding.py::test_synth_prompt_thin_coverage_warning_when_few_sources
PASSED tests/test_synthesis_grounding.py::test_synth_prompt_no_thin_warning_when_enough_sources
PASSED tests/test_synthesis_grounding.py::test_synth_prompt_no_failure_section_when_no_failures
8 passed
```

- [ ] **Step 7: Run full existing test suite to check for regressions**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent \
  PYTHONPATH=src python -m pytest tests/ -v --ignore=tests/test_synthesis_grounding.py \
  --ignore=tests/test_quality_benchmark.py -q 2>&1 | tail -10
```

Expected: all existing tests pass (any count, 0 failures).

- [ ] **Step 8: Commit**

```bash
git add src/web_scout/agent.py tests/test_synthesis_grounding.py
git commit -m "feat: strengthen synthesis grounding — no-training-data rules and failure context in prompt"
```

---

### Task 2: Unblock open-access and abstract-available scientific publishers

**Files:**
- Modify: `src/web_scout/scraping.py:46-68` (`_BLOCKED_DOMAINS`)
- Modify: `tests/test_scraping_routing.py` (add assertions for unblocked domains)

The benchmark showed 2–4 policy-blocks per research query from these domains:
- `frontiersin.org` — fully open access (always readable)
- `mdpi.com` — fully open access
- `journals.plos.org` — fully open access
- `researchgate.net` — self-archived papers, often full text
- `nature.com` — abstract + key findings always available; was scraped successfully in v0.9.x benchmark
- `academic.oup.com` — many open-access papers

Keeping blocked (consistently paywalled, thin HTML):
`sciencedirect.com`, `springer.com`, `link.springer.com`, `wiley.com`, `onlinelibrary.wiley.com`, `cambridge.org`, `jstor.org`, `tandfonline.com`, `sagepub.com`

- [ ] **Step 1: Add tests for the unblocked domains**

In `tests/test_scraping_routing.py`, add these tests at the bottom of the file:

```python
# ---------------------------------------------------------------------------
# Blocked-domain policy — open-access publishers must NOT be blocked
# ---------------------------------------------------------------------------

def test_open_access_publishers_not_blocked():
    """Open-access journals must not be in the default block list."""
    from web_scout.scraping import _BLOCKED_DOMAINS
    open_access = [
        "frontiersin.org",
        "mdpi.com",
        "journals.plos.org",
    ]
    for domain in open_access:
        assert domain not in _BLOCKED_DOMAINS, (
            f"{domain} is open-access and should not be blocked"
        )


def test_abstract_available_publishers_not_blocked():
    """Publishers with accessible abstracts must not be in the default block list."""
    from web_scout.scraping import _BLOCKED_DOMAINS
    abstract_available = [
        "researchgate.net",
        "nature.com",
        "academic.oup.com",
    ]
    for domain in abstract_available:
        assert domain not in _BLOCKED_DOMAINS, (
            f"{domain} has accessible content and should not be blocked"
        )


def test_paywalled_publishers_remain_blocked():
    """Consistently paywalled publishers must stay blocked."""
    from web_scout.scraping import _BLOCKED_DOMAINS
    paywalled = [
        "sciencedirect.com",
        "springer.com",
        "link.springer.com",
        "wiley.com",
        "onlinelibrary.wiley.com",
        "jstor.org",
        "tandfonline.com",
        "sagepub.com",
        "cambridge.org",
    ]
    for domain in paywalled:
        assert domain in _BLOCKED_DOMAINS, (
            f"{domain} is paywalled and should stay blocked"
        )
```

- [ ] **Step 2: Run new tests — expect failure (domains still blocked)**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent \
  PYTHONPATH=src python -m pytest tests/test_scraping_routing.py::test_open_access_publishers_not_blocked \
  tests/test_scraping_routing.py::test_abstract_available_publishers_not_blocked -v
```

Expected: FAIL — `frontiersin.org` is blocked, `AssertionError`

- [ ] **Step 3: Remove the 6 domains from `_BLOCKED_DOMAINS` in `src/web_scout/scraping.py`**

Find `_BLOCKED_DOMAINS` (line ~46). Replace the full block with:

```python
_BLOCKED_DOMAINS = frozenset({
    # Social media and video platforms
    "youtube.com", "youtu.be",
    "twitter.com", "x.com",
    "facebook.com", "instagram.com",
    "linkedin.com", "tiktok.com",
    "reddit.com",
    # Search engines
    "scholar.google.com",
    # Consistently paywalled academic publishers (thin HTML, no useful content without subscription)
    "sciencedirect.com",
    "springer.com",
    "link.springer.com",
    "wiley.com",
    "onlinelibrary.wiley.com",
    "tandfonline.com",
    "sagepub.com",
    "cambridge.org",
    "jstor.org",
    # NOTE: open-access publishers (frontiersin.org, mdpi.com, journals.plos.org) and
    # abstract-available publishers (researchgate.net, nature.com, academic.oup.com)
    # are intentionally NOT blocked — they yield useful content for research queries.
})
```

- [ ] **Step 4: Run all scraping routing tests**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent \
  PYTHONPATH=src python -m pytest tests/test_scraping_routing.py -v -q 2>&1 | tail -15
```

Expected: all tests pass (including the 3 new ones and all pre-existing ones).

- [ ] **Step 5: Run the full test suite**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent \
  PYTHONPATH=src python -m pytest tests/ -q \
  --ignore=tests/test_quality_benchmark.py 2>&1 | tail -10
```

Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add src/web_scout/scraping.py tests/test_scraping_routing.py
git commit -m "feat: unblock open-access and abstract-available scientific publishers"
```

---

### Task 3: Smoke-test the improvements end-to-end

Run the quality benchmark on the two worst-performing queries to confirm the changes help before doing the full 8-query run.

**Files:** None — just execute and inspect.

- [ ] **Step 1: Run the Ethiopia query (worst case — was 1.3/5)**

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-agent \
  PYTHONPATH=src python tests/quality_benchmark.py --limit 3 2>&1 | tail -40
```

This runs queries 1–3 (Kenya, Food Security, Ethiopia). Watch for:
- Ethiopia (query 3): scrape count should increase (more sources from unblocked domains)
- Synthesis quality: should no longer hallucinate 2023 yield figures not in extracts — should say "sources did not contain 2023 data" instead

- [ ] **Step 2: Check the generated Markdown report**

```bash
ls -1 tests/benchmark_results/quality_benchmark_*.md | tail -1
```

Open the latest file and verify:
- Ethiopia synthesis does NOT cite `2,864 kg/ha` unless it actually appears in an extract
- Ethiopia synthesis explicitly states what 2023 data was not found
- The scrape breakdown shows fewer policy-blocked entries for queries that hit nature.com / frontiersin.org

- [ ] **Step 3: Commit results**

```bash
# Stage only the new result files, not the benchmark source
git add tests/benchmark_results/quality_benchmark_$(ls -1 tests/benchmark_results/ | grep quality | tail -1 | sed 's/\.md//')*.json \
        tests/benchmark_results/quality_benchmark_$(ls -1 tests/benchmark_results/ | grep quality | tail -1 | sed 's/\.md//')*.md 2>/dev/null || true
git commit -m "results: smoke test after quality improvements"
```

---

## Self-Review

**Spec coverage:**
- ✅ A — Synthesis hallucination: `SYNTHESISER_INSTRUCTIONS` strengthened (Task 1 step 3); `_build_synth_prompt` adds thin-coverage warning + failure context (Task 1 steps 4–5)
- ✅ B — Unblock publishers: 6 domains removed from `_BLOCKED_DOMAINS` (Task 2 step 3); paywalled ones kept (Task 2 step 3)
- ✅ C — Failure context in synthesis: included in `_build_synth_prompt` — bot-blocked, policy-blocked, failed URLs all surfaced to synthesiser (Task 1 step 4)
- ✅ Unit tests for all changes
- ✅ Regression check against full test suite after each task
- ✅ End-to-end smoke test on the worst-performing query

**Placeholder scan:** None. All code blocks are complete.

**Type consistency:** `_build_synth_prompt` is defined in Task 1 step 4 and called in Task 1 step 5 with matching signature. `_BLOCKED_DOMAINS` reference in tests matches the name in `scraping.py`. `UrlEntry` used in tests is imported from `web_scout.models`.

**Note on installation:** Changes live in `src/web_scout/`. The benchmark uses the installed package from `gee_llm`. To test end-to-end: run with `PYTHONPATH=src python tests/quality_benchmark.py`. To make changes permanent for all tools: the user installs `conda run -p /path/to/env pip install -e .`.
