"""Unit tests for PDF extraction delegation and visual enrichment."""

from __future__ import annotations

import pytest

from web_scout.scraping import _document as doc_module
from web_scout.scraping._document import _SectionVisionModel
from web_scout.scraping.context import URLContext
from web_scout.scraping.types import FetchResult, PdfDocumentLayout, SourceArtifact


class _Visual:
    def __init__(self, visual_id: str) -> None:
        self.visual_id = visual_id
        self.png_bytes = b"png"
        self.caption = "ROI"
        self.page = 5
        self.bbox = "l=1.0, t=2.0, r=3.0, b=4.0 (TOPLEFT)"


class _Page:
    def __init__(self) -> None:
        self.page = 1
        self.start_line = 1
        self.end_line = 4


class _Section:
    def __init__(self) -> None:
        self.title = "Summary"
        self.level = 1
        self.start_line = 1
        self.end_line = 3
        self.page_start = 1
        self.page_end = 1
        self.heading_path = ("Summary",)


class _Layout:
    def __init__(self) -> None:
        self.pages = (_Page(),)
        self.sections = (_Section(),)


@pytest.mark.asyncio
async def test_section_vision_model_forwards_visuals(monkeypatch):
    captured = {}

    async def _ok(*, section_markdown, visuals, vision_model, section_title=""):
        captured["section_markdown"] = section_markdown
        captured["visuals"] = visuals
        captured["vision_model"] = vision_model
        captured["section_title"] = section_title
        return {"s0-v0": "ROI chart: average return USD 2.34"}, None

    monkeypatch.setattr("web_scout.scraping._vision.describe_section_visuals", _ok)

    model = _SectionVisionModel("gemini/gemini-3.7-flash")
    summaries = await model.describe_section(
        section_title="Return on investment",
        section_text="## ROI\n\nChart context",
        visuals=[_Visual("s0-v0")],
    )

    assert summaries == {"s0-v0": "ROI chart: average return USD 2.34"}
    assert captured["vision_model"] == "gemini/gemini-3.7-flash"
    assert captured["section_title"] == "Return on investment"
    assert "<!-- visual:s0-v0 -->" in captured["section_markdown"]
    assert captured["visuals"][0]["visual_id"] == "s0-v0"
    assert captured["visuals"][0]["png_bytes"] == b"png"
    assert captured["visuals"][0]["page"] == 5


@pytest.mark.asyncio
async def test_section_vision_model_returns_empty_when_gemini_fails(monkeypatch):
    async def _fail(**kwargs):
        return {}, "boom"

    monkeypatch.setattr("web_scout.scraping._vision.describe_section_visuals", _fail)

    summaries = await _SectionVisionModel("gemini/gemini-3.7-flash").describe_section(
        section_title="Charts",
        section_text="## Charts",
        visuals=[_Visual("s0-v0")],
    )
    assert summaries == {}


@pytest.mark.asyncio
async def test_convert_pdf_to_markdown_skips_summaries_without_vision_model(monkeypatch):
    document = object()
    captured = {}

    class _Extractor:
        async def extract_async(self, pdf_bytes, **kwargs):
            captured["pdf_bytes"] = pdf_bytes
            captured["extract_kwargs"] = kwargs
            return document

    async def _should_not_summarize(*args, **kwargs):
        raise AssertionError("summarize_images must not run without a vision model")

    monkeypatch.setattr(doc_module, "_get_pdf_extractor", lambda: _Extractor())
    monkeypatch.setattr(doc_module, "summarize_images", _should_not_summarize)
    monkeypatch.setattr(doc_module, "to_markdown", lambda doc: "## Summary\n\nBody")
    monkeypatch.setattr(doc_module, "document_title", lambda doc, fallback: "Summary")
    monkeypatch.setattr(doc_module, "layout_from_markdown", lambda markdown, doc: _Layout())

    result, layout = await doc_module._convert_pdf_to_markdown(b"%PDF", "https://example.org/a.pdf", 2)

    assert result == "## Summary\n\nBody"
    assert layout == PdfDocumentLayout(
        document_title="Summary",
        pages=layout.pages,
        sections=layout.sections,
    )
    assert layout.document_title == "Summary"
    assert layout.sections[0].heading_path == ("Summary",)
    assert captured["pdf_bytes"] == b"%PDF"
    assert captured["extract_kwargs"]["do_ocr"] is False
    assert captured["extract_kwargs"]["max_pages"] == 2
    assert captured["extract_kwargs"]["name"] == "a.pdf"


@pytest.mark.asyncio
async def test_convert_pdf_to_markdown_summarizes_with_vision_model(monkeypatch):
    document = object()
    summarized = object()
    captured = {}

    class _Extractor:
        async def extract_async(self, pdf_bytes, **kwargs):
            return document

    async def _summarize(doc, model, *, model_name):
        captured["doc"] = doc
        captured["model"] = model
        captured["model_name"] = model_name
        return summarized

    monkeypatch.setattr(doc_module, "_get_pdf_extractor", lambda: _Extractor())
    monkeypatch.setattr(doc_module, "summarize_images", _summarize)
    monkeypatch.setattr(doc_module, "to_markdown", lambda doc: "summarized" if doc is summarized else "raw")
    monkeypatch.setattr(doc_module, "document_title", lambda doc, fallback: fallback)
    monkeypatch.setattr(doc_module, "layout_from_markdown", lambda markdown, doc: _Layout())

    result, layout = await doc_module._convert_pdf_to_markdown(
        b"%PDF",
        "https://example.org/a.pdf",
        2,
        vision_model="gemini/gemini-3.7-flash",
    )

    assert result == "summarized"
    assert captured["doc"] is document
    assert captured["model_name"] == "gemini/gemini-3.7-flash"
    assert captured["model"].vision_model == "gemini/gemini-3.7-flash"
    assert layout.document_title == "a.pdf"


@pytest.mark.asyncio
async def test_default_parser_forwards_vision_model_to_scrape_document(monkeypatch):
    from web_scout.scraping import DefaultParser
    from web_scout.scraping import _document as document_mod

    captured = {}

    async def _fake_scrape(url, **kwargs):
        captured.update(kwargs)
        return SourceArtifact(kind="text", title="doc.pdf", text_content="ok"), None

    monkeypatch.setattr(document_mod, "scrape_document", _fake_scrape)

    parser = DefaultParser(vision_model="gemini/gemini-3.7-flash", max_pdf_pages=10)
    result = await parser.parse_document(
        FetchResult(
            url="https://example.org/doc.pdf",
            status=200,
            content_type="application/pdf",
            content_disposition="",
            html_content=None,
            body=b"%PDF-1.7",
            headers={},
            used_browser=False,
        ),
        URLContext(url="https://example.org/doc.pdf", depth=0),
    )

    assert result.error is None
    assert captured["vision_model"] == "gemini/gemini-3.7-flash"
    assert captured["max_pdf_pages"] == 10
    assert captured["prefetched_bytes"] == b"%PDF-1.7"
