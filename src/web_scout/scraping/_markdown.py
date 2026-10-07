"""Markdown extraction and link-enrichment helpers (private).

All functions in this module are implementation details — they are not part
of the public scraping API.  Import via the private module path only.
"""

import re
from urllib.parse import unquote, urlparse

from .constants import LINKS_SECTION_HEADING
from .utils import extract_citation_pdf_urls, looks_like_document_link

_NOISE_LABELS = frozenset({"read more", "click here", "learn more"})


def _link_label(href: str) -> str:
    """Derive a human-readable label from a bare URL (path tail or hostname)."""
    parsed = urlparse(href)
    tail = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1]) if parsed.path else ""
    return tail or parsed.netloc or href


def _extract_links_from_page(result) -> dict:
    """Extract links from a Scrapling Response object into the expected format.

    Returns a dict with 'internal' and 'external' link lists.
    """
    if result is None:
        return {}
    # Scrapling Response objects expose a .css() method
    if not hasattr(result, "css"):
        return {}
    try:
        internal: list = []
        external: list = []
        for a in result.css("a"):
            href = str(a.attrib.get("href", "") or "").strip()
            text = str(a.get_all_text()).strip() if hasattr(a, "get_all_text") else ""
            if not href:
                continue
            entry = {"href": href, "text": text}
            if href.startswith("http"):
                external.append(entry)
            elif href:
                internal.append(entry)
        return {"internal": internal, "external": external}
    except Exception:
        return {}


def append_links(content: str, result, *, limit: int = 50, html: str | None = None) -> str:
    """Append a deduplicated section of useful page links to the markdown.

    Collects links from three sources:
    - Markdown hyperlinks already embedded in ``content``.
    - The fetched Scrapling page's ``result.css('a')`` links.
    - ``citation_pdf_url`` meta tags from ``html`` / ``result.html_content``.

    Icon-only document links (no anchor text) are kept because repository
    pages often expose their primary documents only via file-icon anchors.
    Generic noise labels ("read more", "click here", …) are dropped.
    """
    links_data = _extract_links_from_page(result)
    raw_links = links_data.get("internal", []) + links_data.get("external", [])

    if html is not None:
        html_text = html
    elif result is not None:
        html_text = getattr(result, "html_content", None) or ""
    else:
        html_text = ""

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

    for pdf_url in extract_citation_pdf_urls(html_text):
        lines.append(f"- [Download PDF]({pdf_url})")

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
