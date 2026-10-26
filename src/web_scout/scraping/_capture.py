"""Capture the data files behind dashboard pages during a browser fetch (private).

``NetworkCapture`` plugs into a Scrapling browser fetch: ``page_setup`` registers a Playwright
``response`` listener before navigation and ``page_action`` waits for data responses to settle.
``download_sources`` turns same-site CSV and XLSX links on the rendered page into sources.
"""

import asyncio
import logging
import re
import time
from html import unescape
from urllib.parse import urljoin, urlsplit

from ._data_sources import (
    _MAX_BYTES_PER_PAGE,
    _MAX_BYTES_PER_SOURCE,
    DashboardAssessment,
    DataSource,
    _site,
    assess_dashboard,
    is_data_body,
    is_data_response,
)

logger = logging.getLogger(__name__)

_SETTLE_QUIET_S = 1.5
_SETTLE_MAX_S = 6.0
# Render + capture + downloads for a page sent to the browser only by its dashboard verdict; keeps a round
# within the 5-15 s dashboard latency budget.
DASHBOARD_RENDER_BUDGET_S = 15.0
# Not an xhr/fetch allow-list: preloaded data (<link rel=preload>, as on OWID) arrives as "other".
# Documents are skipped so the page itself, or a JSON iframe, is never taken for background data.
_SKIPPED_RESOURCE_TYPES = frozenset({"document"})
_HREF = re.compile(r"""<a\s[^>]*?href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def _media_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


class NetworkCapture:
    """Collect same-site JSON and CSV responses a page loads in the background.

    Pass ``page_setup`` and ``page_action`` to a browser fetch. After the fetch, ``sources`` holds
    the responses that pass ``is_data_response`` and ``is_data_body`` (at most 5 MB each) in arrival
    order, ``assessment`` the dashboard verdict on the rendered page, ``final_url`` the page URL
    after redirects and ``rendered_text`` the page's visible text. The settle wait and the text
    read happen only when ``dashboard`` is set or the rendered page assesses as a dashboard.
    """

    def __init__(self, page_url: str, *, dashboard: bool = False):
        self.page_url = page_url
        self.final_url = page_url
        self.dashboard = dashboard
        self.assessment: DashboardAssessment | None = None
        self.sources: list[DataSource] = []
        self.rendered_text = ""
        self._page = None
        self._captured: dict[int, DataSource] = {}
        self._pending: set[asyncio.Task] = set()
        self._seen = 0
        self._stored_bytes = 0
        self._last_data = 0.0

    @property
    def is_dashboard(self) -> bool:
        """Whether the fast-HTML hint or the rendered page marks this page as a dashboard."""
        return self.dashboard or bool(self.assessment and self.assessment.is_dashboard)

    async def page_setup(self, page) -> None:
        """Register the response listener; runs before navigation."""
        self._page = page
        page.on("response", self._on_response)

    def _current_url(self) -> str:
        url = getattr(self._page, "url", "") or ""
        return url if url.startswith(("http://", "https://")) else self.page_url

    def _on_response(self, response) -> None:
        try:
            if response.request.resource_type in _SKIPPED_RESOURCE_TYPES:
                return
            content_type = response.headers.get("content-type", "")
            if not is_data_response(response.url, content_type, self._current_url()):
                return
            length = response.headers.get("content-length", "")
            if length.isdigit() and int(length) > _MAX_BYTES_PER_SOURCE:
                return
        except Exception as exc:  # a listener must never break the page load
            logger.debug("[capture] response inspection failed: %s", exc)
            return
        self._last_data = time.monotonic()
        order = self._seen
        self._seen += 1
        task = asyncio.get_running_loop().create_task(self._read(order, response, content_type))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _read(self, order: int, response, content_type: str) -> None:
        # Runs as a detached task: any exception here would surface as "Task exception was never retrieved".
        try:
            raw = await response.body()
            if len(raw) > _MAX_BYTES_PER_SOURCE or self._stored_bytes + len(raw) > _MAX_BYTES_PER_PAGE:
                return
            body = raw.decode("utf-8", "replace")
            if not is_data_body(body, content_type):
                return
            kind = "response_json" if "json" in _media_type(content_type) else "response_csv"
            self._stored_bytes += len(raw)
            self._captured[order] = DataSource("", response.url, kind, _media_type(content_type), body, len(raw))
        except Exception as exc:  # bodies of redirected or aborted requests are unavailable
            logger.debug("[capture] body read failed for %s: %s", response.url, exc)

    async def page_action(self, page) -> None:
        """Assess the rendered page; on dashboards wait for data to settle and read the visible text.

        Settling ends after 1.5 s without a new data response, capped at 6 s. The listener is
        removed afterwards so a pooled page does not keep capturing.
        """
        started = time.monotonic()
        try:
            self.final_url = page.url or self.page_url
            self.assessment = assess_dashboard(self.final_url, await page.content())
            if not self.is_dashboard:
                return
            deadline = started + _SETTLE_MAX_S
            while (now := time.monotonic()) < deadline:
                quiet_for = now - max(self._last_data, started)
                if quiet_for >= _SETTLE_QUIET_S and not self._pending:
                    break
                await asyncio.sleep(min(0.1, deadline - now))
            self.rendered_text = await page.inner_text("body")
            if self._pending:
                await asyncio.wait(set(self._pending), timeout=max(0.0, deadline - time.monotonic()))
        finally:
            page.remove_listener("response", self._on_response)
            self._cancel_pending()
            self.sources = [
                DataSource(f"D{i + 1}", s.url, s.kind, s.content_type, s.body, s.size)
                for i, s in enumerate(self._captured[k] for k in sorted(self._captured))
            ]
            logger.debug(
                "[capture] seconds=%.3f sources=%d dashboard=%s url=%s",
                time.monotonic() - started,
                len(self.sources),
                self.is_dashboard,
                self.final_url,
            )

    def _cancel_pending(self) -> None:
        for task in self._pending:
            task.cancel()

    def detach(self) -> None:
        """Remove the listener and cancel body reads if ``page_action`` never finished.

        Covers a failed readiness wait, a browser error or the dashboard render budget expiring.
        """
        self._cancel_pending()
        if self._page is not None:
            try:
                self._page.remove_listener("response", self._on_response)
            except Exception:
                pass


def _download_links(html: str, page_url: str) -> list[tuple[str, str]]:
    """Same-site ``.csv`` and ``.xlsx`` links in page order, as ``(url, kind)``."""
    site = _site(urlsplit(page_url).hostname or "")
    links: list[tuple[str, str]] = []
    for href in _HREF.findall(html):
        url = urljoin(page_url, unescape(href.strip()))
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or _site(parts.hostname or "") != site:
            continue
        path = parts.path.lower()
        kind = "download_csv" if path.endswith(".csv") else "download_xlsx" if path.endswith(".xlsx") else ""
        if kind and all(url != seen for seen, _ in links):
            links.append((url, kind))
    return links


async def _get_capped(url: str) -> bytes | None:
    """GET ``url`` with the pooled binary session, or ``None`` on HTTP >= 400 or a body above 5 MB.

    Streams so the cap is enforced from ``content-length`` before reading, and while reading when the
    header is missing or wrong.
    """
    from web_scout.config import ROUTING_HEURISTICS

    from ._resources import binary_session, resources

    async with resources().http_limit:
        async with binary_session().stream(
            "GET",
            url,
            impersonate="chrome",
            allow_redirects=True,
            timeout=ROUTING_HEURISTICS.validation_timeout,
        ) as response:
            length = response.headers.get("content-length", "")
            if response.status_code >= 400 or (length.isdigit() and int(length) > _MAX_BYTES_PER_SOURCE):
                return None
            buffer = bytearray()
            async for chunk in response.aiter_content():
                if len(buffer) + len(chunk) > _MAX_BYTES_PER_SOURCE:
                    return None
                buffer += chunk
            return bytes(buffer)


async def download_sources(html: str, page_url: str, *, limit: int) -> list[DataSource]:
    """Turn up to ``limit`` same-site download links on a rendered page into sources.

    CSV links are streamed and kept when the body is at most 5 MB and passes ``is_data_body``;
    XLSX links are listed with an empty body. Failed downloads are skipped.
    """
    links = _download_links(html, page_url)[: max(0, limit)]

    async def fetch_csv(url: str) -> DataSource | None:
        try:
            raw = await _get_capped(url)
        except Exception as exc:
            logger.debug("[capture] CSV download failed for %s: %s", url, exc)
            return None
        if raw is None:
            return None
        body = raw.decode("utf-8", "replace")
        if not is_data_body(body, "text/csv"):
            return None
        return DataSource("", url, "download_csv", "text/csv", body, len(raw))

    async def build(url: str, kind: str) -> DataSource | None:
        if kind == "download_xlsx":
            return DataSource("", url, kind, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "", 0)
        return await fetch_csv(url)

    results = await asyncio.gather(*(build(url, kind) for url, kind in links))
    return [source for source in results if source is not None]
