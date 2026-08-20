"""Optional live integration: direct-URL PDF research with page-span citations.

Disabled by default. Enable with::

    RUN_PDF_PAGE_SPAN_INTEGRATION=1

Requires ``GEMINI_API_KEY`` (loaded from ``.env`` when present).

Uses ``run_web_research(..., direct_url=<Open Knowledge bitstream>)`` for the
Crop Prospects PDF. Docling+vision conversion is injected from a page-bannered
fixture derived from the local enriched markdown (chart on physical page 17),
so the test focuses on PDF extract → synthesize → cite ``pp. 17`` without a
multi-minute Docling run. Set ``RUN_PDF_PAGE_SPAN_LIVE_DOCLING=1`` to convert
the local PDF bytes with live Docling/Gemini instead.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

# Load repo .env so GEMINI_API_KEY is available without exporting manually.
_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
if _ENV_PATH.is_file():
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

from web_scout import run_web_research
from web_scout._pipeline_types import DEFAULT_WEB_RESEARCH_MODELS
from web_scout.scraping._document import scrape_document
from web_scout.scraping.types import (
    PdfDocumentLayout,
    PdfPageSpan,
    PdfSectionSpan,
    FetchResult,
    ParseResult,
    SourceArtifact,
)

PDF_URL = "https://openknowledge.fao.org/server/api/core/bitstreams/1c2612f4-2392-4938-8dd6-064c61d1528f/content"
LOCAL_PDF = (
    Path(__file__).parent
    / "test_data"
    / "Crop Prospects and Food Situation - Triannual Global Report, No. 2, July 2026.pdf"
)
QUERY = "How millet prices in Niger changed over the period from June 2024 to December 2024?"

_ENABLED = os.getenv("RUN_PDF_PAGE_SPAN_INTEGRATION", "").strip() in {"1", "true", "TRUE", "yes"}
_LIVE_DOCLING = os.getenv("RUN_PDF_PAGE_SPAN_LIVE_DOCLING", "").strip() in {"1", "true", "TRUE", "yes"}
_MISSING_KEY = not bool(os.getenv("GEMINI_API_KEY"))

_CHART_BLOCK = """\
[Summarized Image: gemini/gemini-3.7-flash]
page: 17
bounding box: l=40.9, t=511.9, r=238.5, b=763.9 (TOPLEFT)
Summary:
### Millet prices in selected West African markets

**Chart Type & Unit:** Multi-line chart showing monthly millet prices in **CFA franc BCEAO per 100 kg** from May 2024 to May 2026.

- **Y-Axis:** Price in CFA franc BCEAO/100 kg, ranging from 15,000 to 45,000.
- **X-Axis:** Monthly timeline from May 2024 through May 2026.

### Series & Key Data Trends:
1. **NIGER (Niamey)** *(grey line)*:
   - June 2024 ≈ 35,000 CFA franc/100 kg.
   - July 2024 ≈ 40,000 CFA franc/100 kg.
   - August 2024 peaked at ≈ 44,000 CFA franc/100 kg (highest in the period).
   - September 2024 ≈ 43,000 CFA franc/100 kg.
   - October 2024 dropped sharply to ≈ 32,000 CFA franc/100 kg.
   - November 2024 ≈ 28,000 CFA franc/100 kg (lowest in June–December 2024).
   - December 2024 ≈ 29,500 CFA franc/100 kg (slight rebound).

2. **BURKINA FASO (Ouagadougou)** *(yellow line)*: peaked near 42,000 in August 2024.
3. **MALI (Bamako)** *(red line)*: rose from ≈ 26,000 in June to ≈ 38,000 by September–October 2024.
"""


def _bannered_fixture_markdown() -> tuple[str, PdfDocumentLayout]:
    """Build short bannered markdown containing the page-17 millet chart."""
    title = "Crop Prospects and Food Situation"
    parts = [
        "========== page 16 start ==========",
        f"# {title}",
        "",
        "## WEST AFRICA",
        "",
        "## Prices of coarse grains lower on a yearly basis",
        "",
        "Prices of coarse grains followed mixed trends between January and May 2026.",
        "========== page 16 end ==========",
        "========== page 17 start ==========",
        "",
        "## Millet prices in selected West African markets",
        "",
        _CHART_BLOCK.strip(),
        "",
        "========== page 17 end ==========",
        "========== page 18 start ==========",
        "",
        "## CENTRAL AFRICA",
        "",
        "Ongoing conflicts remain key drivers of expected below-average cereal harvests.",
        "========== page 18 end ==========",
    ]
    markdown = "\n".join(parts)
    lines = markdown.splitlines()
    # Locate banners for layout.
    page_spans: list[PdfPageSpan] = []
    open_page = None
    open_start = None
    for idx, line in enumerate(lines, start=1):
        m = re.match(r"^========== page (\d+) start ==========$", line.strip())
        if m:
            open_page = int(m.group(1))
            open_start = idx
            continue
        m = re.match(r"^========== page (\d+) end ==========$", line.strip())
        if m and open_page is not None and open_start is not None:
            page_spans.append(PdfPageSpan(page=open_page, start_line=open_start, end_line=idx))
            open_page = None
            open_start = None

    sections = (
        PdfSectionSpan(
            title=title,
            level=1,
            start_line=1,
            end_line=len(lines),
            page_start=16,
            page_end=18,
            heading_path=(title,),
        ),
        PdfSectionSpan(
            title="Millet prices in selected West African markets",
            level=2,
            start_line=next(i for i, l in enumerate(lines, 1) if "Millet prices in selected" in l),
            end_line=next(i for i, l in enumerate(lines, 1) if l.strip() == "========== page 17 end =========="),
            page_start=17,
            page_end=17,
            heading_path=(title, "Millet prices in selected West African markets"),
        ),
    )
    layout = PdfDocumentLayout(document_title=title, pages=tuple(page_spans), sections=sections)
    return markdown, layout


def _normalize_number_token(token: str) -> int | None:
    cleaned = token.replace(",", "").replace(" ", "").replace("\u00a0", "")
    if cleaned.isdigit():
        return int(cleaned)
    return None


def _numbers_near(text: str, target: int, tol: int) -> bool:
    for match in re.finditer(r"\d[\d\s,\u00a0]{2,}\d", text):
        value = _normalize_number_token(match.group(0))
        if value is None:
            continue
        if abs(value - target) <= tol:
            return True
    return False


pytestmark = [
    pytest.mark.skipif(not _ENABLED, reason="Set RUN_PDF_PAGE_SPAN_INTEGRATION=1 to enable"),
    pytest.mark.skipif(_MISSING_KEY, reason="GEMINI_API_KEY not set"),
]


@pytest.mark.asyncio
async def test_direct_url_millet_prices_niger_page17_chart(monkeypatch):
    import litellm
    import web_scout.scraping as scraping_pkg

    if _LIVE_DOCLING:
        assert LOCAL_PDF.is_file(), f"Missing local fixture PDF: {LOCAL_PDF}"
        pdf_bytes = LOCAL_PDF.read_bytes()

        async def _fake_fetch_and_parse_url(url, **kwargs):
            assert url == PDF_URL
            artifact, error = await scrape_document(
                url,
                max_pdf_pages=kwargs.get("max_pdf_pages", 20),
                known_content_type="application/pdf",
                prefetched_bytes=pdf_bytes,
                vision_model=kwargs.get("vision_model"),
            )
            fetch = FetchResult(
                url=url,
                status=200,
                content_type="application/pdf",
                content_disposition="",
                html_content=None,
                body=pdf_bytes,
                headers={"content-type": "application/pdf"},
                used_browser=False,
            )
            parse = ParseResult(
                url=url,
                title=artifact.title,
                text_content=artifact.text_content,
                links=[],
                artifact=artifact,
                error=error,
            )
            return fetch, parse
    else:
        markdown, layout = _bannered_fixture_markdown()
        assert "44,000" in markdown or "44000" in markdown.replace(",", "")
        assert "page 17" in markdown

        async def _fake_fetch_and_parse_url(url, **kwargs):
            assert url == PDF_URL
            artifact = SourceArtifact(
                kind="text",
                title=layout.document_title,
                text_content=markdown,
                layout=layout,
            )
            fetch = FetchResult(
                url=url,
                status=200,
                content_type="application/pdf",
                content_disposition="",
                html_content=None,
                body=b"%PDF-fixture",
                headers={"content-type": "application/pdf"},
                used_browser=False,
            )
            parse = ParseResult(
                url=url,
                title=artifact.title,
                text_content=artifact.text_content,
                links=[],
                artifact=artifact,
            )
            return fetch, parse

    monkeypatch.setattr(scraping_pkg, "fetch_and_parse_url", _fake_fetch_and_parse_url)

    result = await run_web_research(
        query=QUERY,
        models=DEFAULT_WEB_RESEARCH_MODELS,
        direct_url=PDF_URL,
        max_pdf_pages=20,
        short_pdf_max_chars=24_000,
    )

    assert result.scraped, (
        f"Expected at least one scraped source; failed={result.scrape_failed!r}; synthesis={result.synthesis!r}"
    )
    synthesis = result.synthesis or ""
    assert synthesis.strip(), "Empty synthesis"
    assert "Synthesis failed" not in synthesis

    scraped = result.scraped[0]
    assert "1c2612f4-2392-4938-8dd6-064c61d1528f" in scraped.url

    combined = f"{synthesis}\n{scraped.content}\n{scraped.reference}\n{scraped.title}"
    lowered = combined.lower()

    assert "niger" in lowered or "niamey" in lowered
    assert "millet" in lowered

    reference = scraped.reference or ""
    cite_blob = f"{synthesis}\n{reference}"
    assert re.search(r"\bpp?\.?\s*[^)\n]*\b17\b", cite_blob, re.IGNORECASE), (
        f"Expected page 17 in citation/reference; reference={reference!r}; synthesis={synthesis[:800]!r}"
    )

    assert _numbers_near(combined, 44_000, 3_000), "Missing August 2024 peak near 44,000 CFA for Niger millet prices"
    assert _numbers_near(combined, 28_000, 3_000), (
        "Missing November 2024 trough near 28,000 CFA for Niger millet prices"
    )

    judge_prompt = (
        "You judge whether a research synthesis correctly describes millet prices "
        "in Niger (Niamey) from June 2024 to December 2024 based on a line chart.\n"
        "Ground truth from the chart (CFA franc BCEAO per 100 kg, approximate):\n"
        "- June 2024 ≈ 35,000\n"
        "- July 2024 ≈ 40,000\n"
        "- August 2024 ≈ 44,000 (peak in the period)\n"
        "- September 2024 ≈ 43,000\n"
        "- October 2024 ≈ 32,000\n"
        "- November 2024 ≈ 28,000 (low in the period)\n"
        "- December 2024 ≈ 29,500\n"
        "Trend: sharp rise into Aug peak, then sharp decline through Oct–Nov, slight rebound in Dec.\n"
        "Accept approximate numbers (±3,000) and paraphrase. Reject wrong country, wrong commodity, "
        "or inverted trend.\n"
        "Return JSON only: "
        '{"correct": true/false, "notes": "brief", "missing": ["..."]}\n\n'
        f"Synthesis:\n{synthesis}\n\n"
        f"Extractor content:\n{scraped.content}\n"
    )
    response = await litellm.acompletion(
        model=DEFAULT_WEB_RESEARCH_MODELS["vision_fallback"],
        messages=[{"role": "user", "content": judge_prompt}],
        response_format={"type": "json_object"},
    )
    raw = (response.choices[0].message.content or "").strip()
    assert raw, "Semantic judge returned empty content"
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    verdict = json.loads(raw)
    assert verdict.get("correct") is True, f"Semantic judge failed: {verdict}; synthesis={synthesis}"
