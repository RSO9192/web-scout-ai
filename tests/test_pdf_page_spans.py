"""Unit tests for PDF page banners, layout metadata, extraction helpers, and citations."""

from __future__ import annotations

from web_scout._pipeline_rules import _build_citation_slots, _resolve_slot_citations
from web_scout.scraping._document import (
    PdfSection,
    _infer_document_title,
    _layout_from_markdown,
    _page_end_banner,
    _page_start_banner,
    _PageBannerState,
    _text_without_image_placeholders,
)
from web_scout.scraping.types import PdfDocumentLayout, PdfPageSpan, PdfSectionSpan
from web_scout.tools.pdf_extractor import (
    PdfEvidenceItem,
    filter_evidence_by_layout,
    format_page_span,
    format_reference,
    pack_section_chunks,
)


def test_page_banner_state_transitions():
    state = _PageBannerState()
    assert state.transition(1) == [_page_start_banner(1)]
    assert state.transition(1) == []
    assert state.transition(2) == [_page_end_banner(1), _page_start_banner(2)]
    assert state.close() == [_page_end_banner(2)]
    assert state.close() == []


def test_text_without_placeholders_strips_page_banners():
    md = (
        f"{_page_start_banner(1)}\n\nHello\n\n{_page_end_banner(1)}\n\n"
        "<!-- visual:s0-v0 -->\n"
    )
    assert _text_without_image_placeholders(md) == "Hello"


def test_infer_document_title_uses_highest_heading():
    sections = [
        PdfSection(0, "Subtitle", 2, 1, 1, "body", []),
        PdfSection(1, "Main Title", 1, 1, 2, "body", []),
        PdfSection(2, "Other", 1, 2, 2, "body", []),
    ]
    assert _infer_document_title(sections, "fallback.pdf") == "Main Title"
    assert _infer_document_title([], "fallback.pdf") == "fallback.pdf"


def test_layout_from_markdown_maps_pages_and_sections():
    s0 = (
        f"{_page_start_banner(1)}\n\n"
        "## Main Title\n\nIntro text\n\n"
        f"{_page_end_banner(1)}\n\n"
        f"{_page_start_banner(2)}\n\n"
        "More on page 2"
    )
    s1 = f"## Methods\n\nDetails\n\n{_page_end_banner(2)}"
    sections = [
        PdfSection(0, "Main Title", 1, 1, 2, s0, []),
        PdfSection(1, "Methods", 2, 2, 2, s1, []),
    ]
    joined = f"{s0}\n\n{s1}"
    layout = _layout_from_markdown(joined, sections, document_title="Main Title")
    assert layout.document_title == "Main Title"
    assert [p.page for p in layout.pages] == [1, 2]
    assert layout.pages[0].start_line == 1
    assert len(layout.sections) == 2
    assert layout.sections[0].title == "Main Title"
    assert layout.sections[0].level == 1
    assert layout.sections[0].heading_path == ("Main Title",)
    assert layout.sections[1].heading_path == ("Main Title", "Methods")
    assert layout.sections[0].page_start == 1
    assert layout.sections[1].page_end == 2


def test_format_reference_and_page_spans():
    assert format_page_span([3]) == "p. 3"
    assert format_page_span([3, 4, 5]) == "pp. 3–5"
    assert format_page_span([3, 4, 5, 12]) == "pp. 3–5, 12"
    assert format_reference("Crop Prospects", [3, 4, 5]) == "Crop Prospects, pp. 3–5"
    assert format_reference("Article", []) == "Article"


def test_filter_evidence_by_layout_drops_out_of_range():
    layout = PdfDocumentLayout(
        document_title="Doc",
        pages=(
            PdfPageSpan(1, 1, 10),
            PdfPageSpan(2, 11, 20),
        ),
    )
    evidence = [
        PdfEvidenceItem(text="ok", page_start=1, page_end=2),
        PdfEvidenceItem(text="bad", page_start=9, page_end=9),
        PdfEvidenceItem(text="inverted", page_start=2, page_end=1),
    ]
    kept = filter_evidence_by_layout(evidence, layout)
    assert len(kept) == 1
    assert kept[0].text == "ok"


def test_pack_section_chunks_packs_and_splits():
    markdown = "\n".join(
        [
            _page_start_banner(1),
            "A" * 50,
            _page_end_banner(1),
            _page_start_banner(2),
            "B" * 50,
            _page_end_banner(2),
            _page_start_banner(3),
            "C" * 200,
            _page_end_banner(3),
        ]
    )
    # Build artificial section spans covering the whole markdown.
    lines = markdown.splitlines()
    layout = PdfDocumentLayout(
        document_title="Doc",
        pages=(
            PdfPageSpan(1, 1, 3),
            PdfPageSpan(2, 4, 6),
            PdfPageSpan(3, 7, 9),
        ),
        sections=(
            PdfSectionSpan("A", 1, 1, 3, 1, 1, ("A",)),
            PdfSectionSpan("B", 1, 4, 6, 2, 2, ("B",)),
            PdfSectionSpan("C", 1, 7, 9, 3, 3, ("C",)),
        ),
    )
    packed = pack_section_chunks(markdown, layout, max_chars=120)
    assert len(packed) >= 2
    assert all(len(chunk[2]) <= 200 for chunk in packed)


def test_pdf_reference_page_span_becomes_citation_link_text():
    """A PDF entry's reference (with page span) is rendered as the resolved link text."""
    from web_scout.models import UrlEntry

    entry = UrlEntry(
        url="https://fao.org/report.pdf",
        title="Crop Prospects",
        reference="Crop Prospects, pp. 3–7",
        content="x",
    )
    slots = _build_citation_slots([entry])
    resolved, unknown = _resolve_slot_citations("Fact [S1].", slots)
    assert resolved == "Fact [Crop Prospects, pp. 3–7](https://fao.org/report.pdf)."
    assert unknown == []


def test_url_entry_reference_field():
    from web_scout.models import UrlEntry

    entry = UrlEntry(
        url="https://fao.org/report.pdf",
        title="Crop Prospects",
        content="evidence",
        reference="Crop Prospects, pp. 3–7",
    )
    assert entry.reference == "Crop Prospects, pp. 3–7"
    assert entry.model_dump()["reference"] == "Crop Prospects, pp. 3–7"
