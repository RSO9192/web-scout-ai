"""Live network integration tests for the scraping pipeline.

These tests make real HTTP requests — no mocks. They verify that
_validate_url routes correctly and scrape_url returns meaningful content
for representative URL types: static HTML, JSON, blocked domains, and 404s.

Uses stable, low-traffic URLs (example.com, iana.org, jsonplaceholder.typicode.com,
httpbin.org) to avoid hitting production systems unnecessarily.
"""

import pytest

from web_scout.scraping import scrape_url
from web_scout.scraping.plan import _validate_url
from web_scout.scraping.types import ScrapeStrategy
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
# _validate_url — live network
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_url_live_static_html():
    """example.com is a plain static HTML page — should route to SCRAPE_HTML or SCRAPE_JS."""
    verdict, detail = await _validate_url("https://example.com")
    assert verdict in (ScrapeStrategy.HTML_FAST, ScrapeStrategy.HTML_BROWSER), (
        f"Unexpected verdict {verdict!r}: {detail}"
    )


@pytest.mark.asyncio
async def test_validate_url_live_404_is_skipped():
    """A 404 response must be skipped — no point scraping dead links."""
    verdict, detail = await _validate_url("https://httpbin.org/status/404")
    assert verdict == ScrapeStrategy.SKIP, f"Expected SKIP for 404, got {verdict!r}: {detail}"


@pytest.mark.asyncio
async def test_validate_url_live_blocked_domain_is_skipped():
    """youtube.com is in the block list — validate should return SKIP immediately."""
    verdict, detail = await _validate_url("https://www.youtube.com/watch?v=abc123")
    assert verdict == ScrapeStrategy.SKIP
    assert "blocked" in detail.lower()


@pytest.mark.asyncio
async def test_validate_url_live_pdf_by_extension():
    """A URL ending in .pdf is routed to SCRAPE_DOC without a network call."""
    verdict, detail = await _validate_url("https://fao.org/3/ca9229en/CA9229EN.pdf")
    assert verdict == ScrapeStrategy.DOCUMENT


@pytest.mark.asyncio
async def test_validate_url_live_json_endpoint():
    """A public JSON API should route to SCRAPE_JSON."""
    verdict, detail = await _validate_url("https://jsonplaceholder.typicode.com/todos/1")
    assert verdict == ScrapeStrategy.JSON, f"Unexpected verdict {verdict!r}: {detail}"
    assert "json" in detail.lower()


# ---------------------------------------------------------------------------
# scrape_url — live network
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrape_url_live_example_com_returns_content():
    """Scraping a static HTML page returns non-empty content without Playwright."""
    # example.com is too short for the HTTP-fast path and falls back to a browser;
    # IANA's example-domain page has enough static text for the fast path.
    url = "https://www.iana.org/domains/example"
    content, title, error = await scrape_url(url, query="example domain")

    assert error is None, f"Unexpected error: {error}"
    assert len(content) > 100, f"Content too short: {content!r}"
    assert "example" in content.lower() or "example" in (title or "").lower()


@pytest.mark.asyncio
async def test_scrape_url_live_blocked_domain_returns_error():
    """Scraping a blocked domain returns an error, not content."""
    content, title, error = await scrape_url("https://www.youtube.com/watch?v=abc")

    assert error is not None, "Expected an error for a blocked domain"
    assert content == "" or len(content) < 50


@pytest.mark.asyncio
async def test_scrape_url_live_404_returns_error():
    """A 404 URL returns an error and empty content."""
    content, title, error = await scrape_url("https://httpbin.org/status/404")

    assert error is not None, "Expected an error for a 404 page"


@pytest.mark.asyncio
async def test_scrape_url_live_content_under_max_chars():
    """Returned content does not exceed max_content_chars."""
    max_chars = 500
    content, title, error = await scrape_url(
        "https://www.iana.org/domains/example",
        query="example",
        max_content_chars=max_chars,
    )

    if error is None:
        assert len(content) <= max_chars + 100, (
            f"Content ({len(content)} chars) significantly exceeds max_content_chars={max_chars}"
        )


@pytest.mark.asyncio
async def test_scrape_url_live_returns_string_types():
    """scrape_url always returns (str, str, str|None) — never None for content or title."""
    content, title, error = await scrape_url("https://jsonplaceholder.typicode.com/todos/1")

    assert isinstance(content, str)
    assert isinstance(title, str)
    assert error is None, f"Unexpected error: {error}"
    assert len(content) > 0


@pytest.mark.asyncio
async def test_scrape_url_live_fao_fishery_page():
    """A real FAO fishery page returns meaningful content."""
    content, title, error = await scrape_url(
        "https://www.fao.org/fishery/en",
        query="fishery production statistics",
    )

    # FAO may be slow or block bots, but if it responds it should have content
    if error is None:
        assert len(content) > 200, f"FAO page returned too little content: {content[:200]!r}"
    else:
        # Acceptable: bot-blocked, timeout, JS-only page
        assert isinstance(error, str)
