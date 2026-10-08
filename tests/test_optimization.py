"""Correctness checks for readiness, resource ownership and Jev decisions."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from web_scout import _classification as classification
from web_scout.scraping._readiness import wait_for_content


@pytest.mark.asyncio
async def test_readiness_waits_for_content_then_stability_and_selector():
    states = iter(
        [
            {"ready": False, "signature": "challenge"},
            {"ready": False, "signature": "shell"},
            {"ready": True, "signature": "first"},
            {"ready": True, "signature": "complete"},
            {"ready": True, "signature": "complete"},
        ]
    )
    page = SimpleNamespace(
        wait_for_load_state=AsyncMock(),
        wait_for_selector=AsyncMock(),
        evaluate=AsyncMock(side_effect=lambda _: next(states)),
    )
    elapsed = await wait_for_content(page, selector="#data", timeout_ms=2000)
    assert page.evaluate.await_count == 5
    page.wait_for_selector.assert_awaited_once_with("#data", state="visible", timeout=2000)
    assert elapsed >= 0.5


@pytest.mark.asyncio
async def test_unready_page_times_out_without_reading():
    page = SimpleNamespace(
        wait_for_load_state=AsyncMock(), evaluate=AsyncMock(return_value={"ready": False, "signature": ""})
    )
    with pytest.raises(TimeoutError):
        await wait_for_content(page, timeout_ms=20)


@pytest.mark.asyncio
async def test_visual_readiness_waits_for_fonts_and_visible_images():
    page = SimpleNamespace(
        wait_for_load_state=AsyncMock(),
        evaluate=AsyncMock(
            side_effect=[
                {"ready": True, "signature": "article"},
                {"ready": True, "signature": "article"},
                None,
            ]
        ),
    )
    await wait_for_content(page, visual=True)
    assert "document.fonts.ready" in page.evaluate.call_args.args[0]


def test_default_backend_and_missing_credentials_are_hard_errors(monkeypatch):
    monkeypatch.delenv("WEB_SCOUT_CLASSIFICATION_BACKEND")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert classification.backend() == "jev"
    with pytest.raises(classification.ClassificationError, match="TYPESAFE_API_KEY"):
        classification.require_credentials()
    monkeypatch.setenv("WEB_SCOUT_CLASSIFICATION_BACKEND", "unknown")
    with pytest.raises(classification.ClassificationError):
        classification.backend()


@pytest.mark.asyncio
async def test_coverage_only_accepts_strong_source_support(monkeypatch):
    captured = []

    async def judge(state, questions):
        captured.append(state)
        return {"r0": 0.99, "r1": 0.79} if "requirements" in state else {"c0": 0.9}

    monkeypatch.setattr(classification, "judge", judge)
    answered, gaps, urls = await classification.coverage(
        "query", ["country", "year"], [{"text": "country"}], [{"url": "https://a.test", "snippet": "year found"}]
    )
    assert not answered and gaps == "year"
    assert urls == ["https://a.test"]
    assert "candidates" not in captured[0]


@pytest.mark.asyncio
async def test_verification_checks_actual_pages_and_rejects_fabricated_quotes(monkeypatch):
    from web_scout.tools.pdf_extractor import PdfEvidenceItem

    questions_seen = []

    async def judge(state, questions):
        questions_seen.extend(state["items"])
        return {key: 0.95 if "rose" in state["items"][i]["claim"] else 0.1 for i, key in enumerate(questions)}

    monkeypatch.setattr(classification, "judge", judge)
    evidence = [
        PdfEvidenceItem(text=text, page_start=1, page_end=1)
        for text in (
            "Output rose by 5%.",
            "Output fell by 5%.",
            'It says "Output rose by 99%".',
        )
    ]
    kept = await classification.supported_evidence(evidence, {1: "Output rose by 5%."})
    assert kept == evidence[:1]
    assert len(questions_seen) == 2
    assert all(item["source"] == "Output rose by 5%." for item in questions_seen)


@pytest.mark.asyncio
async def test_judgment_failure_propagates_and_client_is_reused(monkeypatch):
    from typesafe_sdk import TypeSafeAPITimeoutError

    calls = []

    class Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        async def system_one(self, state, questions):
            raise TypeSafeAPITimeoutError("timeout")

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(classification, "AsyncTypeSafeClient", Client)
    for _ in range(2):
        with pytest.raises(classification.ClassificationError):
            await classification.judge({}, {"question": object()})
    assert len(calls) == 1
    assert 404 in calls[0]["retry"].http_statuses
    assert calls[0]["retry"].max_retries == 2


@pytest.mark.asyncio
async def test_failed_browser_does_not_close_siblings(monkeypatch):
    from web_scout.scraping import _stealth_session as pool

    gate, entered = asyncio.Event(), asyncio.Event()

    class Session:
        closed = False

        async def start(self):
            pass

        async def close(self):
            self.closed = True

        async def fetch(self, url, **kwargs):
            if url.endswith("slow"):
                entered.set()
                await gate.wait()
                assert not self.closed
                return "ok"
            raise RuntimeError("failure")

    session = Session()
    monkeypatch.setattr(pool, "_session_factory", lambda **_: session)
    slow = asyncio.create_task(pool.fetch_via_session("https://sibling.test/slow"))
    await entered.wait()
    with pytest.raises(RuntimeError):
        await pool.fetch_via_session("https://sibling.test/fail")
    assert not session.closed
    gate.set()
    assert await slow == "ok"
    assert session.closed
    await pool.close_stealthy_sessions()


@pytest.mark.asyncio
async def test_browser_wrapper_readies_before_action(monkeypatch):
    from web_scout.scraping import _scrapling

    order = []

    async def ready(*args, **kwargs):
        order.append("ready")

    async def action(page):
        order.append("read")

    async def fetch(url, **kwargs):
        assert kwargs["network_idle"] is False
        assert kwargs["wait"] == 0
        await kwargs["page_action"](object())
        return "response"

    monkeypatch.setattr("web_scout.scraping._readiness.wait_for_content", ready)
    monkeypatch.setattr(_scrapling, "fetch_via_session", fetch)
    assert await _scrapling.stealthy_fetch("https://a.test", page_action=action) == "response"
    assert order == ["ready", "read"]


@pytest.mark.asyncio
async def test_interactive_browser_reuse_idle_eviction_and_active_shutdown(monkeypatch):
    from web_scout.scraping import _resources as pool

    contexts = []
    browser = SimpleNamespace(is_connected=lambda: True, close=AsyncMock())

    async def new_context(**kwargs):
        context = SimpleNamespace(close=AsyncMock())
        contexts.append(context)
        return context

    browser.new_context = new_context
    launcher = SimpleNamespace(launch=AsyncMock(return_value=browser))
    manager = SimpleNamespace(
        __aenter__=AsyncMock(return_value=SimpleNamespace(chromium=launcher)), __aexit__=AsyncMock()
    )
    monkeypatch.setattr(pool, "async_playwright", lambda: manager)
    async with pool.interactive_context():
        with pytest.raises(RuntimeError, match="active"):
            await pool.close_resources()
        await pool._close_idle_browser(pool.resources())
        browser.close.assert_not_awaited()
    async with pool.interactive_context():
        assert len(contexts) == 2
    launcher.launch.assert_awaited_once()
    assert all(context.close.await_count == 1 for context in contexts)
    await pool._close_idle_browser(pool.resources())
    browser.close.assert_awaited_once()
    await pool.close_resources()


@pytest.mark.asyncio
async def test_selector_readiness_does_not_require_long_text():
    page = SimpleNamespace(
        wait_for_load_state=AsyncMock(),
        wait_for_selector=AsyncMock(),
        evaluate=AsyncMock(return_value={"ready": True, "signature": "42"}),
    )
    await wait_for_content(page, selector="#answer")
    probe = page.evaluate.call_args.args[0]
    assert 'document.querySelector("#answer")' in probe
    assert "visible(requested)" in probe


@pytest.mark.asyncio
async def test_invalid_probabilities_are_hard_errors(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def system_one(self, state, questions):
            return SimpleNamespace(nouls={key: SimpleNamespace(noul=float("nan")) for key in questions})

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(classification, "AsyncTypeSafeClient", Client)
    with pytest.raises(classification.ClassificationError, match="invalid probabilities"):
        await classification.judge({}, {"question": object()})


@pytest.mark.asyncio
async def test_search_respects_retry_after_without_shortening_it(monkeypatch):
    from web_scout.search_backends import _post_with_retries

    response = SimpleNamespace(status_code=429, headers={"retry-after": "60"})
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    sleep = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr("web_scout.scraping._resources.search_client", lambda: client)
    monkeypatch.setattr("web_scout.search_backends.asyncio.sleep", sleep)
    with pytest.raises(TimeoutError):
        await _post_with_retries("https://search.test", {}, {}, "test")
    sleep.assert_awaited_once_with(60)
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_coverage_reads_scraped_source_instead_of_generated_summary(monkeypatch):
    from web_scout import _pipeline_flow as flow
    from web_scout._pipeline_types import SearchLoopState
    from web_scout.tools.tracker import ResearchTracker

    tracker = ResearchTracker()
    url = "https://source.test/report"
    tracker.record_scrape(url, "Report", "Fabricated generated answer")
    tracker._source_text[tracker.normalize_url(url)] = "Actual page facts"
    captured = []

    async def coverage(query, requirements, sources, candidates):
        captured.extend(sources)
        return True, "", []

    monkeypatch.setenv("WEB_SCOUT_CLASSIFICATION_BACKEND", "jev")
    monkeypatch.setattr(flow, "jev_coverage", coverage)
    assert await flow._evaluate_search_coverage(
        query="query",
        include_domains=None,
        depth={},
        evaluator_agent=None,
        tracker=tracker,
        exclude_domains=None,
        state=SearchLoopState(requirements=["fact"]),
    )
    assert captured == [{"url": url, "text": "Actual page facts"}]


@pytest.mark.asyncio
async def test_shutdown_waits_for_prewarm_and_prevents_stale_pool_launch(monkeypatch):
    from web_scout.scraping import _stealth_session as pool

    entered, release = asyncio.Event(), asyncio.Event()
    session = SimpleNamespace(close=AsyncMock())

    async def start():
        entered.set()
        await release.wait()

    session.start = start
    monkeypatch.setattr(pool, "_session_factory", lambda **kwargs: session)
    state = pool._loop_state()
    warmup = asyncio.create_task(pool.prewarm_host("https://warming.test"))
    await entered.wait()
    closing = asyncio.create_task(pool.close_stealthy_sessions())
    await asyncio.sleep(0.01)
    assert not closing.done()
    release.set()
    await asyncio.gather(warmup, closing)
    session.close.assert_awaited_once()
    assert state.closed
    with pytest.raises(RuntimeError, match="shut down"):
        await pool._get_or_create(state, "another.test", {})
