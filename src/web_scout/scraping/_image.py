"""Image download strategy (private).

Single entry point: ``scrape_image``.

Downloads raw image bytes for session caching; vision extraction is
performed later by ``executor.materialize_source_artifact``.
"""

import logging
import mimetypes
from typing import Optional, Tuple

from web_scout.config import ROUTING_HEURISTICS

from ._scrapling import stealthy_fetch
from .types import SourceArtifact
from .utils import normalize_content_type

logger = logging.getLogger(__name__)


async def scrape_image(url: str, *, needs_browser: bool = False) -> Tuple[SourceArtifact, Optional[str]]:
    """Download raw image bytes for session caching and later vision extraction."""
    try:
        if needs_browser:
            resp = await stealthy_fetch(
                url,
                headless=True,
                network_idle=True,
                solve_cloudflare=True,
                timeout=ROUTING_HEURISTICS.browser_page_timeout_ms,
            )
        else:
            from scrapling.fetchers import AsyncFetcher

            resp = await AsyncFetcher.get(
                url,
                stealthy_headers=True,
                follow_redirects=True,
                timeout=ROUTING_HEURISTICS.image_json_timeout_s,
            )

        if resp.status >= 400:
            raise ValueError(f"HTTP {resp.status}")

        mime_type = (
            normalize_content_type(resp.headers.get("content-type", "")) or mimetypes.guess_type(url)[0] or "image/png"
        )
        title = url.rsplit("/", 1)[-1] or "Image"
        return SourceArtifact(kind="binary", title=title, binary_bytes=resp.body, mime_type=mime_type), None

    except Exception as exc:
        return SourceArtifact(kind="text", title=""), f"Image download failed: {exc}"
