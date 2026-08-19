"""Vision-model extraction helpers (private).

Provides public-within-module entry points:
- ``extract_pdf_via_vision``  — rasterize a PDF page and extract text.
- ``extract_image_via_vision`` — extract information from raw image bytes.
- ``scrape_url_via_vision``   — screenshot a live URL and extract text.
- ``describe_section_visuals`` — summarise PDF section figures in one call.

All functions are implementation details; they are not exported from the
scraping package ``__init__``.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import uuid
from typing import Any, Optional, Tuple

from pydantic import BaseModel, Field

from web_scout.config import ROUTING_HEURISTICS

from ._scrapling import stealthy_fetch
from .types import SourceArtifact

logger = logging.getLogger(__name__)

_MIN_SCREENSHOT_CHARS = 400


class VisualSummary(BaseModel):
    visual_id: str = Field(description="Exact visual token id, e.g. s0-v1")
    summary: str = Field(
        description=(
            "Detailed markdown summary of the visual that preserves quantitative "
            "information such as axis labels, units, series names, and readable values. "
            "For maps, cover world region, main message, regions with data, "
            "quantitative information by region, and how that data fits the surrounding text."
        )
    )


class SectionVisualSummaries(BaseModel):
    request_id: str = Field(default="", description="Echo of the request_id from the prompt")
    visuals: list[VisualSummary] = Field(default_factory=list)


async def _capture_screenshot(url: str) -> bytes:
    """Capture a viewport screenshot of a live URL using Scrapling's StealthyFetcher.

    A ``page_action`` callback is used to wait for the page to settle and then
    capture a PNG screenshot via Playwright's page.screenshot() API.
    """
    screenshot_holder: dict = {}

    async def _take_screenshot(page) -> None:
        await page.wait_for_timeout(ROUTING_HEURISTICS.vision_settle_wait_ms)
        screenshot_holder["data"] = await page.screenshot(type="png", full_page=False)

    await stealthy_fetch(
        url,
        headless=True,
        network_idle=True,
        solve_cloudflare=True,
        timeout=ROUTING_HEURISTICS.vision_goto_timeout_ms,
        page_action=_take_screenshot,
    )

    data = screenshot_holder.get("data")
    if not data:
        raise RuntimeError("Headless browser returned no screenshot data")
    return data


async def _call_vision_model(
    *,
    image_bytes: bytes,
    mime_type: str,
    query: str,
    vision_model: str,
    prompt_prefix: str,
) -> Tuple[str, Optional[str]]:
    """Send image bytes to a vision LLM and return (extracted_text, error)."""
    import litellm

    image_b64 = base64.b64encode(image_bytes).decode()
    query_clause = f" relevant to: {query}" if query else ""
    prompt = prompt_prefix.format(query_clause=query_clause)
    try:
        response = await litellm.acompletion(
            model=vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                    ],
                }
            ],
        )
        content = (response.choices[0].message.content or "").strip()
        return (content, None) if content else ("", "Vision extraction returned empty content")
    except Exception as exc:
        return "", f"Vision extraction failed: {exc}"


async def extract_pdf_via_vision(
    *,
    pdf_bytes: bytes,
    query: str,
    vision_model: str,
) -> Tuple[str, Optional[str]]:
    """Rasterize the first PDF page and extract its text via a vision model."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        if len(pdf) == 0:
            return "", "PDF has no pages"
        page = pdf[0]
        try:
            bitmap = page.render(scale=2)
            pil_image = bitmap.to_pil()
        finally:
            page.close()
    finally:
        pdf.close()

    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    return await _call_vision_model(
        image_bytes=buf.getvalue(),
        mime_type="image/png",
        query=query,
        vision_model=vision_model,
        prompt_prefix=(
            "Extract all useful information{query_clause} from the first page of this PDF rendered as an image. "
            "If it contains text, tables, charts, maps, labels, legends, or numeric values, capture them precisely. "
            "Return clean plain text or markdown."
        ),
    )


async def extract_image_via_vision(
    *,
    image_bytes: bytes,
    mime_type: str,
    query: str,
    vision_model: str,
) -> Tuple[str, Optional[str]]:
    """Extract information from an image (chart, map, figure, etc.) via a vision model."""
    return await _call_vision_model(
        image_bytes=image_bytes,
        mime_type=mime_type,
        query=query,
        vision_model=vision_model,
        prompt_prefix=(
            "Extract all useful information{query_clause} from this image. "
            "If it contains text, tables, charts, maps, labels, legends, or numeric values, capture them precisely. "
            "Return clean plain text or markdown."
        ),
    )


async def scrape_url_via_vision(
    url: str,
    *,
    query: str,
    vision_model: str,
) -> Tuple[SourceArtifact, Optional[str]]:
    """Screenshot a live URL and extract its content via a vision model.

    Used as the last-resort fallback when all text-extraction strategies fail
    (empty page, 404 content, or bot-detection).
    """
    try:
        screenshot_bytes = await _capture_screenshot(url)
    except Exception as exc:
        return SourceArtifact(kind="text", title=""), f"Screenshot failed: {exc}"

    content, error = await _call_vision_model(
        image_bytes=screenshot_bytes,
        mime_type="image/png",
        query=query,
        vision_model=vision_model,
        prompt_prefix=(
            "Extract all text content{query_clause} from this page screenshot. "
            "Return the content as clean plain text or markdown. "
            "Include specific facts, numbers, names, and data. "
            "Exclude navigation bars and footers."
        ),
    )
    if error:
        return SourceArtifact(kind="text", title=""), error
    if len(content) < _MIN_SCREENSHOT_CHARS:
        return (
            SourceArtifact(kind="text", title=""),
            f"Vision extraction returned too little content ({len(content)} chars — page likely blocked)",
        )
    return SourceArtifact(kind="text", title="", text_content=content), None


def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


async def describe_section_visuals(
    *,
    section_markdown: str,
    visuals: list[dict[str, Any]],
    vision_model: str,
    section_title: str = "",
) -> Tuple[dict[str, str], Optional[str]]:
    """Describe all visuals in one section with a single multimodal LLM call.

    ``section_markdown`` must already contain labeled tokens
    ``<!-- visual:{id} -->`` aligned with ``visuals``.

    Returns ``({visual_id: summary}, error)``.
    """
    import litellm

    if not visuals:
        return {}, None

    request_id = uuid.uuid4().hex[:12]
    title_clause = f' titled "{section_title}"' if section_title else ""
    visual_lines = []
    for visual in visuals:
        caption = (visual.get("caption") or "").strip()
        page = visual.get("page")
        bbox = visual.get("bbox") or "unknown"
        meta = f"visual_id={visual['visual_id']}"
        if page is not None:
            meta += f", page={page}"
        meta += f", bbox={bbox}"
        if caption:
            meta += f', caption="{caption}"'
        visual_lines.append(f"- {meta}")

    instruction = (
        f"You are enriching a PDF section{title_clause} that was converted to markdown. "
        f"request_id={request_id}\n"
        "The markdown below contains labeled placeholders of the form "
        "`<!-- visual:ID -->` where figures, charts, maps, diagrams, or graphics appear.\n\n"
        "Describe ONLY the raster images attached to this message. Do not describe a "
        "chart, map, or diagram that is not one of those attached images, even if the "
        "section text mentions other figures. If a caption is provided, use it as a hint "
        "but the attached image is authoritative.\n\n"
        "For EVERY listed visual_id, write a detailed markdown summary that preserves all "
        "important quantitative information readable in the image: axis titles, units, "
        "tick labels, series/legend names, categories, and X/Y (or equivalent) values. "
        "Describe diagrams so a reader who cannot see the image still recovers "
        "the key relationships and labeled quantities. Do not invent values that are not "
        "readable.\n\n"
        "If the visual is a map, the summary MUST use these markdown headings and cover "
        "all five points:\n"
        "1. **World region** — the world region the map shows (for example Latin America "
        "and the Caribbean, Sub-Saharan Africa, global).\n"
        "2. **Main message** — what the map is showing (the theme, indicator, or claim).\n"
        "3. **Regions with data** — the geographical regions or subregions for which the "
        "map provides data (countries, provinces, basins, etc.).\n"
        "4. **Quantitative information by region** — for each region/subregion, the "
        "quantitative information the map presents (legend classes, counts, rates, "
        "percentages, colour/shading meaning, labeled values). Preserve readable numbers "
        "and units; do not invent values.\n"
        "5. **Summary in context** — explain how the quantitative data for all regions "
        "fit into the broader story told by the text around the map in this section.\n\n"
        "For non-map visuals, do not summarise the surrounding section text except as "
        "brief context needed to interpret the visual.\n\n"
        "Return JSON matching this schema:\n"
        f'{{"request_id":"{request_id}","visuals":[{{"visual_id":"s0-v0","summary":"..."}}]}}\n'
        "Echo request_id exactly. Include every visual_id exactly once.\n\n"
        f"Visuals to describe:\n" + "\n".join(visual_lines) + "\n\n"
        "Section markdown with placeholders:\n"
        f"{section_markdown}"
    )

    content: list[dict[str, Any]] = [{"type": "text", "text": instruction}]
    for visual in visuals:
        content.append({"type": "text", "text": f"Image for visual_id={visual['visual_id']}:"})
        image_b64 = base64.b64encode(visual["png_bytes"]).decode()
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_b64}"},
            }
        )

    try:
        response = await litellm.acompletion(
            model=vision_model,
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        if not raw:
            return {}, "Section visual description returned empty content"
        parsed = SectionVisualSummaries.model_validate(_extract_json_object(raw))
        if parsed.request_id and parsed.request_id != request_id:
            return {}, (
                f"Section visual description request_id mismatch "
                f"(expected {request_id}, got {parsed.request_id})"
            )
        return {item.visual_id: item.summary for item in parsed.visuals if item.visual_id}, None
    except Exception as exc:
        return {}, f"Section visual description failed: {exc}"
