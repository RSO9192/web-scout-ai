"""Select follow-up links from parsed content; fetching stays with Scrapling."""

from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Optional

from web_scout._classification import backend
from web_scout._pipeline_types import DEFAULT_WEB_RESEARCH_MODELS

from .context import URLContext
from .types import ParseResult

_USE_DEFAULT_LLM = object()


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


class DefaultCrawler(Crawler):
    """Use the shared Jev/GPT follow-up selectors on already-parsed content.

    WEB_SCOUT_CRAWLER_BACKEND selects jev (default) or gpt; DISABLE_JEV=true
    forces GPT. Omitted llm_config uses these environment settings.
    Explicit None keeps heuristic-only selection. Legacy config objects with
    provider, api_token and base_url fields keep their custom GPT connection.
    """

    def __init__(self, *, llm_config: Optional[object] = _USE_DEFAULT_LLM, max_links: int = 10) -> None:
        self._llm_config = llm_config
        self._max_links = max_links

    async def crawl(
        self, result: ParseResult, context: URLContext, queue_url: Callable[[str], Awaitable[None]]
    ) -> None:
        if not result.links:
            return
        for url in await self._select_links(result, context):
            await queue_url(url)

    async def _select_links(self, result: ParseResult, context: URLContext) -> list[str]:
        if self._llm_config is None:
            return self._heuristic_select(result)
        if self._llm_config is _USE_DEFAULT_LLM and backend("crawler") == "jev":
            from web_scout._pipeline_flow import _select_followup_urls_with_jev

            return await _select_followup_urls_with_jev(
                query=result.title or result.url,
                parent_url=result.url,
                parent_content=result.text_content,
                shortlist=result.links,
                cap=self._max_links,
            )
        return await self._llm_select(result, context)

    async def _llm_select(self, result: ParseResult, context: URLContext) -> list[str]:
        from web_scout._pipeline_flow import _select_followup_urls_with_luna
        from web_scout.utils import get_model

        config = self._llm_config
        if config is _USE_DEFAULT_LLM:
            model = get_model(DEFAULT_WEB_RESEARCH_MODELS["followup_selector"])
        else:
            from agents.extensions.models.litellm_model import LitellmModel

            from web_scout.utils import _prepare_bedrock_mantle_openai

            provider = getattr(config, "provider", None)
            if not isinstance(provider, str) or not provider:
                raise TypeError("llm_config must provide a model name in its provider field, or be None")
            _prepare_bedrock_mantle_openai(provider)
            model = LitellmModel(
                model=provider, api_key=getattr(config, "api_token", None), base_url=getattr(config, "base_url", None)
            )
        return await _select_followup_urls_with_luna(
            query=result.title or result.url,
            parent_url=result.url,
            parent_content=result.text_content,
            shortlist=result.links,
            cap=self._max_links,
            model=model,
        )

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


# Preserve the public class name for callers; no third-party implementation remains.
Crawl4AICrawler = DefaultCrawler
