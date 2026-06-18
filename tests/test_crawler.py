"""Unit tests for Crawl4AICrawler — ensures no duplicate network fetches."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web_scout.scraping import Crawl4AICrawler, ParseResult, SourceArtifact, URLContext
from web_scout.scraping._crawler import _build_default_llm_config


def _make_parse_result(*, raw_html: str | None = "<html><body><a href='/doc.pdf'>PDF</a></body></html>") -> ParseResult:
    artifact = SourceArtifact(kind="text", title="Example", text_content="Example page")
    return ParseResult(
        url="https://example.org/page",
        title="Example",
        text_content="Example page",
        links=["https://example.org/doc.pdf"],
        artifact=artifact,
        raw_html=raw_html,
    )


def test_prefetched_crawl_input_uses_raw_prefix_with_fetched_html():
    crawler = Crawl4AICrawler()
    result = _make_parse_result()

    crawl_input = crawler._prefetched_crawl_input(result)

    assert crawl_input.startswith("raw:")
    assert "<html>" in crawl_input
    assert "https://example.org/page" not in crawl_input


def test_prefetched_crawl_input_builds_synthetic_html_without_raw_html():
    crawler = Crawl4AICrawler()
    result = _make_parse_result(raw_html=None)

    crawl_input = crawler._prefetched_crawl_input(result)

    assert crawl_input.startswith("raw:")
    assert "https://example.org/doc.pdf" in crawl_input


def test_build_default_llm_config_without_api_key_returns_none(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert _build_default_llm_config() is None


def test_build_default_llm_config_uses_followup_selector_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    config = _build_default_llm_config()
    assert config is not None
    assert config.provider == "gemini/gemini-3-flash-preview"
    assert config.api_token == "test-key"
    assert config.temperature == 0


def test_crawl4ai_crawler_uses_heuristics_when_llm_config_is_none():
    crawler = Crawl4AICrawler(llm_config=None)
    assert crawler._llm_config is None


def test_crawl4ai_crawler_resolves_default_llm_config_when_api_key_present(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    crawler = Crawl4AICrawler()
    assert crawler._llm_config is not None
    assert crawler._llm_config.provider == "gemini/gemini-3-flash-preview"


@pytest.mark.asyncio
async def test_llm_select_passes_prefetched_html_to_crawl4ai():
    crawler = Crawl4AICrawler(llm_config=object())
    result = _make_parse_result()
    context = URLContext(url=result.url, depth=0)

    mock_crawl_result = MagicMock()
    mock_crawl_result.extracted_content = '{"relevant_urls": ["https://example.org/doc.pdf"]}'

    mock_arun = AsyncMock(return_value=mock_crawl_result)
    mock_crawler_instance = MagicMock()
    mock_crawler_instance.arun = mock_arun
    mock_crawler_instance.__aenter__ = AsyncMock(return_value=mock_crawler_instance)
    mock_crawler_instance.__aexit__ = AsyncMock(return_value=False)

    mock_crawl4ai = MagicMock()
    mock_crawl4ai.AsyncWebCrawler = MagicMock(return_value=mock_crawler_instance)
    mock_crawl4ai.CrawlerRunConfig = MagicMock(side_effect=lambda **kwargs: kwargs)
    mock_crawl4ai.CacheMode = MagicMock(BYPASS="bypass")

    mock_extraction_strategy = MagicMock()
    mock_extraction_strategy.LLMExtractionStrategy = MagicMock(return_value=MagicMock())

    with patch.dict(
        sys.modules,
        {
            "crawl4ai": mock_crawl4ai,
            "crawl4ai.extraction_strategy": mock_extraction_strategy,
        },
    ):
        selected = await crawler._llm_select(result, context)

    assert selected == ["https://example.org/doc.pdf"]
    mock_arun.assert_awaited_once()
    crawl_input = mock_arun.await_args.args[0]
    assert crawl_input.startswith("raw:")
    assert crawl_input != result.url
    assert not crawl_input.startswith("http")
    config = mock_arun.await_args.kwargs["config"]
    assert config["base_url"] == result.url
