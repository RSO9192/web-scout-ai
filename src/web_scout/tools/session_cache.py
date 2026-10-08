"""URL source cache.

Successful page and document fetches are stored with Prefect. The same URL is
not fetched twice at the same time, and a later process reuses the stored
artifact. Failures are not stored.
"""

from typing import Optional

from prefect import task

from .tracker import ResearchTracker
from .types import CachedSourceArtifact, SourceCacheKey


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
    """Convert a ``ParseResult`` into the cached representation."""
    artifact = getattr(parse_result, "artifact", None)
    title = getattr(parse_result, "title", "") or ""
    raw_html = getattr(parse_result, "raw_html", None) or ""
    if artifact is None:
        return CachedSourceArtifact(url=url, title=title, artifact_kind="text", raw_html=raw_html)
    return CachedSourceArtifact(
        url=url,
        title=title,
        artifact_kind=artifact.kind,
        text_content=artifact.text_content,
        binary_bytes=artifact.binary_bytes,
        mime_type=artifact.mime_type,
        layout=getattr(artifact, "layout", None),
        raw_html=raw_html,
    )


@task(name="web-scout-url-fetch")
async def _url_fetch_task(
    url: str,
    wait_for_selector: str,
    max_pdf_pages: int,
    vision_model: str,
    exclude_domains: tuple[str, ...],
    fetch_max_pdf_pages: int,
) -> CachedSourceArtifact:
    """Fetch one URL. ``fetch_max_pdf_pages`` is excluded from the cache key."""
    from web_scout.scraping import fetch_and_parse_url

    fetch_result, parse_result = await fetch_and_parse_url(
        url,
        wait_for=wait_for_selector or None,
        exclude_domains=frozenset(exclude_domains) or None,
        vision_model=vision_model or None,
        max_pdf_pages=fetch_max_pdf_pages,
    )
    if fetch_result.error and fetch_result.error != "__DOWNLOAD_REDIRECT__":
        raise RuntimeError(fetch_result.error)
    if parse_result.error:
        raise RuntimeError(parse_result.error)
    if not parse_result.text_content.strip() and parse_result.artifact.kind == "text":
        raise RuntimeError("Extraction returned empty content")
    return cacheable_from_parse_result(url, parse_result)


async def get_or_fetch_session_source_artifact(
    *,
    url: str,
    wait_for: Optional[str],
    vision_model: Optional[str],
    exclude_domains: Optional[frozenset],
    max_pdf_pages: int,
    cache_pdf_pages: bool = False,
) -> tuple[Optional[CachedSourceArtifact], Optional[str]]:
    """Load or fetch a query-agnostic source artifact."""
    from web_scout.result_cache import call_cached_task, refresh_url_cache_enabled

    key = make_source_cache_key(
        url=url,
        wait_for=wait_for,
        max_pdf_pages=max_pdf_pages,
        cache_pdf_pages=cache_pdf_pages,
    )
    try:
        cached = await call_cached_task(
            _url_fetch_task,
            kind="url",
            refresh=refresh_url_cache_enabled(),
            exclude=("fetch_max_pdf_pages",),
            url=key.url,
            wait_for_selector=key.wait_for,
            max_pdf_pages=key.max_pdf_pages,
            vision_model=vision_model or "",
            exclude_domains=tuple(sorted(exclude_domains or ())),
            fetch_max_pdf_pages=max_pdf_pages,
        )
    except Exception as exc:
        return None, str(exc)
    return cached, None
