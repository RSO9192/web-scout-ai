import logging
import re
from dataclasses import replace as _dc_replace
from typing import Any, Optional, Tuple

from web_scout.config import ROUTING_HEURISTICS

from .constants import (
    BINARY_CONTENT_TYPES,
    IMAGE_CONTENT_TYPES,
    JSON_CONTENT_TYPES,
)
from .page_classifier import (
    classify_html_page_shape,
    looks_like_auth_wall,
    looks_like_document_resource,
    looks_like_html_body,
    looks_like_metadata_page,
)
from .types import ScrapePlan, ScrapeStrategy
from .utils import (
    extract_text_from_html,
    is_blocked_domain,
    is_json,
    normalize_content_type,
    sniff_document_payload,
    unsupported_legacy_document_reason,
)

logger = logging.getLogger(__name__)


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
    if (
        size < ROUTING_HEURISTICS.short_html_size_chars
        and script_tags >= ROUTING_HEURISTICS.short_html_spa_script_count
    ):
        return _log_scrape_plan(
            url,
            ScrapePlan(
                ScrapeStrategy.HTML_BROWSER,
                f"SPA shell ({size} chars, {script_tags} scripts)",
            ),
            size=size,
            text_chars=text_chars,
            script_tags=script_tags,
        )
    if looks_like_auth_wall(lower_html):
        return _log_scrape_plan(
            url,
            ScrapePlan(
                ScrapeStrategy.SKIP,
                f"auth/paywall wall ({text_chars} text chars from {size} chars HTML)",
            ),
            size=size,
            text_chars=text_chars,
        )
    if looks_like_metadata_page(html):
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
        ScrapePlan(
            ScrapeStrategy.HTML_FAST,
            f"short static HTML ({text_chars} text chars from {size} chars HTML)",
        ),
        size=size,
        text_chars=text_chars,
        script_tags=script_tags,
    )


def _plan_low_text_html(url: str, *, size: int, text_chars: int, script_tags: int) -> Optional[ScrapePlan]:
    """Route probable SPA shells without changing thresholds or precedence."""
    if (
        text_chars < ROUTING_HEURISTICS.low_text_spa_chars
        and script_tags >= ROUTING_HEURISTICS.low_text_spa_script_count
    ):
        return _log_scrape_plan(
            url,
            ScrapePlan(
                ScrapeStrategy.HTML_BROWSER,
                f"likely SPA ({size} chars HTML, {text_chars} text chars)",
            ),
            size=size,
            text_chars=text_chars,
            script_tags=script_tags,
        )
    if text_chars >= ROUTING_HEURISTICS.rich_html_static_text_chars:
        return None
    density = text_chars / size if size else 0.0
    if script_tags >= ROUTING_HEURISTICS.heavy_spa_script_count and density < ROUTING_HEURISTICS.heavy_spa_text_density:
        return _log_scrape_plan(
            url,
            ScrapePlan(
                ScrapeStrategy.HTML_BROWSER,
                f"likely SPA (density {density:.1%}, {script_tags} scripts)",
            ),
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
        for pattern in [
            "page not found",
            "404 error",
            "does not exist",
            "no longer available",
        ]
    ):
        return _log_scrape_plan(
            url,
            ScrapePlan(ScrapeStrategy.SKIP, "soft 404 in content"),
            text_chars=text_chars,
        )
    return None


def _classify_response(url: str, resp) -> ScrapePlan:
    """Classify an HTTP response into a ``ScrapePlan`` (no network I/O)."""
    if resp.status >= 400:
        return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.SKIP, f"HTTP {resp.status}"))

    ct = normalize_content_type(resp.headers.get("content-type", ""))
    cd = resp.headers.get("content-disposition", "")

    unsupported_reason = unsupported_legacy_document_reason(url, ct, cd)
    if unsupported_reason:
        return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.SKIP, unsupported_reason))
    if looks_like_document_resource(url, ct, cd):
        return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.DOCUMENT, ct, ct, cd))
    if sniff_document_payload(resp.body, content_type=ct, content_disposition=cd):
        return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.DOCUMENT, ct or "document-by-body-sniff", ct, cd))
    if any(ct.startswith(t) for t in JSON_CONTENT_TYPES):
        if is_json(resp.html_content):
            return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.JSON, ct or "json-by-body-sniff"))
        if looks_like_html_body(resp.html_content):
            logger.info("[scrape-plan] json-hint overridden by HTML body url=%s final_ct=%s", url, ct or "(empty)")
        else:
            return _log_scrape_plan(
                url,
                ScrapePlan(ScrapeStrategy.SKIP, f"JSON-like endpoint returned non-JSON payload ({ct or 'unknown content-type'})"),
            )
    if is_json(resp.html_content):
        return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.JSON, ct or "json-by-body-sniff"))
    if any(ct.startswith(t) for t in IMAGE_CONTENT_TYPES):
        return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.IMAGE, ct))
    if any(ct.startswith(t) for t in BINARY_CONTENT_TYPES):
        return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.SKIP, f"binary content-type: {ct}"))

    # Analyse HTML content
    html = resp.html_content
    size = len(html)
    script_tags = len(re.findall(r"<script", html, re.I))
    text = extract_text_from_html(html)
    text_chars = len(text)
    lower = html.lower()
    shape = classify_html_page_shape(html)

    # Thin content: paywall, login wall, or near-empty page
    if text_chars < ROUTING_HEURISTICS.short_html_text_chars:
        return _plan_short_html_page(url, size=size, text_chars=text_chars, script_tags=script_tags, lower_html=lower, html=html)

    if (
        shape.page_type == "record_page"
        and shape.record_score >= 5
        and shape.record_score >= shape.content_score + 2
        and text_chars < ROUTING_HEURISTICS.rich_html_static_text_chars
    ):
        return _log_scrape_plan(
            url,
            ScrapePlan(ScrapeStrategy.HTML_FAST, f"metadata-like HTML ({text_chars} text chars from {size} chars HTML)"),
            size=size,
            text_chars=text_chars,
            script_tags=script_tags,
            doc_links=shape.document_link_count,
            metadata_markers=shape.metadata_marker_count,
        )

    plan = _plan_low_text_html(url, size=size, text_chars=text_chars, script_tags=script_tags)
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


async def build_scrape_plan(url: str, allowed_domains: Optional[frozenset] = None) -> ScrapePlan:
    """Build a scrape routing plan for a URL before running any heavy extractor."""
    if is_blocked_domain(url, allowed_domains=allowed_domains):
        return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.SKIP, "blocked domain"))
    unsupported_reason = unsupported_legacy_document_reason(url)
    if unsupported_reason:
        return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.SKIP, unsupported_reason))
    if looks_like_document_resource(url):
        return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.DOCUMENT, "document-by-url"))

    try:
        from scrapling.fetchers import AsyncFetcher
        from ._scrapling import stealthy_fetch

        # Step 1: fast stealthy HTTP (TLS fingerprint spoofing + browser headers).
        resp = None
        try:
            resp = await AsyncFetcher.get(
                url,
                stealthy_headers=True,
                follow_redirects=True,
                timeout=ROUTING_HEURISTICS.validation_timeout,
            )
        except Exception as e:
            error_msg = str(e).lower()
            if "timeout" in error_msg or "timed out" in error_msg:
                return _log_scrape_plan(
                    url,
                    ScrapePlan(ScrapeStrategy.HTML_BROWSER, "GET timed out — attempting browser scrape", needs_browser=True),
                )
            # Network error — try the browser before giving up
            logger.debug("[scrape-plan] AsyncFetcher failed (%s), falling back to browser: %s", type(e).__name__, url)

        # Step 2: if AsyncFetcher was blocked (403/429/503) or failed entirely,
        # retry with StealthyFetcher so Cloudflare challenges are solved before
        # we attempt to classify the response.
        _BOT_STATUS_CODES = {403, 429, 503}
        used_browser = resp is None or resp.status in _BOT_STATUS_CODES
        if used_browser:
            reason = f"HTTP {resp.status}" if resp is not None else "fetch error"
            logger.info("[scrape-plan] bot-wall detected (%s), retrying with StealthyFetcher url=%s", reason, url)
            try:
                resp = await stealthy_fetch(
                    url,
                    headless=True,
                    network_idle=True,
                    solve_cloudflare=True,
                    timeout=ROUTING_HEURISTICS.browser_page_timeout_ms,
                )
            except Exception as e:
                return _log_scrape_plan(url, ScrapePlan(ScrapeStrategy.SKIP, f"browser GET failed: {type(e).__name__}"))

        plan = _classify_response(url, resp)

        # Propagate browser usage so the executor skips the fast HTTP path.
        if used_browser and plan.strategy != ScrapeStrategy.SKIP:
            plan = _dc_replace(plan, needs_browser=True)

        return plan

    except Exception as e:
        return _log_scrape_plan(
            url,
            ScrapePlan(ScrapeStrategy.HTML_BROWSER, f"validation error ({e}) — attempting browser scrape"),
        )


async def _validate_url(url: str, allowed_domains: Optional[frozenset] = None) -> Tuple[str, str]:
    """Compatibility wrapper returning the legacy ``(verdict, detail)`` tuple."""
    plan = await build_scrape_plan(url, allowed_domains=allowed_domains)
    return plan.strategy, plan.reason
