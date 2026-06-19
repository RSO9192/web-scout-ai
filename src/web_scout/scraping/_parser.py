"""Content parser layer — Parser ABC and DefaultParser concrete implementation.

The ``Parser`` ABC defines one method per content type so alternative
implementations can override individual strategies selectively:

- ``parse_html``      — HTML pages (static or JS-rendered)
- ``parse_json``      — JSON API endpoints
- ``parse_document``  — PDFs, DOCX, PPTX, XLSX via docling
- ``parse_image``     — Images (binary artifact for optional vision extraction)

``DefaultParser`` implements each using the existing private strategy modules
(``_html``, ``_document``, ``_json``, ``_image``).  The non-abstract
``dispatch()`` method classifies a ``FetchResult`` and routes to the correct
``parse_*`` method.

``materialize_parse_result`` converts a ``ParseResult`` with a binary artifact
to text via a vision model, mirroring the old ``materialize_source_artifact``.
"""

import json
import logging
import mimetypes
import re
from abc import ABC, abstractmethod
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse

from web_scout.config import ROUTING_HEURISTICS

from . import _document, _vision
from ._markdown import append_links
from .constants import BINARY_CONTENT_TYPES, IMAGE_CONTENT_TYPES, JSON_CONTENT_TYPES
from .context import URLContext
from .page_classifier import (
    looks_like_document_resource,
    looks_like_html_body,
)
from .types import FetchResult, ParseResult, SourceArtifact
from .utils import (
    is_json,
    normalize_content_type,
    sniff_document_payload,
    trim_json_value,
    truncate_content,
    unsupported_legacy_document_reason,
)

logger = logging.getLogger(__name__)

# Sentinel value set by ScraplingFetcher when the browser triggered a file download.
_DOWNLOAD_SIGNAL = "__DOWNLOAD_REDIRECT__"


# ---------------------------------------------------------------------------
# Internal routing helper (replaces plan._classify_response)
# ---------------------------------------------------------------------------

def _classify_fetch_result(result: FetchResult) -> str:
    """Classify a ``FetchResult`` into a parse strategy.

    Returns one of: ``'html'``, ``'json'``, ``'document'``, ``'image'``, ``'skip'``.
    """
    if result.error:
        if result.error == _DOWNLOAD_SIGNAL:
            return "document"
        return "skip"

    if result.status >= 400:
        return "skip"

    ct = result.content_type  # already normalised by ScraplingFetcher
    cd = result.content_disposition

    unsupported = unsupported_legacy_document_reason(result.url, ct, cd)
    if unsupported:
        return "skip"

    if looks_like_document_resource(result.url, ct, cd):
        return "document"

    if result.body and sniff_document_payload(result.body, content_type=ct, content_disposition=cd):
        return "document"

    if any(ct.startswith(t) for t in JSON_CONTENT_TYPES):
        if result.html_content and is_json(result.html_content):
            return "json"
        if result.html_content and looks_like_html_body(result.html_content):
            return "html"
        return "skip"

    if result.html_content and is_json(result.html_content):
        return "json"

    if any(ct.startswith(t) for t in IMAGE_CONTENT_TYPES):
        return "image"

    if any(ct.startswith(t) for t in BINARY_CONTENT_TYPES):
        return "skip"

    return "html"


def _extract_absolute_links(base_url: str, page, content: str) -> list[str]:
    """Collect absolute URLs from a Scrapling page object and the markdown content."""
    seen: set[str] = set()
    links: list[str] = []

    def _add(href: str) -> None:
        if not href:
            return
        try:
            absolute = urljoin(base_url, href)
            parsed = urlparse(absolute)
            if parsed.scheme not in ("http", "https"):
                return
        except Exception:
            return
        if absolute not in seen:
            seen.add(absolute)
            links.append(absolute)

    # From Scrapling page CSS selectors
    if page is not None and hasattr(page, "css"):
        try:
            for a in page.css("a"):
                href = str(a.attrib.get("href", "") or "").strip()
                _add(href)
        except Exception:
            pass

    # From markdown hyperlinks already embedded in content
    for match in re.finditer(r"\[([^\]]+)\]\((https?://[^\)]+)\)", content):
        _add(match.group(2))

    return links


# ---------------------------------------------------------------------------
# Parser ABC
# ---------------------------------------------------------------------------

class Parser(ABC):
    """Abstract base class for content parsers.

    Each ``parse_*`` method receives the already-fetched ``FetchResult`` and
    the per-URL ``URLContext``.  ``dispatch()`` is the non-abstract entry point
    used by the Orchestrator.
    """

    @abstractmethod
    async def parse_html(self, result: FetchResult, context: URLContext) -> ParseResult:
        """Parse an HTML page from a pre-fetched ``FetchResult``."""

    @abstractmethod
    async def parse_json(self, result: FetchResult, context: URLContext) -> ParseResult:
        """Parse a JSON API endpoint from a pre-fetched ``FetchResult``."""

    @abstractmethod
    async def parse_document(self, result: FetchResult, context: URLContext) -> ParseResult:
        """Parse a document (PDF, DOCX, PPTX, XLSX) identified by ``FetchResult``."""

    @abstractmethod
    async def parse_image(self, result: FetchResult, context: URLContext) -> ParseResult:
        """Download image bytes from ``FetchResult`` for optional vision extraction."""

    async def dispatch(self, result: FetchResult, context: URLContext) -> ParseResult:
        """Route to the correct ``parse_*`` method based on ``FetchResult`` content type.

        This method is *not* abstract — subclasses inherit it and only need to
        override the individual ``parse_*`` methods.
        """
        strategy = _classify_fetch_result(result)
        logger.debug("[parser] strategy=%s url=%s", strategy, result.url)

        if strategy == "html":
            return await self.parse_html(result, context)
        if strategy == "json":
            return await self.parse_json(result, context)
        if strategy == "document":
            return await self.parse_document(result, context)
        if strategy == "image":
            return await self.parse_image(result, context)

        # "skip"
        reason = result.error or f"HTTP {result.status}" if result.status else "unsupported content"
        return ParseResult(
            url=result.url,
            title="",
            text_content="",
            links=[],
            artifact=SourceArtifact(kind="text", title=""),
            error=reason,
        )


# ---------------------------------------------------------------------------
# DefaultParser
# ---------------------------------------------------------------------------

class DefaultParser(Parser):
    """Parser that delegates to the existing private strategy modules.

    HTML parsing uses the Scrapling page object already held in
    ``FetchResult.page`` — no second HTTP round-trip is needed.

    Document parsing still downloads the binary separately via
    ``_document.scrape_document`` (which uses the existing PDF download chain),
    because PDFs are not carried in-band in ``FetchResult.body``.
    """

    def __init__(
        self,
        *,
        vision_model: Optional[str] = None,
        max_pdf_pages: int = ROUTING_HEURISTICS.pdf_max_pages_default,
    ) -> None:
        self._vision_model = vision_model
        self._max_pdf_pages = max_pdf_pages

    # ------------------------------------------------------------------
    # parse_html
    # ------------------------------------------------------------------

    async def parse_html(self, result: FetchResult, context: URLContext) -> ParseResult:
        from ._html import _extract_title, _html_to_markdown, _is_404_content

        page = result.page

        if page is not None and hasattr(page, "html_content"):
            html = page.html_content or ""
            from ._html import _parse_page
            content, title = _parse_page(page)
        else:
            # Fallback: markdownify on html_content when page object is unavailable
            html = result.html_content or ""
            content = _html_to_markdown(html)
            title = _extract_title(html)
            content = append_links(content, None)

        if _is_404_content(content):
            return ParseResult(
                url=result.url,
                title=title,
                text_content="",
                links=[],
                artifact=SourceArtifact(kind="text", title=title),
                error="soft 404 detected in content",
            )

        links = _extract_absolute_links(result.url, page, content)
        artifact = SourceArtifact(kind="text", title=title, text_content=content)

        # Vision fallback for pages that rendered empty (SPA race condition, etc.)
        if not content.strip() and self._vision_model:
            artifact, error = await _vision.scrape_url_via_vision(
                result.url, query="", vision_model=self._vision_model
            )
            if not error:
                content = artifact.text_content

        return ParseResult(
            url=result.url,
            title=title,
            text_content=content,
            links=links,
            artifact=artifact,
            raw_html=html or None,
        )

    # ------------------------------------------------------------------
    # parse_json
    # ------------------------------------------------------------------

    async def parse_json(self, result: FetchResult, context: URLContext) -> ParseResult:
        html_content = result.html_content or ""
        try:
            data = json.loads(html_content)
        except Exception:
            return ParseResult(
                url=result.url,
                title="",
                text_content="",
                links=[],
                artifact=SourceArtifact(kind="text", title=""),
                error="JSON parse failed",
            )

        trimmed = trim_json_value(data)
        if isinstance(data, dict):
            summary = f"Top-level object with {len(data)} keys."
            extra = "Keys: " + ", ".join(map(str, list(data.keys())[:20]))
        elif isinstance(data, list):
            summary = f"Top-level array with {len(data)} items."
            extra = ""
        else:
            summary = f"Top-level scalar of type {type(data).__name__}."
            extra = ""

        body = f"JSON extracted from {result.url}\n\n{summary}"
        if extra:
            body += f"\n{extra}"
        body += "\n\n```json\n" + json.dumps(trimmed, ensure_ascii=False, indent=2) + "\n```"

        title = result.url.rsplit("/", 1)[-1] or "JSON endpoint"
        artifact = SourceArtifact(kind="text", title=title, text_content=body)
        return ParseResult(
            url=result.url,
            title=title,
            text_content=body,
            links=[],
            artifact=artifact,
            raw_html=html_content or None,
        )

    # ------------------------------------------------------------------
    # parse_document
    # ------------------------------------------------------------------

    async def parse_document(self, result: FetchResult, context: URLContext) -> ParseResult:
        artifact, error = await _document.scrape_document(
            result.url,
            max_pdf_pages=self._max_pdf_pages,
            known_content_type=result.content_type,
            known_content_disposition=result.content_disposition,
            needs_browser=result.used_browser,
        )
        title = artifact.title if artifact else result.url.rsplit("/", 1)[-1] or "Document"

        if error or artifact is None:
            return ParseResult(
                url=result.url,
                title=title,
                text_content="",
                links=[],
                artifact=artifact or SourceArtifact(kind="text", title=title),
                error=error or "Document extraction returned no content",
            )

        return ParseResult(
            url=result.url,
            title=title,
            text_content=artifact.text_content,
            links=[],
            artifact=artifact,
        )

    # ------------------------------------------------------------------
    # parse_image
    # ------------------------------------------------------------------

    async def parse_image(self, result: FetchResult, context: URLContext) -> ParseResult:
        body = result.body or b""
        mime_type = (
            normalize_content_type(result.content_type)
            or mimetypes.guess_type(result.url)[0]
            or "image/png"
        )
        title = result.url.rsplit("/", 1)[-1] or "Image"
        artifact = SourceArtifact(kind="binary", title=title, binary_bytes=body, mime_type=mime_type)
        return ParseResult(
            url=result.url,
            title=title,
            text_content="",
            links=[],
            artifact=artifact,
        )


# ---------------------------------------------------------------------------
# Materialization helper
# ---------------------------------------------------------------------------

async def materialize_parse_result(
    result: ParseResult,
    *,
    query: str,
    vision_model: Optional[str],
    max_content_chars: int,
) -> Tuple[str, Optional[str]]:
    """Convert a ``ParseResult`` to ``(text_content, error)``.

    Text artifacts are truncated to ``max_content_chars``.
    Binary artifacts (scanned PDFs, images) are passed to the vision model;
    a ``vision_model`` is required for binary artifacts.
    """
    artifact = result.artifact

    if artifact.kind == "text":
        return truncate_content(artifact.text_content, max_content_chars), None

    if not vision_model:
        return "", "Binary artifact requires a vision_model for extraction"

    if artifact.mime_type == "application/pdf":
        content, error = await _vision.extract_pdf_via_vision(
            pdf_bytes=artifact.binary_bytes,
            query=query,
            vision_model=vision_model,
        )
    else:
        mime_type = (
            "image/png"
            if artifact.mime_type == "application/x-page-screenshot"
            else artifact.mime_type or "image/png"
        )
        content, error = await _vision.extract_image_via_vision(
            image_bytes=artifact.binary_bytes,
            mime_type=mime_type,
            query=query,
            vision_model=vision_model,
        )

    if error:
        return "", error

    if artifact.mime_type == "application/x-page-screenshot" and len(content) < 400:
        return (
            "",
            f"Vision extraction returned too little content ({len(content)} chars — page likely blocked)",
        )

    return truncate_content(content, max_content_chars), None
