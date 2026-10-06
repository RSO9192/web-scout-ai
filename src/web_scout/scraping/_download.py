"""Binary-content download helpers using a Chain-of-Responsibility fallback sequence (private).

Three concrete handlers form the chain:
  1. ScraplingFetcher  — stealth HTTP via Scrapling's AsyncFetcher (curl_cffi with TLS spoofing).
  2. UrllibDownloader  — stdlib urllib (tolerates broken Content-Encoding headers).
  3. StealthyBrowser   — Scrapling's StealthyFetcher browser (bypasses JS-gated bot protections).

Each handler returns ``None`` to pass control to the next, or ``bytes`` on success.
"""

import asyncio
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

    async def _attempt(self, url: str) -> Optional[bytes]:
        from ._resources import binary_session, resources

        try:
            async with resources().http_limit:
                async with binary_session().stream(
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
                        data = buffer.getvalue()
                    return data if data.startswith(PDF_MAGIC_BYTES) else None
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


class _StealthyBrowser(_Downloader):
    """Download via Scrapling's StealthyFetcher — bypasses JS-gated bot-protection 403s.

    Note: this handler is effective when the server serves the PDF directly over
    HTTPS but gates it behind a JavaScript challenge.  It retrieves the raw
    response body, which contains the PDF bytes when the URL resolves directly to
    a PDF file.
    """

    async def _attempt(self, url: str) -> Optional[bytes]:
        try:
            page = await stealthy_fetch(
                url,
                headless=True,
                disable_resources=False,
                network_idle=True,
                solve_cloudflare=True,
                timeout=ROUTING_HEURISTICS.browser_download_timeout_ms,
                retries=1,
            )
            data = page.body
            if not isinstance(data, bytes):
                data = data.encode() if isinstance(data, str) else bytes(data)
            if data[:4] != PDF_MAGIC_BYTES:
                logger.debug("[download] stealthy browser: response body is not a PDF")
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
    chain = _BROWSER_PDF_CHAIN if needs_browser else _PDF_CHAIN
    from ._resources import pdf_admission

    started = time.perf_counter()
    try:
        async with asyncio.timeout(75), pdf_admission():
            pdf_bytes = await chain.download(url)
    except TimeoutError:
        return None, f"source_http_error: PDF download deadline exceeded: {url}"
    finally:
        logger.debug("[download-timing] seconds=%.3f url=%s", time.perf_counter() - started, url)
    if pdf_bytes is None:
        methods = "StealthyBrowser → Scrapling → urllib" if needs_browser else "Scrapling → urllib → StealthyBrowser"
        return None, f"source_http_error: PDF download failed after all fallback methods ({methods}): {url}"
    return pdf_bytes, None
