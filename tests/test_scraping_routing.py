"""Unit tests for URL routing and content-type detection."""

import pytest

from web_scout.scraping import (
    _SKIP,
    _SCRAPE_DOC,
    _SCRAPE_HTML,
    _SCRAPE_IMAGE,
    _SCRAPE_JSON,
    _download_pdf_bytes,
    _looks_like_document_resource,
    _trim_json_value,
    _validate_url,
    scrape_url,
)


class _MockResponse:
    def __init__(self, status_code=200, headers=None, text="", content=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        import json

        return json.loads(self.text)


def _mock_async_client_factory(head_response=None, get_response=None):
    class _MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def head(self, url):
            if isinstance(head_response, Exception):
                raise head_response
            return head_response

        async def get(self, url):
            if isinstance(get_response, Exception):
                raise get_response
            return get_response

    return _MockAsyncClient


def test_looks_like_document_resource_uses_content_disposition():
    assert _looks_like_document_resource(
        "https://example.org/download?id=123",
        "application/octet-stream",
        'attachment; filename="report.pdf"',
    ) is True


def test_trim_json_value_limits_large_collections():
    data = {
        "items": list(range(30)),
        "nested": {"a": {"b": {"c": {"d": {"e": 1}}}}},
    }
    trimmed = _trim_json_value(data, max_items=5, max_depth=3)
    assert len(trimmed["items"]) == 6
    assert trimmed["items"][-1] == "... 25 more items omitted"
    assert "truncated" in trimmed["nested"]["a"]["b"]


@pytest.mark.asyncio
async def test_validate_url_routes_extensionless_pdf_from_headers(monkeypatch):
    from web_scout import scraping

    head = _MockResponse(
        headers={
            "content-type": "application/octet-stream",
            "content-disposition": 'attachment; filename="report.pdf"',
        }
    )
    monkeypatch.setattr(scraping.httpx, "AsyncClient", _mock_async_client_factory(head_response=head))

    verdict, detail = await _validate_url("https://example.org/download?id=123")
    assert verdict == _SCRAPE_DOC
    assert "application/octet-stream" in detail


@pytest.mark.asyncio
async def test_validate_url_routes_direct_pdf_without_network(monkeypatch):
    from web_scout import scraping

    class _UnexpectedAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("network probe should not run for direct PDF URLs")

    monkeypatch.setattr(scraping.httpx, "AsyncClient", _UnexpectedAsyncClient)

    verdict, detail = await _validate_url("https://example.org/report.pdf")
    assert verdict == _SCRAPE_DOC
    assert detail == "document-by-url"


@pytest.mark.asyncio
async def test_validate_url_skips_direct_legacy_doc_without_network(monkeypatch):
    from web_scout import scraping

    class _UnexpectedAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("network probe should not run for direct legacy document URLs")

    monkeypatch.setattr(scraping.httpx, "AsyncClient", _UnexpectedAsyncClient)

    verdict, detail = await _validate_url("https://example.org/report.doc")
    assert verdict == _SKIP
    assert detail == "unsupported legacy Office document format (.doc)"


@pytest.mark.asyncio
async def test_validate_url_skips_extensionless_legacy_doc_from_headers(monkeypatch):
    from web_scout import scraping

    head = _MockResponse(headers={"content-type": "application/msword"})
    monkeypatch.setattr(scraping.httpx, "AsyncClient", _mock_async_client_factory(head_response=head))

    verdict, detail = await _validate_url("https://example.org/download?id=123")
    assert verdict == _SKIP
    assert detail == "unsupported legacy Office document format (.doc)"


@pytest.mark.asyncio
async def test_validate_url_prefers_supported_filename_over_legacy_mime(monkeypatch):
    from web_scout import scraping

    head = _MockResponse(
        headers={
            "content-type": "application/msword",
            "content-disposition": 'attachment; filename="report.docx"',
        }
    )
    monkeypatch.setattr(scraping.httpx, "AsyncClient", _mock_async_client_factory(head_response=head))

    verdict, detail = await _validate_url("https://example.org/download?id=123")
    assert verdict == _SCRAPE_DOC
    assert detail == "application/msword"


@pytest.mark.asyncio
async def test_validate_url_routes_json_response(monkeypatch):
    from web_scout import scraping

    head = _MockResponse(headers={"content-type": "application/json; charset=utf-8"})
    monkeypatch.setattr(scraping.httpx, "AsyncClient", _mock_async_client_factory(head_response=head))

    verdict, detail = await _validate_url("https://example.org/api/data")
    assert verdict == _SCRAPE_JSON
    assert detail == "application/json"


@pytest.mark.asyncio
async def test_validate_url_routes_image_response(monkeypatch):
    from web_scout import scraping

    head = _MockResponse(headers={"content-type": "image/png"})
    monkeypatch.setattr(scraping.httpx, "AsyncClient", _mock_async_client_factory(head_response=head))

    verdict, detail = await _validate_url("https://example.org/chart.png")
    assert verdict == _SCRAPE_IMAGE
    assert detail == "image/png"


@pytest.mark.asyncio
async def test_validate_url_keeps_short_metadata_pages(monkeypatch):
    from web_scout import scraping

    head = _MockResponse(headers={"content-type": "text/html"})
    html = """
    <html>
      <head><title>Repository record</title></head>
      <body>
        <p>Metadata</p>
        <a href="/download?id=10">Full text PDF</a>
      </body>
    </html>
    """
    get = _MockResponse(headers={"content-type": "text/html"}, text=html)
    monkeypatch.setattr(
        scraping.httpx,
        "AsyncClient",
        _mock_async_client_factory(head_response=head, get_response=get),
    )

    verdict, detail = await _validate_url("https://example.org/record/10")
    assert verdict == _SCRAPE_HTML
    assert "metadata" in detail


@pytest.mark.asyncio
async def test_download_pdf_bytes_falls_back_to_raw_download(monkeypatch):
    from web_scout import scraping

    class _FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            raise RuntimeError("Unsupported content-encoding: None")

    monkeypatch.setattr(scraping.httpx, "AsyncClient", _FailingAsyncClient)
    monkeypatch.setattr(
        scraping,
        "_download_binary_via_urllib",
        lambda url: (b"%PDF-1.7 mock data", "application/pdf"),
    )

    async def _unexpected_browser_download(url):
        raise AssertionError("browser fallback should not be needed")

    monkeypatch.setattr(scraping, "_download_pdf_via_browser", _unexpected_browser_download)

    pdf_bytes, error = await _download_pdf_bytes("https://example.org/report.pdf")

    assert error is None
    assert pdf_bytes == b"%PDF-1.7 mock data"


@pytest.mark.asyncio
async def test_scrape_url_passes_document_metadata_from_validation(monkeypatch):
    """scrape_url should not discard content metadata discovered during validation."""
    from web_scout import scraping

    captured_kwargs = {}

    async def _fake_build_scrape_plan(url, allowed_domains=None):
        return scraping.ScrapePlan(
            scraping.ScrapeStrategy.DOCUMENT,
            "application/octet-stream",
            "application/octet-stream",
            'attachment; filename="report.pdf"',
        )

    async def _fake_scrape_document(url, **kwargs):
        captured_kwargs.update(kwargs)
        return "PDF content", "report.pdf", None

    monkeypatch.setattr(scraping, "_build_scrape_plan", _fake_build_scrape_plan)
    monkeypatch.setattr(scraping, "_scrape_document", _fake_scrape_document)

    content, title, error = await scrape_url("https://example.org/download?id=123")

    assert error is None
    assert content == "PDF content"
    assert title == "report.pdf"
    assert captured_kwargs["known_content_type"] == "application/octet-stream"
    assert captured_kwargs["known_content_disposition"] == 'attachment; filename="report.pdf"'


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
