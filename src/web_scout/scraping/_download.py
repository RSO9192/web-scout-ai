"""Binary-content download helpers using a Chain-of-Responsibility fallback sequence (private).

Three concrete handlers form the chain:
  1. ScraplingFetcher  — stealth HTTP via Scrapling's AsyncFetcher (curl_cffi with TLS spoofing).
  2. UrllibDownloader  — stdlib urllib (tolerates broken Content-Encoding headers).
  3. StealthyBrowser   — Scrapling's StealthyFetcher browser (bypasses JS-gated bot protections).

Each handler returns ``None`` to pass control to the next, or ``bytes`` on success.
"""

import asyncio
import base64
import io
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Optional
from urllib.request import Request, urlopen

from web_scout.config import ROUTING_HEURISTICS

from ._scrapling import stealthy_fetch
from .constants import FETCH_HEADERS, PDF_MAGIC_BYTES

logger = logging.getLogger(__name__)


class _Downloader(ABC):
    """One link in a progressive binary-download chain."""

    def __init__(self) -> None:
        self._next: Optional[_Downloader] = None

    def then(self, handler: "_Downloader") -> "_Downloader":
        """Attach the next fallback handler and return it (enables fluent chaining).

        Example::

            head = _ScraplingFetcher()
            head.then(_UrllibDownloader()).then(_StealthyBrowser())
        """
        self._next = handler
        return handler

    async def download(self, url: str) -> Optional[bytes]:
        data = await self._attempt(url)
        if data is not None:
            return data
        if self._next is not None:
            return await self._next.download(url)
        return None

    @abstractmethod
    async def _attempt(self, url: str) -> Optional[bytes]:
        """Try to download ``url``.  Return bytes on success, ``None`` to fall through."""


class _ScraplingFetcher(_Downloader):
    """Download via Scrapling's AsyncFetcher with stealth headers and TLS fingerprint spoofing."""

    async def _read(self, session, url: str) -> Optional[bytes]:
        async with session.stream(
            "GET",
            url,
            impersonate="chrome",
            headers={"referer": "https://www.google.com/"},
            allow_redirects=True,
            timeout=ROUTING_HEURISTICS.document_download_timeout,
        ) as response:
            if response.status_code >= 400:
                return None
            with io.BytesIO() as buffer:
                async for chunk in response.aiter_content():
                    if buffer.tell() + len(chunk) > _max_document_bytes():
                        raise ValueError("PDF exceeds download memory budget")
                    buffer.write(chunk)
                return buffer.getvalue()

    async def _attempt(self, url: str) -> Optional[bytes]:
        from ._akamai import follow_akamai_interstitial
        from ._resources import binary_session, resources

        try:
            async with resources().http_limit:
                session = binary_session()
                data = await self._read(session, url)
                if data is None:
                    return None
                if not data.startswith(PDF_MAGIC_BYTES):
                    next_url = await follow_akamai_interstitial(session, url, data, impersonate="chrome")
                    if next_url:
                        data = await self._read(session, next_url)
                return data if data and data.startswith(PDF_MAGIC_BYTES) else None
        except Exception as exc:
            logger.debug("[download] pooled HTTP failed: %s", exc)
            return None


def _max_document_bytes():
    return int(os.getenv("WEB_SCOUT_MAX_PDF_BYTES", str(128 * 1024 * 1024)))


def _urllib_download_sync(url: str) -> tuple[bytes, str]:
    req = Request(url, headers=FETCH_HEADERS)
    with urlopen(req, timeout=ROUTING_HEURISTICS.urllib_download_timeout) as resp:
        with io.BytesIO() as buffer:
            while chunk := resp.read(64 * 1024):
                if buffer.tell() + len(chunk) > _max_document_bytes():
                    raise ValueError("PDF exceeds download memory budget")
                buffer.write(chunk)
            return buffer.getvalue(), resp.headers.get("content-type", "")


class _UrllibDownloader(_Downloader):
    """Download via urllib — tolerates broken Content-Encoding headers that confuse curl_cffi."""

    async def _attempt(self, url: str) -> Optional[bytes]:
        try:
            data, content_type = await asyncio.to_thread(_urllib_download_sync, url)
            if data[:4] != PDF_MAGIC_BYTES:
                logger.debug("[download] urllib: server returned non-PDF (ct=%s)", content_type)
                return None
            return data
        except Exception as exc:
            logger.debug("[download] urllib failed: %s", exc)
            return None


# Runs inside the page: fetch through Chromium's network stack (cookies,
# Cloudflare clearance, TLS handling) and return the body as base64, or null
# on HTTP error / over budget. readAsDataURL encodes natively, so large PDFs
# avoid a JS-side string-building loop.
_IN_PAGE_FETCH_JS = """async ([url, maxBytes]) => {
    const response = await fetch(url, {credentials: "include"});
    if (!response.ok) return null;
    const blob = await response.blob();
    if (blob.size > maxBytes) return null;
    const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(blob);
    });
    return dataUrl.slice(dataUrl.indexOf(",") + 1);
}"""


class _StealthyBrowser(_Downloader):
    """Download via Scrapling's StealthyFetcher — bypasses JS-gated bot-protection 403s.

    After navigation (and any Cloudflare solve), the bytes are fetched from inside
    the page. The navigation response body cannot be used: Chromium shows PDFs in
    its viewer, so Scrapling returns the viewer HTML instead of the file.
    """

    async def _attempt(self, url: str) -> Optional[bytes]:
        encoded: list[Optional[str]] = []

        async def read_pdf(page) -> None:
            # page.url is the post-redirect URL: re-fetching the original URL
            # fails CORS when it redirects to another origin (fao.org → openknowledge).
            encoded.append(await page.evaluate(_IN_PAGE_FETCH_JS, [page.url, _max_document_bytes()]))

        try:
            await stealthy_fetch(
                url,
                wait_ready=False,
                headless=True,
                disable_resources=False,
                solve_cloudflare=True,
                timeout=ROUTING_HEURISTICS.browser_download_timeout_ms,
                retries=1,
                page_action=read_pdf,
            )
            data = base64.b64decode(encoded[0]) if encoded and encoded[0] else b""
            if not data.startswith(PDF_MAGIC_BYTES):
                logger.debug("[download] stealthy browser: in-page fetch returned no PDF")
                return None
            return data
        except Exception as exc:
            logger.debug("[download] stealthy browser fallback failed: %s", exc)
            return None


_PDF_CHAIN: _Downloader = _ScraplingFetcher()
_PDF_CHAIN.then(_UrllibDownloader()).then(_StealthyBrowser())

_BROWSER_PDF_CHAIN: _Downloader = _StealthyBrowser()
_BROWSER_PDF_CHAIN.then(_ScraplingFetcher()).then(_UrllibDownloader())


async def download_pdf(url: str, *, needs_browser: bool = False) -> tuple[Optional[bytes], Optional[str]]:
    """Download PDF bytes via a progressive fallback chain.

    When ``needs_browser`` is True (bot-wall detected during planning), the
    browser is tried first, followed by the HTTP fallbacks. Otherwise the chain
    is Scrapling → urllib → StealthyBrowser.

    Returns ``(pdf_bytes, None)`` on success or ``(None, error_message)`` on failure.
    """
    from .utils import log_fetch, resolve_document_download_url

    url = resolve_document_download_url(url)
    chain = _BROWSER_PDF_CHAIN if needs_browser else _PDF_CHAIN
    from ._resources import pdf_admission

    started = time.perf_counter()
    try:
        async with asyncio.timeout(ROUTING_HEURISTICS.pdf_download_deadline_s), pdf_admission():
            pdf_bytes = await chain.download(url)
    except TimeoutError:
        error = f"source_http_error: PDF download deadline exceeded: {url}"
        log_fetch(url, status="timeout", via="pdf", error=error)
        return None, error
    finally:
        logger.debug("[download-timing] seconds=%.3f url=%s", time.perf_counter() - started, url)
    if pdf_bytes is None:
        methods = "StealthyBrowser → Scrapling → urllib" if needs_browser else "Scrapling → urllib → StealthyBrowser"
        error = f"source_http_error: PDF download failed after all fallback methods ({methods}): {url}"
        log_fetch(url, status="error", via="pdf", error=error)
        return None, error
    log_fetch(url, status=200, via="pdf", bytes_=len(pdf_bytes))
    return pdf_bytes, None
