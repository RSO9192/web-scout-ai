"""Session-level source artifact cache.

Stores and deduplicates query-agnostic page fetches within a single Python
process so that multiple concurrent scrape calls to the same URL share one
network round-trip.
"""

import asyncio
from typing import Optional

from .tracker import ResearchTracker
from .types import CachedSourceArtifact, SourceCacheKey

_SESSION_SOURCE_CACHE: dict[SourceCacheKey, CachedSourceArtifact] = {}
_SESSION_SOURCE_IN_FLIGHT: dict[SourceCacheKey, asyncio.Future[CachedSourceArtifact]] = {}


def make_source_cache_key(
    *,
    url: str,
    wait_for: Optional[str],
    max_pdf_pages: int,
    cache_pdf_pages: bool = False,
) -> SourceCacheKey:
    """Build a cache key that only includes source-shaping parameters."""
    return SourceCacheKey(
        url=ResearchTracker.normalize_url(url),
        wait_for=wait_for or "",
        max_pdf_pages=max_pdf_pages if cache_pdf_pages else 0,
    )


def cacheable_from_parse_result(url: str, parse_result: object) -> CachedSourceArtifact:
    """Convert a ``ParseResult`` into the session-cache representation."""
    artifact = getattr(parse_result, "artifact", None)
    title = getattr(parse_result, "title", "") or ""
    if artifact is None:
        return CachedSourceArtifact(url=url, title=title, artifact_kind="text")
    return CachedSourceArtifact(
        url=url,
        title=title,
        artifact_kind=artifact.kind,
        text_content=artifact.text_content,
        binary_bytes=artifact.binary_bytes,
        mime_type=artifact.mime_type,
    )


async def get_or_fetch_session_source_artifact(
    *,
    url: str,
    wait_for: Optional[str],
    vision_model: Optional[str],
    allowed_domains: Optional[frozenset],
    max_pdf_pages: int,
    cache_pdf_pages: bool = False,
) -> tuple[Optional[CachedSourceArtifact], Optional[str]]:
    """Load or fetch a query-agnostic source artifact for this Python process."""
    from web_scout.scraping import fetch_and_parse_url

    key = make_source_cache_key(
        url=url,
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
        fetch_result, parse_result = await fetch_and_parse_url(
            url,
            wait_for=wait_for,
            allowed_domains=allowed_domains,
            vision_model=vision_model,
            max_pdf_pages=max_pdf_pages,
        )
        if fetch_result.error and fetch_result.error != "__DOWNLOAD_REDIRECT__":
            error = fetch_result.error
            future.set_exception(RuntimeError(error))
            future.exception()
            return None, error

        if parse_result.error:
            error = parse_result.error
            future.set_exception(RuntimeError(error))
            future.exception()
            return None, error

        if not parse_result.text_content.strip() and parse_result.artifact.kind == "text":
            error = "Extraction returned empty content"
            future.set_exception(RuntimeError(error))
            future.exception()
            return None, error

        cached = cacheable_from_parse_result(url, parse_result)
        _SESSION_SOURCE_CACHE[key] = cached
        future.set_result(cached)
        return cached, None
    finally:
        _SESSION_SOURCE_IN_FLIGHT.pop(key, None)
