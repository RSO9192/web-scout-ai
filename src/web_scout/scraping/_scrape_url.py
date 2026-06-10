"""One-shot scrape_url entry point (private implementation module).

``scrape_url`` is the main public convenience function that combines routing,
strategy execution, and artifact materialization into a single call.  It is
re-exported from the package ``__init__`` so callers import it as::

    from web_scout.scraping import scrape_url
"""

import logging
from typing import Optional, Tuple

from web_scout.config import ROUTING_HEURISTICS

from .executor import execute_strategy, materialize_source_artifact
from .plan import build_scrape_plan
from .types import ScrapeStrategy

# Silence noisy third-party loggers used internally by this package.
logging.getLogger("docling").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("crawl4ai").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

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
    """Scrape a URL and return ``(markdown_content, page_title, error_or_none)``.

    The routing flow:

    1. ``build_scrape_plan`` — cheap HEAD+GET validation to classify the URL.
    2. ``execute_strategy``  — run the chosen strategy (HTML fast/browser,
       document, JSON, or image) with the Chain-of-Responsibility fallback.
    3. ``materialize_source_artifact`` — apply truncation (and vision extraction
       for binary artifacts such as scanned PDFs).

    Args:
        url:              The URL to scrape.
        wait_for:         Optional CSS selector to wait for on JS-rendered pages.
        query:            Optional search query for BM25 content filtering.
        vision_model:     Optional litellm model name for vision fallbacks.
        allowed_domains:  Domains to remove from the default block list.
        max_pdf_pages:    Maximum PDF pages to extract (default 50).
        max_content_chars: Maximum characters to return (default 30 000).
    """
    plan = await build_scrape_plan(url, allowed_domains=allowed_domains)

    if plan.strategy == ScrapeStrategy.SKIP:
        return "", "", f"Skipped: {plan.reason}"

    try:
        artifact, error = await execute_strategy(
            plan,
            url,
            wait_for=wait_for,
            query=query,
            vision_model=vision_model,
            max_pdf_pages=max_pdf_pages,
        )
    except Exception as exc:
        logger.error("[scrape] failed %s: %s", url, exc)
        return "", "", str(exc)

    if error:
        if plan.likely_bot_detected:
            logger.info("[scrape] bot_detected %s", url)
            return "", "", f"bot_detected: {error}"
        return "", "", error

    if artifact is None:
        return "", "", "Extraction returned empty content"

    content, title, mat_error = await materialize_source_artifact(
        artifact,
        query=query,
        vision_model=vision_model,
        max_content_chars=max_content_chars,
    )

    if mat_error:
        if plan.likely_bot_detected:
            return "", title or "", f"bot_detected: {mat_error}"
        return "", title or "", mat_error

    if not content.strip():
        if plan.likely_bot_detected:
            return "", title or "", "bot_detected: Browser loaded page but returned empty content"
        return "", title or "", "Extraction returned empty content"

    return content, title or "", None
