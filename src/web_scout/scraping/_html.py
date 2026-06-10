"""HTML scraping strategies (private): HTTP-fast and full Playwright browser.

Two internal entry points:
- ``_scrape_html_fast``    — HTTP-only crawl via crawl4ai's AsyncHTTPCrawlerStrategy.
  Returns ``(None, None)`` when content is too thin, signalling the caller to fall
  through to the browser strategy (Chain-of-Responsibility handoff).
- ``_scrape_html_browser`` — Full Playwright browser crawl with stealth mode.
  Returns ``_DOWNLOAD_SIGNAL`` as the error string when the browser triggers a file
  download, prompting the executor to redirect to the document strategy.

Both functions accept an optional ``query`` parameter; when supplied, crawl4ai's
BM25ContentFilter is applied during crawling to surface the most relevant text.
This single parameterisation eliminates the previous query-aware / query-agnostic
duplication.
"""

import logging
from typing import Any, Optional, Tuple

from web_scout.config import ROUTING_HEURISTICS

from ._markdown import append_links, pick_markdown
from .constants import FETCH_HEADERS
from .types import SourceArtifact

logger = logging.getLogger(__name__)

_DOWNLOAD_SIGNAL = "__DOWNLOAD_REDIRECT__"

_404_PATTERNS = frozenset({"page not found", "was not found", "no longer exists", "404 error page"})


def _is_404_content(content: str) -> bool:
    lower = content.lower()
    return "404" in lower and any(p in lower for p in _404_PATTERNS)


def _make_browser_config(**overrides: Any):
    from crawl4ai import BrowserConfig

    return BrowserConfig(verbose=False, **overrides)


def _make_run_config(*, query: str = "", wait_for: Optional[str] = None, browser: bool = False):
    """Build a CrawlerRunConfig with optional BM25 filtering."""
    from crawl4ai import CacheMode, CrawlerRunConfig
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    if query:
        from crawl4ai.content_filter_strategy import BM25ContentFilter

        md_generator = DefaultMarkdownGenerator(
            content_filter=BM25ContentFilter(
                user_query=query,
                bm25_threshold=ROUTING_HEURISTICS.bm25_threshold,
            )
        )
    else:
        md_generator = DefaultMarkdownGenerator()

    kwargs: dict[str, Any] = dict(
        cache_mode=CacheMode.BYPASS,
        exclude_all_images=True,
        remove_overlay_elements=True,
        markdown_generator=md_generator,
        verbose=False,
    )
    if browser:
        kwargs.update(
            wait_until="networkidle",
            page_timeout=ROUTING_HEURISTICS.browser_page_timeout_ms,
            delay_before_return_html=ROUTING_HEURISTICS.browser_delay_before_return_html_s,
        )
    if wait_for:
        kwargs["wait_for"] = wait_for
    return CrawlerRunConfig(**kwargs)


async def scrape_html_fast(url: str, *, query: str = "") -> Tuple[Optional[SourceArtifact], Optional[str]]:
    """Attempt an HTTP-only crawl.

    Returns ``(None, None)`` when content is too thin to be useful — the caller
    should then fall through to ``_scrape_html_browser`` (Chain-of-Responsibility).
    Returns ``(None, error_message)`` only for truly unrecoverable failures that
    should not be silently retried.
    """
    from crawl4ai import AsyncWebCrawler
    from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy

    try:
        async with AsyncWebCrawler(
            config=_make_browser_config(),
            crawler_strategy=AsyncHTTPCrawlerStrategy(),
        ) as crawler:
            result = await crawler.arun(url=url, config=_make_run_config(query=query))
    except Exception:
        return None, None  # any exception → pass to browser

    if not result.success:
        return None, None

    content = append_links(pick_markdown(result.markdown, query), result)
    if len(content.strip()) < ROUTING_HEURISTICS.html_fast_thin_content_chars:
        return None, None  # thin content → pass to browser

    return SourceArtifact(
        kind="text",
        title=(result.metadata or {}).get("title", ""),
        text_content=content,
    ), None


async def scrape_html_browser(
    url: str,
    *,
    wait_for: Optional[str] = None,
    query: str = "",
) -> Tuple[SourceArtifact, Optional[str]]:
    """Full Playwright browser crawl with stealth mode.

    Returns ``(artifact, _DOWNLOAD_SIGNAL)`` when the browser encounters a file
    download, so the executor can redirect to the document strategy.
    """
    from crawl4ai import AsyncWebCrawler

    browser_cfg = _make_browser_config(
        headless=True,
        enable_stealth=True,
        user_agent=FETCH_HEADERS["User-Agent"],
    )

    async def _run(wf: Optional[str]) -> Any:
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            return await crawler.arun(url=url, config=_make_run_config(query=query, wait_for=wf, browser=True))

    try:
        result = await _run(wait_for)
    except Exception as exc:
        if "Download is starting" in str(exc):
            return SourceArtifact(kind="text", title=""), _DOWNLOAD_SIGNAL
        return SourceArtifact(kind="text", title=""), f"Browser crawl failed: {exc}"

    # Retry without wait_for when it caused a timeout
    if not result.success and wait_for:
        try:
            result = await _run(None)
        except Exception as exc:
            return SourceArtifact(kind="text", title=""), f"Browser retry failed: {exc}"

    if not result.success:
        return SourceArtifact(kind="text", title=""), result.error_message or "Browser crawl failed"

    content = pick_markdown(result.markdown, query)

    # Recover from over-aggressive BM25 filtering
    if (
        query
        and hasattr(result.markdown, "fit_markdown")
        and len((result.markdown.fit_markdown or "").strip()) <= 20
        and hasattr(result.markdown, "raw_markdown")
    ):
        content = getattr(result.markdown, "markdown_with_citations", None) or result.markdown.raw_markdown

    content = append_links(content, result)
    title = (result.metadata or {}).get("title", "")
    return SourceArtifact(kind="text", title=title, text_content=content), None
