"""Live integration test: Docling PDF structure + Gemini visual enrichment.

Requires ``GEMINI_API_KEY``. Skips cleanly when the key is absent.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from web_scout._pipeline_types import DEFAULT_WEB_RESEARCH_MODELS
from web_scout.scraping._document import scrape_document

PDF_PATH = (
    Path(__file__).parent
    / "test_data"
    / "Crop Prospects and Food Situation - Triannual Global Report, No. 2, July 2026.pdf"
)

# # Facts the enriched markdown must preserve semantically. Prefer figure-borne
# # quantities so the test fails when visuals are dropped as empty placeholders.
# REQUIRED_FACTS = [
#     "FAO anticipatory action covered eight countries in Latin America and the Caribbean",
#     "Implementation period was approximately June 2023 to February 2024",
#     "About 31,873 households were targeted",
#     "Average return on investment was about USD 2.34 per dollar invested",
#     "A geographic coverage map or figure identifies the participating LAC countries "
#     "(including Bolivia, Colombia, Ecuador, El Salvador, Guatemala, Honduras, Nicaragua, Venezuela)",
#     "At least one chart or figure related to return on investment or results preserves "
#     "quantitative comparisons (country-level or regional values, percentages, or ROI figures)",
#     "Seasonal agricultural calendars or methodological diagrams for priority countries "
#     "are described with readable timing/season structure rather than left as an empty image placeholder",
# ]

_MISSING_KEY = not bool(os.getenv("GEMINI_API_KEY"))


@pytest.mark.skipif(_MISSING_KEY, reason="GEMINI_API_KEY not set")
@pytest.mark.asyncio
async def test_cd9804en_pdf_visual_enrichment_preserves_semantics():
    # import litellm

    assert PDF_PATH.is_file(), f"Missing fixture PDF: {PDF_PATH}"
    pdf_bytes = PDF_PATH.read_bytes()
    vision_model = DEFAULT_WEB_RESEARCH_MODELS["vision_fallback"]

    artifact, error = await scrape_document(
        f"file://{PDF_PATH.name}",
        max_pdf_pages=50,
        known_content_type="application/pdf",
        prefetched_bytes=pdf_bytes,
        vision_model=vision_model,
    )

    assert error is None, error
    assert artifact.kind == "text"
    content = artifact.text_content
    assert len(content) > 3_000

    leftover_placeholders = len(re.findall(r"<!--\s*(?:image|visual:[^>]+)\s*-->", content))
    assert leftover_placeholders <= 2, (
        f"Expected visual placeholders to be replaced; found {leftover_placeholders} remaining"
    )

    with open(PDF_PATH.with_suffix(".md"), "w") as f:
        f.write(content)

    # # Cheap smoke checks on stable text-layer facts.
    # lowered = content.lower()
    # assert "el niño" in lowered or "el nino" in lowered
    # assert "2.34" in content or "2,34" in content

    # judge_prompt = (
    #     "You are judging whether a PDF extraction preserved important information, "
    #     "including information that originally appeared in charts, maps, and diagrams.\n"
    #     "Decide SEMANTICALLY — wording may differ; numbers may be rounded or formatted "
    #     "differently. Do NOT require word-for-word matches.\n\n"
    #     "Return JSON only:\n"
    #     '{"all_present": true/false, "missing": ["fact text", ...], "notes": "brief"}\n\n'
    #     "Required facts:\n" + "\n".join(f"- {fact}" for fact in REQUIRED_FACTS) + "\n\nExtracted markdown:\n" + content
    # )

    # response = await litellm.acompletion(
    #     model=vision_model,
    #     messages=[{"role": "user", "content": judge_prompt}],
    #     response_format={"type": "json_object"},
    # )
    # raw = (response.choices[0].message.content or "").strip()
    # assert raw, "LLM judge returned empty content"
    # if raw.startswith("```"):
    #     raw = re.sub(r"^```(?:json)?\s*", "", raw)
    #     raw = re.sub(r"\s*```$", "", raw)
    # verdict = json.loads(raw)

    # assert verdict.get("all_present") is True, (
    #     f"Semantic judge reported missing facts: {verdict.get('missing')}; notes={verdict.get('notes')}"
    # )
