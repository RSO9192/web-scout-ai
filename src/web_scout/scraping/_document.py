"""Document scraping strategy (private): PDF, DOCX, PPTX, and XLSX via docling.

Single entry point: ``_scrape_document``.

PDF handling uses a fast text-layer path (no OCR, no table detection) via a
thread-safe lazy singleton ``DocumentConverter``.  Scanned / image-only PDFs
(thin text layer) return a *binary* ``SourceArtifact`` so that the caller can
optionally apply vision extraction via ``materialize_source_artifact``.

Office documents (DOCX, PPTX, XLSX) are converted by docling's default pipeline
via URL fetch.
"""

import asyncio
import logging
import threading
from typing import Optional, Tuple

import httpx

from web_scout.config import ROUTING_HEURISTICS

from ._download import download_pdf
from ._markdown import append_links
from .constants import FETCH_HEADERS
from .page_classifier import looks_like_pdf_resource
from .types import SourceArtifact
from .utils import unsupported_legacy_document_reason

logger = logging.getLogger(__name__)

_PDF_CONVERTER = None
_PDF_CONVERTER_LOCK = threading.Lock()


def _get_pdf_converter():
    """Return a shared Docling PDF converter instance (thread-safe lazy singleton).

    Reusing the same ``DocumentConverter`` lets Docling reuse its initialised
    pipeline and heavy layout model across PDF conversion calls.
    """
    global _PDF_CONVERTER
    if _PDF_CONVERTER is not None:
        return _PDF_CONVERTER
    with _PDF_CONVERTER_LOCK:
        if _PDF_CONVERTER is None:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            opts = PdfPipelineOptions(do_ocr=False, do_table_structure=False, force_backend_text=True)
            _PDF_CONVERTER = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return _PDF_CONVERTER


async def _resolve_is_pdf(url: str, content_type: str, content_disposition: str) -> bool:
    """Return True when the URL is confirmed to serve a PDF.

    Uses known content-type / content-disposition metadata when available;
    falls back to a HEAD request, then to extension sniffing.
    """
    if looks_like_pdf_resource(url, content_type, content_disposition):
        return True
    if content_type or content_disposition:
        return False  # already resolved from headers — not a PDF
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=ROUTING_HEURISTICS.validation_timeout,
            headers=FETCH_HEADERS,
        ) as client:
            head = await client.head(url)
        return looks_like_pdf_resource(
            url,
            head.headers.get("content-type", ""),
            head.headers.get("content-disposition", ""),
        )
    except Exception:
        return url.lower().split("?")[0].endswith(".pdf")


async def _convert_pdf_to_markdown(pdf_bytes: bytes, url: str, max_pages: int) -> str:
    """Convert PDF bytes to markdown in a thread (CPU-bound docling work)."""
    from docling_core.types.io import DocumentStream

    def _sync(pdf_bytes: bytes = pdf_bytes) -> str:
        import gc
        import io

        converter = _get_pdf_converter()
        filename = url.rsplit("/", 1)[-1].split("?")[0] or "document.pdf"
        source = DocumentStream(name=filename, stream=io.BytesIO(pdf_bytes))
        result = converter.convert(source, page_range=(1, max_pages))
        # Explicit del + gc.collect ensures pypdfium2 child objects (pages) are
        # garbage-collected before their parent PdfDocument.
        markdown = result.document.export_to_markdown()
        del result
        gc.collect()
        return markdown

    return await asyncio.to_thread(_sync)


async def scrape_document(
    url: str,
    *,
    max_pdf_pages: int = ROUTING_HEURISTICS.pdf_max_pages_default,
    known_content_type: str = "",
    known_content_disposition: str = "",
) -> Tuple[SourceArtifact, Optional[str]]:
    """Extract content from a document URL.

    Returns a text ``SourceArtifact`` for documents with a readable text layer,
    or a binary ``SourceArtifact`` (``mime_type="application/pdf"``) for scanned
    PDFs so that callers can optionally apply vision extraction.
    """
    title = url.rsplit("/", 1)[-1] or "Document"

    unsupported = unsupported_legacy_document_reason(url, known_content_type, known_content_disposition)
    if unsupported:
        return SourceArtifact(kind="text", title=title), f"Skipped: {unsupported}"

    is_pdf = await _resolve_is_pdf(url, known_content_type, known_content_disposition)

    if is_pdf:
        pdf_bytes, error = await download_pdf(url)
        if error or not pdf_bytes:
            return SourceArtifact(kind="text", title=title), error or "PDF download returned empty bytes"

        content = await _convert_pdf_to_markdown(pdf_bytes, url, max_pdf_pages)
        content = append_links(content, None)

        if len(content.strip()) < ROUTING_HEURISTICS.min_pdf_text_chars:
            # Scanned / image-only PDF: return raw bytes for optional vision extraction
            return SourceArtifact(
                kind="binary",
                title=title,
                binary_bytes=pdf_bytes,
                mime_type="application/pdf",
            ), None

        return SourceArtifact(kind="text", title=title, text_content=content), None

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
