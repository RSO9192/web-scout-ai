"""Image download strategy (private).

Single entry point: ``scrape_image``.

Downloads raw image bytes for session caching; vision extraction is
performed later by ``executor.materialize_source_artifact``.
"""

import logging
import mimetypes
from typing import Optional, Tuple

import httpx

from web_scout.config import ROUTING_HEURISTICS

from .constants import FETCH_HEADERS
from .types import SourceArtifact
from .utils import normalize_content_type

logger = logging.getLogger(__name__)


async def scrape_image(url: str) -> Tuple[SourceArtifact, Optional[str]]:
    """Download raw image bytes for session caching and later vision extraction."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=ROUTING_HEURISTICS.image_json_timeout_s,
            headers=FETCH_HEADERS,
        ) as client:
            resp = await client.get(url)
        resp.raise_for_status()

        mime_type = (
            normalize_content_type(resp.headers.get("content-type", "")) or mimetypes.guess_type(url)[0] or "image/png"
        )
        title = url.rsplit("/", 1)[-1] or "Image"
        return SourceArtifact(kind="binary", title=title, binary_bytes=resp.content, mime_type=mime_type), None

    except Exception as exc:
        return SourceArtifact(kind="text", title=""), f"Image download failed: {exc}"
