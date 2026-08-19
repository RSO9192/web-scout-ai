"""Unit tests for URL routing and content-type detection.

Tests have been updated to target ``_classify_fetch_result`` (the new routing
function in ``_parser.py``) and ``FetchResult`` instead of the removed
``plan._validate_url`` / ``ScrapeStrategy`` API.
"""

import pytest

from web_scout.scraping import DefaultParser, FetchResult, ScraplingFetcher, SourceArtifact, URLContext
from web_scout.scraping._download import download_pdf
from web_scout.scraping._fetcher import _is_download_navigation_error
from web_scout.scraping._markdown import append_links
from web_scout.scraping._parser import _classify_fetch_result
from web_scout.scraping.constants import BLOCKED_DOMAINS
from web_scout.scraping.page_classifier import looks_like_document_resource
from web_scout.scraping.utils import trim_json_value

# ---------------------------------------------------------------------------
# Helpers
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


class _MockLink:
    def __init__(self, href: str, text: str = ""):
        self.href = href
        self.text = text


class _MockResultLinks:
    def __init__(self, internal=None, external=None):
        self.internal = internal or []
        self.external = external or []


class _MockCrawlerResult:
    def __init__(self, internal=None, external=None):
        self.links = _MockResultLinks(internal=internal, external=external)


# ---------------------------------------------------------------------------
# looks_like_document_resource
# ---------------------------------------------------------------------------


def test_looks_like_document_resource_uses_content_disposition():
    assert (
        looks_like_document_resource(
            "https://example.org/download?id=123",
            "application/octet-stream",
            'attachment; filename="report.pdf"',
        )
        is True
    )


# ---------------------------------------------------------------------------
# _classify_fetch_result — routing heuristics
# ---------------------------------------------------------------------------


def test_classify_html():
    assert _classify_fetch_result(_make_result("https://example.org/page")) == "html"


def test_classify_json_by_content_type():
    r = _make_result(
        "https://api.example.org/data",
        content_type="application/json",
        html_content='{"key": "value"}',
    )
    assert _classify_fetch_result(r) == "json"


def test_classify_pdf_by_url_extension():
    r = _make_result("https://example.org/report.pdf", html_content=None, body=None)
    assert _classify_fetch_result(r) == "document"


def test_classify_pdf_by_content_disposition():
    r = _make_result(
        "https://example.org/download?id=123",
        content_type="application/octet-stream",
        content_disposition='attachment; filename="report.pdf"',
        html_content=None,
        body=None,
    )
    assert _classify_fetch_result(r) == "document"


def test_classify_http_404_is_skip():
    r = _make_result("https://example.org/gone", status=404, html_content=None, body=None)
    assert _classify_fetch_result(r) == "skip"


def test_classify_http_410_is_skip():
    r = _make_result("https://example.org/removed", status=410, html_content=None, body=None)
    assert _classify_fetch_result(r) == "skip"


def test_classify_image_by_content_type():
    r = _make_result("https://example.org/chart.png", content_type="image/png", html_content=None)
    assert _classify_fetch_result(r) == "image"


def test_classify_blocked_domain_error_is_skip():
    r = _make_result("https://www.youtube.com/watch?v=abc", status=403, error="blocked domain")
    assert _classify_fetch_result(r) == "skip"


@pytest.mark.asyncio
async def test_parser_preserves_fetch_error_when_status_is_zero():
    error = "source_http_error: browser fetch failed: net::ERR_CONNECTION_TIMED_OUT"
    result = _make_result(
        "https://example.org/report",
        status=0,
        html_content=None,
        error=error,
    )

    parsed = await DefaultParser().dispatch(
        result,
        URLContext(url=result.url, depth=0),
    )

    assert parsed.error == error


def test_classify_legacy_doc_extension_is_skip():
    """Unsupported legacy formats (.doc, .xls) must be skipped."""
    r = _make_result("https://example.org/report.doc", html_content=None, body=None)
    assert _classify_fetch_result(r) == "skip"


def test_classify_json_content_type_with_html_payload_returns_html():
    """application/json served with HTML body should route to html."""
    html = "<html><head><title>Report</title></head><body>" + ("prose " * 100) + "</body></html>"
    r = _make_result(
        "https://example.org/download?id=123",
        content_type="application/json",
        html_content=html,
    )
    assert _classify_fetch_result(r) == "html"


def test_classify_json_content_type_with_pdf_magic_bytes():
    """application/json served with PDF magic bytes should route to document."""
    r = _make_result(
        "https://example.org/download?id=123",
        content_type="application/json",
        content_disposition='attachment; filename="report.pdf"',
        html_content=None,
        body=b"%PDF-1.7 mock data",
    )
    assert _classify_fetch_result(r) == "document"


# ---------------------------------------------------------------------------
# trim_json_value utility
# ---------------------------------------------------------------------------


def test_trim_json_value_limits_large_collections():
    data = {
        "items": list(range(30)),
        "nested": {"a": {"b": {"c": {"d": {"e": 1}}}}},
    }
    trimmed = trim_json_value(data, max_items=5, max_depth=3)
    assert len(trimmed["items"]) == 6
    assert trimmed["items"][-1] == "... 25 more items omitted"
    assert "truncated" in trimmed["nested"]["a"]["b"]


# ---------------------------------------------------------------------------
# append_links
# ---------------------------------------------------------------------------


def test_append_links_keeps_icon_only_external_document_links():
    content = "Repository record content"
    result = _MockCrawlerResult(external=[_MockLink("https://cdn.example.org/laws/kenya-forestry-law.pdf", "")])

    enriched = append_links(content, result)

    assert "### Links on Page:" in enriched
    assert "https://cdn.example.org/laws/kenya-forestry-law.pdf" in enriched


# ---------------------------------------------------------------------------
# download_pdf fallback chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_pdf_falls_back_to_urllib(monkeypatch):
    """When Scrapling fetch fails, the Chain-of-Responsibility falls through to urllib."""
    from web_scout.scraping import _download as dl

    async def _scrapling_fails(self, url):
        return None  # simulate Scrapling returning nothing

    async def _urllib_succeeds(self, url):
        return b"%PDF-1.7 mock data"

    async def _browser_unexpected(self, url):
        raise AssertionError("browser fallback should not be needed")

    monkeypatch.setattr(dl._ScraplingFetcher, "_attempt", _scrapling_fails)
    monkeypatch.setattr(dl._UrllibDownloader, "_attempt", _urllib_succeeds)
    monkeypatch.setattr(dl._StealthyBrowser, "_attempt", _browser_unexpected)

    pdf_bytes, error = await download_pdf("https://example.org/report.pdf")

    assert error is None
    assert pdf_bytes == b"%PDF-1.7 mock data"


@pytest.mark.asyncio
async def test_browser_preferred_pdf_download_retains_http_fallbacks(monkeypatch):
    """A failed browser-first attempt must continue through the HTTP chain."""
    from web_scout.scraping import _download as dl

    async def _browser_fails(self, url):
        return None

    async def _scrapling_succeeds(self, url):
        return b"%PDF-1.7 mock data"

    async def _urllib_unexpected(self, url):
        raise AssertionError("urllib should not be needed")

    monkeypatch.setattr(dl._StealthyBrowser, "_attempt", _browser_fails)
    monkeypatch.setattr(dl._ScraplingFetcher, "_attempt", _scrapling_succeeds)
    monkeypatch.setattr(dl._UrllibDownloader, "_attempt", _urllib_unexpected)

    pdf_bytes, error = await download_pdf("https://example.org/report.pdf", needs_browser=True)

    assert error is None
    assert pdf_bytes == b"%PDF-1.7 mock data"


@pytest.mark.asyncio
async def test_pdf_scrapling_attempt_passes_retry_settings(monkeypatch):
    """Scrapling PDF downloads use AsyncFetcher retries with a fixed delay."""
    from scrapling.fetchers import AsyncFetcher

    from web_scout.scraping import _download as dl

    captured_kwargs = {}

    class _Response:
        status = 200
        body = b"%PDF-1.7 mock data"
        headers = {"content-type": "application/pdf"}

    async def _fake_get(url, **kwargs):
        captured_kwargs.update(kwargs)
        return _Response()

    monkeypatch.setattr(AsyncFetcher, "get", _fake_get)

    pdf_bytes = await dl._ScraplingFetcher()._attempt("https://example.org/report.pdf")

    assert pdf_bytes == b"%PDF-1.7 mock data"
    assert captured_kwargs["retries"] == 3
    assert captured_kwargs["retry_delay"] == 2


# ---------------------------------------------------------------------------
# ScraplingFetcher: URL screening and browser error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetcher_rejects_non_absolute_url_before_network():
    malformed = "[https://example.org/report.pdf](https://example.org/report.pdf)"

    result = await ScraplingFetcher().fetch(malformed, URLContext(url=malformed, depth=0))

    assert result.status == 0
    assert result.error == "invalid URL: URL must use http or https"


@pytest.mark.asyncio
async def test_fetcher_routes_known_pdf_through_binary_download_chain(monkeypatch):
    from scrapling.fetchers import AsyncFetcher

    from web_scout.scraping import _download as dl

    url = "https://example.org/report.pdf"
    pdf_bytes = b"%PDF-1.7 mock data"

    async def _fake_download(requested_url, **kwargs):
        assert requested_url == url
        return pdf_bytes, None

    async def _generic_fetch_unexpected(*args, **kwargs):
        raise AssertionError("known PDFs must not use the generic fetch path")

    monkeypatch.setattr(dl, "download_pdf", _fake_download)
    monkeypatch.setattr(AsyncFetcher, "get", _generic_fetch_unexpected)

    result = await ScraplingFetcher().fetch(url, URLContext(url=url, depth=0))

    assert result.status == 200
    assert result.content_type == "application/pdf"
    assert result.body == pdf_bytes
    assert result.error is None


@pytest.mark.asyncio
async def test_files_url_timeout_is_not_misclassified_as_download(monkeypatch):
    from scrapling.fetchers import AsyncFetcher

    from web_scout.scraping import _scrapling

    url = "https://example.org/files/report?id=123"

    async def _fast_timeout(*args, **kwargs):
        raise TimeoutError("request timed out")

    async def _browser_timeout(*args, **kwargs):
        raise RuntimeError(f"Page.goto: net::ERR_CONNECTION_TIMED_OUT at {url}")

    monkeypatch.setattr(AsyncFetcher, "get", _fast_timeout)
    monkeypatch.setattr(_scrapling, "stealthy_fetch", _browser_timeout)

    result = await ScraplingFetcher().fetch(url, URLContext(url=url, depth=0))

    assert result.error.startswith("source_http_error: browser fetch failed:")
    assert result.error != "__DOWNLOAD_REDIRECT__"


def test_download_navigation_detection_requires_explicit_playwright_signal():
    assert _is_download_navigation_error("Page.goto: Download is starting") is True
    assert _is_download_navigation_error("Timeout at https://example.org/files/report") is False


# ---------------------------------------------------------------------------
# ScraplingFetcher + DefaultParser: document metadata flows to parse_document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrape_pipeline_passes_document_metadata_from_fetch_result(monkeypatch):
    """Metadata (content_type, content_disposition) from FetchResult flows into parse_document."""
    from web_scout.scraping import _document as doc_module

    captured_kwargs: dict = {}

    async def _fake_scrape_document(url, **kwargs):
        captured_kwargs.update(kwargs)
        return SourceArtifact(kind="text", title="report.pdf", text_content="PDF content"), None

    monkeypatch.setattr(doc_module, "scrape_document", _fake_scrape_document)

    fake_fetch = FetchResult(
        url="https://example.org/download?id=123",
        status=200,
        content_type="application/octet-stream",
        content_disposition='attachment; filename="report.pdf"',
        html_content=None,
        body=None,
        headers={},
        used_browser=False,
    )
    monkeypatch.setattr(ScraplingFetcher, "fetch", lambda self, url, context: _async_return(fake_fetch))

    fetcher = ScraplingFetcher()
    parser = DefaultParser()
    context = URLContext(url="https://example.org/download?id=123", depth=0)
    fetch_result = await fetcher.fetch("https://example.org/download?id=123", context)
    parse_result = await parser.dispatch(fetch_result, context)

    assert parse_result.error is None
    assert parse_result.text_content == "PDF content"
    assert captured_kwargs["known_content_type"] == "application/octet-stream"
    assert captured_kwargs["known_content_disposition"] == 'attachment; filename="report.pdf"'


@pytest.mark.asyncio
async def test_scrape_document_reuses_prefetched_pdf_bytes(monkeypatch):
    from web_scout.scraping import _document as doc_module

    pdf_bytes = b"%PDF-1.7 mock data"

    async def _download_unexpected(*args, **kwargs):
        raise AssertionError("prefetched PDF bytes must not be downloaded again")

    async def _fake_convert(received_bytes, url, max_pages, *, vision_model=None):
        assert received_bytes == pdf_bytes
        return "Extracted PDF content with enough text. " * 20

    monkeypatch.setattr(doc_module, "download_pdf", _download_unexpected)
    monkeypatch.setattr(doc_module, "_convert_pdf_to_markdown", _fake_convert)

    artifact, error = await doc_module.scrape_document(
        "https://example.org/report.pdf",
        known_content_type="application/pdf",
        prefetched_bytes=pdf_bytes,
    )

    assert error is None
    assert artifact.kind == "text"
    assert "Extracted PDF content" in artifact.text_content


async def _async_return(value):
    return value


# ---------------------------------------------------------------------------
# Blocked-domain policy
# ---------------------------------------------------------------------------


def test_open_access_publishers_not_blocked():
    """Open-access journals must not be in the default block list."""
    open_access = [
        "frontiersin.org",
        "mdpi.com",
        "journals.plos.org",
    ]
    for domain in open_access:
        assert domain not in BLOCKED_DOMAINS, f"{domain} is open-access and should not be blocked"


def test_abstract_available_publishers_not_blocked():
    """Publishers with accessible abstracts must not be in the default block list."""
    abstract_available = [
        "researchgate.net",
        "nature.com",
        "academic.oup.com",
    ]
    for domain in abstract_available:
        assert domain not in BLOCKED_DOMAINS, f"{domain} has accessible content and should not be blocked"


def test_paywalled_publishers_remain_blocked():
    """Consistently paywalled publishers must stay blocked."""
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
        assert domain in BLOCKED_DOMAINS, f"{domain} is paywalled and should stay blocked"
