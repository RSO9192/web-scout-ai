"""Unit tests for PDF section visual labeling and enrichment."""

from __future__ import annotations

import pytest

from web_scout.scraping import _document as doc_module
from web_scout.scraping._document import (
    PdfSection,
    PdfVisual,
    _apply_visual_summaries,
    _column_count,
    _column_index,
    _format_visual_block,
    _HeadingAnchor,
    _label_section_placeholders,
    _layout_sort_key,
    _section_index_for_picture,
)
from web_scout.scraping.context import URLContext
from web_scout.scraping.types import FetchResult, SourceArtifact


def test_label_section_placeholders_assigns_stable_ids():
    markdown = "Intro\n\n<!-- image -->\n\nMid\n\n<!-- image -->\n\nEnd"
    labeled, ids = _label_section_placeholders(markdown, section_index=2)
    assert ids == ["s2-v0", "s2-v1"]
    assert "<!-- visual:s2-v0 -->" in labeled
    assert "<!-- visual:s2-v1 -->" in labeled
    assert "<!-- image -->" not in labeled


def test_apply_visual_summaries_replaces_only_known_ids():
    labeled = "A\n\n<!-- visual:s0-v0 -->\n\nB\n\n<!-- visual:s0-v1 -->\n\nC"
    result = _apply_visual_summaries(
        labeled,
        {"s0-v0": "**Chart:** values 1, 2, 3"},
    )
    assert "**Chart:** values 1, 2, 3" in result
    assert "<!-- visual:s0-v0 -->" not in result
    assert "<!-- visual:s0-v1 -->" in result


def test_apply_visual_summaries_includes_debug_header():
    labeled = "Intro\n\n<!-- visual:s0-v0 -->\n"
    visual = PdfVisual(
        visual_id="s0-v0",
        png_bytes=b"png",
        page=11,
        bbox="l=320.0, t=80.0, r=560.0, b=280.0 (TOPLEFT)",
        width=400,
        height=200,
    )
    result = _apply_visual_summaries(
        labeled,
        {"s0-v0": "Cereal production line chart."},
        visuals=[visual],
        vision_model="gemini/gemini-3.7-flash",
    )
    assert "[Summarized Image: gemini/gemini-3.7-flash]" in result
    assert "page: 11" in result
    assert "bounding box: l=320.0, t=80.0, r=560.0, b=280.0 (TOPLEFT)" in result
    assert "Summary:\nCereal production line chart." in result


def test_section_index_for_picture_uses_page_and_y_not_reading_order():
    """A physical-page-14 chart must not attach to a later page-17 heading."""
    anchors = [
        _HeadingAnchor(section_index=80, page=14, top=40.0),
        _HeadingAnchor(section_index=81, page=14, top=560.0),
        _HeadingAnchor(section_index=91, page=17, top=40.0),
    ]
    assert _section_index_for_picture(anchors, page=14, top=590.0) == 81
    assert _section_index_for_picture(anchors, page=17, top=512.0) == 91


def test_later_heading_on_earlier_page_does_not_steal_figure():
    """Document-order last-wins is wrong: a later heading on page 13 must not take a page-14 chart."""
    anchors = [
        _HeadingAnchor(section_index=80, page=14, top=40.0),
        _HeadingAnchor(section_index=81, page=14, top=560.0),
        _HeadingAnchor(section_index=90, page=13, top=20.0),
        _HeadingAnchor(section_index=91, page=17, top=40.0),
    ]
    assert _section_index_for_picture(anchors, page=14, top=590.0) == 81


def test_column_count_detects_two_and_three_columns():
    assert _column_count([20.0, 360.0], page_width=600.0) == 2
    assert _column_count([20.0, 220.0, 420.0], page_width=600.0) == 3
    assert _column_count([20.0, 40.0], page_width=600.0) == 1


def test_column_index_uses_nearest_center():
    three = [28.0, 212.0, 396.0]
    assert _column_index(28.0, three) == 0
    assert _column_index(212.6, three) == 1
    assert _column_index(396.9, three) == 2


def test_layout_sort_key_reads_columns_then_pages():
    """Left column finishes before the right-column chart; page 14 before page 15."""
    left_lower = _layout_sort_key(page=14, col=0, top=400.0)
    right_upper = _layout_sort_key(page=14, col=1, top=80.0)
    next_page_left = _layout_sort_key(page=15, col=0, top=40.0)
    next_page_middle = _layout_sort_key(page=15, col=1, top=40.0)
    assert left_lower < right_upper < next_page_left < next_page_middle


def test_format_visual_block_layout():
    visual = PdfVisual(visual_id="s1-v0", png_bytes=b"x", page=14, bbox="l=1.0, t=2.0, r=3.0, b=4.0 (TOPLEFT)")
    block = _format_visual_block(visual, "hello", "gemini/gemini-3.7-flash")
    assert block.startswith("[Summarized Image: gemini/gemini-3.7-flash]\n")
    assert "page: 14\n" in block
    assert "bounding box: l=1.0, t=2.0, r=3.0, b=4.0 (TOPLEFT)\n" in block
    assert block.endswith("Summary:\nhello")


@pytest.mark.asyncio
async def test_enrich_skips_gemini_when_section_has_no_visuals(monkeypatch):
    called = {"count": 0}

    async def _should_not_run(**kwargs):
        called["count"] += 1
        return {}, None

    monkeypatch.setattr("web_scout.scraping._vision.describe_section_visuals", _should_not_run)

    sections = [
        PdfSection(
            index=0,
            title="Summary",
            level=1,
            page_start=1,
            page_end=1,
            markdown="## Summary\n\nPlain text only.",
            visuals=[],
        )
    ]
    result = await doc_module._enrich_sections_with_visuals(sections, vision_model="gemini/gemini-3.7-flash")
    assert result == "## Summary\n\nPlain text only."
    assert called["count"] == 0


@pytest.mark.asyncio
async def test_enrich_leaves_placeholders_when_gemini_fails(monkeypatch):
    async def _fail(**kwargs):
        return {}, "boom"

    monkeypatch.setattr("web_scout.scraping._vision.describe_section_visuals", _fail)

    sections = [
        PdfSection(
            index=0,
            title="Charts",
            level=1,
            page_start=1,
            page_end=1,
            markdown="## Charts\n\n<!-- image -->\n\nText",
            visuals=[
                PdfVisual(visual_id="s0-v0", png_bytes=b"png", page=1, caption="", width=200, height=200),
            ],
        ),
        PdfSection(
            index=1,
            title="Notes",
            level=1,
            page_start=2,
            page_end=2,
            markdown="## Notes\n\nNo figures.",
            visuals=[],
        ),
    ]
    result = await doc_module._enrich_sections_with_visuals(sections, vision_model="gemini/gemini-3.7-flash")
    assert "<!-- image -->" in result
    assert "## Notes" in result
    assert "No figures." in result


@pytest.mark.asyncio
async def test_enrich_replaces_placeholders_with_summaries(monkeypatch):
    async def _ok(*, section_markdown, visuals, vision_model, section_title=""):
        assert "<!-- visual:s0-v0 -->" in section_markdown
        assert visuals[0]["visual_id"] == "s0-v0"
        return {"s0-v0": "ROI chart: average return USD 2.34"}, None

    monkeypatch.setattr("web_scout.scraping._vision.describe_section_visuals", _ok)

    sections = [
        PdfSection(
            index=0,
            title="Return on investment",
            level=1,
            page_start=5,
            page_end=5,
            markdown="## ROI\n\n<!-- image -->",
            visuals=[
                PdfVisual(visual_id="s0-v0", png_bytes=b"png", page=5, caption="ROI", width=400, height=300),
            ],
        )
    ]
    result = await doc_module._enrich_sections_with_visuals(sections, vision_model="gemini/gemini-3.7-flash")
    assert "ROI chart: average return USD 2.34" in result
    assert "[Summarized Image: gemini/gemini-3.7-flash]" in result
    assert "page: 5" in result
    assert "<!-- image -->" not in result
    assert "<!-- visual:" not in result


@pytest.mark.asyncio
async def test_convert_pdf_to_markdown_skips_enrichment_without_vision_model(monkeypatch):
    section = PdfSection(
        index=0,
        title="Summary",
        level=1,
        page_start=1,
        page_end=1,
        markdown="## Summary\n\n<!-- image -->\n\nBody",
        visuals=[
            PdfVisual(visual_id="s0-v0", png_bytes=b"png", page=1, caption="", width=200, height=200),
        ],
    )

    def _fake_extract(pdf_bytes, url, max_pages):
        return [section]

    called = {"count": 0}

    async def _should_not_run(**kwargs):
        called["count"] += 1
        return "should not run"

    monkeypatch.setattr(doc_module, "_extract_pdf_sections_locked", _fake_extract)
    monkeypatch.setattr(doc_module, "_enrich_sections_with_visuals", _should_not_run)

    result = await doc_module._convert_pdf_to_markdown(b"%PDF", "https://example.org/a.pdf", 2)
    assert result == section.markdown
    assert called["count"] == 0


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


def test_text_without_image_placeholders_ignores_tokens():
    text = doc_module._text_without_image_placeholders("Hi <!-- image --> there <!-- visual:s0-v0 --> end")
    assert text == "Hi  there  end"
