"""Content extractor sub-agent — builds and runs a per-URL extraction agent.

``build_extractor_agent`` creates a short-lived ``Agent`` whose only job is
to read pre-fetched page content and return a typed ``ExtractorOutput``.
The agent optionally has access to:

- ``scrape_linked_document``   — fetch one primary document linked from the page
- ``list_interactive_elements`` / ``click_element`` — drive a Playwright browser
  for SPA/thin pages
"""

import asyncio
import logging
from typing import Any, Dict, Optional

import litellm
from agents import Agent, ModelSettings, Runner, function_tool
from playwright.async_api import async_playwright

from web_scout.config import EXTRACTOR_HEURISTICS
from .page_analysis import prefetched_allows_interaction, render_cached_document_text
from .session_cache import get_or_fetch_session_source_artifact
from .types import ExtractorOutput

logger = logging.getLogger(__name__)

_TRANSIENT_LLM_ERRORS = (
    litellm.ServiceUnavailableError,
    litellm.RateLimitError,
    litellm.APIConnectionError,
    litellm.BadGatewayError,
)
_RETRY_DELAYS = (1.0, 2.0, 4.0)

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

_EXTRACTOR_INSTRUCTIONS = """\
You are a precise and comprehensive content extractor for web research.

You receive a URL, a research query, and the pre-fetched page content. Your job:

## Step 1 — Review the provided page content
Read the page content provided in the prompt. Do NOT ask to fetch the page again.

## Step 1b — Handle thin content or low-quality content with interaction
If the provided content has fewer than 500 characters of meaningful text
AND the page is not a document (PDF/DOCX/PPTX/XLSX):

1. Call list_interactive_elements() to see what is clickable on the page.
2. If the list contains tabs, buttons, or controls likely to reveal data
   relevant to the research query, call click_element(n) for the most
   promising element.
3. Use the updated content. You may call click_element up to 5 times total.
4. If content remains thin after clicking all promising elements, proceed
   with what you have.

Also call list_interactive_elements() if the content has a message containing:
- "[SPA: URL fragment detected" — the page uses client-side routing; the visible
  content may be the wrong tab or view. Look for tabs, dropdowns, or section
  selectors that navigate to the target data.
- "[Form/survey content detected" — the page loaded a feedback widget instead of
  data. Look for data tabs, dropdowns, or navigation controls that reveal the
  actual content.
In both cases, click the most promising element and use the updated content.

Do NOT call list_interactive_elements() if the content is already
rich with no signals — interaction is a fallback, not a default.

## Step 2 — Check for a primary source document
After reading the page, ask yourself: **is this a metadata or catalogue page that links to a primary source document?**

Signs of a metadata/catalogue page:
- A legal database record with a link to the law or regulation PDF.
- A library repository entry with a link to the full report or paper PDF.
- An open data catalog or publication index page with a link to the main document.

**If yes: call ``scrape_linked_document`` with the URL of the primary document (PDF, DOCX, etc.).**
- Use the single most important document link — the one that IS the primary source, not supplementary annexes.
- Call it at most once.
- Do NOT call it for navigation links, related documents, or secondary references.

**If no:** skip this step and go straight to Step 3.

Examples of when to call ``scrape_linked_document``:
- A database record for a specific document → call it on the `.pdf` link that is the document text itself.
- A repository page → call it on the main file PDF.
- A report catalogue entry → call it on the full report PDF.

Examples of when NOT to call it:
- A regular article or blog post (the page IS the content).
- A search results or list page.
- A page where the PDF link is a supplementary annex, not the main document.

## Step 3 — Extract relevant content
From all the content you have gathered (page + document if fetched), extract everything that directly answers the research query:
- Include specific facts, numbers, dates, exact names, statistics, quotas, quotes, and full context.
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

Always write ``relevant_content`` and ``title`` in English, regardless of the source language.
"""


async def run_with_retry(agent: Agent, input_text: str, max_turns: int = 30) -> Any:
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


def build_extractor_agent(
    model: Any,
    query: str,
    url: str,
    wait_for: Optional[str],
    vision_model: Optional[str] = None,
    allowed_domains: Optional[frozenset] = None,
    max_pdf_pages: int = 50,
    max_content_chars: int = 30_000,
    doc_cache: Optional[dict] = None,
    doc_in_flight: Optional[Dict[str, asyncio.Future[str]]] = None,
    use_session_cache: bool = False,
    max_interactive_clicks: int = EXTRACTOR_HEURISTICS.max_interactive_clicks,
    domain_expertise: Optional[str] = None,
    pre_fetched_content: str = "",
) -> tuple:
    """Build a content extractor sub-agent with a URL-locked scraping tool.

    A second tool ``scrape_linked_document`` lets the extractor fetch one
    primary source document (PDF, DOCX …) linked from the page, which is
    essential for metadata/catalogue pages (e.g. FAOLEX law records) where
    the page itself only contains a summary and the full text is in a document.
    """
    from web_scout.scraping import executor as scraping_executor
    from web_scout.scraping import plan as scraping_plan
    from web_scout.scraping.page_classifier import classify_prefetched_page_shape, looks_like_pdf_resource
    from web_scout.scraping.types import ScrapeStrategy, SourceArtifact
    from web_scout.scraping.utils import is_blocked_domain

    legacy_direct_agent = not pre_fetched_content
    page_shape = classify_prefetched_page_shape(pre_fetched_content) if pre_fetched_content else None
    allow_interaction = legacy_direct_agent or prefetched_allows_interaction(pre_fetched_content, page_shape)
    allow_linked_document = legacy_direct_agent or (
        page_shape is not None
        and page_shape.page_type == "record_page"
        and page_shape.record_score >= 5
        and page_shape.record_score >= page_shape.content_score + 2
    )
    linked_document_called = False

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
        nonlocal linked_document_called
        logger.info("[extract-tool] sub-agent calling scrape_linked_document for %s", document_url)
        if not allow_linked_document:
            return (
                "[scrape_linked_document skipped: pre-fetched page content is already "
                "rich and does not look like a metadata/catalogue page. Return the "
                "structured extraction from the supplied content.]"
            )
        if linked_document_called and not legacy_direct_agent:
            return (
                "[scrape_linked_document skipped: a linked document was already "
                "attempted for this page. Return the structured extraction now.]"
            )
        linked_document_called = True
        plan = await scraping_plan.build_scrape_plan(document_url, allowed_domains=allowed_domains)
        if plan.strategy != ScrapeStrategy.DOCUMENT:
            return (
                "[scrape_linked_document rejected: URL does not look like a primary "
                f"document ({plan.reason}): {document_url}]"
            )

        if use_session_cache:
            cached_artifact, cache_error = await get_or_fetch_session_source_artifact(
                url=document_url,
                strategy=plan.strategy,
                wait_for=None,
                vision_model=vision_model,
                allowed_domains=allowed_domains,
                max_pdf_pages=max_pdf_pages,
                cache_pdf_pages=looks_like_pdf_resource(document_url, plan.content_type, plan.content_disposition),
            )
            if cache_error or cached_artifact is None:
                return f"[Document scrape failed: {cache_error}]"
            content, title, error = await scraping_executor.materialize_source_artifact(
                SourceArtifact(
                    kind=cached_artifact.artifact_kind,
                    title=cached_artifact.title,
                    text_content=cached_artifact.text_content,
                    binary_bytes=cached_artifact.binary_bytes,
                    mime_type=cached_artifact.mime_type,
                ),
                query=query,
                vision_model=vision_model,
                max_content_chars=max_content_chars,
            )
            if error:
                return f"[Document scrape failed: {error}]"
            if not content.strip():
                return "[Document returned empty content]"
            return render_cached_document_text(document_url, title, content)

        from .tracker import ResearchTracker

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
            result = await _scrape_linked_document_uncached(
                document_url,
                norm,
                known_content_type=plan.content_type,
                known_content_disposition=plan.content_disposition,
            )
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

    async def _scrape_linked_document_uncached(
        document_url: str,
        norm: str,
        *,
        known_content_type: str,
        known_content_disposition: str,
    ) -> str:
        content, title, error = await scraping_executor.scrape_document(
            document_url,
            query=query,
            vision_model=vision_model,
            max_pdf_pages=max_pdf_pages,
            known_content_type=known_content_type,
            known_content_disposition=known_content_disposition,
        )
        if error:
            return f"[Document scrape failed: {error}]"
        if not content.strip():
            return "[Document returned empty content]"
        result = render_cached_document_text(document_url, title, content)
        if doc_cache is not None:
            doc_cache[norm] = result
        return result

    # --- interactive browser session ---
    _browser_holder: list = [None]
    _context_holder: list = [None]
    _pw_holder: list = [None]
    _page_holder: list = [None]
    _click_count: list = [0]

    def _current_page_url() -> str:
        page_url = getattr(_page_holder[0], "url", None)
        if isinstance(page_url, str) and page_url.strip():
            return page_url
        return url

    async def _close_interactive_session() -> None:
        for holder, method in (
            (_page_holder, "close"),
            (_context_holder, "close"),
            (_browser_holder, "close"),
        ):
            if holder[0] is not None:
                try:
                    await getattr(holder[0], method)()
                except Exception:
                    pass
                holder[0] = None
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
            await page.goto(
                url,
                wait_until="networkidle",
                timeout=EXTRACTOR_HEURISTICS.interactive_page_goto_timeout_ms,
            )
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

        Only call this when the pre-fetched content is thin (under 500 chars).
        Do NOT call this if the content is already rich.
        """
        logger.info("[extract-tool] sub-agent calling list_interactive_elements for %s", url)
        if not allow_interaction:
            return (
                "[list_interactive_elements skipped: pre-fetched page content is "
                "already rich. Return the structured extraction from the supplied content.]"
            )
        if _click_count[0] >= max_interactive_clicks:
            return (
                f"INTERACTION LIMIT REACHED: {max_interactive_clicks} clicks used. "
                "Synthesize from content gathered so far."
            )
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
        logger.info("[extract-tool] sub-agent calling click_element(%d) for %s", index, url)
        if _page_holder[0] is None:
            return "[click_element error: no browser session open. Call list_interactive_elements() first.]"
        if _click_count[0] >= max_interactive_clicks:
            return (
                f"INTERACTION LIMIT REACHED: {max_interactive_clicks} clicks used. "
                "Synthesize from content gathered so far."
            )
        try:
            page = _page_holder[0]
            pre_click_url = _current_page_url()
            pre_click_text = (await page.inner_text("body")).strip()

            clicked = await page.evaluate(_CLICK_ELEMENT_JS, index)
            if not clicked:
                return (
                    f"Element [{index}] no longer visible after previous interaction — "
                    "call list_interactive_elements() again to get the updated list."
                )

            _click_count[0] += 1

            try:
                await page.wait_for_load_state(
                    "networkidle",
                    timeout=EXTRACTOR_HEURISTICS.interactive_wait_timeout_ms,
                )
            except Exception:
                pass  # timeout is acceptable — page may not trigger a network event

            post_click_url = _current_page_url()
            if is_blocked_domain(post_click_url, allowed_domains=allowed_domains):
                await page.go_back()
                return (
                    f"[click_element blocked: navigation to {post_click_url} "
                    "is outside the allowed domain scope. Try a different element.]"
                )

            result = (await page.inner_text("body")).strip()

            normalized_before = " ".join(pre_click_text.split())
            normalized_after = " ".join(result.split())
            if post_click_url == pre_click_url and normalized_after == normalized_before:
                if len(result) < EXTRACTOR_HEURISTICS.thin_content_chars:
                    return (
                        f"[Clicking element {index} had no visible effect on the page content. "
                        "Try a different element.]"
                    )
                result += f"\n\n[Clicking element {index} had no visible effect on the page content.]"

            if len(result) < EXTRACTOR_HEURISTICS.thin_content_chars:
                result += (
                    f"\n\n[Content still thin after click ({len(result)} chars) — "
                    "consider clicking a different element.]"
                )

            return result
        except Exception as e:
            return f"[click_element failed: {e}]"

    instructions = _EXTRACTOR_INSTRUCTIONS
    if domain_expertise:
        instructions += f"\n\nDomain Expertise: {domain_expertise}\n"

    agent_tools = []
    if allow_linked_document:
        agent_tools.append(scrape_linked_document)
    if allow_interaction:
        agent_tools.extend([list_interactive_elements, click_element])
    logger.info(
        "[extract] extractor_agent tools=%s chars=%d linked_doc=%s interaction=%s shape=%s doc_links=%s metadata_markers=%s url=%s",
        [getattr(tool, "name", str(tool)) for tool in agent_tools],
        len(pre_fetched_content),
        allow_linked_document,
        allow_interaction,
        page_shape.page_type if page_shape is not None else "legacy",
        page_shape.document_link_count if page_shape is not None else None,
        page_shape.metadata_marker_count if page_shape is not None else None,
        url,
    )

    agent = Agent(
        name="content_extractor",
        model=model,
        tools=agent_tools,
        output_type=ExtractorOutput,
        model_settings=ModelSettings(),
        instructions=instructions,
    )

    async def cleanup() -> None:
        await _close_interactive_session()

    return agent, cleanup
