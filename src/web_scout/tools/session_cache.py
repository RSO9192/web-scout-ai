"""Session-level source artifact cache.

Stores and deduplicates query-agnostic page fetches within a single Python
process so that multiple concurrent scrape calls to the same URL share one
network round-trip.
"""

import asyncio
import time
from typing import Optional

from .tracker import ResearchTracker
from .types import CachedSourceArtifact, SourceCacheKey

_SESSION_SOURCE_CACHE: dict[SourceCacheKey, CachedSourceArtifact] = {}
_SESSION_SOURCE_IN_FLIGHT: dict = {}
_CACHE_TIMES = {}
CACHE_MAX_BYTES = 32 * 1024 * 1024
CACHE_MAX_ITEMS = 128
CACHE_TTL_SECONDS = 300


def _artifact_bytes(value):
    layout = value.layout
    layout_bytes = 0
    if layout is not None:
        layout_bytes = len(layout.document_title.encode()) + 128 * len(layout.pages)
        layout_bytes += sum(
            256 + len(section.title.encode()) + sum(len(title.encode()) for title in section.heading_path)
            for section in layout.sections
        )
    return len(value.text_content.encode()) + len(value.binary_bytes) + len(value.title.encode()) + layout_bytes + 512


def _trim_cache():
    now = time.monotonic()
    for key in list(_CACHE_TIMES):
        if key not in _SESSION_SOURCE_CACHE or now - _CACHE_TIMES[key] > CACHE_TTL_SECONDS:
            _SESSION_SOURCE_CACHE.pop(key, None)
            _CACHE_TIMES.pop(key, None)
    size = sum(_artifact_bytes(value) for value in _SESSION_SOURCE_CACHE.values())
    while _SESSION_SOURCE_CACHE and (size > CACHE_MAX_BYTES or len(_SESSION_SOURCE_CACHE) > CACHE_MAX_ITEMS):
        key = next(iter(_SESSION_SOURCE_CACHE))
        size -= _artifact_bytes(_SESSION_SOURCE_CACHE.pop(key))
        _CACHE_TIMES.pop(key, None)


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
        layout=getattr(artifact, "layout", None),
    )


async def get_or_fetch_session_source_artifact(
    *,
    url: str,
    wait_for: Optional[str],
    vision_model: Optional[str],
    exclude_domains: Optional[frozenset],
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
    key = (key, max_pdf_pages, vision_model or "", tuple(sorted(exclude_domains or [])))
    _trim_cache()
    cached = _SESSION_SOURCE_CACHE.get(key)
    if cached is not None:
        return cached, None

    flight_key = (asyncio.get_running_loop(), key)
    existing = _SESSION_SOURCE_IN_FLIGHT.get(flight_key)
    if existing is not None:
        try:
            return await asyncio.shield(existing), None
        except Exception as exc:
            return None, str(exc)

    future: asyncio.Future[CachedSourceArtifact] = asyncio.get_running_loop().create_future()
    _SESSION_SOURCE_IN_FLIGHT[flight_key] = future
    try:
        fetch_result, parse_result = await fetch_and_parse_url(
            url,
            wait_for=wait_for,
            exclude_domains=exclude_domains,
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
        _CACHE_TIMES[key] = time.monotonic()
        _trim_cache()
        future.set_result(cached)
        return cached, None
    except Exception as exc:
        future.set_exception(exc)
        future.exception()
        raise
    finally:
        if not future.done():
            future.cancel()
        _SESSION_SOURCE_IN_FLIGHT.pop(flight_key, None)
