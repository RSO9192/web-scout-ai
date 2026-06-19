"""Vision-model extraction helpers (private).

Provides three public-within-module entry points:
- ``extract_pdf_via_vision``  — rasterize a PDF page and extract text.
- ``extract_image_via_vision`` — extract information from raw image bytes.
- ``scrape_url_via_vision``   — screenshot a live URL and extract text.

All functions are implementation details; they are not exported from the
scraping package ``__init__``.
"""

import base64
import io
import logging
from typing import Optional, Tuple

from web_scout.config import ROUTING_HEURISTICS

from ._scrapling import stealthy_fetch
from .types import SourceArtifact

logger = logging.getLogger(__name__)

_MIN_SCREENSHOT_CHARS = 400


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
