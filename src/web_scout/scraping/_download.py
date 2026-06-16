"""Binary-content download helpers using a Chain-of-Responsibility fallback sequence (private).

Three concrete handlers form the chain: httpx (fast async) → urllib (tolerates broken
Content-Encoding headers) → browser download (bypasses Akamai / bot-protection 403s).
Each handler returns ``None`` to pass control to the next, or ``bytes`` on success.
"""

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from typing import Optional
from urllib.request import Request, urlopen

import httpx

from web_scout.config import ROUTING_HEURISTICS

from .constants import FETCH_HEADERS, PDF_MAGIC_BYTES

logger = logging.getLogger(__name__)


class _Downloader(ABC):
    """One link in a progressive binary-download chain."""

    def __init__(self) -> None:
        self._next: Optional[_Downloader] = None

    def then(self, handler: "_Downloader") -> "_Downloader":
        """Attach the next fallback handler and return it (enables fluent chaining).

        Example::

            head = _HttpxDownloader()
            head.then(_UrllibDownloader()).then(_BrowserDownloader())
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


class _HttpxDownloader(_Downloader):
    """Download via httpx with linear-backoff retries."""

    async def _attempt(self, url: str) -> Optional[bytes]:
        for attempt in range(ROUTING_HEURISTICS.pdf_download_retries):
            try:
                async with httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=ROUTING_HEURISTICS.document_download_timeout,
                    headers=FETCH_HEADERS,
                ) as client:
                    resp = await client.get(url)
                resp.raise_for_status()
                if resp.content[:4] != PDF_MAGIC_BYTES:
                    logger.debug("[download] httpx: server returned non-PDF (ct=%s)", resp.headers.get("content-type"))
                    return None
                return resp.content
            except Exception as exc:
                logger.debug(
                    "[download] httpx attempt %d/%d failed: %s",
                    attempt + 1,
                    ROUTING_HEURISTICS.pdf_download_retries,
                    exc,
                )
                if attempt < ROUTING_HEURISTICS.pdf_download_retries - 1:
                    await asyncio.sleep(1.0 * (attempt + 1))
        return None


def _urllib_download_sync(url: str) -> tuple[bytes, str]:
    req = Request(url, headers=FETCH_HEADERS)
    with urlopen(req, timeout=ROUTING_HEURISTICS.urllib_download_timeout) as resp:
        return resp.read(), resp.headers.get("content-type", "")


class _UrllibDownloader(_Downloader):
    """Download via urllib — tolerates broken Content-Encoding headers that confuse httpx."""

    async def _attempt(self, url: str) -> Optional[bytes]:
        for attempt in range(ROUTING_HEURISTICS.pdf_download_retries):
            try:
                data, content_type = await asyncio.to_thread(_urllib_download_sync, url)
                if data[:4] != PDF_MAGIC_BYTES:
                    logger.debug("[download] urllib: server returned non-PDF (ct=%s)", content_type)
                    return None
                return data
            except Exception as exc:
                logger.debug(
                    "[download] urllib attempt %d/%d failed: %s",
                    attempt + 1,
                    ROUTING_HEURISTICS.pdf_download_retries,
                    exc,
                )
                if attempt < ROUTING_HEURISTICS.pdf_download_retries - 1:
                    await asyncio.sleep(1.0 * (attempt + 1))
        return None


class _BrowserDownloader(_Downloader):
    """Download via a headless browser — bypasses bot-protection that blocks plain HTTP."""

    async def _attempt(self, url: str) -> Optional[bytes]:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

        browser_cfg = BrowserConfig(
            verbose=False,
            headless=True,
            accept_downloads=True,
            user_agent=FETCH_HEADERS["User-Agent"],
        )
        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            exclude_all_images=True,
            verbose=False,
            page_timeout=ROUTING_HEURISTICS.browser_download_timeout_ms,
        )
        try:
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                result = await crawler.arun(url=url, config=run_cfg)
            if not result.downloaded_files:
                return None
            filepath = result.downloaded_files[0]
            try:
                with open(filepath, "rb") as fh:
                    return fh.read()
            finally:
                if os.path.exists(filepath):
                    os.unlink(filepath)
        except Exception as exc:
            logger.debug("[download] browser fallback failed: %s", exc)
            return None


_PDF_CHAIN: _Downloader = _HttpxDownloader()
_PDF_CHAIN.then(_UrllibDownloader()).then(_BrowserDownloader())


async def download_pdf(url: str) -> tuple[Optional[bytes], Optional[str]]:
    """Download PDF bytes via a progressive fallback chain: httpx → urllib → browser.

    Returns ``(pdf_bytes, None)`` on success or ``(None, error_message)`` on failure.
    """
    pdf_bytes = await _PDF_CHAIN.download(url)
    if pdf_bytes is None:
        return None, f"PDF download failed after all fallback methods (httpx → urllib → browser): {url}"
    return pdf_bytes, None
