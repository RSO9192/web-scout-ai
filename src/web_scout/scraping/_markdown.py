"""Markdown extraction and link-enrichment helpers (private).

All functions in this module are implementation details — they are not part
of the public scraping API.  Import via the private module path only.
"""

import re
from urllib.parse import unquote, urlparse

from .constants import LINKS_SECTION_HEADING
from .utils import looks_like_document_link

_NOISE_LABELS = frozenset({"read more", "click here", "learn more"})


def pick_markdown(md, query: str) -> str:
    """Return the best markdown variant from a crawl4ai result object.

    Prefers BM25-filtered ``fit_markdown`` when a query was provided and the
    filtered output is non-trivial; otherwise falls back to raw markdown.
    """
    if query and hasattr(md, "fit_markdown") and md.fit_markdown and len(md.fit_markdown.strip()) > 20:
        return md.fit_markdown
    if hasattr(md, "raw_markdown"):
        return getattr(md, "markdown_with_citations", None) or md.raw_markdown
    return str(md) if md else ""


def _link_label(href: str) -> str:
    """Derive a human-readable label from a bare URL (path tail or hostname)."""
    parsed = urlparse(href)
    tail = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1]) if parsed.path else ""
    return tail or parsed.netloc or href


def append_links(content: str, result, *, limit: int = 50) -> str:
    """Append a deduplicated section of useful page links to the markdown.

    Collects links from two sources:
    - Markdown hyperlinks already embedded in ``content``.
    - The ``result.links`` dict returned by crawl4ai.

    Icon-only document links (no anchor text) are kept because repository
    pages often expose their primary documents only via file-icon anchors.
    Generic noise labels ("read more", "click here", …) are dropped.
    """
    links_data = getattr(result, "links", {})
    if isinstance(links_data, dict):
        raw_links = list(links_data.get("internal", [])) + list(links_data.get("external", []))
    else:
        raw_links = list(getattr(links_data, "internal", [])) + list(getattr(links_data, "external", []))

    lines: list[str] = []

    for match in re.finditer(r"\[([^\]]+)\]\((https?://[^\)]+)\)", content):
        text, href = match.groups()
        if text.strip() and text.lower() not in _NOISE_LABELS:
            lines.append(f"- [{text.strip().replace(chr(10), ' ')}]({href.strip()})")

    for item in raw_links:
        href = item.get("href", "") if isinstance(item, dict) else getattr(item, "href", "")
        text = ((item.get("text", "") if isinstance(item, dict) else getattr(item, "text", "")) or "").strip()
        if not href:
            continue
        if text and text.lower() not in _NOISE_LABELS:
            lines.append(f"- [{text}]({href})")
        elif looks_like_document_link(href):
            lines.append(f"- [{_link_label(href)}]({href})")

    if not lines:
        return content

    seen: set[str] = set()
    unique: list[str] = []
    for line in lines:
        href = line.rsplit("](", 1)[-1].rstrip(")")
        if href not in seen:
            seen.add(href)
            unique.append(line)

    return content + f"\n\n{LINKS_SECTION_HEADING}\n" + "\n".join(unique[:limit])
