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
    content_rich = (
        text_chars >= EXTRACTOR_HEURISTICS.rich_content_chars
        or (text_chars >= 1200 and paragraph_blocks >= 4 and sentence_count >= 6)
    )
    sparse_prose = paragraph_blocks <= 2 and sentence_count <= 4
    link_heavy = href_count >= 8 and text_chars / max(href_count, 1) < 250
    record_evidence = document_link_count >= 1 and metadata_marker_count >= 2
    document_hub = document_link_count >= 2 and href_count >= 3
    interactive_like = explicit_interactive or (
        text_chars < ROUTING_HEURISTICS.low_text_spa_chars
        and script_tags >= ROUTING_HEURISTICS.low_text_spa_script_count
    ) or (
        text_chars < EXTRACTOR_HEURISTICS.thin_content_chars
        and list_line_ratio >= EXTRACTOR_HEURISTICS.nav_dump_bullet_ratio
    )

    if interactive_like:
        page_type: PageType = "interactive_shell"
    elif record_evidence and (sparse_prose or not content_rich or link_heavy or document_hub):
        page_type = "record_page"
    elif content_rich:
        page_type = "content_page"
    elif record_evidence or (document_link_count >= 1 and metadata_marker_count >= 3 and sparse_prose):
        page_type = "record_page"
    else:
        page_type = "uncertain"

    return PageShapeAssessment(
        page_type=page_type,
        text_chars=text_chars,
        paragraph_blocks=paragraph_blocks,
        sentence_count=sentence_count,
        href_count=href_count,
        document_link_count=document_link_count,
        metadata_marker_count=metadata_marker_count,
        list_line_ratio=list_line_ratio,
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
