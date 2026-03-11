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
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from agents import Agent, ModelSettings, Runner, function_tool
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .models import SearchQuery, UrlEntry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Snippet quality heuristic
# ---------------------------------------------------------------------------

_DIGIT_PATTERN = re.compile(r"\d")
_ERROR_TITLE_RE = re.compile(r"^Error[:\s|\-]", re.IGNORECASE)


def _snippet_quality(snippet: str) -> str:
    """Classify a search snippet as ``[rich]`` or ``[thin]``."""
    if len(snippet) > 120 and _DIGIT_PATTERN.search(snippet):
        return "[rich]"
    return "[thin]"


# ---------------------------------------------------------------------------
# ResearchTracker — tool-level URL/query bookkeeping
# ---------------------------------------------------------------------------

_ACTION_RANK = {"snippet_only": 1, "scrape_failed": 2, "scraped": 3}


class ResearchTracker:
    """Accumulates URL and query records from tool calls."""

    def __init__(self):
        from .models import SearchQuery, UrlEntry

        self._urls: Dict[str, UrlEntry] = {}
        self._actions: Dict[str, str] = {}
        self._queries: List[SearchQuery] = []
        self._consecutive_empty: Dict[str, int] = {}

        self.search_count = 0
        self.scrape_count = 0

    @staticmethod
    def _normalize_url(url: str) -> str:
        p = urlparse(url)
        scheme = "https" if p.scheme in ("http", "https") else p.scheme
        return urlunparse(
            (scheme, p.netloc.lower(), p.path.rstrip("/"), p.params, p.query, "")
        )

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

    def build_result_groups(self) -> dict:
        """Group URLs by action: scraped, scrape_failed, snippet_only."""
        groups: Dict[str, list] = {
            "scraped": [],
            "scrape_failed": [],
            "snippet_only": [],
        }
        for key, entry in self._urls.items():
            action = self._actions.get(key, "snippet_only")
            groups[action].append(entry)
        return groups

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
    relevant_links: List[str] = Field(
        default_factory=list,
        description=(
            "Up to 5 absolute URLs found in the page that are highly likely to contain "
            "additional specific information for the research query (e.g., deeper data, "
            "sub-reports, specific species pages). Return empty list if none."
        )
    )


_EXTRACTOR_INSTRUCTIONS = """\
You are a precise and comprehensive content extractor for web research.

You receive a URL and a research query. Your job:
1. Call ``raw_scrape`` **exactly once** to fetch the page content.
   Do NOT call it again — one fetch is all you get.
2. Read the content carefully.
3. Extract ALL the portions that directly answer or are relevant to the research query.
   - Include specific facts, numbers, dates, exact names (like species or locations), statistics, regulations, quotes, and full context.
   - VERY IMPORTANT: Do NOT describe the page structure (e.g. "This page has a section on endemic flora"). Instead, extract the actual data (e.g. "The endemic flora listed are Species X, Species Y, and Species Z").
   - Do NOT over-summarize! We need a detailed account of the relevant information.
   - Exclude navigation, ads, boilerplate, and completely off-topic sections.
   - If the content is very long, scan for the most relevant sections and extract them comprehensively.
4. If you see links in the content that likely contain deeper details or sub-reports 
   needed to fully answer the query, include up to 5 of them in ``relevant_links``.
   Ensure they are absolute URLs (starting with http/https).
5. Return a highly informative ``relevant_content`` of up to 5,000 characters.

If the page contains no relevant information, set ``relevant_content`` to:
"[No relevant content found for this query]"

If scraping fails, set ``relevant_content`` to the error message verbatim.
"""


def _build_extractor_agent(model: Any, query: str, url: str, wait_for: Optional[str], vision_model: Optional[str] = None) -> Agent:
    """Build a content extractor sub-agent with a URL-locked scraping tool.

    The ``raw_scrape`` tool is a closure that captures ``url`` and ``wait_for``
    deterministically from the outer ``scrape_and_extract`` tool call.  The
    sub-agent LLM cannot scrape a different URL — it calls ``raw_scrape()``
    with no arguments and always fetches the correct page.
    """
    from .scraping import scrape_url as _scrape_url

    @function_tool
    async def raw_scrape() -> str:
        """Fetch and return the full content of the pre-set URL.

        The URL is determined by the outer research task — no argument needed.
        Validates the URL first (skips dead links, empty pages, binary files).
        Works with static HTML, JS-rendered pages, PDFs, DOCX, PPTX, XLSX.
        """
        content, title, error = await _scrape_url(url, wait_for, query=query, vision_model=vision_model)
        if error:
            return f"[Scrape failed: {error}]"
        if not content.strip():
            return "[Page returned empty content]"
        header = f"# {title}\nSource: {url}\n\n" if title else f"Source: {url}\n\n"
        return header + content

    return Agent(
        name="content_extractor",
        model=model,
        tools=[raw_scrape],
        output_type=_ExtractorOutput,
        model_settings=ModelSettings(
            parallel_tool_calls=False,
            extra_args={"reasoning_effort": "high"},
        ),
        instructions=_EXTRACTOR_INSTRUCTIONS,
    )


# ---------------------------------------------------------------------------
# Web search tool
# ---------------------------------------------------------------------------

def create_web_search(backend=None, tracker: Optional[ResearchTracker] = None, force_open_web: bool = False):
    """Create a web search ``@function_tool`` using a pluggable backend."""
    from .search_backends import DuckDuckGoBackend, SearchBackend

    _backend: SearchBackend = backend or DuckDuckGoBackend()

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
    max_concurrent: int = 3,
    vision_model: Optional[str] = None,
):
    """Create a scrape_and_extract function.

    Internally runs a dedicated content extractor sub-agent that:
    1. Calls ``raw_scrape`` (crawl4ai / docling) to fetch the page.
    2. Reads the full raw content in its own isolated context.
    3. Extracts and summarises only the portions relevant to the query.
    4. Returns a detailed, comprehensive excerpt (~5,000 chars) to the main agent.
    """
    
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def scrape_and_extract(
        url: str,
        wait_for: Optional[str] = None,
    ) -> str:
        """Scrape a URL and return a focused summary relevant to the research query.

        Internally validates the URL (skips 404s, empty SPA shells, paywalls,
        binary files) then runs a dedicated extractor sub-agent that fetches
        the page with crawl4ai or docling and summarises the relevant content.

        Returns a detailed extraction of ~5,000 characters — never raw page content.

        Works with static HTML, JavaScript-rendered pages (SPAs, data portals),
        PDFs, DOCX, PPTX, and XLSX.

        Args:
            url: The URL to scrape and extract from.
            wait_for: Optional CSS selector to wait for on JS-rendered pages.
                Only use for known data portals. Omit when in doubt.
        """
        async with semaphore:
            if tracker is not None:
                if tracker.scrape_count >= 25:
                    return (
                        "CIRCUIT BREAKER: You have scraped 25 URLs. "
                        "You MUST STOP scraping and synthesize your findings immediately."
                    )
                # Dedup: if this URL was already attempted, return the cached result immediately.
                # Never retry a URL — if it failed, move on to the next candidate.
                norm = tracker._normalize_url(url)
                if norm in tracker._actions:
                    action = tracker._actions[norm]
                    cached = tracker._urls.get(norm)
                    if action == "scraped" and cached:
                        return f"[Already scraped — cached result] {cached.content[:800]}"
                    elif action == "scrape_failed":
                        cached_msg = (cached.content or "scrape failed") if cached else "scrape failed"
                        return f"[Already attempted this URL — it failed: {cached_msg[:200]}. Move on to a different URL.]"
                tracker.scrape_count += 1
    
            _wait_for = (
                wait_for
                if wait_for and wait_for.lower() not in ("null", "none", "")
                else None
            )
    
            logger.info("[extract] → %s", url)
    
            # Build a fresh extractor agent per call with url locked in the closure
            extractor_agent = _build_extractor_agent(extractor_model, query, url, _wait_for, vision_model=vision_model)
    
            input_text = (
                f"Research query: {query}\n"
                f"URL: {url}\n\n"
                f"Call raw_scrape() to fetch the page, then extract relevant content."
            )
    
            try:
                result = await Runner.run(extractor_agent, input_text, max_turns=3)
                output = result.final_output_as(_ExtractorOutput)
            except Exception as e:
                logger.error("[extract] sub-agent failed for %s: %s", url, e)
                if tracker is not None:
                    tracker.record_scrape_failure(url, str(e))
                return f"Failed to extract content from {url}: {e}"
    
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
                logger.info("[extract] no useful content from %s", url)
                if tracker is not None:
                    tracker.record_scrape_failure(url, content or "empty extraction")
                msg = f"No relevant content found at {url}: {content}"
                
                if tracker is not None:
                    count_scraped = len(tracker.build_result_groups()["scraped"])
                    if count_scraped < 2:
                        msg += (
                            "\n\n⚠ REMINDER: You MUST successfully scrape AT LEAST 2 high-quality sources "
                            f"before synthesising and finishing. You currently have {count_scraped} "
                            "successful scrape(s). You MUST find other URLs and scrape them!"
                        )
                return msg
    
            logger.info("[extract] ok  %s (%d chars)", url, len(content))
            if tracker is not None:
                tracker.record_scrape(url, title, content)
                
            header = f"# {title}\nSource: {url}\n\n" if title else f"Source: {url}\n\n"
            final_output = header + content
            if links:
                final_output += "\n\n**Relevant Links found on page:**\n" + "\n".join(f"- {lnk}" for lnk in links[:5])
                
            if tracker is not None:
                count_scraped = len(tracker.build_result_groups()["scraped"])
                if count_scraped < 2:
                    final_output += (
                        "\n\n⚠ REMINDER: You MUST successfully scrape AT LEAST 2 high-quality sources "
                        "before synthesising and finishing. You currently have "
                        f"{count_scraped} successful scrape(s)."
                    )
    
            return final_output

    return scrape_and_extract
