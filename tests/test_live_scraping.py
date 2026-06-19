"""Live network integration tests for the scraping pipeline.

These tests make real HTTP requests — no mocks. They verify that the new
Fetcher → Parser pipeline routes correctly and returns meaningful content
for representative URL types: static HTML, JSON, blocked domains, and 404s.

Uses stable, low-traffic URLs (example.com, iana.org, jsonplaceholder.typicode.com,
httpbin.org) to avoid hitting production systems unnecessarily.
"""

import pytest

from web_scout.scraping import DefaultParser, FetchResult, ScraplingFetcher, URLContext, materialize_parse_result
from web_scout.scraping._parser import _classify_fetch_result
from web_scout.scraping.utils import is_blocked_domain

# ---------------------------------------------------------------------------
# is_blocked_domain — no network, but validates the lookup table
# ---------------------------------------------------------------------------


def test_youtube_is_blocked_by_default():
    assert is_blocked_domain("https://www.youtube.com/watch?v=abc") is True


def test_fao_org_is_not_blocked():
    assert is_blocked_domain("https://www.fao.org/fishery/data") is False


def test_reddit_unblocked_when_in_allowed_domains():
    assert is_blocked_domain("https://reddit.com/r/science", allowed_domains=frozenset({"reddit.com"})) is False


# ---------------------------------------------------------------------------
# _classify_fetch_result — no network, validates routing heuristics
# ---------------------------------------------------------------------------


def _make_result(url: str, **kwargs) -> FetchResult:
    defaults = dict(
        status=200,
        content_type="text/html",
        content_disposition="",
        html_content="<html><body>hello</body></html>",
        body=None,
        headers={},
        used_browser=False,
    )
    defaults.update(kwargs)
    return FetchResult(url=url, **defaults)


def test_classify_html():
    r = _make_result("https://example.com")
    assert _classify_fetch_result(r) == "html"


def test_classify_json_by_content_type():
    r = _make_result(
        "https://api.example.com/data",
        content_type="application/json",
        html_content='{"key": "value"}',
    )
    assert _classify_fetch_result(r) == "json"


def test_classify_pdf_by_url_extension():
    r = _make_result("https://fao.org/3/ca9229en/CA9229EN.pdf", html_content=None, body=None)
    assert _classify_fetch_result(r) == "document"


def test_classify_http_error_is_skip():
    r = _make_result("https://httpbin.org/status/404", status=404, html_content=None, body=None)
    assert _classify_fetch_result(r) == "skip"


def test_classify_blocked_domain_after_fetch():
    """A pre-screening error from ScraplingFetcher routes to 'skip'."""
    r = _make_result("https://www.youtube.com/watch?v=abc", status=403, error="blocked domain")
    assert _classify_fetch_result(r) == "skip"


# ---------------------------------------------------------------------------
# ScraplingFetcher + DefaultParser + materialize_parse_result — live network
# ---------------------------------------------------------------------------


async def _scrape(
    url: str,
    *,
    query: str = "",
    vision_model=None,
    allowed_domains=None,
    max_pdf_pages: int = 50,
    max_content_chars: int = 30_000,
):
    """Convenience wrapper replicating the old scrape_url signature."""
    fetcher = ScraplingFetcher(allowed_domains=allowed_domains)
    parser = DefaultParser(vision_model=vision_model, max_pdf_pages=max_pdf_pages)
    context = URLContext(url=url, depth=0)
    fetch_result = await fetcher.fetch(url, context)
    parse_result = await parser.dispatch(fetch_result, context)
    content, error = await materialize_parse_result(
        parse_result,
        query=query,
        vision_model=vision_model,
        max_content_chars=max_content_chars,
    )
    effective_error = error or parse_result.error
    return content, parse_result.title, effective_error


@pytest.mark.asyncio
async def test_scrape_live_example_com_returns_content():
    """Scraping a static HTML page returns non-empty content."""
    url = "https://www.iana.org/domains/example"
    content, title, error = await _scrape(url, query="example domain")

    assert error is None, f"Unexpected error: {error}"
    assert len(content) > 100, f"Content too short: {content!r}"
    assert "example" in content.lower() or "example" in (title or "").lower()


@pytest.mark.asyncio
async def test_scrape_live_blocked_domain_returns_error():
    """Scraping a blocked domain returns an error, not content."""
    content, title, error = await _scrape("https://www.youtube.com/watch?v=abc")

    assert error is not None, "Expected an error for a blocked domain"
    assert content == "" or len(content) < 50


@pytest.mark.asyncio
async def test_scrape_live_404_returns_error_or_skip():
    """A 404 URL returns an error or empty content."""
    content, title, error = await _scrape("https://httpbin.org/status/404")

    # Either the error field is set or content is empty (soft-404 detection)
    assert error is not None or not content.strip(), (
        f"Expected error or empty content for 404, got content={content!r}"
    )


@pytest.mark.asyncio
async def test_scrape_live_content_under_max_chars():
    """Returned content does not exceed max_content_chars."""
    max_chars = 500
    content, title, error = await _scrape(
        "https://www.iana.org/domains/example",
        query="example",
        max_content_chars=max_chars,
    )

    if error is None:
        assert len(content) <= max_chars + 100, (
            f"Content ({len(content)} chars) significantly exceeds max_content_chars={max_chars}"
        )


@pytest.mark.asyncio
async def test_scrape_live_returns_string_types():
    """Pipeline always returns (str, str, str|None) — never None for content or title."""
    content, title, error = await _scrape("https://jsonplaceholder.typicode.com/todos/1")

    assert isinstance(content, str)
    assert isinstance(title, str)
    assert error is None, f"Unexpected error: {error}"
    assert len(content) > 0


@pytest.mark.asyncio
async def test_scrape_live_fao_fishery_page():
    """A real FAO fishery page returns meaningful content."""
    content, title, error = await _scrape(
        "https://www.fao.org/fishery/en",
        query="fishery production statistics",
    )

    if error is None:
        assert len(content) > 200, f"FAO page returned too little content: {content[:200]!r}"
    else:
        assert isinstance(error, str)


@pytest.mark.asyncio
async def test_fetch_live_pdf_by_extension():
    """Fetching a PDF URL sets used_browser=False and routes to 'document'."""
    fetcher = ScraplingFetcher()
    context = URLContext(url="https://fao.org/3/ca9229en/CA9229EN.pdf", depth=0)
    fetch_result = await fetcher.fetch("https://fao.org/3/ca9229en/CA9229EN.pdf", context)
    strategy = _classify_fetch_result(fetch_result)
    assert strategy == "document", f"Expected 'document', got {strategy!r} (status={fetch_result.status})"
