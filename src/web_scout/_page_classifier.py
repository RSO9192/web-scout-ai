"""Shared structural page-shape classification helpers.

These helpers are intentionally lightweight: they only use regex and string
features so they can run inside routing and extractor gating without adding
new dependencies or noticeable latency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ._heuristics import EXTRACTOR_HEURISTICS, ROUTING_HEURISTICS

PageType = Literal["content_page", "record_page", "interactive_shell", "uncertain"]

_DOC_URL_RE = re.compile(r"https?://[^\s)\]\"'>]+", re.IGNORECASE)
_HTML_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', flags=re.I)
_BLOCK_TAG_RE = re.compile(r"</?(?:article|section|p|div|li|ul|ol|h[1-6]|br|tr|td|table)[^>]*>", flags=re.I)
_SENTENCE_RE = re.compile(r"[.!?](?:\s|$)")

_RECORD_MARKERS = (
    "abstract",
    "author",
    "authors",
    "citation",
    "doi",
    "full text",
    "download",
    "metadata",
    "publication date",
    "published",
    "repository",
    "catalog",
    "catalogue",
    "record",
    "view details",
)


@dataclass(frozen=True)
class PageShapeAssessment:
    page_type: PageType
    text_chars: int
    paragraph_blocks: int
    sentence_count: int
    href_count: int = 0
    document_link_count: int = 0
    metadata_marker_count: int = 0
    list_line_ratio: float = 0.0
    content_score: int = 0
    record_score: int = 0
    interactive_score: int = 0
    confidence: int = 0


def _looks_like_document_url(url: str) -> bool:
    lower = url.lower()
    path = lower.split("?", 1)[0].split("#", 1)[0]
    if path.endswith((".pdf", ".docx", ".pptx", ".xlsx")):
        return True
    return any(
        marker in lower
        for marker in (
            "/download",
            "/bitstreams/",
            "download?",
            "file-download",
        )
    )


def _strip_html(html: str) -> str:
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
    html = _BLOCK_TAG_RE.sub("\n", html)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"[ \t\r\f\v]+", " ", html)


def _text_blocks_from_html(html: str) -> list[str]:
    text = _strip_html(html)
    return [line.strip() for line in text.splitlines() if len(line.strip()) >= 40]


def _content_lines_from_prefetched(content: str) -> list[str]:
    lines: list[str] = []
    in_link_section = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Source: "):
            continue
        if line == _RENDERED_LINKS_HEADING:
            in_link_section = True
            continue
        if line.startswith("[SPA:") or line.startswith("[Form/survey"):
            continue
        if line.startswith("# "):
            continue
        if in_link_section and line.startswith("- "):
            continue
        lines.append(line)
    return lines


def _count_sentence_endings(text: str) -> int:
    return len(_SENTENCE_RE.findall(text))


def _count_metadata_markers(text: str) -> int:
    lower = text.lower()
    return sum(1 for marker in _RECORD_MARKERS if marker in lower)


def _classify_page_shape(
    *,
    text: str,
    paragraph_blocks: int,
    sentence_count: int,
    href_count: int,
    document_link_count: int,
    metadata_marker_count: int,
    list_line_ratio: float,
    explicit_interactive: bool,
    script_tags: int = 0,
) -> PageShapeAssessment:
    text_chars = len(text)
    sparse_prose = paragraph_blocks <= 2 and sentence_count <= 4
    content_rich = (
        text_chars >= EXTRACTOR_HEURISTICS.rich_content_chars
        or (text_chars >= 1200 and paragraph_blocks >= 4 and sentence_count >= 6)
    )
    link_heavy = href_count >= 8 and text_chars / max(href_count, 1) < 250
    dominant_document_target = (
        document_link_count >= 1
        and (
            href_count <= 3
            or document_link_count == href_count
            or document_link_count / max(href_count, 1) >= 0.5
        )
        and (
            sparse_prose
            or not content_rich
            or (metadata_marker_count >= 4 and sentence_count <= 6)
        )
    )
    document_hub = document_link_count >= 2 and href_count >= 3
    avg_paragraph_chars = text_chars / max(paragraph_blocks, 1)

    content_score = 0
    if text_chars >= EXTRACTOR_HEURISTICS.rich_content_chars:
        content_score += 4
    elif text_chars >= 1200:
        content_score += 3
    elif text_chars >= EXTRACTOR_HEURISTICS.thin_content_chars:
        content_score += 1

    if paragraph_blocks >= 4:
        content_score += 2
    elif paragraph_blocks >= 2:
        content_score += 1

    if sentence_count >= 8:
        content_score += 2
    elif sentence_count >= 4:
        content_score += 1

    if text_chars >= 1200 and sentence_count >= 12:
        content_score += 1

    if paragraph_blocks >= 2 and avg_paragraph_chars >= 140:
        content_score += 1

    record_score = 0
    if document_link_count >= 1:
        record_score += 2
    if metadata_marker_count >= 4:
        record_score += 2
    elif metadata_marker_count >= 2:
        record_score += 1
    if dominant_document_target:
        record_score += 2
    if sparse_prose:
        record_score += 2
    if link_heavy:
        record_score += 1
    if document_hub:
        record_score += 1
    if text_chars < EXTRACTOR_HEURISTICS.rich_content_chars:
        record_score += 1

    interactive_score = 0
    if explicit_interactive:
        interactive_score += 5
    if (
        text_chars < ROUTING_HEURISTICS.low_text_spa_chars
        and script_tags >= ROUTING_HEURISTICS.low_text_spa_script_count
    ):
        interactive_score += 3
    if (
        text_chars < EXTRACTOR_HEURISTICS.thin_content_chars
        and list_line_ratio >= EXTRACTOR_HEURISTICS.nav_dump_bullet_ratio
    ):
        interactive_score += 2
    if (
        script_tags >= ROUTING_HEURISTICS.heavy_spa_script_count
        and text_chars < ROUTING_HEURISTICS.rich_html_static_text_chars
    ):
        interactive_score += 1

    if content_score >= 6 and content_score >= record_score + 2 and content_score >= interactive_score + 2:
        page_type: PageType = "content_page"
    elif (
        interactive_score >= 5
        and interactive_score >= content_score + 2
        and interactive_score >= record_score + 1
        and text_chars < ROUTING_HEURISTICS.rich_html_static_text_chars
    ):
        page_type = "interactive_shell"
    elif (
        record_score >= 5
        and record_score >= content_score + 2
        and record_score >= interactive_score + 1
    ):
        page_type = "record_page"
    elif content_score >= 4 and content_score >= max(record_score, interactive_score):
        page_type = "content_page"
    elif interactive_score >= 6 and text_chars < EXTRACTOR_HEURISTICS.rich_content_chars:
        page_type = "interactive_shell"
    elif record_score >= 6 and sparse_prose:
        page_type = "record_page"
    else:
        page_type = "uncertain"

    sorted_scores = sorted((content_score, record_score, interactive_score), reverse=True)
    confidence = sorted_scores[0] - sorted_scores[1]

    return PageShapeAssessment(
        page_type=page_type,
        text_chars=text_chars,
        paragraph_blocks=paragraph_blocks,
        sentence_count=sentence_count,
        href_count=href_count,
        document_link_count=document_link_count,
        metadata_marker_count=metadata_marker_count,
        list_line_ratio=list_line_ratio,
        content_score=content_score,
        record_score=record_score,
        interactive_score=interactive_score,
        confidence=confidence,
    )


def classify_html_page_shape(html: str) -> PageShapeAssessment:
    text_blocks = _text_blocks_from_html(html)
    text = re.sub(r"\s+", " ", _strip_html(html)).strip()
    hrefs = [m.group(1).strip() for m in _HTML_HREF_RE.finditer(html)]
    document_link_count = sum(1 for href in hrefs if _looks_like_document_url(href))
    return _classify_page_shape(
        text=text,
        paragraph_blocks=sum(1 for block in text_blocks if len(block) >= 80),
        sentence_count=_count_sentence_endings(text),
        href_count=len(hrefs),
        document_link_count=document_link_count,
        metadata_marker_count=_count_metadata_markers(text),
        list_line_ratio=0.0,
        explicit_interactive=False,
        script_tags=len(re.findall(r"<script", html, re.I)),
    )


def classify_prefetched_page_shape(content: str) -> PageShapeAssessment:
    lines = _content_lines_from_prefetched(content)
    text = "\n".join(lines)
    urls = _DOC_URL_RE.findall(text)
    long_lines = [line for line in lines if len(line) >= 80]
    list_lines = sum(1 for line in lines if line.startswith(("* ", "- ", "[", "1.", "2.", "3.")))
    list_line_ratio = list_lines / len(lines) if lines else 0.0
    return _classify_page_shape(
        text=text,
        paragraph_blocks=len(long_lines),
        sentence_count=_count_sentence_endings(text),
        href_count=len(urls),
        document_link_count=sum(1 for url in urls if _looks_like_document_url(url)),
        metadata_marker_count=_count_metadata_markers(text),
        list_line_ratio=list_line_ratio,
        explicit_interactive="[SPA:" in content or "[Form/survey" in content,
    )


_RENDERED_LINKS_HEADING = "**Relevant follow-up links:**"


__all__ = [
    "PageShapeAssessment",
    "classify_html_page_shape",
    "classify_prefetched_page_shape",
]
