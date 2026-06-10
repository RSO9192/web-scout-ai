"""Unified web scraping via crawl4ai, docling, and vision fallbacks.

Provides a single ``scrape_url`` function that:

1. **Validates** the URL cheaply (HEAD + fast GET) to skip 404s, SPA
   shells, paywalls, binary files, and blocked domains before any
   expensive processing starts.
2. **Routes** to the appropriate handler based on content type:
   - Static HTML  → crawl4ai ``AsyncHTTPCrawlerStrategy`` (no browser)
   - JS/SPA pages → crawl4ai full browser (Playwright)
   - Documents    → docling (PDF, DOCX, PPTX, XLSX)
   - JSON         → structured extraction
   - Images       → vision extraction
"""

import logging
from typing import Optional, Tuple

from web_scout.config import ROUTING_HEURISTICS

from .plan import build_scrape_plan
from .strategy import (
    fetch_query_agnostic_source_artifact,
    materialize_source_artifact,
    scrape_document,
    scrape_html_browser,
    scrape_html_fast,
    scrape_image,
    scrape_json,
)
from .types import ScrapePlan, ScrapeStrategy, SourceArtifact

logger = logging.getLogger(__name__)

# Silence noisy third-party loggers used internally by this module.
# These produce excessive output at INFO/DEBUG level that is not useful to callers.
logging.getLogger("docling").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("crawl4ai").setLevel(logging.WARNING)


MAX_CONTENT_CHARS = 30_000


async def scrape_url(
    url: str,
    wait_for: Optional[str] = None,
    query: str = "",
    vision_model: Optional[str] = None,
    allowed_domains: Optional[frozenset] = None,
    max_pdf_pages: int = ROUTING_HEURISTICS.pdf_max_pages_default,
    max_content_chars: int = MAX_CONTENT_CHARS,
) -> Tuple[str, str, Optional[str]]:
    """Scrape a URL and return clean markdown content.

    The routing flow is:

    1. Build a ``ScrapePlan`` from cheap validation (HEAD + fast GET).
    2. Execute the handler chosen by the plan's strategy.
    3. Normalize bot-detection, empty-content, and truncation behavior.

    Args:
        url: The URL to scrape.
        wait_for: Optional CSS selector for JS-rendered pages.
        query: Optional search query for BM25 content filtering.
        allowed_domains: Frozenset of domain strings (e.g. ``frozenset({"reddit.com"})``)
            to remove from the default blocked-domain list. ``None`` uses the full block list.
        max_pdf_pages: Maximum number of pages to extract from PDFs. Defaults to 50.
        max_content_chars: Maximum characters to return per page. Defaults to 30,000.

    Returns:
        Tuple of ``(markdown_content, page_title, error_or_none)``.
    """
    plan = await build_scrape_plan(url, allowed_domains=allowed_domains)

    if plan.strategy == ScrapeStrategy.SKIP:
        return "", "", f"Skipped: {plan.reason}"

    try:
        if plan.strategy == ScrapeStrategy.DOCUMENT:
            content, title, error = await scrape_document(
                url,
                query=query,
                vision_model=vision_model,
                max_pdf_pages=max_pdf_pages,
                known_content_type=plan.content_type,
                known_content_disposition=plan.content_disposition,
            )
        elif plan.strategy == ScrapeStrategy.JSON:
            content, title, error = await scrape_json(url)
        elif plan.strategy == ScrapeStrategy.IMAGE:
            content, title, error = await scrape_image(url, query=query, vision_model=vision_model)
        elif plan.strategy == ScrapeStrategy.HTML_FAST:
            content, title, error = await scrape_html_fast(url, query=query, vision_model=vision_model)
        else:
            content, title, error = await scrape_html_browser(url, wait_for, query=query, vision_model=vision_model)
    except Exception as e:
        logger.error("[scrape] failed %s: %s", url, e)
        return "", "", str(e)

    if error:
        if plan.likely_bot_detected:
            logger.info("[scrape] bot_detected %s", url)
            return "", title or "", f"bot_detected: {error}"
        return "", title or "", error

    if not content.strip():
        if plan.likely_bot_detected:
            logger.info("[scrape] bot_detected %s", url)
            return (
                "",
                title or "",
                "bot_detected: Browser loaded page but returned empty content",
            )
        return "", title or "", "Extraction returned empty content"

    if len(content) > max_content_chars:
        content = content[:max_content_chars] + f"\n\n[Truncated at {max_content_chars:,} chars]"

    return content, title or "", None


__all__ = [
    "scrape_url",
    "ScrapePlan",
    "ScrapeStrategy",
    "SourceArtifact",
    "fetch_query_agnostic_source_artifact",
    "materialize_source_artifact",
]
