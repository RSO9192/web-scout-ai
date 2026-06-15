"""Outcome builders for the scrape-and-extract layer.

Produces typed ``ExtractorOutcome`` objects and their legacy rendered text
for both successful and failed scrapes.
"""

import re
from typing import Literal, Optional

from web_scout._extractor_contract import ExtractorOutcome
from web_scout.config import EXTRACTOR_HEURISTICS
from .rendering import RENDERED_LIST_PAGE_MARKER, RENDERED_RELEVANT_LINKS_HEADING

_HTTP_ERROR_RE = re.compile(r"\bHTTP\s+\d{3}\b", re.IGNORECASE)


def classify_failure_action(content: str) -> str:
    """Map failure content to the canonical action label."""
    lower = content.lower()
    if "bot_detected:" in content:
        return "bot_detected"
    if "skipped: blocked domain" in lower:
        return "blocked_by_policy"
    if content.startswith("[No relevant content") or content.startswith("No relevant content"):
        return "scraped_irrelevant"
    if _HTTP_ERROR_RE.search(content) or "get failed:" in lower or "connecterror" in lower:
        return "source_http_error"
    return "scrape_failed"


def _append_min_successful_scrape_reminder(rendered: str, count_scraped: int, *, force_other_urls: bool) -> str:
    if count_scraped >= 2:
        return rendered
    if force_other_urls:
        return rendered + (
            "\n\n⚠ REMINDER: You MUST successfully scrape AT LEAST 2 high-quality sources "
            f"before synthesising and finishing. You currently have {count_scraped} "
            "successful scrape(s). You MUST find other URLs and scrape them!"
        )
    return rendered + (
        "\n\n⚠ REMINDER: You MUST successfully scrape AT LEAST 2 high-quality sources "
        "before synthesising and finishing. You currently have "
        f"{count_scraped} successful scrape(s)."
    )


def build_success_outcome(
    *,
    url: str,
    title: str,
    content: str,
    page_type: Literal["list", "content"],
    links: list[str],
    count_scraped: Optional[int],
) -> ExtractorOutcome:
    """Build a typed success outcome and its legacy rendered text."""
    header = f"# {title}\nSource: {url}\n\n" if title else f"Source: {url}\n\n"
    rendered = header + content
    if page_type == "list":
        rendered += "\n" + RENDERED_LIST_PAGE_MARKER
    if links:
        rendered += RENDERED_RELEVANT_LINKS_HEADING + "\n".join(
            f"- {link}" for link in links[: EXTRACTOR_HEURISTICS.max_rendered_relevant_links]
        )
    if count_scraped is not None:
        rendered = _append_min_successful_scrape_reminder(rendered, count_scraped, force_other_urls=False)
    return ExtractorOutcome(
        url=url,
        status="success",
        rendered_text=rendered,
        title=title,
        content=content,
        page_type=page_type,
        relevant_links=links[: EXTRACTOR_HEURISTICS.max_rendered_relevant_links],
    )


def build_failure_outcome(
    *,
    url: str,
    content: str,
    count_scraped: Optional[int],
    failure_kind: str,
) -> ExtractorOutcome:
    """Build a typed failure outcome and its legacy rendered text."""
    rendered = f"No relevant content found at {url}: {content}"
    if count_scraped is not None:
        rendered = _append_min_successful_scrape_reminder(rendered, count_scraped, force_other_urls=True)
    return ExtractorOutcome(
        url=url,
        status="failure",
        rendered_text=rendered,
        content=content,
        failure_kind=failure_kind,
    )


def render_successful_extractor_output(
    *,
    url: str,
    title: str,
    content: str,
    page_type: Literal["list", "content"],
    links: list[str],
    count_scraped: Optional[int],
) -> str:
    """Return the rendered text for a successful extraction."""
    return build_success_outcome(
        url=url, title=title, content=content, page_type=page_type, links=links, count_scraped=count_scraped
    ).rendered_text


def render_failed_extractor_output(*, url: str, content: str, count_scraped: Optional[int]) -> str:
    """Return the rendered text for a failed extraction."""
    failure_kind = classify_failure_action(content)
    return build_failure_outcome(url=url, content=content, count_scraped=count_scraped, failure_kind=failure_kind).rendered_text
