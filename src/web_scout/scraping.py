"""Unified web scraping via crawl4ai (HTML/JS) + docling (documents).

Provides a single ``scrape_url`` function that:

1. **Validates** the URL cheaply (HEAD + fast GET) to skip 404s, SPA
   shells, paywalls, binary files, and blocked domains before any
   expensive processing starts.
2. **Routes** to the appropriate handler based on content type:
   - Static HTML  → crawl4ai ``AsyncHTTPCrawlerStrategy`` (no browser)
   - JS/SPA pages → crawl4ai full browser (Playwright)
   - Documents    → docling (PDF, DOCX, PPTX, XLSX, images)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Optional, Tuple
from urllib.parse import urlparse

import httpx
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

logger = logging.getLogger(__name__)

# Silence noisy third-party loggers used internally by this module.
# These produce excessive output at INFO/DEBUG level that is not useful to callers.
logging.getLogger("docling").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("crawl4ai").setLevel(logging.WARNING)

MAX_CONTENT_CHARS = 30_000

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BLOCKED_DOMAINS = frozenset({
    "youtube.com", "youtu.be",
    "twitter.com", "x.com",
    "facebook.com", "instagram.com",
    "linkedin.com", "tiktok.com",
    "reddit.com",
    "scholar.google.com",
})

_BINARY_CONTENT_TYPES = (
    "image/", "video/", "audio/",
    "application/zip", "application/octet-stream",
    "application/x-tar", "application/x-rar",
)

_DOC_CONTENT_TYPES = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument",
    "application/msword",
    "application/vnd.ms-",
)

_DOC_EXTENSIONS = (".pdf", ".docx", ".pptx", ".xlsx", ".doc", ".xls", ".ppt")

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_MIN_PDF_TEXT_CHARS = 300
_PDF_PAGE_RANGE = (1, 50)  # only process first 50 pages


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

def _is_blocked_domain(url: str, allowed_domains: Optional[frozenset] = None) -> bool:
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    effective_blocked = _BLOCKED_DOMAINS
    if allowed_domains:
        effective_blocked = _BLOCKED_DOMAINS - allowed_domains
    return any(netloc == d or netloc.endswith("." + d) for d in effective_blocked)


def _is_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped[0] not in ("{", "["):
        return False
    try:
        json.loads(stripped)
        return True
    except Exception:
        return False


def _extract_text_from_html(html: str) -> str:
    """Strip script/style blocks then all tags; return plain text."""
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()


# Verdict constants
_SKIP = "SKIP"
_SCRAPE_HTML = "SCRAPE_HTML"   # static HTML — use HTTP crawler (fast)
_SCRAPE_JS = "SCRAPE_JS"       # SPA/JS page — use full browser
_SCRAPE_DOC = "SCRAPE_DOC"     # document — use docling


async def _validate_url(url: str, allowed_domains: Optional[frozenset] = None) -> Tuple[str, str]:
    """Validate a URL before scraping.

    Returns ``(verdict, detail)`` where verdict is one of:
    ``SKIP``, ``SCRAPE_HTML``, ``SCRAPE_JS``, ``SCRAPE_DOC``.
    """
    if _is_blocked_domain(url, allowed_domains=allowed_domains):
        return _SKIP, "blocked domain"

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=12, headers=_FETCH_HEADERS
        ) as client:
            # Step 1: HEAD request
            try:
                head = await client.head(url)
            except Exception as e:
                logger.debug("[validate] HEAD failed for %s: %s", url, e)
                head = None

            if head:
                status = head.status_code
                # 405 Method Not Allowed, 501 Not Implemented, 403 Forbidden might just mean HEAD is disabled
                if status >= 400 and status not in (405, 501, 403):
                    return _SKIP, f"HTTP {status}"

                ct = head.headers.get("content-type", "").lower()

                if any(ct.startswith(t) for t in _BINARY_CONTENT_TYPES):
                    return _SKIP, f"binary content-type: {ct}"

                if "application/json" in ct:
                    return _SKIP, "JSON API endpoint"

                url_path = url.lower().split("?")[0]
                if any(ct.startswith(t) for t in _DOC_CONTENT_TYPES) or any(
                    url_path.endswith(ext) for ext in _DOC_EXTENSIONS
                ):
                    return _SCRAPE_DOC, ct

            # Step 2: fast GET for HTML content analysis
            try:
                resp = await client.get(url)
            except httpx.TimeoutException:
                # Server is slow but reachable — let the browser (longer timeout) try
                return _SCRAPE_JS, "GET timed out — attempting browser scrape"
            except Exception as e:
                return _SKIP, f"GET failed: {type(e).__name__}"

            if resp.status_code >= 400:
                return _SKIP, f"HTTP {resp.status_code} on GET"

            final_ct = resp.headers.get("content-type", "").lower()

            if "application/json" in final_ct or _is_json(resp.text):
                return _SKIP, "JSON response"

            if any(final_ct.startswith(t) for t in _BINARY_CONTENT_TYPES):
                return _SKIP, f"binary on GET: {final_ct}"

            if any(final_ct.startswith(t) for t in _DOC_CONTENT_TYPES):
                return _SCRAPE_DOC, final_ct

            # Analyse HTML content
            html = resp.text
            size = len(html)
            script_tags = len(re.findall(r"<script", html, re.I))
            text = _extract_text_from_html(html)
            text_chars = len(text)

            # Thin content: paywall, login wall, or near-empty page
            if text_chars < 150:
                # Small SPA shell → needs JS rendering
                if size < 8000 and script_tags >= 2:
                    return _SCRAPE_JS, f"SPA shell ({size} chars, {script_tags} scripts)"
                # Thin static page: paywall, login wall, etc.
                return _SKIP, f"thin content ({text_chars} text chars from {size} chars HTML)"

            # Larger SPA shell: reasonable HTML size but almost no real text
            if text_chars < 300 and script_tags >= 3:
                return _SCRAPE_JS, f"likely SPA ({size} chars HTML, {text_chars} text chars)"

            # Soft 404 check
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
            title_text = title_match.group(1).lower() if title_match else ""
            if "404" in title_text and ("not found" in title_text or "error" in title_text):
                return _SKIP, "soft 404 in title"

            lower = html.lower()
            if text_chars < 1000 and any(
                p in lower
                for p in ["page not found", "404 error", "does not exist", "no longer available"]
            ):
                return _SKIP, "soft 404 in content"

            return _SCRAPE_HTML, f"static HTML ({size} chars, {text_chars} text chars)"

    except httpx.TimeoutException:
        return _SKIP, "timeout during validation"
    except Exception as e:
        # If validation itself fails, let the scraper try anyway
        logger.warning("[validate] error for %s: %s", url, e)
        return _SCRAPE_JS, f"validation error ({e}) — attempting browser scrape"


# ---------------------------------------------------------------------------
# HTML scraping — fast HTTP path (no browser)
# ---------------------------------------------------------------------------

async def _scrape_html_fast(url: str, query: str = "", vision_model: Optional[str] = None) -> Tuple[str, str, Optional[str]]:
    """Scrape static HTML using crawl4ai's HTTP-only strategy (no browser).

    Much faster than the full browser path.  Falls back to browser if the
    HTTP strategy returns thin content (indicating JS rendering is needed).
    """
    from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy
    from crawl4ai.content_filter_strategy import BM25ContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    md_generator = DefaultMarkdownGenerator(
        content_filter=BM25ContentFilter(user_query=query, bm25_threshold=1.0) if query else None
    )
    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        exclude_all_images=True,
        remove_overlay_elements=True,
        markdown_generator=md_generator,
        verbose=False,
    )

    try:
        async with AsyncWebCrawler(
            crawler_strategy=AsyncHTTPCrawlerStrategy(),
            config=BrowserConfig(verbose=False),
        ) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)
    except Exception as e:
        logger.warning("[scrape-fast] HTTP strategy failed for %s: %s — falling back to browser", url, e)
        return await _scrape_html_browser(url, wait_for=None, query=query, vision_model=vision_model)

    if not result.success:
        logger.info("[scrape-fast] HTTP strategy got no result for %s — falling back to browser", url)
        return await _scrape_html_browser(url, wait_for=None, query=query, vision_model=vision_model)

    md = result.markdown
    content = _pick_markdown(md, query)
    content = _append_internal_links(content, result)

    # If HTTP strategy returned thin content, the page likely needs JS
    if len(content.strip()) < 200:
        logger.info("[scrape-fast] thin content from HTTP strategy for %s — falling back to browser", url)
        return await _scrape_html_browser(url, wait_for=None, query=query, vision_model=vision_model)

    title = (result.metadata or {}).get("title", "")
    return content, title, None


# ---------------------------------------------------------------------------
# HTML scraping — full browser path (JS rendering)
# ---------------------------------------------------------------------------

def _pick_markdown(md, query: str) -> str:
    """Extract the best markdown content from a crawl4ai result."""
    if query and hasattr(md, "fit_markdown") and md.fit_markdown and len(md.fit_markdown.strip()) > 20:
        return md.fit_markdown
    if hasattr(md, "raw_markdown"):
        return getattr(md, "markdown_with_citations", None) or md.raw_markdown
    return str(md) if md else ""


def _append_internal_links(content: str, result, limit: int = 50) -> str:
    """Append a list of unique internal links to the end of the markdown content."""
    links_data = getattr(result, "links", {})
    if isinstance(links_data, dict):
        links = links_data.get("internal", [])
    else:
        links = getattr(links_data, "internal", [])
        
    link_lines = []
    
    # Also extract links directly from markdown via regex
    for match in re.finditer(r'\[([^\]]+)\]\((https?://[^\)]+)\)', content):
        text, href = match.groups()
        if text.strip() and href.strip() and text.lower() not in ("read more", "click here", "learn more"):
            clean_text = text.strip().replace('\n', ' ')
            link_lines.append(f"- [{clean_text}]({href.strip()})")

    for l in links:
        href = l.get("href", "") if isinstance(l, dict) else getattr(l, "href", "")
        text = (l.get("text", "") if isinstance(l, dict) else getattr(l, "text", "")).strip()
        if href and text and text.lower() not in ("read more", "click here", "learn more"):
            link_lines.append(f"- [{text}]({href})")
            
    if not link_lines:
        return content
            
    seen = set()
    unique_links = []
    for line in link_lines:
        if line not in seen:
            seen.add(line)
            unique_links.append(line)
            
    if unique_links:
        content += "\n\n### Links on Page:\n" + "\n".join(unique_links[:limit])
    return content


async def _scrape_html_browser(
    url: str,
    wait_for: Optional[str] = None,
    query: str = "",
    vision_model: Optional[str] = None,
) -> Tuple[str, str, Optional[str]]:
    """Scrape an HTML/JS page with crawl4ai full browser (Playwright)."""
    from crawl4ai.content_filter_strategy import BM25ContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    md_generator = DefaultMarkdownGenerator(
        content_filter=BM25ContentFilter(user_query=query, bm25_threshold=1.0) if query else None
    )
    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        exclude_all_images=True,
        remove_overlay_elements=True,
        markdown_generator=md_generator,
        wait_until="networkidle",
        page_timeout=45_000,
        delay_before_return_html=1.0,
        verbose=False,
    )
    if wait_for:
        run_cfg.wait_for = wait_for

    _browser_cfg = BrowserConfig(
        verbose=False,
        headless=True,
        enable_stealth=True,
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    try:
        async with AsyncWebCrawler(config=_browser_cfg) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)
    except Exception as crawl_err:
        if "Download is starting" in str(crawl_err):
            logger.info("[scrape-browser] download intercepted, retrying as document → %s", url)
            return await _scrape_document(url, query=query, vision_model=vision_model)
        raise

    # If wait_for caused timeout, retry without it
    if not result.success and wait_for:
        logger.warning("wait_for=%r failed for %s — retrying without it", wait_for, url)
        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            exclude_all_images=True,
            remove_overlay_elements=True,
            markdown_generator=md_generator,
            wait_until="networkidle",
            page_timeout=45_000,
            delay_before_return_html=1.0,
            verbose=False,
        )
        async with AsyncWebCrawler(config=_browser_cfg) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)

    if not result.success:
        return "", "", result.error_message or "Crawl failed"

    md = result.markdown
    content = _pick_markdown(md, query)

    # BM25 too aggressive fallback
    if query and hasattr(md, "fit_markdown") and len((md.fit_markdown or "").strip()) <= 20 and hasattr(md, "raw_markdown"):
        content = getattr(md, "markdown_with_citations", None) or md.raw_markdown

    content = _append_internal_links(content, result)

    lower = content.lower()
    if not content.strip() or (
        any(p in lower for p in ["page not found", "was not found", "no longer exists", "404 error page"])
        and "404" in lower
    ):
        if vision_model:
            logger.info("[scrape-browser] empty/404 content, trying vision fallback → %s", url)
            return await _scrape_via_vision(url, query=query, vision_model=vision_model)
        return "", "", "Page loaded but returned empty or 404 content"

    title = (result.metadata or {}).get("title", "")
    return content, title, None


async def _scrape_via_vision(url: str, query: str, vision_model: str) -> Tuple[str, str, Optional[str]]:
    """Screenshot fallback using a vision LLM when text extraction fails.

    Uses Playwright (already installed via crawl4ai) to take a viewport screenshot,
    then passes the image to a vision LLM for content extraction.
    Only fires when both fast and browser text paths return empty/insufficient content.
    """
    import base64
    import litellm
    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=_FETCH_HEADERS["User-Agent"],
            )
            page = await context.new_page()
            try:
                await page.goto(url, timeout=45_000, wait_until="networkidle")
                await page.wait_for_timeout(8000)  # let JS / bot-challenge settle
            except Exception:
                pass  # take screenshot regardless
            screenshot_bytes = await page.screenshot(type="png")  # viewport only (not full_page)
            await browser.close()

        screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
        query_clause = f" relevant to: {query}" if query else ""
        prompt = (
            f"Extract all text content{query_clause} from this page screenshot. "
            "Return the content as clean plain text or markdown. "
            "Include specific facts, numbers, names, and data. "
            "Exclude navigation bars and footers."
        )
        response = await litellm.acompletion(
            model=vision_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
                ],
            }],
            max_tokens=2000,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            return "", "", "Vision extraction returned empty content"
        if len(content) < 400:
            return "", "", f"Vision extraction returned too little content ({len(content)} chars — page likely blocked)"
        return content, "", None

    except Exception as e:
        logger.warning("[scrape-vision] failed for %s: %s", url, e)
        return "", "", f"Vision fallback failed: {e}"


# ---------------------------------------------------------------------------
# Document scraping via docling
# ---------------------------------------------------------------------------

async def _scrape_document(url: str, query: str = "", vision_model: Optional[str] = None) -> Tuple[str, str, Optional[str]]:
    """Extract content from a document (PDF, DOCX, PPTX, XLSX) via docling.

    PDFs use a fast text-layer path (no OCR, no table detection) and are
    limited to the first ``_PDF_PAGE_RANGE`` pages to avoid timeouts on
    large reports.
    """
    title = url.rsplit("/", 1)[-1] or "Document"
    url_lower = url.lower().split("?")[0]

    if url_lower.endswith(".pdf"):
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling_core.types.io import DocumentStream

        # Download the PDF ourselves with a browser user-agent.
        # Docling's internal URL fetcher uses a plain user-agent that is
        # often blocked by government/NGO servers, resulting in "not valid".
        # Downloading first and passing bytes via DocumentStream is reliable.
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=20, headers=_FETCH_HEADERS
            ) as client:
                dl_resp = await client.get(url)
            dl_resp.raise_for_status()
            pdf_bytes = dl_resp.content
            if pdf_bytes[:4] != b'%PDF':
                return "", "", f"URL did not return a PDF (got {dl_resp.headers.get('content-type','?')})"
        except Exception as e:
            logger.warning("[scrape-doc] PDF download failed %s: %s — trying docling direct", url, e)
            pdf_bytes = None

        def _convert_fast(pdf_bytes=pdf_bytes):
            opts = PdfPipelineOptions(
                do_ocr=False,
                do_table_structure=False,
                force_backend_text=True,
            )
            converter = DocumentConverter(
                format_options={"pdf": PdfFormatOption(pipeline_options=opts)}
            )
            if pdf_bytes is not None:
                import io as _io
                filename = url.rsplit("/", 1)[-1].split("?")[0] or "document.pdf"
                source = DocumentStream(name=filename, stream=_io.BytesIO(pdf_bytes))
            else:
                source = url  # fallback to direct URL
            result = converter.convert(
                source,
                page_range=_PDF_PAGE_RANGE,
            )
            return result.document.export_to_markdown()

        content = await asyncio.to_thread(_convert_fast)
        content = _append_internal_links(content, None)
        
        if len(content.strip()) < _MIN_PDF_TEXT_CHARS:
            if vision_model:
                logger.info("[scrape-doc] scanned PDF, trying vision fallback → %s", url)
                return await _scrape_via_vision(url, query=query, vision_model=vision_model)
            return (
                f"[Image-only or scanned PDF — no extractable text layer. "
                f"Extracted {len(content.strip())} chars. URL: {url}]",
                title,
                None,
            )
        return content, title, None

    # Non-PDF documents (DOCX, PPTX, XLSX, images)
    from docling.document_converter import DocumentConverter

    def _convert():
        converter = DocumentConverter()
        result = converter.convert(url)
        return result.document.export_to_markdown()

    content = await asyncio.to_thread(_convert)
    content = _append_internal_links(content, None)
    return content, title, None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def scrape_url(
    url: str,
    wait_for: Optional[str] = None,
    query: str = "",
    vision_model: Optional[str] = None,
    allowed_domains: Optional[frozenset] = None,
) -> Tuple[str, str, Optional[str]]:
    """Scrape a URL and return clean markdown content.

    Validates the URL first (HEAD + fast GET), then routes to:
    - ``SCRAPE_HTML``  → ``_scrape_html_fast`` (HTTP strategy, no browser)
    - ``SCRAPE_JS``    → ``_scrape_html_browser`` (full Playwright browser)
    - ``SCRAPE_DOC``   → ``_scrape_document`` (docling)
    - ``SKIP``         → returns error immediately, no scraping

    Args:
        url: The URL to scrape.
        wait_for: Optional CSS selector for JS-rendered pages.
        query: Optional search query for BM25 content filtering.
        allowed_domains: Frozenset of domain strings (e.g. ``frozenset({"reddit.com"})``)
            to remove from the default blocked-domain list. ``None`` uses the full block list.

    Returns:
        Tuple of ``(markdown_content, page_title, error_or_none)``.
    """
    # Validate first
    verdict, detail = await _validate_url(url, allowed_domains=allowed_domains)
    logger.info("[scrape] validate %s → %s (%s)", url, verdict, detail)

    if verdict == _SKIP:
        return "", "", f"Skipped: {detail}"

    # Flag: plain-HTTP timed out + browser path used = Akamai/Cloudflare tarpit pattern
    _likely_bot = verdict == _SCRAPE_JS and "GET timed out" in detail

    try:
        if verdict == _SCRAPE_DOC:
            logger.info("[scrape] document → %s", url)
            content, title, error = await _scrape_document(url, query=query, vision_model=vision_model)
        elif verdict == _SCRAPE_HTML:
            logger.info("[scrape] html-fast → %s", url)
            content, title, error = await _scrape_html_fast(url, query=query, vision_model=vision_model)
        else:  # SCRAPE_JS
            logger.info("[scrape] html-browser → %s", url)
            content, title, error = await _scrape_html_browser(url, wait_for, query=query, vision_model=vision_model)
    except Exception as e:
        logger.error("[scrape] failed %s: %s", url, e)
        return "", "", str(e)

    if error:
        if _likely_bot:
            logger.info("[scrape] bot_detected %s", url)
            return "", title or "", f"bot_detected: {error}"
        return "", title or "", error

    if not content.strip():
        if _likely_bot:
            logger.info("[scrape] bot_detected %s", url)
            return "", title or "", "bot_detected: Browser loaded page but returned empty content"
        return "", title or "", "Extraction returned empty content"

    if len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS] + f"\n\n[Truncated at {MAX_CONTENT_CHARS:,} chars]"

    logger.info("[scrape] ok   %s (%d chars)", url, len(content))
    return content, title or "", None
