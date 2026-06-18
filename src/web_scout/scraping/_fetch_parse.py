"""Single-URL fetch-and-parse helper for direct callers outside the Orchestrator."""

from typing import Optional

from web_scout.config import ROUTING_HEURISTICS

from ._fetcher import Fetcher, ScraplingFetcher
from ._parser import DefaultParser, Parser
from .context import URLContext
from .types import FetchResult, ParseResult


async def fetch_and_parse_url(
    url: str,
    *,
    wait_for: Optional[str] = None,
    allowed_domains: Optional[frozenset[str]] = None,
    vision_model: Optional[str] = None,
    max_pdf_pages: int = ROUTING_HEURISTICS.pdf_max_pages_default,
    fetcher: Optional[Fetcher] = None,
    parser: Optional[Parser] = None,
) -> tuple[FetchResult, ParseResult]:
    """Fetch *url* once and parse the response into a ``ParseResult``.

    Convenience wrapper around ``ScraplingFetcher`` + ``DefaultParser.dispatch``
    for single-URL use cases (tools, session cache, tests).

    Returns both the raw ``FetchResult`` and the parsed ``ParseResult`` so
    callers can inspect fetch-level metadata (status, content-type) before
    applying their own error handling.
    """
    fetcher = fetcher or ScraplingFetcher(allowed_domains=allowed_domains)
    parser = parser or DefaultParser(vision_model=vision_model, max_pdf_pages=max_pdf_pages)
    context = URLContext(url=url, depth=0, wait_for=wait_for)
    fetch_result = await fetcher.fetch(url, context)
    parse_result = await parser.dispatch(fetch_result, context)
    return fetch_result, parse_result
