"""Public fetch() returns the ScraplingFetcher FetchResult."""

import pytest

from web_scout import PDF_MAGIC_BYTES, fetch, fetch_pdf
from web_scout.scraping import FetchResult, ScraplingFetcher


def _result(url: str, **overrides) -> FetchResult:
    payload = dict(
        url=url,
        status=200,
        content_type="text/html",
        content_disposition="",
        html_content=None,
        body=None,
        headers={},
        used_browser=False,
    )
    payload.update(overrides)
    return FetchResult(**payload)


@pytest.mark.asyncio
async def test_fetch_returns_fetch_result(monkeypatch):
    url = "https://example.org/report.pdf"
    pdf = b"%PDF-1.7"
    captured: dict = {}

    async def _fake(self, requested_url, context):
        captured["url"] = requested_url
        captured["exclude_domains"] = self._exclude_domains
        captured["wait_for"] = context.wait_for
        return _result(url, content_type="application/pdf", body=pdf)

    monkeypatch.setattr(ScraplingFetcher, "fetch", _fake)

    result = await fetch(url, exclude_domains=["evil.test"], wait_for="#ready")

    assert isinstance(result, FetchResult)
    assert result.body == pdf
    assert result.body.startswith(PDF_MAGIC_BYTES)
    assert captured == {
        "url": url,
        "exclude_domains": frozenset({"evil.test"}),
        "wait_for": "#ready",
    }


@pytest.mark.asyncio
async def test_fetch_returns_html_on_html_content(monkeypatch):
    url = "https://example.org/article"
    html = "<html>hello</html>"

    async def _fake(self, requested_url, context):
        return _result(url, html_content=html)

    monkeypatch.setattr(ScraplingFetcher, "fetch", _fake)

    result = await fetch(url)

    assert result.html_content == html
    assert result.body is None
    assert not (result.body and result.body.startswith(PDF_MAGIC_BYTES))


@pytest.mark.asyncio
async def test_fetch_returns_error_on_the_result(monkeypatch):
    url = "https://blocked.example/page"

    async def _fake(self, requested_url, context):
        return _result(url, status=403, error="blocked domain")

    monkeypatch.setattr(ScraplingFetcher, "fetch", _fake)

    result = await fetch(url)

    assert result.status == 403
    assert result.error == "blocked domain"
    assert result.body is None


@pytest.mark.asyncio
async def test_fetch_pdf_returns_pdf_bytes(monkeypatch):
    url = "https://example.org/report.pdf"
    pdf = b"%PDF-1.7"
    captured: dict = {}

    async def _fake(self, requested_url, context):
        captured["exclude_domains"] = self._exclude_domains
        captured["wait_for"] = context.wait_for
        return _result(url, content_type="application/pdf", body=pdf)

    monkeypatch.setattr(ScraplingFetcher, "fetch", _fake)

    assert await fetch_pdf(url, exclude_domains=["evil.test"], wait_for="#ready") == pdf
    assert captured == {
        "exclude_domains": frozenset({"evil.test"}),
        "wait_for": "#ready",
    }


@pytest.mark.asyncio
async def test_fetch_pdf_rejects_non_pdf_body(monkeypatch):
    url = "https://example.org/article"

    async def _fake(self, requested_url, context):
        return _result(url, html_content="<html>hello</html>")

    monkeypatch.setattr(ScraplingFetcher, "fetch", _fake)

    with pytest.raises(RuntimeError, match="not a PDF"):
        await fetch_pdf(url)


@pytest.mark.asyncio
async def test_fetch_pdf_raises_fetch_error(monkeypatch):
    url = "https://blocked.example/report.pdf"

    async def _fake(self, requested_url, context):
        return _result(url, status=403, error="blocked domain")

    monkeypatch.setattr(ScraplingFetcher, "fetch", _fake)

    with pytest.raises(RuntimeError, match="blocked domain"):
        await fetch_pdf(url)
