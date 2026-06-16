"""Public strategy executor and session-cache API.

Exports:
- ``execute_strategy``                    — dispatch to the correct scraping strategy.
- ``fetch_query_agnostic_source_artifact`` — plan + fetch (no BM25, suitable for caching).
- ``materialize_source_artifact``         — convert a cached artifact to query-specific text.
"""

import logging
from typing import Optional, Tuple

from web_scout.config import ROUTING_HEURISTICS

from . import _document, _html, _image, _json, _vision
from .plan import build_scrape_plan
from .types import ScrapePlan, ScrapeStrategy, SourceArtifact
from .utils import truncate_content

logger = logging.getLogger(__name__)


async def execute_strategy(
    plan: ScrapePlan,
    url: str,
    *,
    wait_for: Optional[str] = None,
    query: str = "",
    vision_model: Optional[str] = None,
    max_pdf_pages: int = ROUTING_HEURISTICS.pdf_max_pages_default,
) -> Tuple[Optional[SourceArtifact], Optional[str]]:
    """Dispatch to the appropriate scraping strategy and return a ``SourceArtifact``.

    HTML strategies use a Chain-of-Responsibility fallback sequence:
    ``HTML_FAST`` tries HTTP-only first; if the page is too thin it falls through
    to the browser.  Both HTML paths fall through to vision when the result is
    empty and a ``vision_model`` is provided.

    Returns ``(artifact, None)`` on success or ``(None, error_message)`` on failure.
    """
    if plan.strategy == ScrapeStrategy.DOCUMENT:
        artifact, error = await _document.scrape_document(
            url,
            max_pdf_pages=max_pdf_pages,
            known_content_type=plan.content_type,
            known_content_disposition=plan.content_disposition,
        )

    elif plan.strategy == ScrapeStrategy.JSON:
        artifact, error = await _json.scrape_json(url)

    elif plan.strategy == ScrapeStrategy.IMAGE:
        artifact, error = await _image.scrape_image(url)

    elif plan.strategy == ScrapeStrategy.HTML_FAST:
        # CoR step 1: HTTP-only (fast path)
        artifact, error = await _html.scrape_html_fast(url, query=query)

        # CoR step 2: full browser fallback when HTTP returned thin content
        if artifact is None:
            artifact, error = await _html.scrape_html_browser(url, wait_for=wait_for, query=query)

        # CoR step 3: document redirect (browser triggered a file download)
        if error == _html._DOWNLOAD_SIGNAL:
            artifact, error = await _document.scrape_document(url, max_pdf_pages=max_pdf_pages)

        # CoR step 4: vision fallback for empty / 404 pages
        elif not error and artifact is not None and not artifact.text_content.strip() and vision_model:
            artifact, error = await _vision.scrape_url_via_vision(url, query=query, vision_model=vision_model)

    else:  # HTML_BROWSER — skip HTTP-only step
        artifact, error = await _html.scrape_html_browser(url, wait_for=wait_for, query=query)

        if error == _html._DOWNLOAD_SIGNAL:
            artifact, error = await _document.scrape_document(url, max_pdf_pages=max_pdf_pages)
        elif not error and artifact is not None and not artifact.text_content.strip() and vision_model:
            artifact, error = await _vision.scrape_url_via_vision(url, query=query, vision_model=vision_model)

    if artifact is None:
        return None, error or "Strategy returned no content"
    if artifact.kind == "text" and not artifact.text_content.strip():
        return None, error or "Extraction returned empty content"
    if artifact.kind == "binary" and not artifact.binary_bytes:
        return None, error or "Extraction returned empty binary content"

    return artifact, None


async def materialize_source_artifact(
    artifact: SourceArtifact,
    *,
    query: str,
    vision_model: Optional[str],
    max_content_chars: int,
) -> Tuple[str, str, Optional[str]]:
    """Convert a cached ``SourceArtifact`` into a ``(content, title, error)`` tuple.

    Text artifacts are truncated to ``max_content_chars``.
    Binary artifacts (scanned PDFs, images, screenshots) are passed to the
    vision model; a ``vision_model`` is required for binary artifacts.
    """
    title = artifact.title

    if artifact.kind == "text":
        return truncate_content(artifact.text_content, max_content_chars), title, None

    if not vision_model:
        return "", title, "Binary artifact requires a vision_model for extraction"

    if artifact.mime_type == "application/pdf":
        content, error = await _vision.extract_pdf_via_vision(
            pdf_bytes=artifact.binary_bytes,
            query=query,
            vision_model=vision_model,
        )
    else:
        mime_type = (
            "image/png" if artifact.mime_type == "application/x-page-screenshot" else artifact.mime_type or "image/png"
        )
        content, error = await _vision.extract_image_via_vision(
            image_bytes=artifact.binary_bytes,
            mime_type=mime_type,
            query=query,
            vision_model=vision_model,
        )

    if error:
        return "", title, error

    if artifact.mime_type == "application/x-page-screenshot" and len(content) < 400:
        return (
            "",
            title,
            f"Vision extraction returned too little content ({len(content)} chars — page likely blocked)",
        )

    return truncate_content(content, max_content_chars), title, None


async def scrape_document(
    url: str,
    *,
    query: str = "",
    vision_model: Optional[str] = None,
    max_pdf_pages: int = ROUTING_HEURISTICS.pdf_max_pages_default,
    known_content_type: str = "",
    known_content_disposition: str = "",
    max_content_chars: int = 30_000,
) -> Tuple[str, str, Optional[str]]:
    """Convenience wrapper: scrape a document URL and return ``(content, title, error)``.

    Combines ``_document._scrape_document`` and ``materialize_source_artifact`` into
    a single call, materialising binary artifacts (scanned PDFs) via vision when a
    ``vision_model`` is provided.
    """
    artifact, error = await _document.scrape_document(
        url,
        max_pdf_pages=max_pdf_pages,
        known_content_type=known_content_type,
        known_content_disposition=known_content_disposition,
    )
    title = artifact.title if artifact else url.rsplit("/", 1)[-1] or "Document"
    if error or artifact is None:
        return "", title, error or "Document extraction returned no content"

    return await materialize_source_artifact(
        artifact,
        query=query,
        vision_model=vision_model,
        max_content_chars=max_content_chars,
    )


async def fetch_query_agnostic_source_artifact(
    url: str,
    *,
    wait_for: Optional[str] = None,
    vision_model: Optional[str] = None,
    allowed_domains: Optional[frozenset] = None,
    max_pdf_pages: int = ROUTING_HEURISTICS.pdf_max_pages_default,
) -> Tuple[Optional[SourceArtifact], Optional[str], ScrapeStrategy]:
    """Fetch a query-agnostic ``SourceArtifact`` suitable for session caching.

    No BM25 filtering is applied so the artifact can be reused across queries.
    Returns ``(artifact, error, strategy)`` — ``strategy`` carries the routing
    decision for use by the caller.
    """
    plan = await build_scrape_plan(url, allowed_domains=allowed_domains)

    if plan.strategy == ScrapeStrategy.SKIP:
        return None, f"Skipped: {plan.reason}", plan.strategy

    try:
        artifact, error = await execute_strategy(
            plan,
            url,
            wait_for=wait_for,
            query="",  # no BM25 — fetch the full page for cross-query reuse
            vision_model=vision_model,
            max_pdf_pages=max_pdf_pages,
        )
    except Exception as exc:
        logger.error("[scrape] fetch failed %s: %s", url, exc)
        return None, str(exc), plan.strategy

    if error:
        if plan.likely_bot_detected:
            logger.info("[scrape] bot_detected %s", url)
            return None, f"bot_detected: {error}", plan.strategy
        return None, error, plan.strategy

    if artifact is None:
        return None, "Extraction returned empty content", plan.strategy

    return artifact, None, plan.strategy
