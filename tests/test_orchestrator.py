"""Tests for Orchestrator — full Fetcher → Parser → Crawler pipeline coordination."""

import pytest

from web_scout.scraping import (
    Crawler,
    Crawl4AICrawler,
    Fetcher,
    FetchResult,
    Orchestrator,
    OrchestratorConfig,
    ParseResult,
    Parser,
    SourceArtifact,
    URLContext,
)

_SEED_URL = "https://example.org/"
_CHILD_URL = "https://example.org/about"


def _make_fetch_result(url: str) -> FetchResult:
    return FetchResult(
        url=url,
        status=200,
        content_type="text/html",
        content_disposition="",
        html_content=f"<html><head><title>{url}</title></head><body>content</body></html>",
        body=None,
        headers={},
        used_browser=False,
    )


def _make_parse_result(url: str, *, links: list[str] | None = None) -> ParseResult:
    artifact = SourceArtifact(kind="text", title=url, text_content=f"content for {url}")
    return ParseResult(
        url=url,
        title=url,
        text_content=f"content for {url}",
        links=links or [],
        artifact=artifact,
        raw_html=f"<html><body><a href='{_CHILD_URL}'>about</a></body></html>",
    )


class _RecordingFetcher(Fetcher):
    def __init__(self, responses: dict[str, FetchResult]) -> None:
        self._responses = responses
        self.fetched: list[str] = []

    async def fetch(self, url: str, context: URLContext) -> FetchResult:
        self.fetched.append(url)
        return self._responses[url]


class _FakeParser(Parser):
    def __init__(self, links_by_url: dict[str, list[str]]) -> None:
        self._links_by_url = links_by_url

    async def _parse(self, result: FetchResult, context: URLContext) -> ParseResult:
        return _make_parse_result(result.url, links=self._links_by_url.get(result.url, []))

    async def parse_html(self, result: FetchResult, context: URLContext) -> ParseResult:
        return await self._parse(result, context)

    async def parse_json(self, result: FetchResult, context: URLContext) -> ParseResult:
        return await self._parse(result, context)

    async def parse_document(self, result: FetchResult, context: URLContext) -> ParseResult:
        return await self._parse(result, context)

    async def parse_image(self, result: FetchResult, context: URLContext) -> ParseResult:
        return await self._parse(result, context)


class _QueueAllLinksCrawler(Crawler):
    async def crawl(
        self,
        result: ParseResult,
        context: URLContext,
        queue_url,
    ) -> None:
        for url in result.links:
            await queue_url(url)


@pytest.mark.asyncio
async def test_orchestrator_run_without_queued_urls_returns_empty():
    orch = Orchestrator(OrchestratorConfig())
    assert await orch.run() == []


@pytest.mark.asyncio
async def test_orchestrator_assembles_full_pipeline_and_crawls_followups():
    """Fake components exercise fetch → parse → crawl → queue_url end-to-end."""
    fetcher = _RecordingFetcher(
        {
            _SEED_URL: _make_fetch_result(_SEED_URL),
            _CHILD_URL: _make_fetch_result(_CHILD_URL),
        }
    )
    parser = _FakeParser({_SEED_URL: [_CHILD_URL]})
    crawler = _QueueAllLinksCrawler()

    config = OrchestratorConfig(max_depth=1, max_urls=5, max_concurrent_fetches=2, max_concurrent_parses=2)
    orch = Orchestrator(config, fetcher=fetcher, parser=parser, crawler=crawler)

    await orch.queue_url(_SEED_URL)
    results = await orch.run()

    assert fetcher.fetched == [_SEED_URL, _CHILD_URL]
    assert {result.url for result in results} == {_SEED_URL, _CHILD_URL}
    assert all(result.text_content for result in results)


@pytest.mark.asyncio
async def test_orchestrator_live_full_pipeline_starts_search():
    """Live network test — default Fetcher, Parser, and Crawler via Orchestrator."""
    config = OrchestratorConfig(
        max_depth=1,
        max_urls=5,
        max_concurrent_fetches=2,
        max_concurrent_parses=2,
    )
    orch = Orchestrator(config, crawler=Crawl4AICrawler(max_links=2))

    seed_url = "https://www.iana.org/domains/example"
    await orch.queue_url(seed_url)
    results = await orch.run()

    assert len(results) >= 1
    assert any(result.url == seed_url for result in results)

    seed_results = [result for result in results if result.url == seed_url]
    assert seed_results[0].error is None
    assert len(seed_results[0].text_content) > 50
    assert "example" in seed_results[0].text_content.lower() or "example" in seed_results[0].title.lower()
