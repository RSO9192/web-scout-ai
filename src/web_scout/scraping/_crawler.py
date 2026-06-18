"""Crawler layer — Crawler ABC and Crawl4AICrawler concrete implementation.

The ``Crawler`` analyses the already-parsed content of a page and decides which
URLs to explore next.  It does *not* re-fetch the page; Scrapling remains the
sole HTTP/browser transport layer.

``Crawl4AICrawler`` uses crawl4ai's extraction and link-ranking capabilities on
pre-parsed content.  By default it builds a crawl4ai ``LLMConfig`` from
``DEFAULT_WEB_RESEARCH_MODELS["followup_selector"]`` and uses
``LLMExtractionStrategy`` to identify the most relevant links.  When no API key
is available, or when ``llm_config=None`` is passed explicitly, it applies a
lightweight heuristic filter on the links already extracted by the Parser.
"""

import html
import logging
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Optional

from web_scout._pipeline_types import DEFAULT_WEB_RESEARCH_MODELS

from .context import URLContext
from .types import ParseResult

logger = logging.getLogger(__name__)

_USE_DEFAULT_LLM = object()


def _build_default_llm_config() -> Optional[object]:
    """Build a crawl4ai ``LLMConfig`` from web-scout defaults, or ``None`` if unavailable."""
    try:
        from crawl4ai import LLMConfig
    except ImportError:
        return None

    from web_scout.utils import _detect_provider, _find_api_key

    model = DEFAULT_WEB_RESEARCH_MODELS["followup_selector"]
    provider = _detect_provider(model)
    if provider == "ollama":
        return LLMConfig(provider=model, temperature=0)

    api_key = _find_api_key(provider) if provider else None
    if not api_key:
        return None

    return LLMConfig(provider=model, api_token=api_key, temperature=0)


class Crawler(ABC):
    """Abstract base class for crawlers.

    The ``crawl`` method receives the parsed page content together with the
    per-URL context and a *callback* bound to ``Orchestrator.queue_url``.
    The implementation calls ``await queue_url(new_url)`` for every URL worth
    following — depth and deduplication are enforced by the Orchestrator.
    """

    @abstractmethod
    async def crawl(
        self,
        result: ParseResult,
        context: URLContext,
        queue_url: Callable[[str], Awaitable[None]],
    ) -> None:
        """Analyse *result* and queue follow-up URLs via *queue_url*.

        Args:
            result:    Parsed content of the current page.
            context:   Per-URL context (depth, parent URL, stop flag).
            queue_url: Async callback — ``await queue_url(url)`` to schedule
                       a URL for fetching.  The Orchestrator enforces depth /
                       deduplication / URL-cap constraints.
        """


class Crawl4AICrawler(Crawler):
    """Crawler that uses crawl4ai's extraction strategies to rank and filter links.

    crawl4ai is used as a *content-understanding layer* only.  The actual HTTP
    fetching is always performed by ``ScraplingFetcher``.

    By default ``LLMExtractionStrategy`` is applied using a crawl4ai ``LLMConfig``
    built from ``DEFAULT_WEB_RESEARCH_MODELS["followup_selector"]``.  Pass
    ``llm_config=None`` explicitly to use heuristic filtering only, or pass a
    custom ``LLMConfig`` to override the default.

    Args:
        llm_config: crawl4ai ``LLMConfig`` for LLM-based link ranking.
                    Omitted (default) uses ``followup_selector`` from
                    ``DEFAULT_WEB_RESEARCH_MODELS`` when an API key is available.
                    ``None`` disables LLM selection and uses heuristics only.
        max_links:  Maximum number of follow-up URLs to queue per page.
    """

    def __init__(
        self,
        *,
        llm_config: Optional[object] = _USE_DEFAULT_LLM,
        max_links: int = 10,
    ) -> None:
        if llm_config is _USE_DEFAULT_LLM:
            self._llm_config = _build_default_llm_config()
        else:
            self._llm_config = llm_config
        self._max_links = max_links

    async def crawl(
        self,
        result: ParseResult,
        context: URLContext,
        queue_url: Callable[[str], Awaitable[None]],
    ) -> None:
        if not result.links:
            return

        selected = await self._select_links(result, context)
        for url in selected:
            await queue_url(url)

    async def _select_links(self, result: ParseResult, context: URLContext) -> list[str]:
        """Return up to ``max_links`` URLs worth following from ``result.links``."""
        if self._llm_config is not None:
            return await self._llm_select(result, context)
        return self._heuristic_select(result)

    # ------------------------------------------------------------------
    # LLM-based selection via crawl4ai LLMExtractionStrategy
    # ------------------------------------------------------------------

    def _prefetched_crawl_input(self, result: ParseResult) -> str:
        """Build a crawl4ai input that uses already-fetched HTML (no network fetch).

        crawl4ai treats ``raw:``-prefixed strings as in-memory HTML and skips
        browser navigation entirely.
        """
        page_html = result.raw_html
        if not page_html:
            # Last-resort fallback when only parsed text is available.
            links_html = "".join(
                f'<a href="{html.escape(url)}">{html.escape(url)}</a>'
                for url in result.links
            )
            title = html.escape(result.title or result.url)
            body = html.escape(result.text_content)
            page_html = (
                f"<html><head><title>{title}</title></head>"
                f"<body>{body}{links_html}</body></html>"
            )
        return f"raw:{page_html}"

    async def _llm_select(self, result: ParseResult, context: URLContext) -> list[str]:
        """Use crawl4ai's LLMExtractionStrategy on pre-fetched HTML to rank links."""
        try:
            from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig
            from crawl4ai.extraction_strategy import LLMExtractionStrategy
        except ImportError:
            logger.warning("[crawler] crawl4ai not installed — falling back to heuristic selection")
            return self._heuristic_select(result)

        schema = {
            "type": "object",
            "properties": {
                "relevant_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Absolute URLs from the page most worth following for deeper content",
                }
            },
            "required": ["relevant_urls"],
        }

        strategy = LLMExtractionStrategy(
            llm_config=self._llm_config,
            schema=schema,
            instruction=(
                f"From the following page content (URL: {result.url}), "
                "identify the most relevant internal and external URLs to follow for deeper research. "
                f"Choose at most {self._max_links} URLs. "
                "Return only absolute URLs already present in the content."
            ),
        )

        # Feed pre-fetched HTML into crawl4ai — never pass a live http(s) URL here.
        config = CrawlerRunConfig(
            extraction_strategy=strategy,
            word_count_threshold=0,
            cache_mode=CacheMode.BYPASS,
            base_url=result.url,
        )

        try:
            async with AsyncWebCrawler() as crawler:
                crawl_result = await crawler.arun(
                    self._prefetched_crawl_input(result),
                    config=config,
                )

            import json as _json

            extracted = _json.loads(crawl_result.extracted_content or "{}")
            urls = []
            for extracted_object in extracted:
                urls.extend(extracted_object.get("relevant_urls", []))
            # Filter to only URLs present in the already-extracted link set
            known = set(result.links)
            return [u for u in urls if u in known][: self._max_links]
        except Exception as e:
            logger.warning("[crawler] LLM link selection failed (%s), using heuristics", e)
            return self._heuristic_select(result)

    # ------------------------------------------------------------------
    # Heuristic-based selection
    # ------------------------------------------------------------------

    def _heuristic_select(self, result: ParseResult) -> list[str]:
        """Filter and return up to ``max_links`` links using lightweight heuristics.

        Preference order:
        1. Document links (PDF, DOCX, etc.)
        2. Same-domain links
        3. Other external links (de-prioritised)
        """
        from urllib.parse import urlparse

        from .utils import looks_like_document_link

        base_domain = urlparse(result.url).netloc.lower()

        document_links: list[str] = []
        same_domain: list[str] = []
        other: list[str] = []

        for url in result.links:
            if looks_like_document_link(url):
                document_links.append(url)
            elif urlparse(url).netloc.lower() == base_domain:
                same_domain.append(url)
            else:
                other.append(url)

        ranked = document_links + same_domain + other
        return ranked[: self._max_links]
