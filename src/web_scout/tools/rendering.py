"""Content parsing and rendering helpers for scraped output.

Handles the string contract used by the scrape-and-extract layer:
rich/thin snippet classification, list-page detection, link extraction,
and reconstructing typed ``ExtractorOutcome`` objects from legacy rendered text.
"""

import re
from typing import Any

from web_scout._extractor_contract import ExtractorOutcome

_DIGIT_PATTERN = re.compile(r"\d")
_ERROR_TITLE_RE = re.compile(r"^Error[:\s\-]", re.IGNORECASE)
_HTTP_ERROR_RE = re.compile(r"\bHTTP\s+\d{3}\b", re.IGNORECASE)

RENDERED_LIST_PAGE_MARKER = "**Page type: list**"
RENDERED_RELEVANT_LINKS_HEADING = "\n\n**Relevant Links found on page:**\n"


def snippet_quality(snippet: str) -> str:
    """Classify a search snippet as ``[rich]`` or ``[thin]``."""
    if len(snippet) > 120 and _DIGIT_PATTERN.search(snippet):
        return "[rich]"
    return "[thin]"


def is_rendered_list_page(content: str) -> bool:
    """Detect the list-page marker in rendered scrape output."""
    return RENDERED_LIST_PAGE_MARKER in content


def _extract_rendered_links_section(content: str) -> list[str]:
    """Parse the explicit rendered follow-up section when present."""
    heading_index = content.find(RENDERED_RELEVANT_LINKS_HEADING)
    if heading_index < 0:
        return []

    section = content[heading_index + len(RENDERED_RELEVANT_LINKS_HEADING):]
    lines = []
    for line in section.splitlines():
        if line.startswith("- "):
            lines.append(line[2:].strip())
            continue
        if not line.strip():
            continue
        break
    return [line for line in lines if line.startswith("http://") or line.startswith("https://")]


def extract_rendered_followup_links(content: str) -> list[str]:
    """Extract follow-up URLs from rendered scrape output.

    Scans the full rendered markdown payload (not just the explicit section),
    preserving the existing behavior for pipeline consumers.
    """
    seen: set[str] = set()
    links: list[str] = []
    for match in re.finditer(r"\]\((https?://[^\s)]+)\)|(https?://\S+)", content):
        url = match.group(1) or match.group(2)
        url = url.rstrip(".,;:)>\"'")
        if url and url not in seen:
            seen.add(url)
            links.append(url)
    return links


def extract_explicit_rendered_followup_links(content: str) -> list[str]:
    """Extract only the explicit 'Relevant Links' section from rendered scrape output."""
    return _extract_rendered_links_section(content)


def extract_primary_rendered_content(content: str) -> str:
    """Remove known rendered suffix sections from the legacy output."""
    heading_index = content.find(RENDERED_RELEVANT_LINKS_HEADING)
    if heading_index >= 0:
        return content[:heading_index].rstrip()
    return content.rstrip()


def infer_rendered_outcome(url: str, rendered_text: str) -> ExtractorOutcome:
    """Reconstruct a typed outcome from the legacy rendered string contract."""
    from typing import Literal

    page_type: Literal["list", "content"] = "list" if is_rendered_list_page(rendered_text) else "content"
    links = _extract_rendered_links_section(rendered_text) or extract_rendered_followup_links(rendered_text)
    if rendered_text.startswith("Failed to extract content from "):
        content = rendered_text.split(": ", 1)[1] if ": " in rendered_text else rendered_text
        return ExtractorOutcome(
            url=url,
            status="failure",
            rendered_text=rendered_text,
            content=content,
            page_type=page_type,
            relevant_links=links,
            failure_kind="subagent_failed",
        )
    if rendered_text.startswith("No relevant content found at "):
        from .outcomes import classify_failure_action

        content = rendered_text.split(": ", 1)[1] if ": " in rendered_text else rendered_text
        return ExtractorOutcome(
            url=url,
            status="failure",
            rendered_text=rendered_text,
            content=content,
            page_type=page_type,
            relevant_links=links,
            failure_kind=classify_failure_action(content),
        )

    title = ""
    body = rendered_text
    if rendered_text.startswith("# "):
        header, _, remainder = rendered_text.partition("\n")
        title = header[2:].strip()
        body = remainder
    if body.startswith("Source: "):
        _, _, body = body.partition("\n\n")
    body = extract_primary_rendered_content(body)
    if page_type == "list" and body.endswith("\n" + RENDERED_LIST_PAGE_MARKER):
        body = body[: -len("\n" + RENDERED_LIST_PAGE_MARKER)].rstrip()
    return ExtractorOutcome(
        url=url,
        status="success",
        rendered_text=rendered_text,
        title=title,
        content=body,
        page_type=page_type,
        relevant_links=links,
    )


def resolve_scrape_outcome(scrape_tool: Any, url: str, rendered_text: str) -> ExtractorOutcome:
    """Return the typed outcome for a scrape tool call, falling back to legacy parsing."""
    from .tracker import ResearchTracker

    outcome_cache = getattr(scrape_tool, "_outcome_cache", None)
    norm = ResearchTracker.normalize_url(url)
    if isinstance(outcome_cache, dict) and norm in outcome_cache:
        return outcome_cache[norm]
    return infer_rendered_outcome(url, rendered_text)
