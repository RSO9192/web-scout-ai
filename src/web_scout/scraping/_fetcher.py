"""URL fetcher layer — Fetcher ABC and ScraplingFetcher concrete implementation.

``ScraplingFetcher`` replaces the dual-fetch anti-pattern that existed between
``plan.build_scrape_plan`` (fetch #1 to classify) and the strategy modules
(fetch #2 to extract).  A single fetch is made and the Scrapling page object
is preserved in ``FetchResult.page`` so the Parser can re-use it for CSS
selector access without an additional round-trip.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Optional
from urllib.parse import urlparse

from web_scout.config import ROUTING_HEURISTICS

from . import _capture
from ._capture import NetworkCapture, download_sources
from ._data_sources import (
    _MAX_SOURCES_PER_PAGE,
    DashboardAssessment,
    assess_dashboard,
    cap_sources,
    inline_json_sources,
)
from .constants import BINARY_CONTENT_TYPES, IMAGE_CONTENT_TYPES, PDF_MAGIC_BYTES
from .context import URLContext
from .page_classifier import looks_like_pdf_resource
from .types import FetchResult
from .utils import (
    extract_text_from_html,
    invalid_http_url_reason,
    is_blocked_domain,
    is_network_error,
    normalize_content_type,
    resolve_document_download_url,
    resolve_primary_pdf_url,
    sniff_document_payload,
    unsupported_legacy_document_reason,
)

logger = logging.getLogger(__name__)
_BROWSER_HOSTS = {}


class _KnownBrowserHost(Exception):
    pass


_DOWNLOAD_SIGNAL = "__DOWNLOAD_REDIRECT__"


def _as_bytes(value: object) -> bytes | None:
    """Coerce a Scrapling response body to ``bytes``."""
    if value is None or isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode()
    try:
        return bytes(value)  # type: ignore[arg-type]
    except Exception:
        return None


def _is_download_navigation_error(value: object) -> bool:
    """Return True only for Playwright's explicit download navigation signal."""
    return "download is starting" in str(value).lower()


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

    ``exclude_domains`` enforces domain blocking before any network call is made.
    """

    def __init__(self, *, exclude_domains: Optional[frozenset] = None) -> None:
        self._exclude_domains = exclude_domains

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
        started = time.perf_counter()
        try:
            # _fetch may chain probes and a PDF download, each with its own deadline.
            async with asyncio.timeout(
                ROUTING_HEURISTICS.fetch_deadline_s + ROUTING_HEURISTICS.pdf_download_deadline_s
            ):
                return await self._fetch(url, context)
        except TimeoutError:
            return self._empty(url).model_copy(update={"error": "source_http_error: fetch deadline exceeded"})
        finally:
            logger.debug("[fetch-timing] seconds=%.3f url=%s", time.perf_counter() - started, url)

    async def _fetch(self, url: str, context: URLContext) -> FetchResult:
        from ._resources import http_get
        from ._scrapling import stealthy_fetch

        # Pre-fetch URL screening — return early without touching the network
        invalid_reason = invalid_http_url_reason(url)
        if invalid_reason:
            return self._empty(url).model_copy(update={"error": f"invalid URL: {invalid_reason}"})

        if is_blocked_domain(url, exclude_domains=self._exclude_domains):
            return self._empty(url).model_copy(update={"status": 403, "error": "blocked domain"})

        unsupported = unsupported_legacy_document_reason(url)
        if unsupported:
            return self._empty(url).model_copy(update={"status": 415, "error": unsupported})

        # Direct PDF URLs belong to the binary download chain. Browser page
        # navigation is both slower and unreliable for attachment responses.
        # Also rewrite DSpace /bitstreams/{uuid}/download frontend routes.
        resolved_document_url = resolve_document_download_url(url)
        if looks_like_pdf_resource(url) or resolved_document_url != url:
            from ._download import download_pdf

            pdf_url = resolved_document_url
            pdf_bytes, error = await download_pdf(pdf_url)
            if error or not pdf_bytes:
                return self._empty(url).model_copy(update={"error": error or "PDF download returned empty bytes"})
            return FetchResult(
                url=pdf_url,
                status=200,
                content_type="application/pdf",
                content_disposition="",
                html_content=None,
                body=pdf_bytes,
                headers={"content-type": "application/pdf"},
                used_browser=False,
            )

        resp = None
        used_browser = False
        needs_browser_retry = False
        fast_assessment = DashboardAssessment("", ())
        # Fast response kept when only the dashboard verdict sent us to the browser, so a browser
        # failure still returns the page.
        dashboard_fallback = None
        capture: NetworkCapture | None = None
        dashboard: dict = {}

        host = urlparse(url).netloc.lower()
        cached_browser = _BROWSER_HOSTS.get(host, 0) > time.monotonic()
        # Step 1: fast HTTP with TLS fingerprint spoofing
        try:
            if cached_browser:
                raise _KnownBrowserHost()
            resp = await http_get(
                url,
                stealthy_headers=True,
                follow_redirects=True,
                timeout=ROUTING_HEURISTICS.validation_timeout,
            )
            if resp.status == 429:
                return self._empty(url).model_copy(
                    update={"status": 429, "headers": dict(resp.headers), "error": "source_http_error: rate limited"}
                )
            if resp.status in {403, 503}:
                html = resp.html_content or ""
                needs_browser_retry = any(
                    token in html.lower()
                    for token in ("cloudflare", "captcha", "challenge", "javascript", "just a moment")
                )
            elif resp is not None:
                # Thin-content heuristic: if the fast HTTP response has very few
                # visible text chars it is likely a SPA shell → use the browser.
                html = resp.html_content or ""
                is_html = normalize_content_type(resp.headers.get("content-type", "")).startswith("text/html")
                if is_html and html and resp.status < 400:
                    fast_assessment = assess_dashboard(url, html)
                if (
                    is_html
                    and html
                    and len(extract_text_from_html(html)) < ROUTING_HEURISTICS.html_fast_thin_content_chars
                    and "<script" in html.lower()
                ):
                    # Repository SPA shells often expose the primary PDF via
                    # citation_pdf_url or the DSpace item API. Prefer that binary
                    # over a browser render of the landing page.
                    pdf_url = await asyncio.to_thread(resolve_primary_pdf_url, url, html)
                    if pdf_url:
                        from ._download import download_pdf

                        pdf_bytes, pdf_error = await download_pdf(pdf_url)
                        if pdf_bytes and not pdf_error:
                            logger.debug(
                                "[fetcher] thin SPA shell → primary PDF %s from %s",
                                pdf_url,
                                url,
                            )
                            return FetchResult(
                                url=pdf_url,
                                status=200,
                                content_type="application/pdf",
                                content_disposition="",
                                html_content=None,
                                body=pdf_bytes,
                                headers={"content-type": "application/pdf"},
                                used_browser=False,
                            )
                    needs_browser_retry = True
                elif fast_assessment.is_dashboard:
                    # Dashboards keep their numbers in background data files, which only a render loads.
                    needs_browser_retry = True
                    dashboard_fallback = resp
        except _KnownBrowserHost:
            needs_browser_retry = True
        except Exception as e:
            return self._empty(url).model_copy(
                update={"error": f"source_http_error: HTTP fetch failed: {type(e).__name__}: {e}"}
            )

        # Step 2: browser fallback (Cloudflare bypass)
        if needs_browser_retry:
            used_browser = True
            reason = f"HTTP {resp.status}" if resp is not None else "fetch error / thin content"
            logger.debug("[fetcher] falling back to StealthyFetcher (%s) url=%s", reason, url)
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
                capture = NetworkCapture(url, dashboard=fast_assessment.is_dashboard)
                kwargs["page_setup"] = capture.page_setup
                kwargs["page_action"] = capture.page_action
                # Only renders forced by the dashboard verdict are bounded; the others keep today's timeouts.
                render_deadline = (
                    asyncio.get_running_loop().time() + _capture.DASHBOARD_RENDER_BUDGET_S
                    if dashboard_fallback is not None
                    else None
                )
                try:
                    async with asyncio.timeout_at(render_deadline):
                        resp = await stealthy_fetch(url, **kwargs)
                        if resp.status >= 400 and dashboard_fallback is not None:
                            raise RuntimeError(f"browser returned HTTP {resp.status}")
                        if resp.html_content:
                            dashboard = await _dashboard_data(url, resp.html_content, fast_assessment, capture)
                    # A dashboard-forced render says nothing about whether the host needs a browser.
                    if resp.status < 400 and dashboard_fallback is None:
                        if len(_BROWSER_HOSTS) >= 128:
                            _BROWSER_HOSTS.pop(next(iter(_BROWSER_HOSTS)))
                        _BROWSER_HOSTS[host] = time.monotonic() + 300
                except Exception as e:
                    if dashboard_fallback is None:
                        logger.debug(
                            "[fetcher] StealthyFetcher failed (%s), trying AsyncFetcher: %s",
                            type(e).__name__,
                            url,
                        )
                    raise
                finally:
                    capture.detach()
            except Exception as e:
                exc_str = str(e)
                if dashboard_fallback is not None:
                    logger.debug(
                        "[fetcher] dashboard render failed, keeping fast HTML (%s: %s): %s",
                        type(e).__name__,
                        exc_str,
                        url,
                    )
                    resp, used_browser, capture, dashboard = dashboard_fallback, False, None, {}
                # Check for download signal (browser navigating to a file download)
                elif _is_download_navigation_error(exc_str):
                    # Return a minimal FetchResult that triggers parse_document
                    return self._empty(url).model_copy(
                        update={
                            "used_browser": True,
                            "status": 200,
                            "error": _DOWNLOAD_SIGNAL,
                        }
                    )
                else:
                    error_prefix = "source_http_error: " if is_network_error(e) else ""
                    return self._empty(url).model_copy(
                        update={
                            "used_browser": True,
                            "error": f"{error_prefix}browser fetch failed: {type(e).__name__}: {exc_str}",
                        }
                    )

        if resp is None:
            return self._empty(url).model_copy(update={"error": "fetch returned no response"})

        ct = normalize_content_type(resp.headers.get("content-type", ""))
        cd = resp.headers.get("content-disposition", "")

        raw_body = _as_bytes(getattr(resp, "body", None))
        # Some hosts, including MDPI, send a PDF with no Content-Type header.
        if not ct and raw_body and raw_body.startswith(PDF_MAGIC_BYTES):
            ct = "application/pdf"

        # Classify as binary or text
        is_binary = any(ct.startswith(t) for t in BINARY_CONTENT_TYPES + IMAGE_CONTENT_TYPES) or (
            raw_body and sniff_document_payload(raw_body, content_type=ct, content_disposition=cd)
        )

        html_content = (resp.html_content or None) if not is_binary else None
        body = raw_body if is_binary else None

        headers: dict[str, str] = {}
        if hasattr(resp, "headers") and resp.headers:
            try:
                headers = dict(resp.headers)
            except Exception:
                pass
        if ct == "application/pdf" and not normalize_content_type(headers.get("content-type", "")):
            headers["content-type"] = "application/pdf"

        if is_binary:
            dashboard = {}
        elif html_content and capture is None and fast_assessment.is_dashboard:
            dashboard = await _dashboard_data(url, html_content, fast_assessment, None)

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
            **dashboard,
        )


async def _dashboard_data(
    url: str, html: str, fast_assessment: DashboardAssessment, capture: NetworkCapture | None
) -> dict:
    """Return the ``FetchResult`` dashboard fields for a page, or ``{}`` when neither assessment is a dashboard.

    ``capture`` is the browser capture, or ``None`` when the fast HTML is the final page.
    Sources keep page order (captured responses, inline JSON, downloads) within the per-page caps.
    """
    if capture is None:
        page_url, rendered = url, fast_assessment
    else:
        page_url = capture.final_url
        rendered = capture.assessment or assess_dashboard(page_url, html)
    kind = fast_assessment.kind or rendered.kind
    if not kind:
        return {}
    sources = (capture.sources if capture is not None else []) + inline_json_sources(html, page_url)
    if capture is not None:
        sources += await download_sources(html, page_url, limit=_MAX_SOURCES_PER_PAGE - len(sources))
    return {
        "dashboard_kind": kind,
        "data_sources": cap_sources(sources),
        "rendered_text": capture.rendered_text if capture is not None else "",
    }


async def fetch(
    url: str,
    *,
    exclude_domains: Optional[Iterable[str]] = None,
    wait_for: Optional[str] = None,
) -> FetchResult:
    """Fetch *url* with :class:`ScraplingFetcher` and return the :class:`FetchResult`.

    Fast HTTP is tried first. A stealth browser retry runs for bot walls,
    timeouts, and thin HTML shells. PDF URLs are downloaded as raw bytes on
    ``FetchResult.body``. HTML and other text responses use
    ``FetchResult.html_content`` and leave ``body`` as ``None``.

    ``exclude_domains`` blocks matching hosts before any network call.
    ``wait_for`` is a CSS selector passed to the browser fallback.

    Failures do not raise. ``FetchResult.error`` is set when the URL is
    rejected or the fetch fails, and ``FetchResult.status`` is non-2xx for
    HTTP errors.
    """
    fetcher = ScraplingFetcher(
        exclude_domains=frozenset(exclude_domains) if exclude_domains else None,
    )
    return await fetcher.fetch(url, URLContext(url=url, depth=0, wait_for=wait_for))


async def fetch_pdf(
    url: str,
    *,
    exclude_domains: Optional[Iterable[str]] = None,
    wait_for: Optional[str] = None,
) -> bytes:
    """Fetch *url* and return the PDF bytes.

    Thin wrapper around :func:`fetch`. The body must start with
    :data:`PDF_MAGIC_BYTES` (``b"%PDF"``).

    Raises:
        RuntimeError: The fetch failed, or the response body is not a PDF.
    """
    result = await fetch(url, exclude_domains=exclude_domains, wait_for=wait_for)
    if result.error:
        raise RuntimeError(result.error)
    if result.body and result.body.startswith(PDF_MAGIC_BYTES):
        return result.body
    raise RuntimeError(f"response is not a PDF: {url}")
