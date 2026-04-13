"""Unit tests for URL routing and content-type detection."""

import pytest

from web_scout.scraping import (
    _SCRAPE_DOC,
    _SCRAPE_HTML,
    _SCRAPE_IMAGE,
    _SCRAPE_JSON,
    _looks_like_document_resource,
    _trim_json_value,
    _validate_url,
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
