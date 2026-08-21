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
        self.fail_start = False
        FakeSession.instances.append(self)

    async def start(self):
        if self.fail_start:
            raise RuntimeError("launch failed")
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
    assert "retries" not in session.kwargs
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
async def test_start_failure_closes_and_evicts_session(monkeypatch):
    def failing_factory(**kw):
        session = FakeSession(**kw)
        session.fail_start = True
        return session

    monkeypatch.setattr(ss, "_session_factory", failing_factory)
    with pytest.raises(RuntimeError, match="launch failed"):
        await ss.fetch_via_session("https://example.org/x")
    assert FakeSession.instances[0].closed

    # host absent from registry: a fresh factory produces a brand-new session
    monkeypatch.setattr(ss, "_session_factory", lambda **kw: FakeSession(**kw))
    result = await ss.fetch_via_session("https://example.org/y")
    assert result == "resp:https://example.org/y"
    assert len(FakeSession.instances) == 2
    assert FakeSession.instances[1].started
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


def test_run_web_research_closes_sessions_in_finally():
    """Teardown guard: the pipeline entry point must always release sessions."""
    import inspect

    from web_scout import agent

    src = inspect.getsource(agent.run_web_research)
    assert "acquire_stealth_sessions" in src
    assert "release_stealth_sessions" in src
    assert "finally" in src


@pytest.mark.asyncio
async def test_release_closes_only_when_last_pipeline_exits():
    ss.acquire_stealth_sessions()
    ss.acquire_stealth_sessions()
    await ss.fetch_via_session("https://a.org/1")
    await ss.release_stealth_sessions()
    assert not FakeSession.instances[0].closed  # sibling still active
    await ss.release_stealth_sessions()
    assert FakeSession.instances[0].closed  # last one out closes
