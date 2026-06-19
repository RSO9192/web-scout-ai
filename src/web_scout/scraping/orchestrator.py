"""Orchestrator — mediator that coordinates Fetcher → Parser → Crawler.

The ``Orchestrator`` holds three asyncio queues:

1. ``_fetch_queue``  — ``URLContext`` objects waiting to be fetched.
2. ``_parse_queue``  — ``(URLContext, FetchResult)`` pairs waiting to be parsed.
3. ``_crawl_queue``  — ``(URLContext, ParseResult)`` pairs waiting to be crawled.

Worker coroutines drain each queue concurrently.  When the Crawler decides to
follow a link it calls ``Orchestrator.queue_url`` — the public entry point —
directly, which increments the pending-work counter before enqueuing.

The ``run()`` method blocks until all queues are drained (pending counter
reaches zero) and then returns the list of collected ``ParseResult`` objects.
"""

import asyncio
import logging
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

from web_scout.config import ROUTING_HEURISTICS

from ._crawler import Crawl4AICrawler, Crawler
from ._fetcher import Fetcher, ScraplingFetcher
from ._parser import DefaultParser, Parser
from .context import URLContext
from .types import ParseResult
from .utils import is_blocked_domain, unsupported_legacy_document_reason

logger = logging.getLogger(__name__)


class OrchestratorConfig(BaseSettings):
    """Configuration for the ``Orchestrator``.

    All fields can be overridden via environment variables prefixed with
    ``ORCHESTRATOR_`` (e.g. ``ORCHESTRATOR_MAX_DEPTH=5``).

    Defaults for low-level timeouts and thresholds are sourced from
    ``ROUTING_HEURISTICS`` in ``web_scout.config``.
    """

    model_config = SettingsConfigDict(env_prefix="ORCHESTRATOR_")

    max_depth: int = 3
    max_urls: int = 50
    max_concurrent_fetches: int = 5
    max_concurrent_parses: int = 10
    allowed_domains: Optional[frozenset[str]] = None
    vision_model: Optional[str] = None
    # Defaults sourced from ROUTING_HEURISTICS in config.py
    max_pdf_pages: int = ROUTING_HEURISTICS.pdf_max_pages_default        # 50
    fetch_timeout: float = ROUTING_HEURISTICS.validation_timeout         # 20.0 s
    browser_timeout_ms: int = ROUTING_HEURISTICS.browser_page_timeout_ms # 60_000 ms


class Orchestrator:
    """Mediator that coordinates Fetcher, Parser, and Crawler into a pipeline.

    Usage::

        config = OrchestratorConfig(max_depth=2, max_urls=20)
        orch = Orchestrator(config)
        await orch.queue_url("https://example.com")
        results = await orch.run()

    Custom implementations can be injected::

        orch = Orchestrator(config, fetcher=MyFetcher(), parser=MyParser(), crawler=MyCrawler())

    ``queue_url`` is also the callback passed to ``Crawler.crawl``, so
    the Crawler can queue new URLs directly into the Orchestrator.
    """

    def __init__(
        self,
        config: OrchestratorConfig,
        *,
        fetcher: Optional[Fetcher] = None,
        parser: Optional[Parser] = None,
        crawler: Optional[Crawler] = None,
    ) -> None:
        self.config = config
        self._fetcher: Fetcher = fetcher or ScraplingFetcher(allowed_domains=config.allowed_domains)
        self._parser: Parser = parser or DefaultParser(
            vision_model=config.vision_model,
            max_pdf_pages=config.max_pdf_pages,
        )
        self._crawler: Crawler = crawler or Crawl4AICrawler()

        self._fetch_queue: asyncio.Queue[URLContext] = asyncio.Queue()
        self._parse_queue: asyncio.Queue[tuple[URLContext, object]] = asyncio.Queue()
        self._crawl_queue: asyncio.Queue[tuple[URLContext, ParseResult]] = asyncio.Queue()

        self._visited: set[str] = set()
        self._results: list[ParseResult] = []

        # Pending-work counter — incremented when a URL is queued, decremented
        # at the terminal stage of each URL's pipeline.
        self._pending: int = 0
        self._done_event: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def queue_url(
        self,
        url: str,
        *,
        depth: int = 0,
        parent_url: Optional[str] = None,
        wait_for: Optional[str] = None,
    ) -> None:
        """Queue *url* for processing.

        This is the primary public entry point and also the callback handed to
        ``Crawler.crawl`` so the Crawler can queue follow-up URLs directly.

        The call is silently ignored when:
        - *url* was already visited
        - the URL cap (``config.max_urls``) has been reached
        - *url* belongs to a blocked domain
        - *url* is an unsupported legacy document format
        """
        if url in self._visited:
            return
        if len(self._visited) >= self.config.max_urls:
            return
        if is_blocked_domain(url, allowed_domains=self.config.allowed_domains):
            return
        if unsupported_legacy_document_reason(url):
            return

        self._visited.add(url)
        self._pending += 1

        context = URLContext(url=url, depth=depth, parent_url=parent_url, wait_for=wait_for)
        await self._fetch_queue.put(context)

    async def run(self) -> list[ParseResult]:
        """Process all queued URLs until the queues drain or limits are hit.

        Starts worker coroutines, waits for all pending work to complete, then
        cancels the workers and returns the collected ``ParseResult`` list.
        """
        if self._pending == 0:
            return []

        fetch_sem = asyncio.Semaphore(self.config.max_concurrent_fetches)
        parse_sem = asyncio.Semaphore(self.config.max_concurrent_parses)

        workers = [
            asyncio.create_task(self._fetch_worker(fetch_sem))
            for _ in range(self.config.max_concurrent_fetches)
        ] + [
            asyncio.create_task(self._parse_worker(parse_sem))
            for _ in range(self.config.max_concurrent_parses)
        ] + [
            asyncio.create_task(self._crawl_worker())
        ]

        await self._done_event.wait()

        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

        return list(self._results)

    # ------------------------------------------------------------------
    # Internal workers
    # ------------------------------------------------------------------

    async def _fetch_worker(self, sem: asyncio.Semaphore) -> None:
        while True:
            context = await self._fetch_queue.get()
            try:
                if context.is_stopped:
                    self._complete()
                    continue

                async with sem:
                    try:
                        fetch_result = await self._fetcher.fetch(context.url, context)
                    except Exception as exc:
                        logger.error("[orchestrator] fetch error for %s: %s", context.url, exc)
                        self._complete()
                        continue

                await self._parse_queue.put((context, fetch_result))
            finally:
                self._fetch_queue.task_done()

    async def _parse_worker(self, sem: asyncio.Semaphore) -> None:
        while True:
            context, fetch_result = await self._parse_queue.get()
            try:
                if context.is_stopped:
                    self._complete()
                    continue

                async with sem:
                    try:
                        parse_result = await self._parser.dispatch(fetch_result, context)
                    except Exception as exc:
                        logger.error("[orchestrator] parse error for %s: %s", context.url, exc)
                        self._complete()
                        continue

                self._results.append(parse_result)

                if context.depth >= self.config.max_depth:
                    # Terminal: depth limit reached — do not crawl
                    self._complete()
                else:
                    await self._crawl_queue.put((context, parse_result))
            finally:
                self._parse_queue.task_done()

    async def _crawl_worker(self) -> None:
        while True:
            context, parse_result = await self._crawl_queue.get()
            try:
                if context.is_stopped:
                    self._complete()
                    continue

                async def _queue_child(url: str) -> None:
                    await self.queue_url(
                        url,
                        depth=context.depth + 1,
                        parent_url=context.url,
                    )

                try:
                    await self._crawler.crawl(parse_result, context, _queue_child)
                except Exception as exc:
                    logger.error("[orchestrator] crawl error for %s: %s", context.url, exc)

                self._complete()
            finally:
                self._crawl_queue.task_done()

    def _complete(self) -> None:
        """Decrement the pending counter and signal completion when it reaches zero."""
        self._pending -= 1
        if self._pending <= 0:
            self._pending = 0
            self._done_event.set()
