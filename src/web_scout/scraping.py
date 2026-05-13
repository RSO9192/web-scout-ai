"""Unified web scraping via crawl4ai, docling, and vision fallbacks.

Provides a single ``scrape_url`` function that:

1. **Validates** the URL cheaply (HEAD + fast GET) to skip 404s, SPA
   shells, paywalls, binary files, and blocked domains before any
   expensive processing starts.
2. **Routes** to the appropriate handler based on content type:
   - Static HTML  → crawl4ai ``AsyncHTTPCrawlerStrategy`` (no browser)
   - JS/SPA pages → crawl4ai full browser (Playwright)
   - Documents    → docling (PDF, DOCX, PPTX, XLSX)
   - JSON         → structured extraction
   - Images       → vision extraction
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Optional, Tuple
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

import httpx
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

from ._heuristics import ROUTING_HEURISTICS

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
    # Social media and video platforms
    "youtube.com", "youtu.be",
    "twitter.com", "x.com",
    "facebook.com", "instagram.com",
    "linkedin.com", "tiktok.com",
    "reddit.com",
    # Search engines
    "scholar.google.com",
    # Consistently paywalled academic publishers (thin HTML without subscription)
    "sciencedirect.com",
    "springer.com",
    "link.springer.com",
    "wiley.com",
    "onlinelibrary.wiley.com",
    "tandfonline.com",
    "sagepub.com",
    "cambridge.org",
    "jstor.org",
    # NOTE: open-access publishers (frontiersin.org, mdpi.com, journals.plos.org) and
    # abstract-available publishers (researchgate.net, nature.com, academic.oup.com)
    # are intentionally NOT blocked — they yield useful content for research queries.
})

_BINARY_CONTENT_TYPES = (
    "video/", "audio/",
    "application/zip", "application/octet-stream",
    "application/x-tar", "application/x-rar",
)

_IMAGE_CONTENT_TYPES = ("image/",)

_JSON_CONTENT_TYPES = (
    "application/json",
    "application/geo+json",
    "application/ld+json",
    "application/vnd.api+json",
    "text/json",
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

_MIN_PDF_TEXT_CHARS = ROUTING_HEURISTICS.min_pdf_text_chars
_PDF_MAX_PAGES_DEFAULT = ROUTING_HEURISTICS.pdf_max_pages_default
_VALIDATION_TIMEOUT = ROUTING_HEURISTICS.validation_timeout
_DOCUMENT_DOWNLOAD_TIMEOUT = ROUTING_HEURISTICS.document_download_timeout
_URLLIB_DOWNLOAD_TIMEOUT = ROUTING_HEURISTICS.urllib_download_timeout
_PDF_DOWNLOAD_RETRIES = ROUTING_HEURISTICS.pdf_download_retries
_PDF_DOCLING_CONVERTER = None
_PDF_DOCLING_CONVERTER_LOCK = threading.Lock()


def _quiet_browser_config(**overrides: Any) -> BrowserConfig:
    """Build a crawl4ai BrowserConfig that suppresses its own startup banners."""
    return BrowserConfig(verbose=False, **overrides)


def _get_pdf_docling_converter():
    """Return a shared Docling converter for the fast PDF configuration.

    Reusing the same ``DocumentConverter`` lets Docling reuse its initialized
    pipeline and heavy layout model across PDF calls.
    """
    global _PDF_DOCLING_CONVERTER
    if _PDF_DOCLING_CONVERTER is not None:
        return _PDF_DOCLING_CONVERTER

    with _PDF_DOCLING_CONVERTER_LOCK:
        if _PDF_DOCLING_CONVERTER is None:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            opts = PdfPipelineOptions(
                do_ocr=False,
                do_table_structure=False,
                force_backend_text=True,
            )
            _PDF_DOCLING_CONVERTER = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
            )
    return _PDF_DOCLING_CONVERTER


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


def _normalize_content_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _filename_from_content_disposition(value: str) -> str:
    if not value:
        return ""

    filename_star = re.search(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", value, flags=re.I)
    if filename_star:
        return unquote(filename_star.group(1).strip().strip('"'))

    filename = re.search(r'filename\s*=\s*"([^"]+)"', value, flags=re.I)
    if filename:
        return filename.group(1).strip()

    filename = re.search(r"filename\s*=\s*([^;]+)", value, flags=re.I)
    if filename:
        return filename.group(1).strip().strip('"')

    return ""


def _looks_like_document_resource(url: str, content_type: str = "", content_disposition: str = "") -> bool:
    ct = _normalize_content_type(content_type)
    url_path = url.lower().split("?", 1)[0].split("#", 1)[0]
    if any(url_path.endswith(ext) for ext in _DOC_EXTENSIONS):
        return True
    if any(ct.startswith(t) for t in _DOC_CONTENT_TYPES):
        return True
    filename = _filename_from_content_disposition(content_disposition).lower()
    return any(filename.endswith(ext) for ext in _DOC_EXTENSIONS)


def _looks_like_pdf_resource(url: str, content_type: str = "", content_disposition: str = "") -> bool:
    ct = _normalize_content_type(content_type)
    url_path = url.lower().split("?", 1)[0].split("#", 1)[0]
    if url_path.endswith(".pdf") or ct == "application/pdf":
        return True
    filename = _filename_from_content_disposition(content_disposition).lower()
    return filename.endswith(".pdf")


def _extract_hrefs_from_html(html: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r'href=["\']([^"\']+)["\']', html, flags=re.I)]


def _looks_like_auth_wall(html_lower: str) -> bool:
    auth_markers = (
        "sign in",
        "log in",
        "login required",
        "subscribe to continue",
        "subscription required",
        "create an account",
        "members only",
        "access denied",
    )
    return any(marker in html_lower for marker in auth_markers)


def _looks_like_metadata_page(html: str) -> bool:
    lower = html.lower()
    hrefs = _extract_hrefs_from_html(html)
    if not hrefs:
        return False

    doc_links = [href for href in hrefs if _looks_like_document_resource(href)]
    if doc_links:
        return True

    metadata_markers = (
        "full text",
        "download",
        "document",
        "report",
        "dataset",
        "publication",
        "regulation",
        "law",
        "view details",
        "metadata",
        "repository",
        "catalog",
    )
    return len(hrefs) >= 5 or any(marker in lower for marker in metadata_markers)


def _trim_json_value(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int = 4,
    max_items: int = 20,
    max_string_chars: int = 500,
) -> Any:
    if depth >= max_depth:
        if isinstance(value, list):
            return f"[list truncated: {len(value)} items]"
        if isinstance(value, dict):
            return f"{{object truncated: {len(value)} keys}}"
        return value

    if isinstance(value, dict):
        items = list(value.items())
        trimmed = {
            str(k): _trim_json_value(v, depth=depth + 1, max_depth=max_depth, max_items=max_items, max_string_chars=max_string_chars)
            for k, v in items[:max_items]
        }
        if len(items) > max_items:
            trimmed["..."] = f"{len(items) - max_items} more keys omitted"
        return trimmed

    if isinstance(value, list):
        trimmed = [
            _trim_json_value(v, depth=depth + 1, max_depth=max_depth, max_items=max_items, max_string_chars=max_string_chars)
            for v in value[:max_items]
        ]
        if len(value) > max_items:
            trimmed.append(f"... {len(value) - max_items} more items omitted")
        return trimmed

    if isinstance(value, str) and len(value) > max_string_chars:
        return value[:max_string_chars] + f"... [truncated, original length {len(value)}]"

    return value


# Verdict constants
class ScrapeStrategy(str, Enum):
    """Normalized scrape strategies chosen during URL routing."""

    SKIP = "SKIP"
    HTML_FAST = "SCRAPE_HTML"
    HTML_BROWSER = "SCRAPE_JS"
    DOCUMENT = "SCRAPE_DOC"
    JSON = "SCRAPE_JSON"
    IMAGE = "SCRAPE_IMAGE"


@dataclass(frozen=True)
class ScrapePlan:
    """Routing plan produced by URL validation before executing a scraper."""

    strategy: ScrapeStrategy
    reason: str
    content_type: str = ""
    content_disposition: str = ""

    @property
    def likely_bot_detected(self) -> bool:
        return self.strategy == ScrapeStrategy.HTML_BROWSER and "GET timed out" in self.reason


_SKIP = ScrapeStrategy.SKIP
_SCRAPE_HTML = ScrapeStrategy.HTML_FAST
_SCRAPE_JS = ScrapeStrategy.HTML_BROWSER
_SCRAPE_DOC = ScrapeStrategy.DOCUMENT


@dataclass(frozen=True)
class SourceArtifact:
    """Query-agnostic source artifact that can be reused across queries."""

    kind: Literal["text", "binary"]
    title: str
    text_content: str = ""
    binary_bytes: bytes = b""
    mime_type: str = ""
_SCRAPE_JSON = ScrapeStrategy.JSON
_SCRAPE_IMAGE = ScrapeStrategy.IMAGE


def _log_scrape_plan(url: str, plan: ScrapePlan, **details: Any) -> ScrapePlan:
    """Emit structured routing diagnostics and return the plan unchanged."""
    detail_bits = " ".join(f"{key}={value}" for key, value in details.items() if value is not None)
    suffix = f" {detail_bits}" if detail_bits else ""
    logger.info(
        "[scrape-plan] route=%s reason=%s url=%s%s",
        plan.strategy,
        plan.reason,
        url,
        suffix,
    )
    return plan


def _plan_short_html_page(
    url: str,
    *,
    size: int,
    text_chars: int,
    script_tags: int,
    lower_html: str,
    html: str,
) -> ScrapePlan:
    """Route a very low-text HTML page using the existing heuristics unchanged."""
    if size < ROUTING_HEURISTICS.short_html_size_chars and script_tags >= ROUTING_HEURISTICS.short_html_spa_script_count:
        return _log_scrape_plan(
            url,
            ScrapePlan(ScrapeStrategy.HTML_BROWSER, f"SPA shell ({size} chars, {script_tags} scripts)"),
            size=size,
            text_chars=text_chars,
            script_tags=script_tags,
        )
    if _looks_like_auth_wall(lower_html):
        return _log_scrape_plan(
            url,
            ScrapePlan(
                ScrapeStrategy.SKIP,
                f"auth/paywall wall ({text_chars} text chars from {size} chars HTML)",
            ),
            size=size,
            text_chars=text_chars,
        )
    if _looks_like_metadata_page(html):
        return _log_scrape_plan(
            url,
            ScrapePlan(
                ScrapeStrategy.HTML_FAST,
                f"short metadata page ({text_chars} text chars from {size} chars HTML)",
            ),
            size=size,
            text_chars=text_chars,
            script_tags=script_tags,
        )
    return _log_scrape_plan(
        url,
        ScrapePlan(ScrapeStrategy.HTML_FAST, f"short static HTML ({text_chars} text chars from {size} chars HTML)"),
        size=size,
        text_chars=text_chars,
        script_tags=script_tags,
    )


def _plan_low_text_html(url: str, *, size: int, text_chars: int, script_tags: int) -> Optional[ScrapePlan]:
    """Route probable SPA shells without changing thresholds or precedence."""
    if text_chars < ROUTING_HEURISTICS.low_text_spa_chars and script_tags >= ROUTING_HEURISTICS.low_text_spa_script_count:
        return _log_scrape_plan(
            url,
            ScrapePlan(ScrapeStrategy.HTML_BROWSER, f"likely SPA ({size} chars HTML, {text_chars} text chars)"),
            size=size,
            text_chars=text_chars,
            script_tags=script_tags,
        )
    density = text_chars / size if size else 0.0
    if script_tags >= ROUTING_HEURISTICS.heavy_spa_script_count and density < ROUTING_HEURISTICS.heavy_spa_text_density:
        return _log_scrape_plan(
            url,
            ScrapePlan(ScrapeStrategy.HTML_BROWSER, f"likely SPA (density {density:.1%}, {script_tags} scripts)"),
            size=size,
            text_chars=text_chars,
            script_tags=script_tags,
            density=f"{density:.3f}",
        )
    return None


def _plan_soft_404(url: str, *, title_text: str, lower_html: str, text_chars: int) -> Optional[ScrapePlan]:
    """Route soft-404 pages using the existing checks unchanged."""
    if "404" in title_text and ("not found" in title_text or "error" in title_text):
        return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.SKIP, "soft 404 in title"))
    if text_chars < ROUTING_HEURISTICS.soft_404_text_chars and any(
        pattern in lower_html
        for pattern in ["page not found", "404 error", "does not exist", "no longer available"]
    ):
        return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.SKIP, "soft 404 in content"), text_chars=text_chars)
    return None


async def _build_scrape_plan(url: str, allowed_domains: Optional[frozenset] = None) -> ScrapePlan:
    """Build a scrape routing plan for a URL before running any heavy extractor."""
    if _is_blocked_domain(url, allowed_domains=allowed_domains):
        return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.SKIP, "blocked domain"))
    if _looks_like_document_resource(url):
        return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.DOCUMENT, "document-by-url"))

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=_VALIDATION_TIMEOUT, headers=_FETCH_HEADERS
        ) as client:
            # Step 1: HEAD request
            try:
                head = await client.head(url)
            except Exception:
                head = None

            if head:
                status = head.status_code
                # 405 Method Not Allowed, 501 Not Implemented, 403 Forbidden might just mean HEAD is disabled
                if status >= 400 and status not in (405, 501, 403):
                    return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.SKIP, f"HTTP {status}"), phase="head")

                ct = _normalize_content_type(head.headers.get("content-type", ""))
                cd = head.headers.get("content-disposition", "")

                if _looks_like_document_resource(url, ct, cd):
                    return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.DOCUMENT, ct, ct, cd), phase="head")
                if any(ct.startswith(t) for t in _JSON_CONTENT_TYPES):
                    return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.JSON, ct), phase="head")
                if any(ct.startswith(t) for t in _IMAGE_CONTENT_TYPES):
                    return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.IMAGE, ct), phase="head")
                if any(ct.startswith(t) for t in _BINARY_CONTENT_TYPES):
                    return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.SKIP, f"binary content-type: {ct}"), phase="head")

            # Step 2: fast GET for HTML content analysis
            try:
                resp = await client.get(url)
            except httpx.TimeoutException:
                # Server is slow but reachable — let the browser (longer timeout) try
                return _log_scrape_plan(
                    url,
                    ScrapePlan(ScrapeStrategy.HTML_BROWSER, "GET timed out — attempting browser scrape"),
                    phase="get",
                )
            except Exception as e:
                return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.SKIP, f"GET failed: {type(e).__name__}"), phase="get")

            if resp.status_code >= 400:
                return _log_scrape_plan(
                    url,
                    ScrapePlan(ScrapeStrategy.SKIP, f"HTTP {resp.status_code} on GET"),
                    phase="get",
                )

            final_ct = _normalize_content_type(resp.headers.get("content-type", ""))
            final_cd = resp.headers.get("content-disposition", "")

            if _looks_like_document_resource(url, final_ct, final_cd):
                return _log_scrape_plan(
                    url,
                    ScrapePlan(ScrapeStrategy.DOCUMENT, final_ct, final_ct, final_cd),
                    phase="get",
                )
            if any(final_ct.startswith(t) for t in _JSON_CONTENT_TYPES) or _is_json(resp.text):
                return _log_scrape_plan(
                    url,
                    ScrapePlan(ScrapeStrategy.JSON, final_ct or "json-by-body-sniff"),
                    phase="get",
                )
            if any(final_ct.startswith(t) for t in _IMAGE_CONTENT_TYPES):
                return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.IMAGE, final_ct), phase="get")
            if any(final_ct.startswith(t) for t in _BINARY_CONTENT_TYPES):
                return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.SKIP, f"binary on GET: {final_ct}"), phase="get")

            # Analyse HTML content
            html = resp.text
            size = len(html)
            script_tags = len(re.findall(r"<script", html, re.I))
            text = _extract_text_from_html(html)
            text_chars = len(text)
            lower = html.lower()

            # Thin content: paywall, login wall, or near-empty page
            if text_chars < ROUTING_HEURISTICS.short_html_text_chars:
                return _plan_short_html_page(
                    url,
                    size=size,
                    text_chars=text_chars,
                    script_tags=script_tags,
                    lower_html=lower,
                    html=html,
                )

            plan = _plan_low_text_html(
                url,
                size=size,
                text_chars=text_chars,
                script_tags=script_tags,
            )
            if plan:
                return plan

            # Soft 404 check
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
            title_text = title_match.group(1).lower() if title_match else ""
            plan = _plan_soft_404(url, title_text=title_text, lower_html=lower, text_chars=text_chars)
            if plan:
                return plan

            return _log_scrape_plan(
                url,
                ScrapePlan(ScrapeStrategy.HTML_FAST, f"static HTML ({size} chars, {text_chars} text chars)"),
                size=size,
                text_chars=text_chars,
                script_tags=script_tags,
            )

    except httpx.TimeoutException:
        return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.SKIP, "timeout during validation"))
    except Exception as e:
        # If validation itself fails, let the scraper try anyway
        return _log_scrape_plan(
            url,
            ScrapePlan(ScrapeStrategy.HTML_BROWSER, f"validation error ({e}) — attempting browser scrape"),
        )


async def _validate_url(url: str, allowed_domains: Optional[frozenset] = None) -> Tuple[str, str]:
    """Compatibility wrapper returning the legacy ``(verdict, detail)`` tuple."""
    plan = await _build_scrape_plan(url, allowed_domains=allowed_domains)
    return plan.strategy, plan.reason


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
        content_filter=BM25ContentFilter(
            user_query=query,
            bm25_threshold=ROUTING_HEURISTICS.bm25_threshold,
        )
        if query
        else None
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
            config=_quiet_browser_config(),
            crawler_strategy=AsyncHTTPCrawlerStrategy(),
        ) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)
    except Exception:
        return await _scrape_html_browser(url, wait_for=None, query=query, vision_model=vision_model)

    if not result.success:
        return await _scrape_html_browser(url, wait_for=None, query=query, vision_model=vision_model)

    md = result.markdown
    content = _pick_markdown(md, query)
    content = _append_internal_links(content, result)

    # If HTTP strategy returned thin content, the page likely needs JS
    if len(content.strip()) < ROUTING_HEURISTICS.html_fast_thin_content_chars:
        return await _scrape_html_browser(url, wait_for=None, query=query, vision_model=vision_model)

    title = (result.metadata or {}).get("title", "")
    return content, title, None


async def _scrape_html_fast_query_agnostic(
    url: str,
    *,
    wait_for: Optional[str] = None,
    vision_model: Optional[str] = None,
) -> Tuple[SourceArtifact, Optional[str]]:
    """Fetch broad HTML content suitable for cross-query reuse."""
    from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        exclude_all_images=True,
        remove_overlay_elements=True,
        markdown_generator=DefaultMarkdownGenerator(),
        verbose=False,
    )

    try:
        async with AsyncWebCrawler(
            config=_quiet_browser_config(),
            crawler_strategy=AsyncHTTPCrawlerStrategy(),
        ) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)
    except Exception:
        return await _scrape_html_browser_query_agnostic(
            url,
            wait_for=wait_for,
            vision_model=vision_model,
        )

    if not result.success:
        return await _scrape_html_browser_query_agnostic(
            url,
            wait_for=wait_for,
            vision_model=vision_model,
        )

    content = _pick_markdown_query_agnostic(result.markdown)
    content = _append_internal_links(content, result)
    if len(content.strip()) < ROUTING_HEURISTICS.html_fast_thin_content_chars:
        return await _scrape_html_browser_query_agnostic(
            url,
            wait_for=wait_for,
            vision_model=vision_model,
        )

    return SourceArtifact(
        kind="text",
        title=(result.metadata or {}).get("title", ""),
        text_content=content,
    ), None


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

    for item in links:
        href = item.get("href", "") if isinstance(item, dict) else getattr(item, "href", "")
        text = (item.get("text", "") if isinstance(item, dict) else getattr(item, "text", "")).strip()
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


def _pick_markdown_query_agnostic(md) -> str:
    """Extract the broadest markdown available for cacheable source text."""
    if hasattr(md, "raw_markdown"):
        return getattr(md, "markdown_with_citations", None) or md.raw_markdown
    return str(md) if md else ""


def _truncate_content(content: str, max_content_chars: int) -> str:
    """Apply the public truncation contract after content materialization."""
    if len(content) > max_content_chars:
        return content[:max_content_chars] + f"\n\n[Truncated at {max_content_chars:,} chars]"
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
        content_filter=BM25ContentFilter(
            user_query=query,
            bm25_threshold=ROUTING_HEURISTICS.bm25_threshold,
        )
        if query
        else None
    )
    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        exclude_all_images=True,
        remove_overlay_elements=True,
        markdown_generator=md_generator,
        wait_until="networkidle",
        page_timeout=ROUTING_HEURISTICS.browser_page_timeout_ms,
        delay_before_return_html=ROUTING_HEURISTICS.browser_delay_before_return_html_s,
        verbose=False,
    )
    if wait_for:
        run_cfg.wait_for = wait_for

    _browser_cfg = _quiet_browser_config(
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
            return await _scrape_document(url, query=query, vision_model=vision_model)
        raise

    # If wait_for caused timeout, retry without it
    if not result.success and wait_for:
        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            exclude_all_images=True,
            remove_overlay_elements=True,
            markdown_generator=md_generator,
            wait_until="networkidle",
            page_timeout=ROUTING_HEURISTICS.browser_page_timeout_ms,
            delay_before_return_html=ROUTING_HEURISTICS.browser_delay_before_return_html_s,
            verbose=False,
        )
        try:
            async with AsyncWebCrawler(config=_browser_cfg) as crawler:
                result = await crawler.arun(url=url, config=run_cfg)
        except Exception as e:
            return "", "", f"Browser retry failed: {e}"

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
            return await _scrape_via_vision(url, query=query, vision_model=vision_model)
        return "", "", "Page loaded but returned empty or 404 content"

    title = (result.metadata or {}).get("title", "")
    return content, title, None


async def _scrape_html_browser_query_agnostic(
    url: str,
    *,
    wait_for: Optional[str] = None,
    vision_model: Optional[str] = None,
) -> Tuple[SourceArtifact, Optional[str]]:
    """Fetch broad browser-rendered content suitable for cross-query reuse."""
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        exclude_all_images=True,
        remove_overlay_elements=True,
        markdown_generator=DefaultMarkdownGenerator(),
        wait_until="networkidle",
        page_timeout=ROUTING_HEURISTICS.browser_page_timeout_ms,
        delay_before_return_html=ROUTING_HEURISTICS.browser_delay_before_return_html_s,
        verbose=False,
    )
    if wait_for:
        run_cfg.wait_for = wait_for

    browser_cfg = _quiet_browser_config(
        headless=True,
        enable_stealth=True,
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    try:
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)
    except Exception as crawl_err:
        if "Download is starting" in str(crawl_err):
            artifact, _, error = await _fetch_document_source_artifact(
                url,
                vision_model=vision_model,
                max_pdf_pages=_PDF_MAX_PAGES_DEFAULT,
            )
            return artifact, error
        raise

    if not result.success and wait_for:
        retry_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            exclude_all_images=True,
            remove_overlay_elements=True,
            markdown_generator=DefaultMarkdownGenerator(),
            wait_until="networkidle",
            page_timeout=ROUTING_HEURISTICS.browser_page_timeout_ms,
            delay_before_return_html=ROUTING_HEURISTICS.browser_delay_before_return_html_s,
            verbose=False,
        )
        try:
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                result = await crawler.arun(url=url, config=retry_cfg)
        except Exception as e:
            return SourceArtifact(kind="text", title=""), f"Browser retry failed: {e}"

    if not result.success:
        return SourceArtifact(kind="text", title=""), result.error_message or "Crawl failed"

    content = _pick_markdown_query_agnostic(result.markdown)
    content = _append_internal_links(content, result)
    lower = content.lower()
    if not content.strip() or (
        any(p in lower for p in ["page not found", "was not found", "no longer exists", "404 error page"])
        and "404" in lower
    ):
        if vision_model:
            try:
                screenshot_bytes = await _capture_page_screenshot(url)
            except Exception as e:
                return SourceArtifact(kind="text", title=""), f"Vision fallback failed: {e}"
            return SourceArtifact(
                kind="binary",
                title=(result.metadata or {}).get("title", ""),
                binary_bytes=screenshot_bytes,
                mime_type="application/x-page-screenshot",
            ), None
        return SourceArtifact(kind="text", title=""), "Page loaded but returned empty or 404 content"

    return SourceArtifact(
        kind="text",
        title=(result.metadata or {}).get("title", ""),
        text_content=content,
    ), None


async def _capture_page_screenshot(url: str) -> bytes:
    """Capture a viewport screenshot of a URL without performing any extraction."""
    import base64
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=_FETCH_HEADERS["User-Agent"],
            )
            try:
                page = await context.new_page()
                try:
                    await page.goto(
                        url,
                        timeout=ROUTING_HEURISTICS.vision_goto_timeout_ms,
                        wait_until="networkidle",
                    )
                    await page.wait_for_timeout(ROUTING_HEURISTICS.vision_settle_wait_ms)
                except Exception:
                    pass
                return await page.screenshot(type="png")
            finally:
                await context.close()
        finally:
            await browser.close()


async def _vision_extract_image_bytes(
    *,
    image_bytes: bytes,
    mime_type: str,
    query: str,
    vision_model: str,
    prompt_prefix: str,
) -> Tuple[str, Optional[str]]:
    """Run vision extraction on already-fetched image bytes."""
    import base64
    import litellm

    try:
        image_b64 = base64.b64encode(image_bytes).decode()
        query_clause = f" relevant to: {query}" if query else ""
        prompt = prompt_prefix.format(query_clause=query_clause)
        response = await litellm.acompletion(
            model=vision_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                ],
            }],
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            return "", "Vision extraction returned empty content"
        return content, None
    except Exception as e:
        return "", f"Vision fallback failed: {e}"


async def _vision_extract_pdf_bytes(
    *,
    pdf_bytes: bytes,
    query: str,
    vision_model: str,
) -> Tuple[str, Optional[str]]:
    """Run vision extraction on cached PDF bytes by rasterizing the first page."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        if len(pdf) == 0:
            return "", "Vision extraction returned empty content"
        page = pdf[0]
        try:
            bitmap = page.render(scale=2)
            pil_image = bitmap.to_pil()
        finally:
            page.close()
    finally:
        pdf.close()

    import io

    buffer = io.BytesIO()
    pil_image.save(buffer, format="PNG")
    return await _vision_extract_image_bytes(
        image_bytes=buffer.getvalue(),
        mime_type="image/png",
        query=query,
        vision_model=vision_model,
        prompt_prefix=(
            "Extract all useful information{query_clause} from the first page of this PDF rendered as an image. "
            "If it contains text, tables, charts, maps, labels, legends, or numeric values, capture them precisely. "
            "Return clean plain text or markdown."
        ),
    )


async def _scrape_via_vision(url: str, query: str, vision_model: str) -> Tuple[str, str, Optional[str]]:
    """Screenshot fallback using a vision LLM when text extraction fails."""
    try:
        screenshot_bytes = await _capture_page_screenshot(url)
    except Exception as e:
        return "", "", f"Vision fallback failed: {e}"

    content, error = await _vision_extract_image_bytes(
        image_bytes=screenshot_bytes,
        mime_type="image/png",
        query=query,
        vision_model=vision_model,
        prompt_prefix=(
            "Extract all text content{query_clause} from this page screenshot. "
            "Return the content as clean plain text or markdown. "
            "Include specific facts, numbers, names, and data. "
            "Exclude navigation bars and footers."
        ),
    )
    if error:
        return "", "", error
    if len(content) < 400:
        return "", "", f"Vision extraction returned too little content ({len(content)} chars — page likely blocked)"
    return content, "", None


async def _scrape_json(url: str) -> Tuple[str, str, Optional[str]]:
    """Fetch a JSON endpoint and return a trimmed markdown representation."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=ROUTING_HEURISTICS.image_json_timeout_s,
            headers=_FETCH_HEADERS,
        ) as client:
            resp = await client.get(url)
        resp.raise_for_status()

        try:
            data = resp.json()
        except Exception:
            data = json.loads(resp.text)

        trimmed = _trim_json_value(data)
        if isinstance(data, dict):
            summary = f"Top-level object with {len(data)} keys."
            extra = "Keys: " + ", ".join(map(str, list(data.keys())[:20]))
        elif isinstance(data, list):
            summary = f"Top-level array with {len(data)} items."
            extra = ""
        else:
            summary = f"Top-level scalar of type {type(data).__name__}."
            extra = ""

        content = f"JSON extracted from {url}\n\n{summary}"
        if extra:
            content += f"\n{extra}"
        content += "\n\n```json\n" + json.dumps(trimmed, ensure_ascii=False, indent=2) + "\n```"
        title = url.rsplit("/", 1)[-1] or "JSON endpoint"
        return content, title, None
    except Exception as e:
        return "", "", f"JSON extraction failed: {e}"


async def _scrape_image(url: str, query: str = "", vision_model: Optional[str] = None) -> Tuple[str, str, Optional[str]]:
    """Fetch an image URL and extract visible information with a vision model."""
    if not vision_model:
        return "", "", "Image URL requires vision_model for extraction"

    import base64

    import litellm

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=ROUTING_HEURISTICS.image_json_timeout_s,
            headers=_FETCH_HEADERS,
        ) as client:
            resp = await client.get(url)
        resp.raise_for_status()

        content_type = _normalize_content_type(resp.headers.get("content-type", "")) or mimetypes.guess_type(url)[0] or "image/png"
        image_b64 = base64.b64encode(resp.content).decode()
        query_clause = f" relevant to: {query}" if query else ""
        prompt = (
            f"Extract all useful information{query_clause} from this image. "
            "If it contains text, tables, charts, maps, labels, legends, or numeric values, capture them precisely. "
            "Return clean plain text or markdown."
        )
        response = await litellm.acompletion(
            model=vision_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{image_b64}"}},
                ],
            }],
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            return "", "", "Vision extraction returned empty content"
        title = url.rsplit("/", 1)[-1] or "Image"
        return content, title, None
    except Exception as e:
        return "", "", f"Image extraction failed: {e}"


async def _download_image_bytes(url: str) -> Tuple[Optional[bytes], str, Optional[str]]:
    """Download raw image bytes for session caching."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=ROUTING_HEURISTICS.image_json_timeout_s,
            headers=_FETCH_HEADERS,
        ) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        mime_type = (
            _normalize_content_type(resp.headers.get("content-type", ""))
            or mimetypes.guess_type(url)[0]
            or "image/png"
        )
        return resp.content, mime_type, None
    except Exception as e:
        return None, "", f"Image extraction failed: {e}"


# ---------------------------------------------------------------------------
# Document scraping via docling
# ---------------------------------------------------------------------------

async def _download_pdf_via_browser(url: str) -> Optional[bytes]:
    """Download a PDF using a real Chromium browser via Playwright.

    Used as a fallback when the plain httpx download is blocked (e.g. Akamai
    bot-protection returns 403 to plain HTTP clients but serves the file to
    real browsers).  Returns raw PDF bytes, or ``None`` on failure.
    """
    import tempfile

    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    user_agent=_FETCH_HEADERS["User-Agent"],
                    accept_downloads=True,
                )
                try:
                    page = await context.new_page()
                    tmp: Optional[str] = None
                    try:
                        async with page.expect_download(
                            timeout=ROUTING_HEURISTICS.browser_download_timeout_ms
                        ) as dl_info:
                            try:
                                await page.goto(
                                    url,
                                    timeout=ROUTING_HEURISTICS.browser_download_timeout_ms,
                                )
                            except Exception:
                                pass  # "Download is starting" error is expected
                        download = await dl_info.value
                        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                            tmp = f.name
                        await download.save_as(tmp)
                        with open(tmp, "rb") as f:
                            return f.read()
                    finally:
                        if tmp and os.path.exists(tmp):
                            os.unlink(tmp)
                finally:
                    await context.close()
            finally:
                await browser.close()
    except Exception:
        return None


def _download_binary_via_urllib(url: str) -> tuple[bytes, str]:
    """Download raw bytes while tolerating broken content-encoding headers."""
    req = Request(url, headers=_FETCH_HEADERS)
    with urlopen(req, timeout=_URLLIB_DOWNLOAD_TIMEOUT) as resp:
        content_type = resp.headers.get("content-type", "?")
        return resp.read(), content_type


def _format_exception(e: Exception) -> str:
    message = str(e).strip()
    return f"{type(e).__name__}: {message}" if message else type(e).__name__


async def _download_pdf_bytes(url: str) -> Tuple[Optional[bytes], Optional[str]]:
    """Download PDF bytes with progressively more tolerant fallbacks."""
    httpx_error: Optional[Exception] = None
    urllib_error: Optional[Exception] = None

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=_DOCUMENT_DOWNLOAD_TIMEOUT, headers=_FETCH_HEADERS
        ) as client:
            for attempt in range(_PDF_DOWNLOAD_RETRIES):
                try:
                    dl_resp = await client.get(url)
                    dl_resp.raise_for_status()
                    pdf_bytes = dl_resp.content
                    if pdf_bytes[:4] != b"%PDF":
                        return None, f"URL did not return a PDF (got {dl_resp.headers.get('content-type','?')})"
                    return pdf_bytes, None
                except Exception as e:
                    httpx_error = e
                    if attempt < _PDF_DOWNLOAD_RETRIES - 1:
                        await asyncio.sleep(1.0 * (attempt + 1))
    except Exception as e:
        httpx_error = e

    for attempt in range(_PDF_DOWNLOAD_RETRIES):
        try:
            pdf_bytes, content_type = await asyncio.to_thread(_download_binary_via_urllib, url)
            if pdf_bytes[:4] != b"%PDF":
                return None, f"URL did not return a PDF (got {content_type})"
            return pdf_bytes, None
        except Exception as e:
            urllib_error = e
            if attempt < _PDF_DOWNLOAD_RETRIES - 1:
                await asyncio.sleep(1.0 * (attempt + 1))

    pdf_bytes = await _download_pdf_via_browser(url)
    if pdf_bytes is None:
        return (
            None,
            f"PDF inaccessible (httpx: {_format_exception(httpx_error) if httpx_error else '?'}; raw-byte: {_format_exception(urllib_error) if urllib_error else '?'}; browser download also failed)",
        )
    return pdf_bytes, None


async def _scrape_document(url: str, query: str = "", vision_model: Optional[str] = None, max_pdf_pages: int = _PDF_MAX_PAGES_DEFAULT, known_content_type: str = "", known_content_disposition: str = "") -> Tuple[str, str, Optional[str]]:
    """Extract content from a document (PDF, DOCX, PPTX, XLSX) via docling.

    PDFs use a fast text-layer path (no OCR, no table detection) and are
    limited to the first ``_PDF_PAGE_RANGE`` pages to avoid timeouts on
    large reports.
    """
    title = url.rsplit("/", 1)[-1] or "Document"
    url_lower = url.lower().split("?")[0]
    is_pdf = _looks_like_pdf_resource(url, known_content_type, known_content_disposition)

    if not is_pdf and not (known_content_type or known_content_disposition):
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=_VALIDATION_TIMEOUT, headers=_FETCH_HEADERS
            ) as client:
                head = await client.head(url)
            is_pdf = _looks_like_pdf_resource(
                url,
                head.headers.get("content-type", ""),
                head.headers.get("content-disposition", ""),
            )
        except Exception:
            is_pdf = url_lower.endswith(".pdf")

    if is_pdf:
        pdf_bytes, error = await _download_pdf_bytes(url)
        if error:
            return "", "", error

        from docling_core.types.io import DocumentStream

        def _convert_fast(pdf_bytes: bytes = pdf_bytes):
            import gc
            import io as _io

            converter = _get_pdf_docling_converter()
            filename = url.rsplit("/", 1)[-1].split("?")[0] or "document.pdf"
            source = DocumentStream(name=filename, stream=_io.BytesIO(pdf_bytes))
            result = converter.convert(
                source,
                page_range=(1, max_pdf_pages),
            )
            # Extract markdown before releasing references — pypdfium2 child
            # objects (pages) must be GC'd before their parent PdfDocument.
            # Explicit deletion + gc.collect() enforces that order.
            markdown = result.document.export_to_markdown()
            del result
            gc.collect()
            return markdown

        content = await asyncio.to_thread(_convert_fast)
        content = _append_internal_links(content, None)

        if len(content.strip()) < _MIN_PDF_TEXT_CHARS:
            if vision_model:
                return await _scrape_via_vision(url, query=query, vision_model=vision_model)
            return (
                f"[Image-only or scanned PDF — no extractable text layer. "
                f"Extracted {len(content.strip())} chars. URL: {url}]",
                title,
                None,
            )
        return content, title, None

    # Non-PDF documents (DOCX, PPTX, XLSX)
    from docling.document_converter import DocumentConverter

    def _convert():
        converter = DocumentConverter()
        result = converter.convert(url)
        return result.document.export_to_markdown()

    try:
        content = await asyncio.to_thread(_convert)
    except Exception as e:
        return "", "", f"Document conversion failed: {e}"
    content = _append_internal_links(content, None)
    return content, title, None


async def _fetch_document_source_artifact(
    url: str,
    *,
    vision_model: Optional[str],
    max_pdf_pages: int,
    known_content_type: str = "",
    known_content_disposition: str = "",
) -> Tuple[SourceArtifact, str, Optional[str]]:
    """Fetch a query-agnostic cacheable artifact for a document URL."""
    title = url.rsplit("/", 1)[-1] or "Document"
    is_pdf = _looks_like_pdf_resource(url, known_content_type, known_content_disposition)

    if not is_pdf and not (known_content_type or known_content_disposition):
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=_VALIDATION_TIMEOUT, headers=_FETCH_HEADERS
            ) as client:
                head = await client.head(url)
            is_pdf = _looks_like_pdf_resource(
                url,
                head.headers.get("content-type", ""),
                head.headers.get("content-disposition", ""),
            )
        except Exception:
            is_pdf = url.lower().split("?")[0].endswith(".pdf")

    if is_pdf:
        pdf_bytes, error = await _download_pdf_bytes(url)
        if error or not pdf_bytes:
            return SourceArtifact(kind="text", title=title), title, error or "PDF returned empty content"

        from docling_core.types.io import DocumentStream

        def _convert_fast(pdf_bytes: bytes = pdf_bytes):
            import gc
            import io as _io

            converter = _get_pdf_docling_converter()
            filename = url.rsplit("/", 1)[-1].split("?")[0] or "document.pdf"
            source = DocumentStream(name=filename, stream=_io.BytesIO(pdf_bytes))
            result = converter.convert(source, page_range=(1, max_pdf_pages))
            markdown = result.document.export_to_markdown()
            del result
            gc.collect()
            return markdown

        content = await asyncio.to_thread(_convert_fast)
        content = _append_internal_links(content, None)
        if len(content.strip()) < _MIN_PDF_TEXT_CHARS:
            if vision_model:
                return SourceArtifact(
                    kind="binary",
                    title=title,
                    binary_bytes=pdf_bytes,
                    mime_type="application/pdf",
                ), title, None
            return SourceArtifact(kind="text", title=title), title, (
                f"[Image-only or scanned PDF — no extractable text layer. "
                f"Extracted {len(content.strip())} chars. URL: {url}]"
            )
        return SourceArtifact(kind="text", title=title, text_content=content), title, None

    from docling.document_converter import DocumentConverter

    def _convert():
        converter = DocumentConverter()
        result = converter.convert(url)
        return result.document.export_to_markdown()

    try:
        content = await asyncio.to_thread(_convert)
    except Exception as e:
        return SourceArtifact(kind="text", title=title), title, f"Document conversion failed: {e}"
    content = _append_internal_links(content, None)
    return SourceArtifact(kind="text", title=title, text_content=content), title, None


async def fetch_query_agnostic_source_artifact(
    url: str,
    *,
    wait_for: Optional[str] = None,
    vision_model: Optional[str] = None,
    allowed_domains: Optional[frozenset] = None,
    max_pdf_pages: int = _PDF_MAX_PAGES_DEFAULT,
) -> Tuple[Optional[SourceArtifact], Optional[str], ScrapeStrategy]:
    """Fetch a query-agnostic source artifact suitable for session caching."""
    plan = await _build_scrape_plan(url, allowed_domains=allowed_domains)
    if plan.strategy == ScrapeStrategy.SKIP:
        return None, f"Skipped: {plan.reason}", plan.strategy

    try:
        if plan.strategy == ScrapeStrategy.DOCUMENT:
            artifact, title, error = await _fetch_document_source_artifact(
                url,
                vision_model=vision_model,
                max_pdf_pages=max_pdf_pages,
                known_content_type=plan.content_type,
                known_content_disposition=plan.content_disposition,
            )
        elif plan.strategy == ScrapeStrategy.JSON:
            content, title, error = await _scrape_json(url)
            artifact = SourceArtifact(kind="text", title=title or "", text_content=content)
        elif plan.strategy == ScrapeStrategy.IMAGE:
            image_bytes, mime_type, error = await _download_image_bytes(url)
            title = url.rsplit("/", 1)[-1] or "Image"
            artifact = SourceArtifact(
                kind="binary",
                title=title,
                binary_bytes=image_bytes or b"",
                mime_type=mime_type,
            )
        elif plan.strategy == ScrapeStrategy.HTML_FAST:
            artifact, error = await _scrape_html_fast_query_agnostic(
                url,
                wait_for=wait_for,
                vision_model=vision_model,
            )
            title = artifact.title
        else:
            artifact, error = await _scrape_html_browser_query_agnostic(
                url,
                wait_for=wait_for,
                vision_model=vision_model,
            )
            title = artifact.title
    except Exception as e:
        logger.error("[scrape] failed %s: %s", url, e)
        return None, str(e), plan.strategy

    if error:
        if plan.likely_bot_detected:
            logger.info("[scrape] bot_detected %s", url)
            return None, f"bot_detected: {error}", plan.strategy
        return None, error, plan.strategy

    if artifact is None:
        return None, "Extraction returned empty content", plan.strategy

    if artifact.kind == "text" and not artifact.text_content.strip():
        if plan.likely_bot_detected:
            logger.info("[scrape] bot_detected %s", url)
            return None, "bot_detected: Browser loaded page but returned empty content", plan.strategy
        return None, "Extraction returned empty content", plan.strategy

    if artifact.kind == "binary" and not artifact.binary_bytes:
        return None, "Extraction returned empty content", plan.strategy

    return artifact, None, plan.strategy


async def materialize_source_artifact(
    artifact: SourceArtifact,
    *,
    query: str,
    vision_model: Optional[str],
    max_content_chars: int,
) -> Tuple[str, str, Optional[str]]:
    """Turn a cached source artifact into the current query's scrape result."""
    title = artifact.title
    if artifact.kind == "text":
        return _truncate_content(artifact.text_content, max_content_chars), title, None

    if not vision_model:
        return "", title, "Binary source requires vision_model for extraction"

    if artifact.mime_type == "application/pdf":
        content, error = await _vision_extract_pdf_bytes(
            pdf_bytes=artifact.binary_bytes,
            query=query,
            vision_model=vision_model,
        )
    else:
        mime_type = (
            "image/png"
            if artifact.mime_type == "application/x-page-screenshot"
            else artifact.mime_type or "image/png"
        )
        content, error = await _vision_extract_image_bytes(
            image_bytes=artifact.binary_bytes,
            mime_type=mime_type,
            query=query,
            vision_model=vision_model,
            prompt_prefix=(
                "Extract all useful information{query_clause} from this image. "
                "If it contains text, tables, charts, maps, labels, legends, or numeric values, capture them precisely. "
                "Return clean plain text or markdown."
            ),
        )
    if error:
        return "", title, error
    if artifact.mime_type == "application/x-page-screenshot" and len(content) < 400:
        return "", title, f"Vision extraction returned too little content ({len(content)} chars — page likely blocked)"
    return _truncate_content(content, max_content_chars), title, None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def scrape_url(
    url: str,
    wait_for: Optional[str] = None,
    query: str = "",
    vision_model: Optional[str] = None,
    allowed_domains: Optional[frozenset] = None,
    max_pdf_pages: int = _PDF_MAX_PAGES_DEFAULT,
    max_content_chars: int = MAX_CONTENT_CHARS,
) -> Tuple[str, str, Optional[str]]:
    """Scrape a URL and return clean markdown content.

    The routing flow is:

    1. Build a ``ScrapePlan`` from cheap validation (HEAD + fast GET).
    2. Execute the handler chosen by the plan's strategy.
    3. Normalize bot-detection, empty-content, and truncation behavior.

    Args:
        url: The URL to scrape.
        wait_for: Optional CSS selector for JS-rendered pages.
        query: Optional search query for BM25 content filtering.
        allowed_domains: Frozenset of domain strings (e.g. ``frozenset({"reddit.com"})``)
            to remove from the default blocked-domain list. ``None`` uses the full block list.
        max_pdf_pages: Maximum number of pages to extract from PDFs. Defaults to 50.
        max_content_chars: Maximum characters to return per page. Defaults to 30,000.

    Returns:
        Tuple of ``(markdown_content, page_title, error_or_none)``.
    """
    plan = await _build_scrape_plan(url, allowed_domains=allowed_domains)

    if plan.strategy == ScrapeStrategy.SKIP:
        return "", "", f"Skipped: {plan.reason}"

    try:
        if plan.strategy == ScrapeStrategy.DOCUMENT:
            content, title, error = await _scrape_document(
                url,
                query=query,
                vision_model=vision_model,
                max_pdf_pages=max_pdf_pages,
                known_content_type=plan.content_type,
                known_content_disposition=plan.content_disposition,
            )
        elif plan.strategy == ScrapeStrategy.JSON:
            content, title, error = await _scrape_json(url)
        elif plan.strategy == ScrapeStrategy.IMAGE:
            content, title, error = await _scrape_image(url, query=query, vision_model=vision_model)
        elif plan.strategy == ScrapeStrategy.HTML_FAST:
            content, title, error = await _scrape_html_fast(url, query=query, vision_model=vision_model)
        else:
            content, title, error = await _scrape_html_browser(url, wait_for, query=query, vision_model=vision_model)
    except Exception as e:
        logger.error("[scrape] failed %s: %s", url, e)
        return "", "", str(e)

    if error:
        if plan.likely_bot_detected:
            logger.info("[scrape] bot_detected %s", url)
            return "", title or "", f"bot_detected: {error}"
        return "", title or "", error

    if not content.strip():
        if plan.likely_bot_detected:
            logger.info("[scrape] bot_detected %s", url)
            return "", title or "", "bot_detected: Browser loaded page but returned empty content"
        return "", title or "", "Extraction returned empty content"

    if len(content) > max_content_chars:
        content = content[:max_content_chars] + f"\n\n[Truncated at {max_content_chars:,} chars]"

    return content, title or "", None
