"""URL fetcher layer — Fetcher ABC and ScraplingFetcher concrete implementation.

``ScraplingFetcher`` replaces the dual-fetch anti-pattern that existed between
``plan.build_scrape_plan`` (fetch #1 to classify) and the strategy modules
(fetch #2 to extract).  A single fetch is made and the Scrapling page object
is preserved in ``FetchResult.page`` so the Parser can re-use it for CSS
selector access without an additional round-trip.
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

from web_scout.config import ROUTING_HEURISTICS

from .constants import BINARY_CONTENT_TYPES, IMAGE_CONTENT_TYPES
from .context import URLContext
from .types import FetchResult
from .utils import (
    extract_text_from_html,
    is_blocked_domain,
    normalize_content_type,
    sniff_document_payload,
    unsupported_legacy_document_reason,
)

logger = logging.getLogger(__name__)

_BOT_STATUS_CODES = frozenset({403, 429, 503})


class Fetcher(ABC):
    """Abstract base class for URL fetchers.

    Implementations are responsible for retrieving a URL's content, handling
    bot-wall bypassing, redirect following, and distinguishing binary from
    text responses.  The returned ``FetchResult`` is consumed by a ``Parser``.
    """

    @abstractmethod
    async def fetch(self, url: str, context: URLContext) -> FetchResult:
        """Fetch *url* and return a ``FetchResult``.

        Must never raise — errors are encoded in ``FetchResult.error`` and/or
        a non-2xx ``FetchResult.status``.
        """


class ScraplingFetcher(Fetcher):
    """Fetcher using Scrapling: AsyncFetcher (fast HTTP) → StealthyFetcher (Cloudflare bypass).

    Fallback triggers:
    - HTTP 403 / 429 / 503 → bot-wall detected, retry with StealthyFetcher
    - Timeout or connection error → retry with StealthyFetcher
    - Thin HTML content (< ``html_fast_thin_content_chars`` text chars) → retry with browser

    ``allowed_domains`` enforces domain blocking before any network call is made.
    """

    def __init__(self, *, allowed_domains: Optional[frozenset] = None) -> None:
        self._allowed_domains = allowed_domains

    def _empty(self, url: str) -> FetchResult:
        return FetchResult(
            url=url,
            status=0,
            content_type="",
            content_disposition="",
            html_content=None,
            body=None,
            headers={},
            used_browser=False,
        )

    async def fetch(self, url: str, context: URLContext) -> FetchResult:
        from scrapling.fetchers import AsyncFetcher

        from ._scrapling import stealthy_fetch

        # Pre-fetch URL screening — return early without touching the network
        if is_blocked_domain(url, allowed_domains=self._allowed_domains):
            return self._empty(url).model_copy(update={"status": 403, "error": "blocked domain"})

        unsupported = unsupported_legacy_document_reason(url)
        if unsupported:
            return self._empty(url).model_copy(update={"status": 415, "error": unsupported})

        resp = None
        used_browser = False
        needs_browser_retry = False

        # Step 1: fast HTTP with TLS fingerprint spoofing
        try:
            resp = await AsyncFetcher.get(
                url,
                stealthy_headers=True,
                follow_redirects=True,
                timeout=ROUTING_HEURISTICS.validation_timeout,
            )
            if resp.status in _BOT_STATUS_CODES:
                needs_browser_retry = True
            elif resp is not None:
                # Thin-content heuristic: if the fast HTTP response has very few
                # visible text chars it is likely a SPA shell → use the browser.
                html = resp.html_content or ""
                if html and len(extract_text_from_html(html)) < ROUTING_HEURISTICS.html_fast_thin_content_chars:
                    needs_browser_retry = True
        except Exception as e:
            logger.debug("[fetcher] AsyncFetcher failed (%s), trying browser: %s", type(e).__name__, url)
            needs_browser_retry = True

        # Step 2: browser fallback (Cloudflare bypass)
        if needs_browser_retry:
            used_browser = True
            reason = f"HTTP {resp.status}" if resp is not None else "fetch error / thin content"
            logger.info("[fetcher] falling back to StealthyFetcher (%s) url=%s", reason, url)
            try:
                kwargs: dict = dict(
                    headless=True,
                    network_idle=True,
                    solve_cloudflare=True,
                    timeout=ROUTING_HEURISTICS.browser_page_timeout_ms,
                    wait=int(ROUTING_HEURISTICS.browser_delay_before_return_html_s * 1000),
                )
                if context.wait_for:
                    kwargs["wait_selector"] = context.wait_for
                resp = await stealthy_fetch(url, **kwargs)
            except Exception as e:
                exc_str = str(e)
                # Check for download signal (browser navigating to a file download)
                if "download" in exc_str.lower() or "file" in exc_str.lower():
                    # Return a minimal FetchResult that triggers parse_document
                    return self._empty(url).model_copy(update={
                        "used_browser": True,
                        "status": 200,
                        "error": "__DOWNLOAD_REDIRECT__",
                    })
                return self._empty(url).model_copy(update={
                    "used_browser": True,
                    "error": f"browser fetch failed: {type(e).__name__}: {exc_str}",
                })

        if resp is None:
            return self._empty(url).model_copy(update={"error": "fetch returned no response"})

        ct = normalize_content_type(resp.headers.get("content-type", ""))
        cd = resp.headers.get("content-disposition", "")

        raw_body = getattr(resp, "body", None)
        if isinstance(raw_body, str):
            raw_body = raw_body.encode()
        elif raw_body is not None and not isinstance(raw_body, bytes):
            try:
                raw_body = bytes(raw_body)
            except Exception:
                raw_body = None

        # Classify as binary or text
        is_binary = (
            any(ct.startswith(t) for t in BINARY_CONTENT_TYPES + IMAGE_CONTENT_TYPES)
            or (raw_body and sniff_document_payload(raw_body, content_type=ct, content_disposition=cd))
        )

        html_content = (resp.html_content or None) if not is_binary else None
        body = raw_body if is_binary else None

        headers: dict[str, str] = {}
        if hasattr(resp, "headers") and resp.headers:
            try:
                headers = dict(resp.headers)
            except Exception:
                pass

        return FetchResult(
            url=url,
            status=resp.status,
            content_type=ct,
            content_disposition=cd,
            html_content=html_content,
            body=body,
            headers=headers,
            used_browser=used_browser,
            page=resp,
        )
