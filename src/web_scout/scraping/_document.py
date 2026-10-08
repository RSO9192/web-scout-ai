"""Document scraping strategy (private): PDF, DOCX, PPTX, and XLSX.

Single entry point: ``scrape_document``.

PDFs are extracted by ``pdf-extractor-ai``: bytes become a Docling document,
optional vision summaries replace figures, then the document is serialized to
markdown. Scanned / image-only PDFs (thin text layer) return a *binary*
``SourceArtifact`` so that the caller can optionally apply vision extraction via
``materialize_parse_result``.

Office documents (DOCX, PPTX, XLSX) are converted by docling's default pipeline
via URL fetch.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from dataclasses import replace
from typing import Optional, Tuple

from pdf_extractor_ai import PdfExtractor, document_title, is_low_text, summarize_images, to_markdown
from pdf_extractor_ai.image_summarizer import SectionVisual
from pdf_extractor_ai.markdown import layout_from_markdown
from prefect import task

from web_scout.config import ROUTING_HEURISTICS

from ._download import download_pdf
from ._markdown import append_links
from .page_classifier import looks_like_pdf_resource
from .types import PdfDocumentLayout, PdfPageSpan, PdfSectionSpan, SourceArtifact
from .utils import unsupported_legacy_document_reason

logger = logging.getLogger(__name__)

# One worker owns one Docling converter. The package queue serializes jobs so
# native PDF conversions do not overlap (overlapping conversions can segfault).
_PDF_EXTRACTOR_WORKERS = 1
_PDF_EXTRACTOR: PdfExtractor | None = None
_PDF_EXTRACTOR_LOCK = threading.Lock()


def _get_pdf_extractor() -> PdfExtractor:
    """Return the process-wide extractor, creating it on first use."""
    global _PDF_EXTRACTOR
    if _PDF_EXTRACTOR is None:
        with _PDF_EXTRACTOR_LOCK:
            if _PDF_EXTRACTOR is None:
                _PDF_EXTRACTOR = PdfExtractor(num_workers=_PDF_EXTRACTOR_WORKERS)
    return _PDF_EXTRACTOR


class _SectionVisionModel:
    """Adapt ``describe_section_visuals`` to the package vision protocol."""

    def __init__(self, vision_model: str) -> None:
        self.vision_model = vision_model

    async def describe_section(
        self,
        *,
        section_title: str,
        section_text: str,
        visuals: list[SectionVisual],
    ) -> dict[str, str]:
        from . import _vision

        tokens = "\n\n".join(f"<!-- visual:{visual.visual_id} -->" for visual in visuals)
        section_markdown = "\n\n".join(part for part in (section_text.strip(), tokens) if part)
        payload = [
            {
                "visual_id": visual.visual_id,
                "png_bytes": visual.png_bytes,
                "caption": visual.caption,
                "page": visual.page,
                "bbox": visual.bbox,
            }
            for visual in visuals
        ]
        summaries, error = await _vision.describe_section_visuals(
            section_markdown=section_markdown,
            visuals=payload,
            vision_model=self.vision_model,
            section_title=section_title,
        )
        if error:
            logger.warning("Visual enrichment failed for section %r: %s", section_title, error)
            return {}
        return summaries


def _to_pdf_document_layout(layout, *, document_title: str) -> PdfDocumentLayout:
    """Copy package layout spans into this project's layout types."""
    return PdfDocumentLayout(
        document_title=document_title,
        pages=tuple(
            PdfPageSpan(page=page.page, start_line=page.start_line, end_line=page.end_line) for page in layout.pages
        ),
        sections=tuple(
            PdfSectionSpan(
                title=section.title,
                level=section.level,
                start_line=section.start_line,
                end_line=section.end_line,
                page_start=section.page_start,
                page_end=section.page_end,
                heading_path=section.heading_path,
            )
            for section in layout.sections
        ),
    )


async def _resolve_is_pdf(
    url: str, content_type: str, content_disposition: str, *, needs_browser: bool = False
) -> bool:
    """Return True when the URL is confirmed to serve a PDF.

    Uses known content-type / content-disposition metadata when available;
    falls back to a GET request, then to extension sniffing.  When
    ``needs_browser`` is True the fallback GET uses ``StealthyFetcher`` to
    bypass bot-walls instead of ``AsyncFetcher``.
    """
    if looks_like_pdf_resource(url, content_type, content_disposition):
        return True
    if content_type or content_disposition:
        return False  # already resolved from headers — not a PDF
    try:
        if needs_browser:
            from ._scrapling import stealthy_fetch

            resp = await stealthy_fetch(
                url,
                headless=True,
                network_idle=True,
                solve_cloudflare=True,
                timeout=ROUTING_HEURISTICS.browser_page_timeout_ms,
            )
        else:
            from ._resources import http_get

            resp = await http_get(
                url,
                stealthy_headers=True,
                follow_redirects=True,
                timeout=ROUTING_HEURISTICS.validation_timeout,
            )
        return looks_like_pdf_resource(
            url,
            resp.headers.get("content-type", ""),
            resp.headers.get("content-disposition", ""),
        )
    except Exception:
        return url.lower().split("?")[0].endswith(".pdf")


def _filename_title(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?")[0] or "Document"


def _pdf_stream_name(url: str) -> str:
    name = url.rsplit("/", 1)[-1].split("?")[0]
    return name or "document.pdf"


async def _convert_pdf_bytes(
    pdf_bytes: bytes,
    max_pages: int,
    vision_model: str | None,
    filename: str,
) -> tuple[str, PdfDocumentLayout]:
    """Convert PDF bytes to markdown plus layout metadata."""
    extractor = _get_pdf_extractor()
    stream_name = filename or "document.pdf"
    if vision_model:
        document = await extractor.extract_async(
            pdf_bytes,
            do_ocr=False,
            max_pages=max_pages,
            name=stream_name,
        )
        document = await summarize_images(document, _SectionVisionModel(vision_model), model_name=vision_model)
        markdown = await asyncio.to_thread(to_markdown, document)
    elif hasattr(extractor, "_extract_markdown_async"):
        markdown, document = await extractor._extract_markdown_async(
            pdf_bytes,
            max_pages=max_pages,
            name=stream_name,
        )
    else:
        # Older installed pdf-extractor-ai releases retain their public API.
        document = await extractor.extract_async(
            pdf_bytes, do_ocr=False, max_pages=max_pages, name=stream_name
        )
        markdown = await asyncio.to_thread(to_markdown, document)
    # Filename is a per-URL fallback applied by the caller, so it stays out of the cache.
    title = document_title(document, fallback=None) or ""
    package_layout = await asyncio.to_thread(layout_from_markdown, markdown, document)
    return markdown, _to_pdf_document_layout(package_layout, document_title=title)


@task(name="web-scout-pdf-parse")
async def _pdf_parse_task(
    pdf_sha256: str,
    pdf_bytes: bytes,
    max_pages: int,
    vision_model: str,
    filename: str,
) -> tuple[str, PdfDocumentLayout]:
    """Parse one PDF. The cache key is the hash, page limit, and vision model."""
    del pdf_sha256  # Identity for the Prefect cache key; the bytes are excluded from it.
    return await _convert_pdf_bytes(pdf_bytes, max_pages, vision_model or None, filename)


async def _convert_pdf_to_markdown(
    pdf_bytes: bytes,
    url: str,
    max_pages: int,
    *,
    vision_model: str | None = None,
) -> tuple[str, PdfDocumentLayout]:
    """Convert PDF bytes to markdown plus layout metadata."""
    from web_scout.result_cache import call_cached_task, refresh_pdf_cache_enabled

    fetch_logger = logging.getLogger("web_scout.fetch")
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    fetch_logger.info(
        "[pdf-extractor] parsing PDF bytes=%d sha256=%s url=%s",
        len(pdf_bytes),
        digest,
        url,
    )
    started = time.perf_counter()
    filename = _pdf_stream_name(url)
    markdown, layout = await call_cached_task(
        _pdf_parse_task,
        kind="pdf",
        refresh=refresh_pdf_cache_enabled(),
        exclude=("pdf_bytes", "filename"),
        pdf_sha256=digest,
        pdf_bytes=pdf_bytes,
        max_pages=max_pages,
        vision_model=vision_model or "",
        filename=filename,
    )
    title = layout.document_title or _filename_title(url)
    if title != layout.document_title:
        layout = replace(layout, document_title=title)
    fetch_logger.info(
        "[pdf-extractor] finished PDF bytes=%d elapsed=%.1fs url=%s",
        len(pdf_bytes),
        time.perf_counter() - started,
        url,
    )
    return markdown, layout


async def scrape_document(
    url: str,
    *,
    max_pdf_pages: int = ROUTING_HEURISTICS.pdf_max_pages_default,
    known_content_type: str = "",
    known_content_disposition: str = "",
    needs_browser: bool = False,
    prefetched_bytes: bytes | None = None,
    vision_model: str | None = None,
) -> Tuple[SourceArtifact, Optional[str]]:
    """Extract content from a document URL.

    Returns a text ``SourceArtifact`` for documents with a readable text layer,
    or a binary ``SourceArtifact`` (``mime_type="application/pdf"``) for scanned
    PDFs so that callers can optionally apply vision extraction.
    """
    title = _filename_title(url)

    unsupported = unsupported_legacy_document_reason(url, known_content_type, known_content_disposition)
    if unsupported:
        return SourceArtifact(kind="text", title=title), f"Skipped: {unsupported}"

    is_pdf = await _resolve_is_pdf(url, known_content_type, known_content_disposition, needs_browser=needs_browser)

    if is_pdf:
        pdf_bytes = prefetched_bytes if prefetched_bytes and prefetched_bytes.startswith(b"%PDF") else None
        error = None
        if pdf_bytes is None:
            pdf_bytes, error = await download_pdf(url, needs_browser=needs_browser)
        if error or not pdf_bytes:
            return SourceArtifact(kind="text", title=title), error or "PDF download returned empty bytes"

        content, layout = await _convert_pdf_to_markdown(
            pdf_bytes,
            url,
            max_pdf_pages,
            vision_model=vision_model,
        )
        content = append_links(content, None)
        title = layout.document_title or title

        if is_low_text(content, min_chars=ROUTING_HEURISTICS.min_pdf_text_chars):
            # Scanned / image-only PDF: return raw bytes for optional vision extraction
            return SourceArtifact(
                kind="binary",
                title=title,
                binary_bytes=pdf_bytes,
                mime_type="application/pdf",
            ), None

        return SourceArtifact(kind="text", title=title, text_content=content, layout=layout), None

    # Non-PDF office documents (DOCX, PPTX, XLSX) — let docling fetch and convert
    from docling.document_converter import DocumentConverter

    def _convert_office() -> str:
        converter = DocumentConverter()
        result = converter.convert(url)
        return result.document.export_to_markdown()

    try:
        content = await asyncio.to_thread(_convert_office)
    except Exception as exc:
        return SourceArtifact(kind="text", title=title), f"Document conversion failed: {exc}"

    content = append_links(content, None)
    return SourceArtifact(kind="text", title=title, text_content=content), None
