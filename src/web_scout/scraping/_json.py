"""JSON endpoint scraping strategy (private).

Single entry point: ``scrape_json``.

Fetches a JSON API endpoint and returns an annotated markdown representation
as a text ``SourceArtifact``.  Image download lives in ``_image.py``.
"""

import json
import logging
from typing import Optional, Tuple

from web_scout.config import ROUTING_HEURISTICS

from ._scrapling import stealthy_fetch
from .types import SourceArtifact
from .utils import trim_json_value

logger = logging.getLogger(__name__)


async def scrape_json(url: str, *, needs_browser: bool = False) -> Tuple[SourceArtifact, Optional[str]]:
    """Fetch a JSON endpoint and return a trimmed, annotated markdown representation."""
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

        try:
            data = resp.json()
        except Exception:
            data = json.loads(resp.html_content)

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
