"""Agent tool factories for the web researcher.

Provides two ``@function_tool`` factories and the ``ResearchTracker``
that accumulates URL/query records from tool calls.

- ``create_web_search()``           — URL discovery via pluggable search backend,
                                      returns rich metadata (PAA, KG, date) when
                                      supported by the backend (e.g. Serper)
- ``create_scrape_and_extract()``   — scrapes a URL via a dedicated sub-agent
                                      (crawl4ai/docling) that extracts and comprehensively
                                      summarises content relevant to the query; the main agent
                                      sees this detailed extraction (~5000 chars)
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import litellm
from agents import Agent, ModelSettings, Runner, function_tool
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .models import SearchQuery, UrlEntry

logger = logging.getLogger(__name__)

_TRANSIENT_LLM_ERRORS = (
    litellm.ServiceUnavailableError,
    litellm.RateLimitError,
    litellm.APIConnectionError,
    litellm.BadGatewayError,
)
_RETRY_DELAYS = (1.0, 2.0, 4.0)

_THIN_CONTENT_CHARS = 500
_MAX_INTERACTIVE_CLICKS = 5

# JavaScript that queries all visible interactive elements on a page.
# Returns a list of {tag, text} objects in a stable, deduplicated order.
_GET_ELEMENTS_JS = """
(() => {
    const all = [];
    const seen = new Set();
    document.querySelectorAll('button, [role="tab"], [role="button"], select').forEach(el => {
        if (el.offsetParent === null || el.disabled) return;
        const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
        if (!text) return;
        const key = text.slice(0, 60);
        if (seen.has(key)) return;
        seen.add(key);
        all.push({tag: el.getAttribute('role') || el.tagName.toLowerCase(), text: key});
    });
    document.querySelectorAll('a').forEach(el => {
        if (el.offsetParent === null) return;
        const text = (el.innerText || '').trim();
        if (!/load more|show more|show all|expand|next|view all/i.test(text)) return;
        const key = text.slice(0, 60);
        if (seen.has(key)) return;
        seen.add(key);
        all.push({tag: 'a', text: key});
    });
    return all;
})()
"""

# JavaScript that re-queries interactive elements and clicks the one at `index` (1-based).
# Returns true if clicked, false if index is out of range.
_CLICK_ELEMENT_JS = """
(index) => {
    const all = [];
    const seen = new Set();
    document.querySelectorAll('button, [role="tab"], [role="button"], select').forEach(el => {
        if (el.offsetParent === null || el.disabled) return;
        const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
        if (!text) return;
        const key = text.slice(0, 60);
        if (seen.has(key)) return;
        seen.add(key);
        all.push(el);
    });
    document.querySelectorAll('a').forEach(el => {
        if (el.offsetParent === null) return;
        const text = (el.innerText || '').trim();
        if (!/load more|show more|show all|expand|next|view all/i.test(text)) return;
        const key = text.slice(0, 60);
        if (seen.has(key)) return;
        seen.add(key);
        all.push(el);
    });
    const target = all[index - 1];
    if (!target) return false;
    target.click();
    return true;
}
"""


async def _run_with_retry(agent: Agent, input_text: str, max_turns: int = 30) -> Any:
    """Run Runner.run() with exponential backoff on transient LLM errors."""
    last_exc: Exception = RuntimeError("unreachable")
    for delay in (*_RETRY_DELAYS, None):
        try:
            return await Runner.run(agent, input_text, max_turns=max_turns)
        except _TRANSIENT_LLM_ERRORS as e:
            last_exc = e
            if delay is None:
                raise
            await asyncio.sleep(delay)
    raise last_exc  # unreachable, satisfies type checkers


# ---------------------------------------------------------------------------
# Snippet quality heuristic
# ---------------------------------------------------------------------------

_DIGIT_PATTERN = re.compile(r"\d")
_ERROR_TITLE_RE = re.compile(r"^Error[:\s\-]", re.IGNORECASE)
_HTTP_ERROR_RE = re.compile(r"\bHTTP\s+\d{3}\b", re.IGNORECASE)


def _snippet_quality(snippet: str) -> str:
    """Classify a search snippet as ``[rich]`` or ``[thin]``."""
    if len(snippet) > 120 and _DIGIT_PATTERN.search(snippet):
        return "[rich]"
    return "[thin]"


# ---------------------------------------------------------------------------
# ResearchTracker — tool-level URL/query bookkeeping
# ---------------------------------------------------------------------------

_ACTION_RANK = {
    "snippet_only": 1,
    "scrape_failed": 2,
    "blocked_by_policy": 2,
    "source_http_error": 2,
    "scraped_irrelevant": 2,
    "bot_detected": 2,
    "scraped": 3,
}

_TRACKING_PARAMS: frozenset = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_source_platform",
    "fbclid", "gclid", "msclkid",
    "mc_cid", "mc_eid",
    "_ga", "ref",
})
_BOT_BLOCK_THRESHOLD = 2


class ResearchTracker:
    """Accumulates URL and query records from tool calls."""

    def __init__(self):

        self._urls: Dict[str, UrlEntry] = {}
        self._actions: Dict[str, str] = {}
        self._queries: List[SearchQuery] = []
        self._consecutive_empty: Dict[str, int] = {}
        self._domain_bot_counts: Dict[str, int] = {}
        self._bot_blocked_domains: set[str] = set()

        self.search_count = 0
        self.scrape_count = 0

    @staticmethod
    def _normalize_url(url: str) -> str:
        p = urlparse(url)
        scheme = "https" if p.scheme in ("http", "https") else p.scheme
        if p.query:
            filtered = [
                (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                if k.lower() not in _TRACKING_PARAMS
            ]
            query = urlencode(filtered)
        else:
            query = ""
        return urlunparse(
            (scheme, p.netloc.lower(), p.path.rstrip("/"), p.params, query, "")
        )

    @staticmethod
    def normalize_url(url: str) -> str:
        """Public URL normalization used across pipeline components."""
        return ResearchTracker._normalize_url(url)

    @staticmethod
    def _normalize_domain(url: str) -> str:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc

    def _upgrade_action(self, key: str, new_action: str):
        current = self._actions.get(key)
        if current is None or _ACTION_RANK[new_action] > _ACTION_RANK[current]:
            self._actions[key] = new_action

    def record_search(
        self,
        query: str,
        num_results: int,
        domains: Optional[List[str]],
        results: list,
    ):
        from .models import SearchQuery, UrlEntry

        self._queries.append(
            SearchQuery(
                query=query,
                num_results_returned=num_results,
                domains_restricted=domains or [],
            )
        )
        for r in results:
            key = self._normalize_url(r.url)
            if key not in self._urls:
                self._urls[key] = UrlEntry(
                    url=r.url, title=r.title, content=r.snippet
                )
            self._upgrade_action(key, "snippet_only")

    def record_direct_query(self, query: str) -> None:
        """Record a direct-URL run so result metadata stays consistent."""
        from .models import SearchQuery

        self._queries.append(
            SearchQuery(
                query=query,
                num_results_returned=1,
                domains_restricted=[],
            )
        )

    def record_scrape(self, url: str, title: str, extracted_content: str):
        from .models import UrlEntry

        key = self._normalize_url(url)
        self._upgrade_action(key, "scraped")
        entry = self._urls.setdefault(key, UrlEntry(url=url))
        entry.content = extracted_content
        if title:
            entry.title = title

    def record_scrape_failure(self, url: str, error: str):
        from .models import UrlEntry

        key = self._normalize_url(url)
        self._upgrade_action(key, "scrape_failed")
        entry = self._urls.setdefault(key, UrlEntry(url=url))
        entry.content = f"[scrape failed: {error}]"

    def record_blocked_by_policy(self, url: str, error: str):
        from .models import UrlEntry

        key = self._normalize_url(url)
        self._upgrade_action(key, "blocked_by_policy")
        entry = self._urls.setdefault(key, UrlEntry(url=url))
        entry.content = f"[blocked by policy: {error}]"

    def record_source_http_error(self, url: str, error: str):
        from .models import UrlEntry

        key = self._normalize_url(url)
        self._upgrade_action(key, "source_http_error")
        entry = self._urls.setdefault(key, UrlEntry(url=url))
        entry.content = f"[source http error: {error}]"

    def record_scraped_irrelevant(self, url: str, error: str):
        from .models import UrlEntry

        key = self._normalize_url(url)
        self._upgrade_action(key, "scraped_irrelevant")
        entry = self._urls.setdefault(key, UrlEntry(url=url))
        entry.content = f"[scraped but irrelevant: {error}]"

    def record_bot_detection(self, url: str, error: str):
        from .models import UrlEntry

        key = self._normalize_url(url)
        self._upgrade_action(key, "bot_detected")
        entry = self._urls.setdefault(key, UrlEntry(url=url))
        entry.content = f"[bot detection: {error}]"
        domain = self._normalize_domain(url)
        if domain:
            count = self._domain_bot_counts.get(domain, 0) + 1
            self._domain_bot_counts[domain] = count
            if count >= _BOT_BLOCK_THRESHOLD:
                self._bot_blocked_domains.add(domain)

    def build_result_groups(self) -> dict:
        """Group URLs by action: scraped, scrape_failed, bot_detected, snippet_only."""
        groups: Dict[str, list] = {
            "scraped": [],
            "scrape_failed": [],
            "blocked_by_policy": [],
            "source_http_error": [],
            "scraped_irrelevant": [],
            "bot_detected": [],
            "snippet_only": [],
        }
        for key, entry in self._urls.items():
            action = self._actions.get(key, "snippet_only")
            groups[action].append(entry)
        return groups

    def entries_for_action(self, action: str) -> list:
        """Return tracker entries for a specific action group."""
        return self.build_result_groups()[action]

    def count_for_action(self, action: str) -> int:
        """Return how many entries currently belong to an action group."""
        return len(self.entries_for_action(action))

    def action_for(self, url: str) -> Optional[str]:
        """Return the recorded action for a URL, if any."""
        return self._actions.get(self.normalize_url(url))

    def entry_for(self, url: str):
        """Return the tracked entry for a URL, if any."""
        return self._urls.get(self.normalize_url(url))

    def has_attempted_url(self, url: str) -> bool:
        """True when a URL has been scraped or failed previously."""
        return self.action_for(url) is not None

    def is_unscraped_candidate(self, url: str) -> bool:
        """True when a URL is new or only known from snippets."""
        action = self.action_for(url)
        return action in (None, "snippet_only")

    def cached_scrape_response(self, url: str) -> Optional[str]:
        """Return a user-facing cached response for previously seen URLs."""
        action = self.action_for(url)
        entry = self.entry_for(url)
        if action == "scraped" and entry:
            return f"[Already scraped — cached result] {entry.content[:800]}"
        if action in {
            "scrape_failed",
            "blocked_by_policy",
            "source_http_error",
            "scraped_irrelevant",
            "bot_detected",
        }:
            cached_msg = (entry.content or action) if entry else action
            return f"[Already attempted this URL — it failed: {cached_msg[:200]}. Move on to a different URL.]"
        if self.is_domain_bot_blocked(url):
            domain = self._normalize_domain(url)
            return (
                "[Skipped URL from domain blocked by bot protection earlier in this run: "
                f"{domain}. Move on to a different domain or source.]"
            )
        return None

    def is_domain_bot_blocked(self, url: str) -> bool:
        """True when the URL's domain crossed the bot-detection threshold this run."""
        domain = self._normalize_domain(url)
        return bool(domain) and domain in self._bot_blocked_domains

    def bot_blocked_domains(self) -> set[str]:
        """Return the set of domains blocked for the current run."""
        return set(self._bot_blocked_domains)

    def increment_empty(self, domains_key: str) -> int:
        """Increment and return the consecutive-empty count for a domain set."""
        count = self._consecutive_empty.get(domains_key, 0) + 1
        self._consecutive_empty[domains_key] = count
        return count

    def reset_empty(self, domains_key: str) -> None:
        """Reset the consecutive-empty count for a domain set."""
        self._consecutive_empty[domains_key] = 0

    @property
    def queries(self) -> list:
        return list(self._queries)


# ---------------------------------------------------------------------------
# Content extractor sub-agent
# ---------------------------------------------------------------------------

class _ExtractorOutput(BaseModel):
    """Structured output from the content extractor sub-agent."""

    title: str = Field(
        default="",
        description="Title of the page or document.",
    )
    relevant_content: str = Field(
        description=(
            "Comprehensive extraction from the page that directly answers the research query. "
            "Include ALL specific facts, numbers, dates, regulations, quotes, species names, location names, and detailed context. "
            "Do NOT summarize what the page is about; explicitly extract the actual data and facts from the page. "
            "If the page is an article or report, extract the specific findings, not just a table of contents or structural overview. "
            "Exclude boilerplate, navigation, ads, and completely off-topic content. "
            "Maximum 5,000 characters."
        )
    )
    page_type: Literal["list", "content"] = Field(
        default="content",
        description=(
            'Set to "list" if this page is a database view, search results page, '
            'index, or any page whose primary purpose is listing many items with links '
            'to detail pages. Set to "content" for articles, reports, and detail pages.'
        ),
    )
    relevant_links: List[str] = Field(
        default_factory=list,
        description=(
            "Up to 15 absolute URLs found in the page that are highly likely to contain "
            "additional specific information for the research query. "
            "If page_type is 'list', treat each visible item's detail-page link as a candidate "
            "and rank by relevance to the query. Return up to 15."
        ),
    )


def _classify_failure_action(content: str) -> str:
    lower = content.lower()
    if "bot_detected:" in content:
        return "bot_detected"
    if "skipped: blocked domain" in lower:
        return "blocked_by_policy"
    if content.startswith("[No relevant content") or content.startswith("No relevant content"):
        return "scraped_irrelevant"
    if _HTTP_ERROR_RE.search(content) or "get failed:" in lower or "connecterror" in lower:
        return "source_http_error"
    return "scrape_failed"


_EXTRACTOR_INSTRUCTIONS = """\
You are a precise and comprehensive content extractor for web research.

You receive a URL and a research query. Your job:

## Step 1 — Fetch the page
Call ``raw_scrape`` to fetch the page content. Call it exactly once.

## Step 1b — Handle thin content or low-quality content with interaction
If raw_scrape returned fewer than 500 characters of meaningful content
AND the page is not a document (PDF/DOCX/PPTX/XLSX):

1. Call list_interactive_elements() to see what is clickable on the page.
2. If the list contains tabs, buttons, or controls likely to reveal data
   relevant to the research query, call click_element(n) for the most
   promising element.
3. Use the updated content. You may call click_element up to 5 times total.
4. If content remains thin after clicking all promising elements, proceed
   with what you have.

Also call list_interactive_elements() if raw_scrape returned a message containing:
- "[SPA: URL fragment detected" — the page uses client-side routing; the visible
  content may be the wrong tab or view. Look for tabs, dropdowns, or section
  selectors that navigate to the target data.
- "[Form/survey content detected" — the page loaded a feedback widget instead of
  data. Look for data tabs, dropdowns, or navigation controls that reveal the
  actual content.
In both cases, click the most promising element and use the updated content.

Do NOT call list_interactive_elements() if raw_scrape already returned
rich content with no signals — interaction is a fallback, not a default.

## Step 2 — Check for a primary source document
After reading the page, ask yourself: **is this a metadata or catalogue page that links to a primary source document?**

Signs of a metadata/catalogue page:
- A legal database record (e.g. FAOLEX, EUR-Lex, national law portals) with a link to the law or regulation PDF.
- A library or repository entry with a link to the full report or paper PDF.
- A dataset/publication index page with a link to the main document.

**If yes: call ``scrape_linked_document`` with the URL of the primary document (PDF, DOCX, etc.).**
- Use the single most important document link — the one that IS the primary source, not supplementary annexes.
- Call it at most once.
- Do NOT call it for navigation links, related documents, or secondary references.

**If no:** skip this step and go straight to Step 3.

Examples of when to call ``scrape_linked_document``:
- FAOLEX page for a law → call it on the `.pdf` link that is the law text itself.
- A UN treaty repository page → call it on the treaty PDF.
- A report catalogue entry → call it on the full report PDF.

Examples of when NOT to call it:
- A regular article or blog post (the page IS the content).
- A search results or list page.
- A page where the PDF link is a supplementary annex, not the main document.

## Step 3 — Extract relevant content
From all the content you have gathered (page + document if fetched), extract everything that directly answers the research query:
- Include specific facts, numbers, dates, exact names (species, locations), statistics, regulations, quotes, and full context.
- VERY IMPORTANT: Do NOT describe the page structure. Extract the actual data.
- Do NOT over-summarize. We need a detailed account of the relevant information.
- Exclude navigation, ads, boilerplate, and completely off-topic sections.
- If the content is very long, scan for the most relevant sections and extract them comprehensively.

## Step 4 — Identify follow-up links
If you see links likely to contain deeper details needed to answer the query, include up to 15 in ``relevant_links`` (absolute URLs only).

## Step 5 — Return output
Return a highly informative ``relevant_content`` of up to 5,000 characters.

If the page is a list/database/search-results view (page_type = "list"), your primary job
is to identify and rank the item links, not to extract prose. Return up to 15 item URLs in
``relevant_links``, ordered by likely relevance to the research query.

If the page contains no relevant information, set ``relevant_content`` to:
"[No relevant content found for this query]"

If scraping fails, set ``relevant_content`` to the error message verbatim.

Always write ``relevant_content`` and ``title`` in English, regardless of the source language.
"""


def _has_fragment(url: str) -> bool:
    """True if the URL contains a non-empty #fragment (SPA client-side routing)."""
    return bool(urlparse(url).fragment)


_FORM_TOKENS = (
    "strongly agree", "strongly disagree", "please rate",
    "kindly provide", "please provide", "select an option",
)


def _is_form_contaminated(content: str) -> bool:
    """True if content is dominated by survey/form patterns despite char count > 500.

    Triggers on either:
    - A survey token appearing 2+ times (case-insensitive).
    - 20+ lines where >75% are bullet-point lines (nav-only dump).
    """
    lower = content.lower()
    if any(lower.count(tok) >= 2 for tok in _FORM_TOKENS):
        return True
    lines = [line for line in content.splitlines() if line.strip()]
    if len(lines) >= 20:
        bullet_lines = sum(1 for line in lines if line.strip().startswith(("* ", "- ")))
        if bullet_lines / len(lines) > 0.75:
            return True
    return False


def _build_extractor_agent(model: Any, query: str, url: str, wait_for: Optional[str], vision_model: Optional[str] = None, allowed_domains: Optional[frozenset] = None, max_pdf_pages: int = 50, max_content_chars: int = 30_000, doc_cache: Optional[dict] = None, doc_in_flight: Optional[Dict[str, asyncio.Future[str]]] = None) -> tuple:
    """Build a content extractor sub-agent with a URL-locked scraping tool.

    The ``raw_scrape`` tool is a closure that captures ``url`` and ``wait_for``
    deterministically from the outer ``scrape_and_extract`` tool call.  The
    sub-agent LLM cannot scrape a different URL — it calls ``raw_scrape()``
    with no arguments and always fetches the correct page.

    A second tool ``scrape_linked_document`` lets the extractor fetch one
    primary source document (PDF, DOCX …) linked from the page, which is
    essential for metadata/catalogue pages (e.g. FAOLEX law records) where
    the page itself only contains a summary and the full text is in a document.
    """
    from . import scraping as _scraping_module
    from .scraping import _SCRAPE_DOC, _is_blocked_domain

    @function_tool
    async def raw_scrape() -> str:
        """Fetch and return the full content of the pre-set URL.

        The URL is determined by the outer research task — no argument needed.
        Validates the URL first (skips dead links, empty pages, binary files).
        Works with static HTML, JS-rendered pages, JSON endpoints, images,
        PDFs, DOCX, PPTX, and XLSX.
        """
        content, title, error = await _scraping_module.scrape_url(url, wait_for, query=query, vision_model=vision_model, allowed_domains=allowed_domains, max_pdf_pages=max_pdf_pages, max_content_chars=max_content_chars)
        if error:
            return f"[Scrape failed: {error}]"
        if not content.strip():
            return "[Page returned empty content]"
        header = f"# {title}\nSource: {url}\n\n" if title else f"Source: {url}\n\n"

        signals = []
        if _has_fragment(url):
            signals.append(
                "[SPA: URL fragment detected — current content may be the wrong "
                "tab/view. Call list_interactive_elements to find the data section.]"
            )
        if len(content) >= _THIN_CONTENT_CHARS and _is_form_contaminated(content):
            signals.append(
                "[Form/survey content detected — actual data is likely behind "
                "interactive elements. Call list_interactive_elements.]"
            )
        if signals:
            return header + content + "\n\n" + "\n".join(signals)
        return header + content

    @function_tool
    async def scrape_linked_document(document_url: str) -> str:
        """Fetch and return the text content of a primary source document linked from the page.

        Use this when the page you scraped is a metadata or catalogue record
        (e.g. a FAOLEX law entry, a library repository page, a UN treaty record)
        that links to the actual primary document (a law text, full report, treaty PDF, etc.).

        Only call this for the single most important primary document — not for
        supplementary annexes, navigation links, or secondary references.
        Only accepts links that validate as real document resources, including
        extensionless download URLs that return document content-types.

        Args:
            document_url: Absolute URL of the primary source document to fetch.
        """
        norm = ResearchTracker.normalize_url(document_url)
        if doc_cache is not None and norm in doc_cache:
            return doc_cache[norm]

        existing = doc_in_flight.get(norm) if doc_in_flight is not None else None
        if existing is not None:
            return await asyncio.shield(existing)

        future: Optional[asyncio.Future[str]] = None
        if doc_in_flight is not None:
            future = asyncio.get_running_loop().create_future()
            doc_in_flight[norm] = future

        try:
            result = await _scrape_linked_document_uncached(document_url, norm)
        except Exception as exc:
            if future is not None and not future.done():
                future.set_exception(exc)
                future.exception()
            raise
        else:
            if future is not None and not future.done():
                future.set_result(result)
            return result
        finally:
            if doc_in_flight is not None:
                doc_in_flight.pop(norm, None)

    async def _scrape_linked_document_uncached(document_url: str, norm: str) -> str:
        plan = await _scraping_module._build_scrape_plan(document_url, allowed_domains=allowed_domains)
        if plan.strategy != _SCRAPE_DOC:
            return (
                "[scrape_linked_document rejected: URL does not look like a primary "
                f"document ({plan.reason}): {document_url}]"
            )
        content, title, error = await _scraping_module._scrape_document(
            document_url,
            query=query,
            vision_model=vision_model,
            max_pdf_pages=max_pdf_pages,
            known_content_type=plan.content_type,
            known_content_disposition=plan.content_disposition,
        )
        if error:
            return f"[Document scrape failed: {error}]"
        if not content.strip():
            return "[Document returned empty content]"
        header = f"# {title}\nSource: {document_url}\n\n" if title else f"Source: {document_url}\n\n"
        result = header + content
        if doc_cache is not None:
            doc_cache[norm] = result
        return result

    # --- interactive browser session ---
    _browser_holder: list = [None]   # [playwright Browser | None]
    _context_holder: list = [None]   # [playwright BrowserContext | None]
    _pw_holder: list = [None]        # [AsyncPlaywrightContextManager | None]
    _page_holder: list = [None]      # [playwright Page | None]
    _click_count: list = [0]

    def _current_page_url() -> str:
        page_url = getattr(_page_holder[0], "url", None)
        if isinstance(page_url, str) and page_url.strip():
            return page_url
        return url

    async def _close_interactive_session() -> None:
        if _page_holder[0] is not None:
            try:
                await _page_holder[0].close()
            except Exception:
                pass
            _page_holder[0] = None
        if _context_holder[0] is not None:
            try:
                await _context_holder[0].close()
            except Exception:
                pass
            _context_holder[0] = None
        if _browser_holder[0] is not None:
            try:
                await _browser_holder[0].close()
            except Exception:
                pass
            _browser_holder[0] = None
        if _pw_holder[0] is not None:
            try:
                await _pw_holder[0].__aexit__(None, None, None)
            except Exception:
                pass
            _pw_holder[0] = None

    async def _ensure_interactive_page() -> Any:
        if _page_holder[0] is not None:
            return _page_holder[0]

        try:
            pw_cm = async_playwright()
            pw = await pw_cm.__aenter__()
            _pw_holder[0] = pw_cm
            browser = await pw.chromium.launch(headless=True)
            _browser_holder[0] = browser
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            _context_holder[0] = context
            page = await context.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30_000)
            _page_holder[0] = page
            return page
        except Exception:
            await _close_interactive_session()
            raise

    @function_tool
    async def list_interactive_elements() -> str:
        """List all visible, clickable elements on the page as a numbered list.

        Opens a Playwright browser for the pre-set URL and returns all visible
        buttons, tabs, dropdowns, and load-more links with numeric indices.
        Call click_element(n) to interact with a specific element.

        Only call this when raw_scrape returned thin content (under 500 chars).
        Do NOT call this if raw_scrape already returned rich content.
        """
        try:
            page = await _ensure_interactive_page()
            elements = await page.evaluate(_GET_ELEMENTS_JS)
            if not elements:
                return "No interactive elements found on this page."

            lines = ["Interactive elements on page:"]
            for i, el in enumerate(elements, 1):
                lines.append(f'[{i}] {el["tag"]}: "{el["text"]}"')
            return "\n".join(lines)

        except Exception as e:
            return f"[list_interactive_elements failed: {e}]"

    @function_tool
    async def click_element(index: int) -> str:
        """Click an interactive element by its index from list_interactive_elements.

        Re-queries the page for the element (handles DOM changes), clicks it,
        waits for the page to settle, and returns the updated page content.
        Maximum 5 clicks per page.

        Args:
            index: 1-based index of the element to click, as returned by
                list_interactive_elements.
        """
        if _page_holder[0] is None:
            return (
                "[click_element error: no browser session open. "
                "Call list_interactive_elements() first.]"
            )

        if _click_count[0] >= _MAX_INTERACTIVE_CLICKS:
            return (
                f"INTERACTION LIMIT REACHED: {_MAX_INTERACTIVE_CLICKS} clicks used. "
                "Synthesize from content gathered so far."
            )

        try:
            page = _page_holder[0]
            clicked = await page.evaluate(_CLICK_ELEMENT_JS, index)
            if not clicked:
                return (
                    f"Element [{index}] no longer visible after previous interaction — "
                    "call list_interactive_elements() again to get the updated list."
                )

            _click_count[0] += 1

            try:
                await page.wait_for_load_state("networkidle", timeout=3_000)
            except Exception:
                pass  # timeout is acceptable — page may not trigger a network event

            post_click_url = _current_page_url()
            if _is_blocked_domain(post_click_url, allowed_domains=allowed_domains):
                await page.go_back()
                return (
                    f"[click_element blocked: navigation to {post_click_url} "
                    "is outside the allowed domain scope. Try a different element.]"
                )

            content = await page.inner_text("body")
            result = content.strip()

            if len(result) < _THIN_CONTENT_CHARS:
                result += (
                    f"\n\n[Content still thin after click ({len(result)} chars) — "
                    "consider clicking a different element.]"
                )

            return result

        except Exception as e:
            return f"[click_element failed: {e}]"

    agent = Agent(
        name="content_extractor",
        model=model,
        tools=[raw_scrape, scrape_linked_document, list_interactive_elements, click_element],
        output_type=_ExtractorOutput,
        model_settings=ModelSettings(),
        instructions=_EXTRACTOR_INSTRUCTIONS,
    )

    async def cleanup() -> None:
        await _close_interactive_session()

    return agent, cleanup


# ---------------------------------------------------------------------------
# Web search tool
# ---------------------------------------------------------------------------

def create_web_search(backend=None, tracker: Optional[ResearchTracker] = None, force_open_web: bool = False):
    """Create a web search ``@function_tool`` using a pluggable backend."""
    from .search_backends import SearchBackend

    if backend is None:
        raise ValueError("A SearchBackend instance must be provided. See search_backends.py.")
    _backend: SearchBackend = backend

    @function_tool
    async def web_search(
        query: str,
        include_domains: Optional[List[str]] = None,
    ) -> str:
        """Search the web for information.

        Returns a numbered list of sources with titles, URLs, snippets,
        and publication dates.  Rank positions are included when the
        backend supports them (e.g. Serper).  Snippets marked ``[rich]``
        contain specific data; ``[thin]`` are generic.

        Args:
            query: Search query.
            include_domains: Restrict results to these domains (only valid
                in domain-restricted mode; ignored in open-web mode).
        """
        # Enforce open-web mode: strip any self-imposed domain restrictions
        if force_open_web and include_domains:
            logger.info("[search] open-web mode: stripping self-imposed include_domains %s", include_domains)
            include_domains = None

        domains_label = f" [domains: {include_domains}]" if include_domains else ""
        logger.info("[search] %r%s", query, domains_label)

        if tracker is not None:
            if tracker.search_count >= 15:
                return (
                    "CIRCUIT BREAKER: You have performed 15 searches. "
                    "You MUST STOP searching and synthesize your findings immediately."
                )
            # Search-fixation guard: agent is searching repeatedly without scraping.
            # Stage 1 (searches 6-7, scrapes=0): warn but still return results.
            # Stage 2 (searches 8+, scrapes=0): hard block — no results returned.
            if tracker.scrape_count == 0:
                if tracker.search_count >= 7:
                    logger.info(
                        "[search] BLOCKED: search fixation (searches=%d, scrapes=0)",
                        tracker.search_count,
                    )
                    return (
                        "SEARCH BLOCKED: You have searched 7+ times without scraping "
                        "a single URL. Further searching is not permitted. "
                        "You MUST call scrape_and_extract on your best candidates NOW "
                        "before you are allowed to search again."
                    )
                elif tracker.search_count >= 5:
                    logger.info(
                        "[search] scrape-nudge (searches=%d, scrapes=0)",
                        tracker.search_count,
                    )
            tracker.search_count += 1

        try:
            max_results = 10 if include_domains else 12
            response = await _backend.search(query, max_results, include_domains)

            if not response.results:
                logger.info("[search] no results")
                if include_domains:
                    domain_list = ", ".join(include_domains)
                    domains_key = ",".join(sorted(include_domains))
                    count = tracker.increment_empty(domains_key) if tracker is not None else 1
                    if count >= 3:
                        return (
                            f"[STOP SEARCHING] You have now received 0 results "
                            f"{count} times in a row for domain(s): {domain_list}. "
                            f"These domains do not expose this content via web search. "
                            f"You MUST stop searching and produce your final output."
                        )
                    return (
                        f"No results found within domain(s): {domain_list}. "
                        f"This domain may not index this content via web search."
                    )
                return "No results found. Consider broadening the search."

            logger.info("[search] → %d results", len(response.results))

            if tracker is not None:
                tracker.record_search(
                    query=query,
                    num_results=len(response.results),
                    domains=include_domains,
                    results=response.results,
                )
                if include_domains:
                    tracker.reset_empty(",".join(sorted(include_domains)))

            parts = []

            # Knowledge Graph — free structured facts, no scraping needed
            if response.knowledge_graph:
                kg = response.knowledge_graph
                kg_lines = [f"\n**Knowledge Graph: {kg.title}**"]
                if kg.entity_type:
                    kg_lines.append(f"Type: {kg.entity_type}")
                if kg.description:
                    kg_lines.append(kg.description)
                if kg.attributes:
                    attrs = ", ".join(
                        f"{k}: {v}" for k, v in list(kg.attributes.items())[:6]
                    )
                    kg_lines.append(f"Attributes: {attrs}")
                parts.append("\n".join(kg_lines))

            # Organic results
            parts.append("\n**Sources:**")
            for i, r in enumerate(response.results, 1):
                quality = _snippet_quality(r.snippet)
                date_tag = f" · {r.date}" if r.date else ""
                rank_tag = f" · rank #{r.position}" if r.position else ""
                parts.append(
                    f"\n[{i}] **{r.title}** {quality}{date_tag}{rank_tag}"
                    f"\nURL: {r.url}"
                    f"\n{r.snippet}"
                )

            # People Also Ask — pre-answered Q&A pairs
            if response.people_also_ask:
                parts.append("\n**People Also Ask:**")
                for paa in response.people_also_ask[:4]:
                    parts.append(f"\nQ: {paa.question}")
                    if paa.snippet:
                        parts.append(f"A: {paa.snippet}")
                    if paa.link:
                        parts.append(f"Source: {paa.link}")

            # Related searches
            if response.related_searches:
                parts.append("\n**Related searches:**")
                for rs in response.related_searches[:5]:
                    parts.append(f"- {rs}")

            # Stage 1 nudge: append warning to result for searches 5-6 with 0 scrapes.
            if tracker is not None and tracker.scrape_count == 0 and tracker.search_count > 5:
                parts.append(
                    "\n\n⚠ WARNING: You have now searched 5+ times without scraping "
                    "anything. The data you need is inside documents, not snippets. "
                    "You MUST call scrape_and_extract on your best candidates NEXT. "
                    "One more search without scraping will BLOCK further searching."
                )

            return "\n".join(parts)

        except Exception as e:
            logger.error("web_search failed: %s", e)
            return f"Web search failed: {e}"

    return web_search


# ---------------------------------------------------------------------------
# Scrape-and-extract tool (wraps content extractor sub-agent)
# ---------------------------------------------------------------------------

def create_scrape_and_extract_tool(
    extractor_model: Any,
    tracker: Optional[ResearchTracker] = None,
    query: str = "",
    max_concurrent: int = 6,
    vision_model: Optional[str] = None,
    allowed_domains: Optional[frozenset] = None,
    max_pdf_pages: int = 50,
    max_content_chars: int = 30_000,
):
    """Create a scrape_and_extract function.

    Internally runs a dedicated content extractor sub-agent that:
    1. Calls ``raw_scrape`` to fetch the page through the unified router.
    2. Optionally calls ``scrape_linked_document`` once for a primary source
       document linked from a metadata page.
    3. Reads the fetched content in its own isolated context.
    4. Extracts and summarises only the portions relevant to the query.
    5. Returns a detailed, comprehensive excerpt (~5,000 chars) to the main agent.
    """

    semaphore = asyncio.Semaphore(max_concurrent)
    _doc_cache: dict = {}
    _doc_in_flight: Dict[str, asyncio.Future[str]] = {}
    in_flight: Dict[str, asyncio.Future[str]] = {}

    async def scrape_and_extract(
        url: str,
        wait_for: Optional[str] = None,
    ) -> str:
        """Scrape a URL and return a focused summary relevant to the research query.

        Internally validates the URL (skips 404s, auth walls, unsupported
        binary files) then runs a dedicated extractor sub-agent that fetches
        the page with the unified scraper and summarises the relevant content.

        Returns a detailed extraction of ~5,000 characters — never raw page content.

        Works with static HTML, JavaScript-rendered pages (SPAs, data portals),
        JSON endpoints, image URLs, PDFs, DOCX, PPTX, and XLSX.

        Args:
            url: The URL to scrape and extract from.
            wait_for: Optional CSS selector to wait for on JS-rendered pages.
                Only use for known data portals. Omit when in doubt.
        """
        norm = tracker.normalize_url(url) if tracker is not None else url

        if tracker is not None:
            if tracker.scrape_count >= 25:
                return (
                    "CIRCUIT BREAKER: You have scraped 25 URLs. "
                    "You MUST STOP scraping and synthesize your findings immediately."
                )
            cached_response = tracker.cached_scrape_response(url)
            if cached_response is not None:
                return cached_response

        existing = in_flight.get(norm)
        if existing is not None:
            try:
                return await asyncio.shield(existing)
            except Exception as e:
                return f"Failed to extract content from {url}: {e}"

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        in_flight[norm] = future

        try:
            async with semaphore:
                if tracker is not None:
                    tracker.scrape_count += 1

                _wait_for = (
                    wait_for
                    if wait_for and wait_for.lower() not in ("null", "none", "")
                    else None
                )

                # Build a fresh extractor agent per call with url locked in the closure
                extractor_agent, extractor_cleanup = _build_extractor_agent(extractor_model, query, url, _wait_for, vision_model=vision_model, allowed_domains=allowed_domains, max_pdf_pages=max_pdf_pages, max_content_chars=max_content_chars, doc_cache=_doc_cache, doc_in_flight=_doc_in_flight)

                input_text = (
                    f"Research query: {query}\n"
                    f"URL: {url}\n\n"
                    f"Call raw_scrape() to fetch the page, then extract relevant content."
                )

                try:
                    result = await _run_with_retry(extractor_agent, input_text, max_turns=30)
                    output = result.final_output_as(_ExtractorOutput)
                except Exception as e:
                    logger.error("[extract] sub-agent failed for %s: %s", url, e)
                    if tracker is not None:
                        tracker.record_scrape_failure(url, str(e))
                    final_output = f"Failed to extract content from {url}: {e}"
                    future.set_result(final_output)
                    return final_output
                finally:
                    await extractor_cleanup()

                content = output.relevant_content.strip()
                title = output.title.strip()
                links = output.relevant_links

                # Treat diagnostic placeholders and error titles as failures.
                # Covers: explicit sentinel strings, LLM paraphrases of errors,
                # and titles like "Error: Could not access document" or "Error | 403".
                _content_lower = content.lower()
                _short = len(content) < 400
                is_failure = (
                    not content
                    or content.startswith("[No relevant content")
                    or content.startswith("[Scrape failed")
                    or content.startswith("Scrape failed")
                    or content.startswith("[Page returned empty")
                    or (_ERROR_TITLE_RE.match(title) and _short)
                    or ("is not valid" in _content_lower and _short)
                    or ("could not access" in _content_lower and _short)
                    or ("not found" in _content_lower and ("404" in content or "http" in _content_lower) and _short)
                    or ("page not found" in _content_lower and _short)
                    or ("access denied" in _content_lower and _short)
                    or ("403" in content and ("forbidden" in _content_lower or "error" in _content_lower or "skipped" in _content_lower) and _short)
                    or ("skipped" in _content_lower and "http" in _content_lower and _short)
                )

                if is_failure:
                    action = _classify_failure_action(content or "")
                    if action in {"scraped_irrelevant", "blocked_by_policy"}:
                        logger.debug("[extract] %s %s", action, url)
                    elif action in {"source_http_error", "bot_detected"}:
                        logger.info("[extract] %s %s", action, url)
                    else:
                        logger.info("[extract] scrape_failed %s", url)
                    if tracker is not None:
                        if action == "bot_detected":
                            tracker.record_bot_detection(url, content)
                        elif action == "blocked_by_policy":
                            tracker.record_blocked_by_policy(url, content)
                        elif action == "source_http_error":
                            tracker.record_source_http_error(url, content)
                        elif action == "scraped_irrelevant":
                            tracker.record_scraped_irrelevant(url, content)
                        else:
                            tracker.record_scrape_failure(url, content or "empty extraction")
                    final_output = f"No relevant content found at {url}: {content}"

                    if tracker is not None:
                        count_scraped = tracker.count_for_action("scraped")
                        if count_scraped < 2:
                            final_output += (
                                "\n\n⚠ REMINDER: You MUST successfully scrape AT LEAST 2 high-quality sources "
                                f"before synthesising and finishing. You currently have {count_scraped} "
                                "successful scrape(s). You MUST find other URLs and scrape them!"
                            )
                    future.set_result(final_output)
                    return final_output

                if tracker is not None:
                    tracker.record_scrape(url, title, content)

                header = f"# {title}\nSource: {url}\n\n" if title else f"Source: {url}\n\n"
                final_output = header + content
                if output.page_type == "list":
                    final_output += "\n**Page type: list**"
                if links:
                    final_output += "\n\n**Relevant Links found on page:**\n" + "\n".join(f"- {lnk}" for lnk in links[:15])

                if tracker is not None:
                    count_scraped = tracker.count_for_action("scraped")
                    if count_scraped < 2:
                        final_output += (
                            "\n\n⚠ REMINDER: You MUST successfully scrape AT LEAST 2 high-quality sources "
                            "before synthesising and finishing. You currently have "
                            f"{count_scraped} successful scrape(s)."
                        )

                future.set_result(final_output)
                return final_output
        finally:
            if not future.done():
                future.set_exception(RuntimeError("scrape aborted unexpectedly"))
            in_flight.pop(norm, None)

    return scrape_and_extract
