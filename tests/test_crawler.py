"""Shared crawler selectors preserve URL boundaries without fetching pages."""

import builtins
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from web_scout import _pipeline_flow as flow
from web_scout._classification import ClassificationError
from web_scout.agent import FollowupSelection
from web_scout.scraping import Crawl4AICrawler, DefaultCrawler, ParseResult, SourceArtifact, URLContext


def _page():
    return ParseResult(
        url="https://example.org/page",
        title="Climate reports",
        text_content="Available climate reports",
        links=[f"https://example.org/reports/report-{i}.pdf" for i in range(3)],
        artifact=SourceArtifact(kind="text", title="Climate reports", text_content="Available climate reports"),
        raw_html="<html>already fetched</html>",
    )


def test_legacy_public_class_alias_is_preserved():
    assert Crawl4AICrawler is DefaultCrawler


@pytest.mark.asyncio
async def test_gpt_selects_using_existing_runner_and_never_imports_crawl4ai(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert not name.startswith("crawl4ai")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setenv("WEB_SCOUT_CRAWLER_BACKEND", "gpt")
    page = _page()
    output = SimpleNamespace(
        final_output_as=lambda _: FollowupSelection(
            selected_urls=["https://invented.org/", page.links[1], page.links[1]],
        )
    )
    runner = AsyncMock(return_value=output)
    monkeypatch.setattr(flow.Runner, "run", runner)
    monkeypatch.setattr("web_scout.utils.get_model", lambda name: "dummy")
    selected = await DefaultCrawler(max_links=1)._select_links(page, URLContext(url=page.url, depth=0))
    assert selected == [page.links[1]]
    runner.assert_awaited_once()
    assert "Available climate reports" in runner.await_args.args[1]
    assert page.url in runner.await_args.args[1]


@pytest.mark.asyncio
async def test_jev_selects_using_shared_followup_selector(monkeypatch):
    monkeypatch.setenv("WEB_SCOUT_CRAWLER_BACKEND", "jev")
    page = _page()
    jev = AsyncMock(return_value=([page.links[2]], None))
    monkeypatch.setattr(flow, "select_links_with_jev", jev)
    selected = await DefaultCrawler(max_links=1)._select_links(page, URLContext(url=page.url, depth=0))
    assert selected == [page.links[2]]
    assert jev.await_args.kwargs["candidates"] == page.links
    assert jev.await_args.kwargs["parent_content"] == page.text_content


@pytest.mark.asyncio
async def test_disable_jev_overrides_crawler_setting(monkeypatch):
    monkeypatch.setenv("DISABLE_JEV", "true")
    monkeypatch.setenv("WEB_SCOUT_CRAWLER_BACKEND", "jev")
    crawler = DefaultCrawler()
    gpt = AsyncMock(return_value=[_page().links[0]])
    jev = AsyncMock(side_effect=AssertionError("Jev must not run"))
    monkeypatch.setattr(crawler, "_llm_select", gpt)
    monkeypatch.setattr(flow, "select_links_with_jev", jev)
    assert await crawler._select_links(_page(), URLContext(url=_page().url, depth=0)) == [_page().links[0]]
    gpt.assert_awaited_once()
    jev.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_none_keeps_heuristics_without_ai(monkeypatch):
    crawler = Crawl4AICrawler(llm_config=None, max_links=1)
    gpt = AsyncMock(side_effect=AssertionError("AI must not run"))
    monkeypatch.setattr(crawler, "_llm_select", gpt)
    page = _page()
    assert await crawler._select_links(page, URLContext(url=page.url, depth=0)) == page.links[:1]
    gpt.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_custom_connection_is_preserved(monkeypatch):
    config = SimpleNamespace(provider="openai/gpt-4o-mini", api_token="custom-key", base_url="https://custom.test")
    captured = {}

    def model(**kwargs):
        captured.update(kwargs)
        return "custom-model"

    monkeypatch.setattr("agents.extensions.models.litellm_model.LitellmModel", model)
    selector = AsyncMock(return_value=_page().links[:1])
    monkeypatch.setattr(flow, "_select_followup_urls_with_luna", selector)
    crawler = Crawl4AICrawler(llm_config=config, max_links=1)
    await crawler._select_links(_page(), URLContext(url=_page().url, depth=0))
    assert captured == {"model": config.provider, "api_key": config.api_token, "base_url": config.base_url}
    assert selector.await_args.kwargs["model"] == "custom-model"


@pytest.mark.asyncio
async def test_gpt_failure_keeps_existing_shortlist_fallback(monkeypatch):
    monkeypatch.setenv("WEB_SCOUT_CRAWLER_BACKEND", "gpt")
    monkeypatch.setattr("web_scout.utils.get_model", lambda name: "dummy")
    monkeypatch.setattr(flow.Runner, "run", AsyncMock(side_effect=TimeoutError()))
    assert (
        await DefaultCrawler(max_links=1)._select_links(_page(), URLContext(url=_page().url, depth=0))
        == _page().links[:1]
    )


@pytest.mark.asyncio
async def test_jev_failure_remains_a_hard_error(monkeypatch):
    monkeypatch.setenv("WEB_SCOUT_CRAWLER_BACKEND", "jev")
    monkeypatch.setattr(flow, "select_links_with_jev", AsyncMock(side_effect=ClassificationError("missing token")))
    with pytest.raises(ClassificationError):
        await DefaultCrawler()._select_links(_page(), URLContext(url=_page().url, depth=0))


@pytest.mark.asyncio
async def test_crawl_queues_selected_links_and_skips_empty_pages(monkeypatch):
    crawler = DefaultCrawler()
    page = _page()
    selector = AsyncMock(return_value=[page.links[2]])
    queue = AsyncMock()
    monkeypatch.setattr(crawler, "_select_links", selector)
    await crawler.crawl(page, URLContext(url=page.url, depth=0), queue)
    queue.assert_awaited_once_with(page.links[2])
    page.links.clear()
    await crawler.crawl(page, URLContext(url=page.url, depth=0), queue)
    selector.assert_awaited_once()
