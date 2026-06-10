"""Unit tests for URL routing and content-type detection."""

import pytest

from web_scout.scraping import scrape_url
from web_scout.scraping.constants import BLOCKED_DOMAINS
from web_scout.scraping.page_classifier import looks_like_document_resource
from web_scout.scraping.plan import _validate_url
from web_scout.scraping.strategy import (
    _append_internal_links,
    _download_pdf_bytes,
)
from web_scout.scraping.types import ScrapePlan, ScrapeStrategy
from web_scout.scraping.utils import trim_json_value


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
    assert (
        looks_like_document_resource(
            "https://example.org/download?id=123",
            "application/octet-stream",
            'attachment; filename="report.pdf"',
        )
        is True
    )


def test_trim_json_value_limits_large_collections():
    data = {
        "items": list(range(30)),
        "nested": {"a": {"b": {"c": {"d": {"e": 1}}}}},
    }
    trimmed = trim_json_value(data, max_items=5, max_depth=3)
    assert len(trimmed["items"]) == 6
    assert trimmed["items"][-1] == "... 25 more items omitted"
    assert "truncated" in trimmed["nested"]["a"]["b"]


def test_append_internal_links_keeps_icon_only_external_document_links():
    content = "Repository record content"
    result = _MockCrawlerResult(external=[_MockLink("https://cdn.example.org/laws/kenya-forestry-law.pdf", "")])

    enriched = _append_internal_links(content, result)

    assert "### Links on Page:" in enriched
    assert "https://cdn.example.org/laws/kenya-forestry-law.pdf" in enriched


@pytest.mark.asyncio
async def test_validate_url_routes_extensionless_pdf_from_headers(monkeypatch):
    from web_scout.scraping import plan as scraping_plan

    head = _MockResponse(
        headers={
            "content-type": "application/octet-stream",
            "content-disposition": 'attachment; filename="report.pdf"',
        }
    )
    monkeypatch.setattr(scraping_plan.httpx, "AsyncClient", _mock_async_client_factory(head_response=head))

    verdict, detail = await _validate_url("https://example.org/download?id=123")
    assert verdict == ScrapeStrategy.DOCUMENT
    assert "application/octet-stream" in detail


@pytest.mark.asyncio
async def test_validate_url_routes_direct_pdf_without_network(monkeypatch):
    from web_scout.scraping import plan as scraping_plan

    class _UnexpectedAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("network probe should not run for direct PDF URLs")

    monkeypatch.setattr(scraping_plan.httpx, "AsyncClient", _UnexpectedAsyncClient)

    verdict, detail = await _validate_url("https://example.org/report.pdf")
    assert verdict == ScrapeStrategy.DOCUMENT
    assert detail == "document-by-url"


@pytest.mark.asyncio
async def test_validate_url_skips_direct_legacy_doc_without_network(monkeypatch):
    from web_scout.scraping import plan as scraping_plan

    class _UnexpectedAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("network probe should not run for direct legacy document URLs")

    monkeypatch.setattr(scraping_plan.httpx, "AsyncClient", _UnexpectedAsyncClient)

    verdict, detail = await _validate_url("https://example.org/report.doc")
    assert verdict == ScrapeStrategy.SKIP
    assert detail == "unsupported legacy Office document format (.doc)"


@pytest.mark.asyncio
async def test_validate_url_skips_extensionless_legacy_doc_from_headers(monkeypatch):
    from web_scout.scraping import plan as scraping_plan

    head = _MockResponse(headers={"content-type": "application/msword"})
    monkeypatch.setattr(scraping_plan.httpx, "AsyncClient", _mock_async_client_factory(head_response=head))

    verdict, detail = await _validate_url("https://example.org/download?id=123")
    assert verdict == ScrapeStrategy.SKIP
    assert detail == "unsupported legacy Office document format (.doc)"


@pytest.mark.asyncio
async def test_validate_url_prefers_supported_filename_over_legacy_mime(monkeypatch):
    from web_scout.scraping import plan as scraping_plan

    head = _MockResponse(
        headers={
            "content-type": "application/msword",
            "content-disposition": 'attachment; filename="report.docx"',
        }
    )
    monkeypatch.setattr(scraping_plan.httpx, "AsyncClient", _mock_async_client_factory(head_response=head))

    verdict, detail = await _validate_url("https://example.org/download?id=123")
    assert verdict == ScrapeStrategy.DOCUMENT
    assert detail == "application/msword"


@pytest.mark.asyncio
async def test_validate_url_routes_json_response(monkeypatch):
    from web_scout.scraping import plan as scraping_plan

    head = _MockResponse(headers={"content-type": "application/json; charset=utf-8"})
    get = _MockResponse(headers={"content-type": "application/json; charset=utf-8"}, text='{"ok": true}')
    monkeypatch.setattr(
        scraping_plan.httpx,
        "AsyncClient",
        _mock_async_client_factory(head_response=head, get_response=get),
    )

    verdict, detail = await _validate_url("https://example.org/api/data")
    assert verdict == ScrapeStrategy.JSON
    assert detail == "application/json"


@pytest.mark.asyncio
async def test_validate_url_routes_image_response(monkeypatch):
    from web_scout.scraping import plan as scraping_plan

    head = _MockResponse(headers={"content-type": "image/png"})
    monkeypatch.setattr(scraping_plan.httpx, "AsyncClient", _mock_async_client_factory(head_response=head))

    verdict, detail = await _validate_url("https://example.org/chart.png")
    assert verdict == ScrapeStrategy.IMAGE
    assert detail == "image/png"


@pytest.mark.asyncio
async def test_validate_url_keeps_short_metadata_pages(monkeypatch):
    from web_scout.scraping import plan as scraping_plan

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
        scraping_plan.httpx,
        "AsyncClient",
        _mock_async_client_factory(head_response=head, get_response=get),
    )

    verdict, detail = await _validate_url("https://example.org/record/10")
    assert verdict == ScrapeStrategy.HTML_FAST
    assert "metadata" in detail


@pytest.mark.asyncio
async def test_validate_url_keeps_rich_script_heavy_html_on_fast_path(monkeypatch):
    """Script-heavy pages with enough static text should not be routed to Playwright."""
    from web_scout.scraping import plan as scraping_plan

    head = _MockResponse(headers={"content-type": "text/html"})
    scripts = "\n".join(f"<script>const payload{i} = '{'x' * 4000}';</script>" for i in range(20))
    body_text = "Kenya procurement certification producer rule. " * 100
    html = f"<html><head><title>Rich page</title>{scripts}</head><body>{body_text}</body></html>"
    get = _MockResponse(headers={"content-type": "text/html"}, text=html)
    monkeypatch.setattr(
        scraping_plan.httpx,
        "AsyncClient",
        _mock_async_client_factory(head_response=head, get_response=get),
    )

    verdict, detail = await _validate_url("https://example.org/rich-script-page")

    assert verdict == ScrapeStrategy.HTML_FAST
    assert verdict != ScrapeStrategy.HTML_BROWSER
    assert "static HTML" in detail


@pytest.mark.asyncio
async def test_validate_url_rich_publication_page_with_download_stays_static(
    monkeypatch,
):
    from web_scout.scraping import plan as scraping_plan

    head = _MockResponse(headers={"content-type": "text/html"})
    prose = (
        "The page already contains substantial narrative findings on rainfall "
        "trends, regional contrasts, recent anomalies, and climate impacts. "
        "It explains how the recent observations compare with longer-term "
        "climatology and seasonal performance across Kenya. "
    ) * 10
    html = (
        """
    <html>
      <head><title>State of Climate Publication</title></head>
      <body>
        <h1>State of Climate Publication</h1>
        <p>Abstract: This publication summarises Kenya precipitation variability,
        observed trends, recent anomalies, and seasonal outlooks.</p>
        <p>Authors: Kenya Meteorological Department. Published: 2025.</p>
        <p><a href="/files/state-of-climate.pdf">Download PDF</a></p>
        <p>%s</p>
      </body>
    </html>
    """
        % prose
    )
    get = _MockResponse(headers={"content-type": "text/html"}, text=html)
    monkeypatch.setattr(
        scraping_plan.httpx,
        "AsyncClient",
        _mock_async_client_factory(head_response=head, get_response=get),
    )

    verdict, detail = await _validate_url("https://example.org/publication/state-of-climate")

    assert verdict == ScrapeStrategy.HTML_FAST
    assert "metadata-like HTML" not in detail
    assert "static HTML" in detail


@pytest.mark.asyncio
async def test_validate_url_overrides_json_head_with_pdf_payload(monkeypatch):
    from web_scout.scraping import plan as scraping_plan

    head = _MockResponse(headers={"content-type": "application/json"})
    get = _MockResponse(
        headers={
            "content-type": "application/json",
            "content-disposition": 'attachment; filename="report.pdf"',
        },
        text="%PDF-1.7 mock data",
        content=b"%PDF-1.7 mock data",
    )
    monkeypatch.setattr(
        scraping_plan.httpx,
        "AsyncClient",
        _mock_async_client_factory(head_response=head, get_response=get),
    )

    verdict, detail = await _validate_url("https://example.org/download?id=123")

    assert verdict == ScrapeStrategy.DOCUMENT
    assert detail == "application/json"


@pytest.mark.asyncio
async def test_validate_url_overrides_json_head_with_html_payload(monkeypatch):
    from web_scout.scraping import plan as scraping_plan

    head = _MockResponse(headers={"content-type": "application/json"})
    html = "<html><head><title>Report page</title></head><body>" + ("Kenya rainfall analysis " * 100) + "</body></html>"
    get = _MockResponse(
        headers={"content-type": "application/json"},
        text=html,
        content=html.encode("utf-8"),
    )
    monkeypatch.setattr(
        scraping_plan.httpx,
        "AsyncClient",
        _mock_async_client_factory(head_response=head, get_response=get),
    )

    verdict, detail = await _validate_url("https://example.org/download?id=123")

    assert verdict == ScrapeStrategy.HTML_FAST
    assert "static HTML" in detail


@pytest.mark.asyncio
async def test_download_pdf_bytes_falls_back_to_raw_download(monkeypatch):
    from web_scout.scraping import strategy as scraping_strategy

    class _FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            raise RuntimeError("Unsupported content-encoding: None")

    monkeypatch.setattr(scraping_strategy.httpx, "AsyncClient", _FailingAsyncClient)
    monkeypatch.setattr(
        scraping_strategy,
        "_download_binary_via_urllib",
        lambda url: (b"%PDF-1.7 mock data", "application/pdf"),
    )

    async def _unexpected_browser_download(url):
        raise AssertionError("browser fallback should not be needed")

    monkeypatch.setattr(scraping_strategy, "_download_pdf_via_browser", _unexpected_browser_download)

    pdf_bytes, error = await _download_pdf_bytes("https://example.org/report.pdf")

    assert error is None
    assert pdf_bytes == b"%PDF-1.7 mock data"


@pytest.mark.asyncio
async def test_scrape_url_passes_document_metadata_from_validation(monkeypatch):
    """scrape_url should not discard content metadata discovered during validation."""
    import web_scout.scraping as scraping

    captured_kwargs = {}

    async def _fake_build_scrape_plan(url, allowed_domains=None):
        return ScrapePlan(
            ScrapeStrategy.DOCUMENT,
            "application/octet-stream",
            "application/octet-stream",
            'attachment; filename="report.pdf"',
        )

    async def _fake_scrape_document(url, **kwargs):
        captured_kwargs.update(kwargs)
        return "PDF content", "report.pdf", None

    monkeypatch.setattr(scraping, "build_scrape_plan", _fake_build_scrape_plan)
    monkeypatch.setattr(scraping, "scrape_document", _fake_scrape_document)

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
