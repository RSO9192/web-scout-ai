"""JSON endpoint scraping strategy (private).

Single entry point: ``scrape_json``.

Fetches a JSON API endpoint and returns an annotated markdown representation
as a text ``SourceArtifact``.  Image download lives in ``_image.py``.
"""

import json
import logging
from typing import Optional, Tuple

import httpx

from web_scout.config import ROUTING_HEURISTICS

from .constants import FETCH_HEADERS
from .types import SourceArtifact
from .utils import trim_json_value

logger = logging.getLogger(__name__)


async def scrape_json(url: str) -> Tuple[SourceArtifact, Optional[str]]:
    """Fetch a JSON endpoint and return a trimmed, annotated markdown representation."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=ROUTING_HEURISTICS.image_json_timeout_s,
            headers=FETCH_HEADERS,
        ) as client:
            resp = await client.get(url)
        resp.raise_for_status()

        try:
            data = resp.json()
        except Exception:
            data = json.loads(resp.text)

        trimmed = trim_json_value(data)
        if isinstance(data, dict):
            summary = f"Top-level object with {len(data)} keys."
            extra = "Keys: " + ", ".join(map(str, list(data.keys())[:20]))
        elif isinstance(data, list):
            summary = f"Top-level array with {len(data)} items."
            extra = ""
        else:
            summary = f"Top-level scalar of type {type(data).__name__}."
            extra = ""

        body = f"JSON extracted from {url}\n\n{summary}"
        if extra:
            body += f"\n{extra}"
        body += "\n\n```json\n" + json.dumps(trimmed, ensure_ascii=False, indent=2) + "\n```"

        title = url.rsplit("/", 1)[-1] or "JSON endpoint"
        return SourceArtifact(kind="text", title=title, text_content=body), None

    except Exception as exc:
        return SourceArtifact(kind="text", title=""), f"JSON extraction failed: {exc}"
