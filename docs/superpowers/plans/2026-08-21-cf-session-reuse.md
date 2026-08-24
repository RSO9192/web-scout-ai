# Cloudflare Session Reuse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-URL throwaway browser launches with per-host shared `AsyncStealthySession` browsers (single-flight creation, page pooling, global concurrency cap) so Cloudflare-protected domains survive parallel pipeline load.

**Architecture:** A new module `_stealth_session.py` owns a per-event-loop registry of long-lived scrapling sessions keyed by URL host, with an asyncio.Lock per host (single-flight creation), a global semaphore capping concurrent browser work at 4, and LRU eviction of idle sessions above 3. `stealthy_fetch` in `_scrapling.py` keeps its exact public API and routes through the new module, so all six call sites benefit without modification. `run_web_research` closes sessions in a `finally`.

**Tech Stack:** Python 3.13, asyncio, scrapling 0.4.14 (`AsyncStealthySession` from `scrapling.engines._browsers._stealth`), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-08-21-cf-session-reuse-design.md`

## Global Constraints

- Run all tests with: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout python -m pytest <path> -v` (never `pip install` / `poetry add` anything — user installs dependencies).
- No new dependencies. No config plumbing — tuning values are module-level constants.
- `stealthy_fetch(url, **kwargs)` public API must not change: `solve_cloudflare` defaults to True, exceptions propagate to callers, the scrapling `< 0.4.9` RuntimeError guard stays.
- Do NOT change `browser_page_timeout_ms` (60_000) and do NOT touch `BLOCKED_DOMAINS` — both are explicitly out of scope (spec).
- Existing tests (notably `tests/test_scraping_routing.py`, which monkeypatches `_scrapling.stealthy_fetch`) must keep passing unmodified.
- Work on a feature branch: `git checkout dev && git pull && git checkout -b feat/cf-session-reuse` before Task 1.

---

### Task 1: Session manager module (`_stealth_session.py`)

**Files:**
- Create: `src/web_scout/scraping/_stealth_session.py`
- Test: `tests/test_stealth_session.py`

**Interfaces:**
- Consumes: `scrapling.engines._browsers._stealth.AsyncStealthySession` (has `async start()`, `async close()`, `async fetch(url, **StealthFetchParams)`, constructor takes `max_pages`, `headless`, `retries`, ...).
- Produces (used by Task 2 and Task 3):
  - `async def fetch_via_session(url: str, **kwargs) -> Any` — fetch through the host's shared session; splits kwargs into session-constructor keys vs per-fetch keys.
  - `async def close_stealthy_sessions() -> None` — close every session belonging to the current event loop.
  - `def _session_factory(**kwargs)` — indirection point that imports and constructs `AsyncStealthySession`; tests monkeypatch this.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stealth_session.py`:

```python
"""Unit tests for the per-host shared stealth session manager."""

import asyncio

import pytest

from web_scout.scraping import _stealth_session as ss


class FakeSession:
    """Stands in for scrapling's AsyncStealthySession."""

    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        self.fetches = []
        self.fetch_gate = None  # set to an asyncio.Event to make fetch block
        self.fail_fetch = False
        FakeSession.instances.append(self)

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True

    async def fetch(self, url, **kw):
        self.fetches.append((url, kw))
        if self.fetch_gate is not None:
            await self.fetch_gate.wait()
        if self.fail_fetch:
            raise RuntimeError("boom")
        return f"resp:{url}"


@pytest.fixture(autouse=True)
def fake_factory(monkeypatch):
    FakeSession.instances = []
    monkeypatch.setattr(ss, "_session_factory", lambda **kw: FakeSession(**kw))
    yield
    FakeSession.instances = []


@pytest.mark.asyncio
async def test_concurrent_same_host_creates_one_session():
    urls = [f"https://onlinelibrary.wiley.com/doi/10.1111/x{i}" for i in range(6)]
    results = await asyncio.gather(*(ss.fetch_via_session(u) for u in urls))
    assert len(FakeSession.instances) == 1
    assert FakeSession.instances[0].started
    assert sorted(results) == sorted(f"resp:{u}" for u in urls)
    await ss.close_stealthy_sessions()


@pytest.mark.asyncio
async def test_sequential_calls_reuse_session():
    await ss.fetch_via_session("https://onlinelibrary.wiley.com/a")
    await ss.fetch_via_session("https://onlinelibrary.wiley.com/b")
    assert len(FakeSession.instances) == 1
    assert len(FakeSession.instances[0].fetches) == 2
    await ss.close_stealthy_sessions()


@pytest.mark.asyncio
async def test_kwarg_split_session_vs_fetch():
    await ss.fetch_via_session(
        "https://example.org/x",
        headless=True,
        retries=1,
        network_idle=True,
        solve_cloudflare=True,
        timeout=60_000,
        wait_selector="#main",
    )
    session = FakeSession.instances[0]
    # constructor got session-level keys plus the pool size
    assert session.kwargs["headless"] is True
    assert session.kwargs["retries"] == 1
    assert session.kwargs["max_pages"] == ss.SESSION_MAX_PAGES
    assert "wait_selector" not in session.kwargs
    # fetch got only per-fetch keys
    _, fetch_kw = session.fetches[0]
    assert fetch_kw == {
        "network_idle": True,
        "solve_cloudflare": True,
        "timeout": 60_000,
        "wait_selector": "#main",
    }
    await ss.close_stealthy_sessions()


@pytest.mark.asyncio
async def test_fetch_error_evicts_session():
    await ss.fetch_via_session("https://example.org/ok")
    FakeSession.instances[0].fail_fetch = True
    with pytest.raises(RuntimeError, match="boom"):
        await ss.fetch_via_session("https://example.org/bad")
    assert FakeSession.instances[0].closed
    # next call gets a brand-new session
    await ss.fetch_via_session("https://example.org/again")
    assert len(FakeSession.instances) == 2
    await ss.close_stealthy_sessions()


@pytest.mark.asyncio
async def test_lru_eviction_caps_idle_sessions():
    hosts = ["a.org", "b.org", "c.org", "d.org"]
    for h in hosts:
        await ss.fetch_via_session(f"https://{h}/page")
    # cap is 3: the least-recently-used idle session (a.org) was closed
    assert len(FakeSession.instances) == 4
    assert FakeSession.instances[0].closed
    assert not any(s.closed for s in FakeSession.instances[1:])
    await ss.close_stealthy_sessions()


@pytest.mark.asyncio
async def test_busy_sessions_are_not_evicted():
    # pre-create the three sessions with a quick ungated fetch each,
    # THEN gate them, so the slow fetches are guaranteed to block inside
    for h in ["a.org", "b.org", "c.org"]:
        await ss.fetch_via_session(f"https://{h}/warm")
    gates = []
    for s in FakeSession.instances:
        s.fetch_gate = asyncio.Event()
        gates.append(s.fetch_gate)
    tasks = [
        asyncio.create_task(ss.fetch_via_session(f"https://{h}/slow"))
        for h in ["a.org", "b.org", "c.org"]
    ]
    await asyncio.sleep(0.01)  # let the fetches enter their sessions
    # all 3 sessions busy; a 4th host must overshoot the cap, not kill a busy one
    await ss.fetch_via_session("https://d.org/page")
    assert not any(s.closed for s in FakeSession.instances[:3])
    for g in gates:
        g.set()
    await asyncio.gather(*tasks)
    await ss.close_stealthy_sessions()


@pytest.mark.asyncio
async def test_semaphore_caps_concurrent_browser_fetches():
    peak = 0
    running = 0

    class CountingSession(FakeSession):
        async def fetch(self, url, **kw):
            nonlocal peak, running
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.02)
            running -= 1
            return f"resp:{url}"

    FakeSession.instances = []
    ss._session_factory = lambda **kw: CountingSession(**kw)
    # 3 hosts x 3 urls = 9 concurrent fetch attempts, cap is MAX_CONCURRENT_BROWSER_FETCHES
    urls = [f"https://h{i}.org/p{j}" for i in range(3) for j in range(3)]
    await asyncio.gather(*(ss.fetch_via_session(u) for u in urls))
    assert peak <= ss.MAX_CONCURRENT_BROWSER_FETCHES
    await ss.close_stealthy_sessions()


@pytest.mark.asyncio
async def test_close_stealthy_sessions_closes_everything():
    await ss.fetch_via_session("https://a.org/1")
    await ss.fetch_via_session("https://b.org/1")
    await ss.close_stealthy_sessions()
    assert all(s.closed for s in FakeSession.instances)
    # registry is empty: a new call creates a new session
    await ss.fetch_via_session("https://a.org/2")
    assert len(FakeSession.instances) == 3
    await ss.close_stealthy_sessions()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout python -m pytest tests/test_stealth_session.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'web_scout.scraping._stealth_session'`

- [ ] **Step 3: Implement the module**

Create `src/web_scout/scraping/_stealth_session.py`:

```python
"""Per-host shared stealth browser sessions (private).

Replaces the one-browser-per-fetch pattern: the first fetch for a host
launches an ``AsyncStealthySession`` (patchright Chromium) and passes the
bot wall; subsequent fetches for the same host reuse pooled pages inside
that browser, where the fingerprint/cookies that satisfied Cloudflare live.

Design (see docs/superpowers/specs/2026-08-21-cf-session-reuse-design.md):
- single-flight: a per-host lock guards session *creation* only
- a global semaphore caps concurrent browser fetches; it is acquired
  before ``session.fetch`` so queue wait never consumes the page timeout
- at most MAX_SESSIONS idle sessions per event loop (LRU-evicted);
  busy sessions are never evicted, the cap overshoots instead
- sessions are per-event-loop (a Playwright object is unusable from
  another loop); pipeline teardown calls ``close_stealthy_sessions()``
"""

import asyncio
import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Dict
from urllib.parse import urlparse
from weakref import WeakKeyDictionary

logger = logging.getLogger(__name__)

MAX_SESSIONS = 3
MAX_CONCURRENT_BROWSER_FETCHES = 4
SESSION_MAX_PAGES = 3

# Keys accepted only by the AsyncStealthySession constructor, not by
# session.fetch() (scrapling 0.4.14 StealthSession vs StealthFetchParams).
_SESSION_ONLY_KEYS = frozenset({"headless", "max_pages", "retries", "retry_delay"})


def _session_factory(**kwargs):
    """Construct an AsyncStealthySession. Indirection point for tests."""
    from scrapling.engines._browsers._stealth import AsyncStealthySession

    return AsyncStealthySession(**kwargs)


@dataclass
class _Entry:
    session: Any
    last_used: int
    in_flight: int = 0


@dataclass
class _LoopState:
    sessions: Dict[str, _Entry] = field(default_factory=dict)
    locks: Dict[str, asyncio.Lock] = field(default_factory=dict)
    semaphore: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(MAX_CONCURRENT_BROWSER_FETCHES)
    )
    counter: Any = field(default_factory=itertools.count)


_registry: "WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopState]" = WeakKeyDictionary()


def _loop_state() -> _LoopState:
    loop = asyncio.get_running_loop()
    state = _registry.get(loop)
    if state is None:
        state = _LoopState()
        _registry[loop] = state
    return state


async def _close_entry(host: str, entry: _Entry) -> None:
    try:
        await entry.session.close()
    except Exception as exc:  # closing must never mask the original failure
        logger.debug("[stealth-session] close failed for %s: %s", host, exc)


async def _evict_idle_lru(state: _LoopState) -> None:
    idle = [(e.last_used, h) for h, e in state.sessions.items() if e.in_flight == 0]
    if not idle:
        # Every session is mid-fetch: overshoot the cap rather than kill one.
        return
    _, host = min(idle)
    entry = state.sessions.pop(host)
    logger.info("[stealth-session] evicting idle session for %s", host)
    await _close_entry(host, entry)


async def _get_or_create(state: _LoopState, host: str, session_kwargs: dict) -> _Entry:
    lock = state.locks.setdefault(host, asyncio.Lock())
    async with lock:
        entry = state.sessions.get(host)
        if entry is None:
            while len(state.sessions) >= MAX_SESSIONS:
                before = len(state.sessions)
                await _evict_idle_lru(state)
                if len(state.sessions) == before:
                    break  # all busy — accept overshoot
            session = _session_factory(max_pages=SESSION_MAX_PAGES, **session_kwargs)
            async with state.semaphore:  # a launch is browser work too
                await session.start()
            entry = _Entry(session=session, last_used=next(state.counter))
            state.sessions[host] = entry
            logger.info("[stealth-session] new browser session for %s", host)
        entry.last_used = next(state.counter)
        return entry


async def fetch_via_session(url: str, **kwargs: Any):
    """Fetch *url* through the shared browser session for its host.

    Session-constructor kwargs (headless, retries, ...) apply on first
    creation for the host; per-fetch kwargs are passed to every fetch.
    On fetch failure the session is closed and evicted so the next call
    for this host starts fresh; the exception propagates to the caller.
    """
    host = urlparse(url).netloc.lower()
    state = _loop_state()
    session_kwargs = {k: v for k, v in kwargs.items() if k in _SESSION_ONLY_KEYS}
    fetch_kwargs = {k: v for k, v in kwargs.items() if k not in _SESSION_ONLY_KEYS}

    entry = await _get_or_create(state, host, session_kwargs)
    entry.in_flight += 1
    try:
        async with state.semaphore:
            result = await entry.session.fetch(url, **fetch_kwargs)
    except Exception:
        if state.sessions.get(host) is entry:
            state.sessions.pop(host, None)
            await _close_entry(host, entry)
        raise
    finally:
        entry.in_flight -= 1
    entry.last_used = next(state.counter)
    return result


async def close_stealthy_sessions() -> None:
    """Close every stealth session belonging to the current event loop."""
    loop = asyncio.get_running_loop()
    state = _registry.pop(loop, None)
    if state is None:
        return
    for host, entry in list(state.sessions.items()):
        await _close_entry(host, entry)
    state.sessions.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout python -m pytest tests/test_stealth_session.py -v`
Expected: 8 passed. If `pytest.mark.asyncio` errors appear, check how existing async tests are marked in `tests/test_scraping_routing.py` and mirror that convention (the project already tests async code; do not add new dependencies).

- [ ] **Step 5: Commit**

```bash
git add src/web_scout/scraping/_stealth_session.py tests/test_stealth_session.py
git commit -m "feat: per-host shared stealth browser sessions with single-flight creation"
```

---

### Task 2: Route `stealthy_fetch` through the session manager

**Files:**
- Modify: `src/web_scout/scraping/_scrapling.py` (whole file is 33 lines)
- Test: `tests/test_stealth_session.py` (append two tests)

**Interfaces:**
- Consumes: `fetch_via_session(url, **kwargs)` from Task 1.
- Produces: `stealthy_fetch(url, **kwargs)` — identical public API as today; all six call sites (`_fetcher.py`, `_html.py`, `_json.py`, `_image.py`, `_document.py`, `_download.py`, `_vision.py`) stay untouched.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stealth_session.py`:

```python
@pytest.mark.asyncio
async def test_stealthy_fetch_defaults_solve_cloudflare_and_delegates(monkeypatch):
    from web_scout.scraping import _scrapling

    seen = {}

    async def fake_fetch_via_session(url, **kw):
        seen["url"] = url
        seen["kw"] = kw
        return "page"

    monkeypatch.setattr(_scrapling, "fetch_via_session", fake_fetch_via_session)
    result = await _scrapling.stealthy_fetch("https://example.org/x", timeout=1000)
    assert result == "page"
    assert seen["url"] == "https://example.org/x"
    assert seen["kw"]["solve_cloudflare"] is True
    assert seen["kw"]["timeout"] == 1000


@pytest.mark.asyncio
async def test_stealthy_fetch_maps_old_scrapling_typeerror(monkeypatch):
    from web_scout.scraping import _scrapling

    async def fake_fetch_via_session(url, **kw):
        raise TypeError("unexpected keyword argument 'solve_cloudflare'")

    monkeypatch.setattr(_scrapling, "fetch_via_session", fake_fetch_via_session)
    with pytest.raises(RuntimeError, match="0.4.9"):
        await _scrapling.stealthy_fetch("https://example.org/x")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout python -m pytest tests/test_stealth_session.py -v -k stealthy_fetch`
Expected: FAIL — `_scrapling` has no attribute `fetch_via_session` yet.

- [ ] **Step 3: Rewrite `_scrapling.py`**

Replace the body of `src/web_scout/scraping/_scrapling.py` with:

```python
"""Thin wrappers around Scrapling fetchers (private).

Centralises stealth browser fetches with ``solve_cloudflare=True`` always
enabled.  Fetches are routed through per-host shared browser sessions
(``_stealth_session``) instead of launching one browser per call.
Requires Scrapling >= 0.4.9; raises ``RuntimeError`` on older installs
that do not support the ``solve_cloudflare`` keyword.
"""

import logging
from typing import Any

from ._stealth_session import fetch_via_session

logger = logging.getLogger(__name__)


async def stealthy_fetch(url: str, **kwargs: Any):
    """Fetch *url* via the host's shared stealth session, ``solve_cloudflare=True``.

    Raises ``RuntimeError`` when the installed Scrapling version does not
    support ``solve_cloudflare`` (requires >= 0.4.9).
    """
    kwargs.setdefault("solve_cloudflare", True)

    try:
        return await fetch_via_session(url, **kwargs)
    except TypeError as exc:
        if "solve_cloudflare" in str(exc):
            raise RuntimeError(
                "Scrapling >= 0.4.9 is required for solve_cloudflare support. "
                "Run: pip install 'scrapling[fetchers]>=0.4.9'"
            ) from exc
        raise
```

- [ ] **Step 4: Run the new tests AND the full suite**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout python -m pytest tests/ -v`
Expected: all pass — in particular `tests/test_scraping_routing.py` (it monkeypatches `_scrapling.stealthy_fetch` directly, so it must be unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/web_scout/scraping/_scrapling.py tests/test_stealth_session.py
git commit -m "feat: route stealthy_fetch through shared per-host sessions"
```

---

### Task 3: Pipeline teardown

**Files:**
- Modify: `src/web_scout/agent.py` (function `run_web_research`, defined at line 220 — wrap the mode dispatch near lines 337–360)
- Test: `tests/test_stealth_session.py` (append one test)

**Interfaces:**
- Consumes: `close_stealthy_sessions()` from Task 1.
- Produces: nothing new — `run_web_research`'s signature and return are unchanged.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_stealth_session.py`:

```python
def test_run_web_research_closes_sessions_in_finally():
    """Teardown guard: the pipeline entry point must always close sessions."""
    import inspect

    from web_scout import agent

    src = inspect.getsource(agent.run_web_research)
    assert "close_stealthy_sessions" in src
    assert "finally" in src
```

(A source-inspection test is deliberate: running the real pipeline needs live models/network. The behavioural coverage for `close_stealthy_sessions` itself is in Task 1.)

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout python -m pytest tests/test_stealth_session.py::test_run_web_research_closes_sessions_in_finally -v`
Expected: FAIL — `close_stealthy_sessions` not present in `run_web_research`.

- [ ] **Step 3: Add the finally block**

In `src/web_scout/agent.py`, inside `run_web_research`, wrap the existing mode dispatch (the `if direct_url: await _run_direct_url_mode(...) else: await _run_search_mode(...)` block) in try/finally:

```python
    from web_scout.scraping._stealth_session import close_stealthy_sessions

    try:
        if direct_url:
            await _run_direct_url_mode(
                query=query,
                direct_url=direct_url,
                tracker=tracker,
                scrape_tool=scrape_tool,
                depth=depth,
                followup_model=followup_model,
            )
        else:
            await _run_search_mode(
                query=query,
                include_domains=include_domains,
                search_backend=search_backend,
                domain_expertise=domain_expertise,
                depth=depth,
                query_gen_model=query_gen_model,
                evaluator_model=evaluator_model,
                followup_model=followup_model,
                tracker=tracker,
                scrape_tool=scrape_tool,
                exclude_domains=_excluded,
                evaluator_extra_prompt=evaluator_extra_prompt,
            )
    finally:
        await close_stealthy_sessions()
```

Keep the existing keyword arguments exactly as they appear in the current file (copy them from the file, not from this plan, if they have drifted). Only the `try:`/`finally:` wrapper and the import are new lines.

- [ ] **Step 4: Run the full suite**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout python -m pytest tests/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/web_scout/agent.py tests/test_stealth_session.py
git commit -m "feat: close shared stealth sessions on pipeline teardown"
```

---

### Task 4: Live verification + changelog

**Files:**
- Modify: `CHANGELOG.md` (new entry at top, following the existing entry format in the file)
- No new source files. Verification scripts already exist: `/private/tmp/ws_wiley_concurrency_test.py`, `/private/tmp/webscout_domain_test.py`.

**Interfaces:**
- Consumes: the complete feature from Tasks 1–3.
- Produces: evidence that the concurrency failure is fixed; changelog entry.

- [ ] **Step 1: Smoke-test a real shared-session fetch (any network)**

Run:

```bash
conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout python -c "
import asyncio
from web_scout.scraping._scrapling import stealthy_fetch
from web_scout.scraping._stealth_session import close_stealthy_sessions, _registry

async def main():
    r1 = await stealthy_fetch('https://onlinelibrary.wiley.com/', headless=True, network_idle=True, timeout=120_000)
    r2 = await stealthy_fetch('https://onlinelibrary.wiley.com/journal/13652486', headless=True, network_idle=True, timeout=120_000)
    print('statuses:', r1.status, r2.status)
    loop_state = list(_registry.values())[0]
    print('sessions created:', len(loop_state.sessions), '(expect 1)')
    await close_stealthy_sessions()

asyncio.run(main())
"
```

Expected: both statuses 200, `sessions created: 1`, and the second fetch is dramatically faster than the first (it reuses the browser). If status 403 on the second fetch, the session context is not being reused — stop and debug before proceeding.

- [ ] **Step 2: Concurrency verification — REQUIRES STABLE NETWORK**

The user flagged that network instability inflates absolute timings. Confirm with the user that the connection is stable before running; otherwise defer this step (and note it as deferred in the PR/commit message).

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout python /private/tmp/webscout_domain_test.py 2>&1 | tee /private/tmp/ws_acceptance_run.log`

Expected (per spec acceptance): wiley.com scrape count under 4-way parallelism matches its solo baseline (previously 0 vs 8/8), and `grep -c "falling back to StealthyFetcher" /private/tmp/ws_acceptance_run.log` shows the wiley fallback count collapsed to ~1–2 (one per wiley host, not one per URL).

- [ ] **Step 3: Add changelog entry**

Add at the top of `CHANGELOG.md`, matching the file's existing entry style:

```markdown
## Unreleased

### Fixed
- Cloudflare-protected domains (e.g. onlinelibrary.wiley.com) no longer fail
  under parallel pipeline load. Browser fetches now reuse one shared stealth
  session per host (single-flight creation, page pooling, max 4 concurrent
  browser fetches) instead of launching a fresh browser per URL, which
  inflated per-fetch latency past the 60 s page timeout under contention.
```

- [ ] **Step 4: Final full-suite run and commit**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout python -m pytest tests/ -v`
Expected: all pass.

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for shared stealth session reuse"
```

---

## Follow-ups (explicitly NOT in this plan)

1. **`BLOCKED_DOMAINS` removal** (`src/web_scout/scraping/constants.py:19-24`) — only after Task 4 Step 2 passes on a stable network, as its own reviewed change (wiley + tandfonline + sagepub are candidates; the ESSapp report pipeline drops its `EXCLUDED_DOMAINS` mirror at the same time).
2. **Timeout tuning** (`browser_page_timeout_ms`) — re-measure solo solve times on a stable connection first; today's 128–227 s Wiley solves were network-inflated.
3. **Opportunistic `cf_clearance` HTTP replay** — refuted for Wiley (no wiley-scoped clearance is ever issued; fingerprint-gated). Revisit only if a domain appears that both issues a domain-scoped clearance and challenges the curl_cffi path.
