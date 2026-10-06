"""Page content analysis — thin/rich detection, SPA/form signals, cached text rendering."""

import re
from typing import Optional
from urllib.parse import urlparse

from web_scout.config import EXTRACTOR_HEURISTICS
from web_scout.scraping.page_classifier import PageShapeAssessment
from web_scout.scraping.utils import looks_like_document_link

_FORM_TOKENS = (
    "strongly agree",
    "strongly disagree",
    "please rate",
    "kindly provide",
    "please provide",
    "select an option",
)

_DOC_URL_RE = re.compile(r"https?://[^\s)\]\"'>]+", re.IGNORECASE)
_PRIMARY_DOCUMENT_CUES = (
    "download pdf",
    "full text pdf",
    "full-text pdf",
    "full text:",
    "full-text:",
    "[download pdf]",
    "download the pdf",
    "download the full",
)


def _has_fragment(url: str) -> bool:
    """True if the URL contains a non-empty #fragment (SPA client-side routing)."""
    return bool(urlparse(url).fragment)


def _is_form_contaminated(content: str) -> bool:
    """True if content is dominated by survey/form patterns despite char count > 500."""
    lower = content.lower()
    if any(lower.count(tok) >= EXTRACTOR_HEURISTICS.form_token_min_repeat for tok in _FORM_TOKENS):
        return True
    lines = [line for line in content.splitlines() if line.strip()]
    if len(lines) >= EXTRACTOR_HEURISTICS.nav_dump_min_lines:
        bullet_lines = sum(1 for line in lines if line.strip().startswith(("* ", "- ")))
        if bullet_lines / len(lines) > EXTRACTOR_HEURISTICS.nav_dump_bullet_ratio:
            return True
    return False


def render_cached_page_text(url: str, title: str, content: str) -> str:
    """Render cached page content back into a text contract for extraction."""
    header = f"# {title}\nSource: {url}\n\n" if title else f"Source: {url}\n\n"
    signals = []
    if _has_fragment(url):
        signals.append(
            "[SPA: URL fragment detected — current content may be the wrong "
            "tab/view. Call list_interactive_elements to find the data section.]"
        )
    if len(content) >= EXTRACTOR_HEURISTICS.thin_content_chars and _is_form_contaminated(content):
        signals.append(
            "[Form/survey content detected — actual data is likely behind "
            "interactive elements. Call list_interactive_elements.]"
        )
    if signals:
        return header + content + "\n\n" + "\n".join(signals)
    return header + content


def render_cached_document_text(document_url: str, title: str, content: str) -> str:
    """Render cached document content back into the legacy document tool contract."""
    header = f"# {title}\nSource: {document_url}\n\n" if title else f"Source: {document_url}\n\n"
    return header + content


def _prefetched_has_signal(content: str) -> bool:
    return "[SPA:" in content or "[Form/survey" in content


def _prefetched_is_thin(content: str) -> bool:
    return len(content.strip()) < EXTRACTOR_HEURISTICS.thin_content_chars


def prefetched_has_strong_content(
    content: str,
    page_shape: Optional[PageShapeAssessment],
) -> bool:
    """True when pre-fetched content is rich prose that needs no interaction."""
    if not content or page_shape is None:
        return False
    if page_shape.page_type != "content_page":
        return False
    return (
        page_shape.content_score >= 4
        and page_shape.content_score >= page_shape.record_score + 1
        and page_shape.content_score >= page_shape.interactive_score + 1
        and page_shape.text_chars >= 3_000
    )


def _has_primary_document_download_cue(content: str) -> bool:
    """True when the page advertises a primary PDF/document download."""
    if not content:
        return False
    lower = content.lower()
    urls = _DOC_URL_RE.findall(content)
    document_urls = [url for url in urls if looks_like_document_link(url)]
    if not document_urls:
        return False
    if any(cue in lower for cue in _PRIMARY_DOCUMENT_CUES):
        return True
    # Extensionless repository downloads (DSpace bitstreams, /download endpoints)
    # are themselves a strong primary-document signal.
    return any(
        any(marker in url.lower() for marker in ("/bitstreams/", "/bitstream/", "/download"))
        for url in document_urls
    )


def prefetched_allows_linked_document(
    content: str,
    page_shape: Optional[PageShapeAssessment],
) -> bool:
    """True when the extractor may fetch one primary linked document.

    Strong ``record_page`` shapes keep the existing path. Publication /
    repository landing pages with a primary Download PDF (or bitstream
    download) are also allowed even when the abstract looks content-rich —
    the landing page itself often lacks the evidence the query needs.
    """
    if page_shape is None:
        return False
    if (
        page_shape.page_type == "record_page"
        and page_shape.record_score >= 5
        and page_shape.record_score >= page_shape.content_score + 2
    ):
        return True
    if page_shape.document_link_count < 1 or page_shape.metadata_marker_count < 2:
        return False
    return _has_primary_document_download_cue(content)


def prefetched_allows_interaction(
    content: str,
    page_shape: Optional[PageShapeAssessment],
) -> bool:
    """True when the extractor sub-agent should be allowed to interact with the page."""
    if not content:
        return True

    # Rich prose should stay on the cheap one-turn extraction path even if
    # weak SPA/form markers are present in the rendered content.
    if prefetched_has_strong_content(content, page_shape):
        return False

    if _prefetched_has_signal(content):
        return True

    if page_shape is not None and page_shape.page_type == "interactive_shell" and page_shape.interactive_score >= 5:
        return True

    if not _prefetched_is_thin(content):
        return False

    if page_shape is None:
        return True

    return (
        page_shape.page_type == "uncertain"
        and page_shape.interactive_score >= 2
        and page_shape.record_score < 5
        and page_shape.content_score < 5
    )


def prefetched_is_recoverable(content: str) -> bool:
    """True when pre-fetched content is usable as a fallback if the extractor fails."""
    stripped = content.strip()
    if len(stripped) < EXTRACTOR_HEURISTICS.recovery_min_content_chars:
        return False
    return not (
        stripped.startswith("[Scrape failed")
        or stripped.startswith("[Page returned empty")
        or stripped.startswith("[No relevant content")
    )
