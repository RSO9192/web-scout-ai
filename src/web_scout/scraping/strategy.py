import asyncio
import base64
import json
import logging
import mimetypes
import os
import re
import threading
from typing import Any, Optional, Tuple
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

import httpx
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

from web_scout.config import ROUTING_HEURISTICS

from .constants import FETCH_HEADERS
from .page_classifier import looks_like_pdf_resource
from .plan import build_scrape_plan
from .types import ScrapeStrategy, SourceArtifact
from .utils import (
    looks_like_document_link,
    normalize_content_type,
    trim_json_value,
    unsupported_legacy_document_reason,
)

logger = logging.getLogger(__name__)


def _quiet_browser_config(**overrides: Any) -> BrowserConfig:
    """Build a crawl4ai BrowserConfig that suppresses its own startup banners."""
    return BrowserConfig(verbose=False, **overrides)


def _minimal_browser_run_config(**overrides: Any) -> CrawlerRunConfig:
    """Build a lightweight crawl4ai run config for browser-only side effects."""
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        exclude_all_images=True,
        verbose=False,
        **overrides,
    )


# ---------------------------------------------------------------------------
# HTML scraping — fast HTTP path (no browser)
# ---------------------------------------------------------------------------


async def scrape_html_fast(
    url: str, query: str = "", vision_model: Optional[str] = None
) -> Tuple[str, str, Optional[str]]:
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
        return await scrape_html_browser(url, wait_for=None, query=query, vision_model=vision_model)

    if not result.success:
        return await scrape_html_browser(url, wait_for=None, query=query, vision_model=vision_model)

    md = result.markdown
    content = _pick_markdown(md, query)
    content = _append_internal_links(content, result)

    # If HTTP strategy returned thin content, the page likely needs JS
    if len(content.strip()) < ROUTING_HEURISTICS.html_fast_thin_content_chars:
        return await scrape_html_browser(url, wait_for=None, query=query, vision_model=vision_model)

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
        return await scrape_html_browser_query_agnostic(
            url,
            wait_for=wait_for,
            vision_model=vision_model,
        )

    if not result.success:
        return await scrape_html_browser_query_agnostic(
            url,
            wait_for=wait_for,
            vision_model=vision_model,
        )

    content = _pick_markdown_query_agnostic(result.markdown)
    content = _append_internal_links(content, result)
    if len(content.strip()) < ROUTING_HEURISTICS.html_fast_thin_content_chars:
        return await scrape_html_browser_query_agnostic(
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


def _link_text_fallback(href: str) -> str:
    parsed = urlparse(href)
    tail = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1]) if parsed.path else ""
    if tail:
        return tail
    return parsed.netloc or href


def _append_internal_links(content: str, result, limit: int = 50) -> str:
    """Append a list of useful page links to the end of the markdown content.

    Preserve external links too, and keep icon-only document links by falling back
    to the URL as the label. This matters for repository/detail pages whose primary
    source links are rendered as file icons rather than visible anchor text.
    """
    links_data = getattr(result, "links", {})
    if isinstance(links_data, dict):
        links = list(links_data.get("internal", [])) + list(links_data.get("external", []))
    else:
        links = list(getattr(links_data, "internal", [])) + list(getattr(links_data, "external", []))

    link_lines = []

    # Also extract links directly from markdown via regex
    for match in re.finditer(r"\[([^\]]+)\]\((https?://[^\)]+)\)", content):
        text, href = match.groups()
        if text.strip() and href.strip() and text.lower() not in ("read more", "click here", "learn more"):
            clean_text = text.strip().replace("\n", " ")
            link_lines.append(f"- [{clean_text}]({href.strip()})")

    for item in links:
        href = item.get("href", "") if isinstance(item, dict) else getattr(item, "href", "")
        text = (item.get("text", "") if isinstance(item, dict) else getattr(item, "text", "")).strip()
        if not href:
            continue
        if text and text.lower() not in ("read more", "click here", "learn more"):
            link_lines.append(f"- [{text}]({href})")
            continue
        if looks_like_document_link(href):
            link_lines.append(f"- [{_link_text_fallback(href)}]({href})")

    if not link_lines:
        return content

    seen = set()
    unique_links = []
    for line in link_lines:
        href = line.rsplit("](", 1)[-1].rstrip(")")
        if href not in seen:
            seen.add(href)
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


async def scrape_html_browser(
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
            # TODO: Use FETCH_HEADERS from constants.py? Chrome version is different.
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
            return await scrape_document(url, query=query, vision_model=vision_model)
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
    if (
        query
        and hasattr(md, "fit_markdown")
        and len((md.fit_markdown or "").strip()) <= 20
        and hasattr(md, "raw_markdown")
    ):
        content = getattr(md, "markdown_with_citations", None) or md.raw_markdown

    content = _append_internal_links(content, result)

    lower = content.lower()
    if not content.strip() or (
        any(
            p in lower
            for p in [
                "page not found",
                "was not found",
                "no longer exists",
                "404 error page",
            ]
        )
        and "404" in lower
    ):
        if vision_model:
            return await _scrape_via_vision(url, query=query, vision_model=vision_model)
        return "", "", "Page loaded but returned empty or 404 content"

    title = (result.metadata or {}).get("title", "")
    return content, title, None


async def scrape_html_browser_query_agnostic(
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
                max_pdf_pages=ROUTING_HEURISTICS.pdf_max_pages_default,
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
        any(
            p in lower
            for p in [
                "page not found",
                "was not found",
                "no longer exists",
                "404 error page",
            ]
        )
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
    browser_cfg = _quiet_browser_config(
        headless=True,
        viewport={"width": 1280, "height": 900},
        user_agent=FETCH_HEADERS["User-Agent"],
    )
    run_cfg = _minimal_browser_run_config(
        screenshot=True,
        force_viewport_screenshot=True,
        screenshot_wait_for=ROUTING_HEURISTICS.vision_settle_wait_ms / 1000.0,
        wait_until="networkidle",
        page_timeout=ROUTING_HEURISTICS.vision_goto_timeout_ms,
    )
    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        result = await crawler.arun(url=url, config=run_cfg)

    if not result.screenshot:
        raise RuntimeError("Screenshot capture failed")
    return base64.b64decode(result.screenshot)


async def _vision_extract_image_bytes(
    *,
    image_bytes: bytes,
    mime_type: str,
    query: str,
    vision_model: str,
    prompt_prefix: str,
) -> Tuple[str, Optional[str]]:
    """Run vision extraction on already-fetched image bytes."""
    import litellm

    try:
        image_b64 = base64.b64encode(image_bytes).decode()
        query_clause = f" relevant to: {query}" if query else ""
        prompt = prompt_prefix.format(query_clause=query_clause)
        response = await litellm.acompletion(
            model=vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
                        },
                    ],
                }
            ],
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
        return (
            "",
            "",
            f"Vision extraction returned too little content ({len(content)} chars — page likely blocked)",
        )
    return content, "", None


async def scrape_json(url: str) -> Tuple[str, str, Optional[str]]:
    """Fetch a JSON endpoint and return a trimmed markdown representation."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=ROUTING_HEURISTICS.image_json_timeout_s,
            headers=FETCH_HEADERS,
        ) as client:
            resp = await client.get(url)
        resp.raise_for_status()

        try:
            data = resp.json()
        except Exception:
            data = json.loads(resp.text)

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

        content = f"JSON extracted from {url}\n\n{summary}"
        if extra:
            content += f"\n{extra}"
        content += "\n\n```json\n" + json.dumps(trimmed, ensure_ascii=False, indent=2) + "\n```"
        title = url.rsplit("/", 1)[-1] or "JSON endpoint"
        return content, title, None
    except Exception as e:
        return "", "", f"JSON extraction failed: {e}"


async def scrape_image(url: str, query: str = "", vision_model: Optional[str] = None) -> Tuple[str, str, Optional[str]]:
    """Fetch an image URL and extract visible information with a vision model."""
    if not vision_model:
        return "", "", "Image URL requires vision_model for extraction"

    import base64

    import litellm

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=ROUTING_HEURISTICS.image_json_timeout_s,
            headers=FETCH_HEADERS,
        ) as client:
            resp = await client.get(url)
        resp.raise_for_status()

        content_type = (
            normalize_content_type(resp.headers.get("content-type", "")) or mimetypes.guess_type(url)[0] or "image/png"
        )
        image_b64 = base64.b64encode(resp.content).decode()
        query_clause = f" relevant to: {query}" if query else ""
        prompt = (
            f"Extract all useful information{query_clause} from this image. "
            "If it contains text, tables, charts, maps, labels, legends, or numeric values, capture them precisely. "
            "Return clean plain text or markdown."
        )
        response = await litellm.acompletion(
            model=vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{content_type};base64,{image_b64}"},
                        },
                    ],
                }
            ],
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
            headers=FETCH_HEADERS,
        ) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        mime_type = (
            normalize_content_type(resp.headers.get("content-type", "")) or mimetypes.guess_type(url)[0] or "image/png"
        )
        return resp.content, mime_type, None
    except Exception as e:
        return None, "", f"Image extraction failed: {e}"


# ---------------------------------------------------------------------------
# Document scraping via docling
# ---------------------------------------------------------------------------


async def _download_pdf_via_browser(url: str) -> Optional[bytes]:
    """Download a PDF using crawl4ai's browser download handling.

    Used as a fallback when the plain httpx download is blocked (e.g. Akamai
    bot-protection returns 403 to plain HTTP clients but serves the file to
    real browsers).  Returns raw PDF bytes, or ``None`` on failure.
    """
    browser_cfg = _quiet_browser_config(
        headless=True,
        accept_downloads=True,
        user_agent=FETCH_HEADERS["User-Agent"],
    )
    run_cfg = _minimal_browser_run_config(
        page_timeout=ROUTING_HEURISTICS.browser_download_timeout_ms,
    )
    try:
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)
        if not result.downloaded_files:
            return None
        filepath = result.downloaded_files[0]
        try:
            with open(filepath, "rb") as f:
                return f.read()
        finally:
            if os.path.exists(filepath):
                os.unlink(filepath)
    except Exception:
        return None


def _download_binary_via_urllib(url: str) -> tuple[bytes, str]:
    """Download raw bytes while tolerating broken content-encoding headers."""
    req = Request(url, headers=FETCH_HEADERS)
    with urlopen(req, timeout=ROUTING_HEURISTICS.urllib_download_timeout) as resp:
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
            follow_redirects=True,
            timeout=ROUTING_HEURISTICS.document_download_timeout,
            headers=FETCH_HEADERS,
        ) as client:
            for attempt in range(ROUTING_HEURISTICS.pdf_download_retries):
                try:
                    dl_resp = await client.get(url)
                    dl_resp.raise_for_status()
                    pdf_bytes = dl_resp.content
                    if pdf_bytes[:4] != b"%PDF":
                        return (
                            None,
                            f"URL did not return a PDF (got {dl_resp.headers.get('content-type', '?')})",
                        )
                    return pdf_bytes, None
                except Exception as e:
                    httpx_error = e
                    if attempt < ROUTING_HEURISTICS.pdf_download_retries - 1:
                        await asyncio.sleep(1.0 * (attempt + 1))
    except Exception as e:
        httpx_error = e

    for attempt in range(ROUTING_HEURISTICS.pdf_download_retries):
        try:
            pdf_bytes, content_type = await asyncio.to_thread(_download_binary_via_urllib, url)
            if pdf_bytes[:4] != b"%PDF":
                return None, f"URL did not return a PDF (got {content_type})"
            return pdf_bytes, None
        except Exception as e:
            urllib_error = e
            if attempt < ROUTING_HEURISTICS.pdf_download_retries - 1:
                await asyncio.sleep(1.0 * (attempt + 1))

    pdf_bytes = await _download_pdf_via_browser(url)
    if pdf_bytes is None:
        return (
            None,
            (
                f"PDF inaccessible (httpx: {_format_exception(httpx_error) if httpx_error else '?'}; "
                f"raw-byte: {_format_exception(urllib_error) if urllib_error else '?'}; browser download also failed)"
            ),
        )
    return pdf_bytes, None


_PDF_DOCLING_CONVERTER = None
_PDF_DOCLING_CONVERTER_LOCK = threading.Lock()


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


async def scrape_document(
    url: str,
    query: str = "",
    vision_model: Optional[str] = None,
    max_pdf_pages: int = ROUTING_HEURISTICS.pdf_max_pages_default,
    known_content_type: str = "",
    known_content_disposition: str = "",
) -> Tuple[str, str, Optional[str]]:
    """Extract content from a document (PDF, DOCX, PPTX, XLSX) via docling.

    PDFs use a fast text-layer path (no OCR, no table detection) and are
    limited to the first ``_PDF_PAGE_RANGE`` pages to avoid timeouts on
    large reports.
    """
    title = url.rsplit("/", 1)[-1] or "Document"
    unsupported_reason = unsupported_legacy_document_reason(url, known_content_type, known_content_disposition)
    if unsupported_reason:
        return "", title, f"Skipped: {unsupported_reason}"
    url_lower = url.lower().split("?")[0]
    is_pdf = looks_like_pdf_resource(url, known_content_type, known_content_disposition)

    if not is_pdf and not (known_content_type or known_content_disposition):
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=ROUTING_HEURISTICS.validation_timeout,
                headers=FETCH_HEADERS,
            ) as client:
                head = await client.head(url)
            is_pdf = looks_like_pdf_resource(
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

        if len(content.strip()) < ROUTING_HEURISTICS.min_pdf_text_chars:
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
    unsupported_reason = unsupported_legacy_document_reason(url, known_content_type, known_content_disposition)
    if unsupported_reason:
        return (
            SourceArtifact(kind="text", title=title),
            title,
            f"Skipped: {unsupported_reason}",
        )
    is_pdf = looks_like_pdf_resource(url, known_content_type, known_content_disposition)

    if not is_pdf and not (known_content_type or known_content_disposition):
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=ROUTING_HEURISTICS.validation_timeout,
                headers=FETCH_HEADERS,
            ) as client:
                head = await client.head(url)
            is_pdf = looks_like_pdf_resource(
                url,
                head.headers.get("content-type", ""),
                head.headers.get("content-disposition", ""),
            )
        except Exception:
            is_pdf = url.lower().split("?")[0].endswith(".pdf")

    if is_pdf:
        pdf_bytes, error = await _download_pdf_bytes(url)
        if error or not pdf_bytes:
            return (
                SourceArtifact(kind="text", title=title),
                title,
                error or "PDF returned empty content",
            )

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
        if len(content.strip()) < ROUTING_HEURISTICS.min_pdf_text_chars:
            if vision_model:
                return (
                    SourceArtifact(
                        kind="binary",
                        title=title,
                        binary_bytes=pdf_bytes,
                        mime_type="application/pdf",
                    ),
                    title,
                    None,
                )
            return (
                SourceArtifact(kind="text", title=title),
                title,
                (
                    f"[Image-only or scanned PDF — no extractable text layer. "
                    f"Extracted {len(content.strip())} chars. URL: {url}]"
                ),
            )
        return (
            SourceArtifact(kind="text", title=title, text_content=content),
            title,
            None,
        )

    from docling.document_converter import DocumentConverter

    def _convert():
        converter = DocumentConverter()
        result = converter.convert(url)
        return result.document.export_to_markdown()

    try:
        content = await asyncio.to_thread(_convert)
    except Exception as e:
        return (
            SourceArtifact(kind="text", title=title),
            title,
            f"Document conversion failed: {e}",
        )
    content = _append_internal_links(content, None)
    return SourceArtifact(kind="text", title=title, text_content=content), title, None


async def fetch_query_agnostic_source_artifact(
    url: str,
    *,
    wait_for: Optional[str] = None,
    vision_model: Optional[str] = None,
    allowed_domains: Optional[frozenset] = None,
    max_pdf_pages: int = ROUTING_HEURISTICS.pdf_max_pages_default,
) -> Tuple[Optional[SourceArtifact], Optional[str], ScrapeStrategy]:
    """Fetch a query-agnostic source artifact suitable for session caching."""
    plan = await build_scrape_plan(url, allowed_domains=allowed_domains)
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
            content, title, error = await scrape_json(url)
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
            artifact, error = await scrape_html_browser_query_agnostic(
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
            return (
                None,
                "bot_detected: Browser loaded page but returned empty content",
                plan.strategy,
            )
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
            "image/png" if artifact.mime_type == "application/x-page-screenshot" else artifact.mime_type or "image/png"
        )
        content, error = await _vision_extract_image_bytes(
            image_bytes=artifact.binary_bytes,
            mime_type=mime_type,
            query=query,
            vision_model=vision_model,
            prompt_prefix=(
                "Extract all useful information{query_clause} from this image. "
                "If it contains text, tables, charts, maps, labels, legends, or numeric values, capture them precisely."
                " Return clean plain text or markdown."
            ),
        )
    if error:
        return "", title, error
    if artifact.mime_type == "application/x-page-screenshot" and len(content) < 400:
        return (
            "",
            title,
            f"Vision extraction returned too little content ({len(content)} chars — page likely blocked)",
        )
    return _truncate_content(content, max_content_chars), title, None
