"""Prefect caches for PDF bytes and URL fetches."""

import asyncio

import pytest

import web_scout.scraping._document as document_module
from web_scout.result_cache import use_result_cache
from web_scout.scraping import FetchResult, ParseResult
from web_scout.scraping._document import _convert_pdf_to_markdown
from web_scout.scraping.types import PdfDocumentLayout, SourceArtifact
from web_scout.tools.session_cache import get_or_fetch_session_source_artifact
from web_scout.tools.types import CachedSourceArtifact


def _parse_result(url: str, content: str) -> ParseResult:
    artifact = SourceArtifact(kind="text", title="Report", text_content=content)
    return ParseResult(url=url, title="Report", text_content=content, links=[], artifact=artifact)


@pytest.mark.asyncio
async def test_same_pdf_bytes_are_parsed_once(monkeypatch):
    calls = {"n": 0}
    layout = PdfDocumentLayout(document_title="Report")

    async def _fake_convert(pdf_bytes, max_pages, vision_model, filename):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return f"pages={max_pages} vision={vision_model}", layout

    monkeypatch.setattr(document_module, "_convert_pdf_bytes", _fake_convert)
    pdf = b"%PDF-1.7 same-bytes"

    first, second = await asyncio.gather(
        _convert_pdf_to_markdown(pdf, "https://example.org/a.pdf", 5),
        _convert_pdf_to_markdown(pdf, "https://example.org/b.pdf", 5),
    )

    assert calls["n"] == 1
    assert first == second == ("pages=5 vision=None", layout)

    await _convert_pdf_to_markdown(pdf, "https://example.org/c.pdf", 5)
    assert calls["n"] == 1

    await _convert_pdf_to_markdown(pdf, "https://example.org/a.pdf", 2)
    await _convert_pdf_to_markdown(pdf, "https://example.org/a.pdf", 5, vision_model="gemini/flash")
    assert calls["n"] == 3

    with use_result_cache(refresh_pdf_cache=True):
        await _convert_pdf_to_markdown(pdf, "https://example.org/a.pdf", 5)
    assert calls["n"] == 4


@pytest.mark.asyncio
async def test_same_url_is_fetched_once(monkeypatch):
    calls = {"n": 0}
    url = "https://example.org/report"

    async def _fake_fetch(url, **kwargs):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return FetchResult(
            url=url,
            status=200,
            content_type="text/html",
            content_disposition="",
            html_content="<html></html>",
            body=None,
            headers={},
            used_browser=False,
        ), _parse_result(url, "Broad source content")

    monkeypatch.setattr("web_scout.scraping.fetch_and_parse_url", _fake_fetch)
    kwargs = dict(
        url=url,
        wait_for=None,
        vision_model=None,
        exclude_domains=None,
        max_pdf_pages=50,
    )
    first, second = await asyncio.gather(
        get_or_fetch_session_source_artifact(**kwargs),
        get_or_fetch_session_source_artifact(**kwargs),
    )

    assert calls["n"] == 1
    assert first == second
    assert first[0] == CachedSourceArtifact(
        url=url,
        title="Report",
        artifact_kind="text",
        text_content="Broad source content",
    )

    await get_or_fetch_session_source_artifact(**kwargs)
    assert calls["n"] == 1

    with use_result_cache(refresh_url_cache=True):
        refreshed, error = await get_or_fetch_session_source_artifact(**kwargs)
    assert error is None
    assert refreshed == first[0]
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_caller_storage_is_used_for_url_fetches(monkeypatch, tmp_path):
    url = "https://example.org/stored"

    async def _fake_fetch(url, **kwargs):
        return FetchResult(
            url=url,
            status=200,
            content_type="text/html",
            content_disposition="",
            html_content="<html></html>",
            body=None,
            headers={},
            used_browser=False,
        ), _parse_result(url, "Stored content")

    monkeypatch.setattr("web_scout.scraping.fetch_and_parse_url", _fake_fetch)
    storage = tmp_path / "caller-bucket"
    with use_result_cache(storage):
        artifact, error = await get_or_fetch_session_source_artifact(
            url=url,
            wait_for=None,
            vision_model=None,
            exclude_domains=None,
            max_pdf_pages=10,
        )
    assert error is None
    assert artifact.text_content == "Stored content"
    assert any(path.is_file() for path in storage.rglob("*"))


@pytest.mark.asyncio
async def test_filesystem_block_can_be_cache_storage(monkeypatch, tmp_path):
    from prefect.filesystems import LocalFileSystem

    url = "https://example.org/block"

    async def _fake_fetch(url, **kwargs):
        return FetchResult(
            url=url,
            status=200,
            content_type="text/html",
            content_disposition="",
            html_content="<html></html>",
            body=None,
            headers={},
            used_browser=False,
        ), _parse_result(url, "Block content")

    monkeypatch.setattr("web_scout.scraping.fetch_and_parse_url", _fake_fetch)
    storage = LocalFileSystem(basepath=str(tmp_path / "block"))
    with use_result_cache(storage):
        artifact, error = await get_or_fetch_session_source_artifact(
            url=url,
            wait_for=None,
            vision_model=None,
            exclude_domains=frozenset({"skip.example"}),
            max_pdf_pages=10,
        )
    assert error is None
    assert artifact is not None
    assert artifact.text_content == "Block content"
