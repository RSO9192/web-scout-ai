"""Session-level source artifact cache.

Stores and deduplicate query-agnostic page fetches within a single Python
process so that multiple concurrent scrape calls to the same URL share one
network round-trip.
"""

import asyncio
from typing import TYPE_CHECKING, Optional

from .tracker import ResearchTracker
from .types import CachedSourceArtifact, SourceCacheKey

if TYPE_CHECKING:
    from web_scout.scraping.types import SourceArtifact

_SESSION_SOURCE_CACHE: dict[SourceCacheKey, CachedSourceArtifact] = {}
_SESSION_SOURCE_IN_FLIGHT: dict[SourceCacheKey, asyncio.Future[CachedSourceArtifact]] = {}


def make_source_cache_key(
    *,
    url: str,
    strategy: object,
    wait_for: Optional[str],
    max_pdf_pages: int,
    cache_pdf_pages: bool = False,
) -> SourceCacheKey:
    """Build a cache key that only includes source-shaping parameters."""
    strategy_name = getattr(strategy, "value", str(strategy))
    return SourceCacheKey(
        url=ResearchTracker.normalize_url(url),
        wait_for=(wait_for or "") if strategy_name in {"SCRAPE_HTML", "SCRAPE_JS"} else "",
        max_pdf_pages=max_pdf_pages if cache_pdf_pages else 0,
    )


def cacheable_from_source_artifact(url: str, artifact: "SourceArtifact") -> CachedSourceArtifact:
    """Convert a scraping-layer artifact into the session-cache representation."""
    return CachedSourceArtifact(
        url=url,
        title=artifact.title,
        artifact_kind=artifact.kind,
        text_content=artifact.text_content,
        binary_bytes=artifact.binary_bytes,
        mime_type=artifact.mime_type,
    )


async def get_or_fetch_session_source_artifact(
    *,
    url: str,
    strategy: object,
    wait_for: Optional[str],
    vision_model: Optional[str],
    allowed_domains: Optional[frozenset],
    max_pdf_pages: int,
    cache_pdf_pages: bool = False,
) -> tuple[Optional[CachedSourceArtifact], Optional[str]]:
    """Load or fetch a query-agnostic source artifact for this Python process."""
    from web_scout.scraping import executor as scraping_executor

    key = make_source_cache_key(
        url=url,
        strategy=strategy,
        wait_for=wait_for,
        max_pdf_pages=max_pdf_pages,
        cache_pdf_pages=cache_pdf_pages,
    )
    cached = _SESSION_SOURCE_CACHE.get(key)
    if cached is not None:
        return cached, None

    existing = _SESSION_SOURCE_IN_FLIGHT.get(key)
    if existing is not None:
        try:
            return await asyncio.shield(existing), None
        except Exception as exc:
            return None, str(exc)

    future: asyncio.Future[CachedSourceArtifact] = asyncio.get_running_loop().create_future()
    _SESSION_SOURCE_IN_FLIGHT[key] = future
    try:
        artifact, error, _ = await scraping_executor.fetch_query_agnostic_source_artifact(
            url,
            wait_for=wait_for,
            vision_model=vision_model,
            allowed_domains=allowed_domains,
            max_pdf_pages=max_pdf_pages,
        )
        if error or artifact is None:
            if error is None:
                error = "Extraction returned empty content"
            future.set_exception(RuntimeError(error))
            future.exception()
            return None, error
        cached = cacheable_from_source_artifact(url, artifact)
        _SESSION_SOURCE_CACHE[key] = cached
        future.set_result(cached)
        return cached, None
    finally:
        _SESSION_SOURCE_IN_FLIGHT.pop(key, None)
