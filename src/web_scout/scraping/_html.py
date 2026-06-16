"""HTML scraping strategy (private): HTTP-fast and full Scrapling stealth browser.

Single entry point: ``scrape_html``.

When ``needs_browser=False`` (default) an HTTP-only crawl is attempted via
Scrapling's ``AsyncFetcher``.  Returns ``(None, None)`` when content is too
thin, signalling the caller to retry with ``needs_browser=True``
(Chain-of-Responsibility handoff).

When ``needs_browser=True`` a full stealth browser crawl is performed via
Scrapling's ``StealthyFetcher``, automatically bypassing Cloudflare
Turnstile/Interstitial and other bot-detection mechanisms.  Returns
``_DOWNLOAD_SIGNAL`` as the error string when the browser encounters a file
download, prompting the executor to redirect to the document strategy.
"""

import logging
import re
from typing import Any, Optional, Tuple

from web_scout.config import ROUTING_HEURISTICS

from ._markdown import append_links
from ._scrapling import stealthy_fetch
from .types import SourceArtifact

logger = logging.getLogger(__name__)

_DOWNLOAD_SIGNAL = "__DOWNLOAD_REDIRECT__"

_404_PATTERNS = frozenset({"page not found", "was not found", "no longer exists", "404 error page"})


def _is_404_content(content: str) -> bool:
    lower = content.lower()
    return "404" in lower and any(p in lower for p in _404_PATTERNS)


def _html_to_markdown(html: str) -> str:
    """Convert raw HTML to clean Markdown using markdownify."""
    from markdownify import markdownify

    return markdownify(html, heading_style="ATX", strip=["script", "style", "noscript"])


def _extract_title(html: str, page=None) -> str:
    """Extract page title from Scrapling page object or HTML regex fallback."""
    if page is not None and hasattr(page, "css"):
        try:
            title_el = page.css("title")
            if title_el:
                return str(title_el[0].get_all_text()).strip()
        except Exception:
            pass
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return match.group(1).strip() if match else ""


def _parse_page(page) -> Tuple[str, str]:
    """Convert a Scrapling page object into ``(content_markdown, title)``."""
    html = page.html_content or ""
    content = _html_to_markdown(html)
    content = append_links(content, page)
    title = _extract_title(html, page)
    return content, title


async def scrape_html(
    url: str,
    *,
    needs_browser: bool = False,
    wait_for: Optional[str] = None,
    query: str = "",
) -> Tuple[Optional[SourceArtifact], Optional[str]]:
    """Fetch and parse an HTML page, returning a ``SourceArtifact``.

    ``needs_browser=False`` (fast path):
        Uses ``AsyncFetcher`` (HTTP-only, TLS fingerprint spoofing).
        Returns ``(None, None)`` when content is thin or the request fails —
        the caller should retry with ``needs_browser=True``.

    ``needs_browser=True`` (browser path):
        Uses ``StealthyFetcher`` (full headless browser, CloudFlare bypass).
        Returns ``(artifact, _DOWNLOAD_SIGNAL)`` when the browser detects a
        file download; never returns ``(None, None)``.
    """
    if not needs_browser:
        from scrapling.fetchers import AsyncFetcher

        try:
            page = await AsyncFetcher.get(
                url,
                stealthy_headers=True,
                follow_redirects=True,
                timeout=30,
            )
        except Exception:
            return None, None  # any exception → pass to browser

        if not page or page.status >= 400:
            return None, None

        html = page.html_content or ""
        if not html.strip():
            return None, None

        content, title = _parse_page(page)

        if len(content.strip()) < ROUTING_HEURISTICS.html_fast_thin_content_chars:
            return None, None  # thin content → pass to browser

        return SourceArtifact(kind="text", title=title, text_content=content), None

    # --- browser path ---
    kwargs: dict[str, Any] = dict(
        headless=True,
        # disable_resources=True,  # This is the reason we can't solve CloudFlare
        network_idle=True,
        timeout=ROUTING_HEURISTICS.browser_page_timeout_ms,
        wait=int(ROUTING_HEURISTICS.browser_delay_before_return_html_s * 1000),
        solve_cloudflare=True,
    )

    async def _run(wf: Optional[str]) -> Any:
        kw = dict(kwargs)
        if wf:
            kw["wait_selector"] = wf
        else:
            kw.pop("wait_selector", None)
        return await stealthy_fetch(url, **kw)

    try:
        page = await _run(wait_for)
    except Exception as exc:
        exc_str = str(exc)
        if "download" in exc_str.lower() or "file" in exc_str.lower():
            return SourceArtifact(kind="text", title=""), _DOWNLOAD_SIGNAL
        return SourceArtifact(kind="text", title=""), f"Browser crawl failed: {exc}"

    # Retry without wait_for when it caused a timeout
    if page is None or (wait_for and page.status >= 400):
        try:
            page = await _run(None)
        except Exception as exc:
            return SourceArtifact(kind="text", title=""), f"Browser retry failed: {exc}"

    if page is None:
        return SourceArtifact(kind="text", title=""), "Browser crawl returned no response"

    if page.status >= 400:
        return SourceArtifact(kind="text", title=""), f"Browser returned HTTP {page.status}"

    content, title = _parse_page(page)
    return SourceArtifact(kind="text", title=title, text_content=content), None
