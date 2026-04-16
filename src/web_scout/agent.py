"""Web researcher deterministic pipeline.

Uses pluggable search backends (Serper / DuckDuckGo). A dedicated
content extractor sub-agent (crawl4ai / docling) scrapes and summarises
each URL so the main researcher only sees focused excerpts.

Three input modes — all share a single linear pipeline:

1. **Query-only** — open web search -> triage -> parallel scrape -> synthesis.
2. **Domain + query** — domain-restricted search -> parallel scrape -> optional deep scrape -> synthesis.
3. **Direct URL** — skip search, extract a given URL directly -> hub detection
   -> if list/database page: follow up to N item links + one pagination hop -> synthesis.

Two research depth presets control how aggressively the pipeline searches:

- ``"standard"`` (default) — 2 iterations, up to ~10 sources.
- ``"deep"`` — 3 iterations, more search queries, up to ~28 sources.

Public API
----------
- ``run_web_research(query, models, ...)`` — full pipeline
"""

from __future__ import annotations

import asyncio
import logging
import os
import re as _re
from collections import OrderedDict
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urljoin, urlparse

from agents import Agent, ModelSettings, Runner
from pydantic import BaseModel, Field

from .models import WebResearchResult, WebResearchResultRaw
from .scraping import _is_blocked_domain
from .tools import ResearchTracker, create_scrape_and_extract_tool

logger = logging.getLogger(__name__)

# Suppress verbose LiteLLM INFO logs (keep WARNING+ only)
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("litellm").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Default models
# ---------------------------------------------------------------------------

DEFAULT_WEB_RESEARCH_MODELS = {
    "web_researcher": "gemini/gemini-3-flash-preview",
    "content_extractor": "gemini/gemini-3-flash-preview",
    "vision_fallback": "gemini/gemini-3-flash-preview",
    "followup_selector": "gemini/gemini-3-flash-preview",
}

# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------

QUERY_GENERATOR_INSTRUCTIONS = """\
You are an expert web researcher.
Generate highly effective search queries to find evidence for the user's research query.\
"""

COVERAGE_EVALUATOR_INSTRUCTIONS = """\
You evaluate whether the provided extracted web content fully answers the research query.
Be strict: if key specific data (like species names, numbers, thresholds) is requested but missing, it is not fully answered.
If the query is NOT fully answered, review the provided "Unscraped Candidates" (search result snippets not yet scraped).
- If any candidates look likely to contain the missing information, list their exact URLs in `promising_unscraped_urls` and set `needs_new_searches` to false.
- If the candidates look useless or unrelated to the missing information, set `needs_new_searches` to true and leave `promising_unscraped_urls` empty.\
"""

SYNTHESISER_INSTRUCTIONS = """\
You are a web research synthesiser. Your job is to read the extracted contents
from various web pages and produce a coherent narrative ``synthesis`` answering the query.

## Absolute rules — no exceptions

**NO TRAINING DATA.** Every specific fact, number, statistic, name, date, quota,
rate, or decision in your synthesis MUST be explicitly present in one of the provided
scraped sources. Do NOT recall, infer, or approximate from your own training knowledge.
This rule applies even when you are confident you know the answer from prior knowledge.

**REPORT GAPS, DO NOT FILL THEM.** When the sources do not contain a specific piece of
information the query asks for, write: "The available sources did not contain [missing item]."
Do not substitute related data, use approximate figures, or blend in background knowledge.
A synthesis that honestly reports gaps is more valuable than one that fills them silently.

**THIN COVERAGE.** If very few sources were scraped (the count appears in your prompt),
do not compensate with broader knowledge. Synthesize only what the sources contain and
explicitly state that coverage is limited.

## Citation rules

- **Only cite scraped sources.** Markdown citations [Title](URL) are only permitted for
  URLs listed under "Scraped sources (full extracts)". Do NOT create citations for URLs
  that appear only under "Additional sources (search snippets only)" — use snippet
  information as supporting context but do not attach a citation link to it.
- Every factual claim with a specific number, date, or named fact must have an inline
  citation pointing to a scraped source that contains that fact.
- Lead with what was found; address the query directly.
- If sources contradict each other, note the contradiction explicitly.
- Do NOT cite URLs that appear in the "SOURCES THAT COULD NOT BE ACCESSED" section —
  those were never scraped and their content is unknown.
"""


class SearchQueryGeneration(BaseModel):
    """LLM output for generating diverse search queries."""
    queries: List[str] = Field(description="List of search queries")

class CoverageEvaluation(BaseModel):
    """LLM output for evaluating coverage and routing the next pipeline step.

    After inspecting scraped content, the evaluator decides whether to scrape
    promising URLs already in the backlog or run new web searches.
    """
    fully_answered: bool = Field(description="True if the extracted content fully answers the original research query.")
    gaps: str = Field(description="If not fully answered, what specific information is still missing?")
    promising_unscraped_urls: List[str] = Field(
        default_factory=list,
        description="If not fully answered, list exact URLs from the Unscraped Candidates that likely contain the missing information. Leave empty if none are promising.",
    )
    needs_new_searches: bool = Field(
        default=True,
        description="True if the unscraped candidates are insufficient and new web searches must be run. False if promising_unscraped_urls candidates are enough to try first.",
    )


class FollowupSelection(BaseModel):
    """LLM output for selecting the best follow-up URLs from a same-domain set."""
    selected_urls: List[str] = Field(
        default_factory=list,
        description="Exact URLs chosen from the provided candidate list only, ordered best-first.",
    )


# ---------------------------------------------------------------------------
# Research depth presets
# ---------------------------------------------------------------------------

_DEPTH_PRESETS = {
    "standard": {
        "max_iterations": 2,
        "queries_first": 3,
        "queries_followup": 2,
        "urls_first": 6,
        "urls_followup": 4,
        "hub_deepening_cap": 10,
    },
    "deep": {
        "max_iterations": 3,
        "queries_first": 5,
        "queries_followup": 4,
        "urls_first": 12,
        "urls_followup": 8,
        "hub_deepening_cap": 15,
    },
}

async def _gather_scrapes(tasks: list) -> list:
    """Run scrape coroutines concurrently; concurrency is bounded inside the scrape tool."""
    return await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

_NEXT_PAGE_TOKENS: frozenset = frozenset({"next", "next page", "›", "»"})
_DOCUMENT_EXTENSIONS: tuple[str, ...] = (".pdf", ".docx", ".pptx", ".xlsx", ".doc", ".xls", ".ppt")
_FOLLOWUP_POSITIVE_TOKENS: tuple[str, ...] = (
    "report", "document", "publication", "bulletin", "factsheet", "assessment",
    "recommendation", "summary", "execsum", "study", "analysis", "trend",
    "state-of-the-climate", "climatology", "monitoring", "dataset",
    "download", "article", "paper",
)
_FOLLOWUP_NEGATIVE_TOKENS: tuple[str, ...] = (
    "service", "services", "forecast", "daily-forecast", "weekly-forecast",
    "seasonal-forecast", "weather-warning", "weather-warnings", "warning",
    "warnings", "home", "homepage", "contact", "about", "vision-statement",
    "department-history", "geography-research", "mapviewer",
)
_FOLLOWUP_GENERIC_SEGMENTS: frozenset[str] = frozenset({
    "publications", "publication", "our-products", "products", "services",
    "service", "weather", "climate", "resources", "library", "documents",
})
_FOLLOWUP_LIST_SEGMENTS: frozenset[str] = frozenset({
    "search", "results", "list", "listing", "archive", "archives", "browse",
    "catalog", "catalogue", "collection", "collections", "publications",
    "publications-full", "database",
})
_FOLLOWUP_DETAIL_TOKENS: tuple[str, ...] = (
    "report", "document", "publication", "article", "paper", "brief",
    "factsheet", "assessment", "analysis", "countrybrief", "record",
    "item", "handle", "bitstream",
)
_DATA_PORTAL_TOKENS: tuple[str, ...] = ("maproom", "dataset", "data", "api", "csv", "thredds")
_QUERY_DATA_HINTS: tuple[str, ...] = (
    "dataset", "data portal", "maproom", "api", "csv", "download data", "timeseries",
    "time series", "gridded", "grid", "raster",
)
_QUERY_REPORT_HINTS: tuple[str, ...] = (
    "report", "trend", "variability", "assessment", "analysis", "current status",
    "recent trend", "state of the climate", "bulletin",
)
_QUERY_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "for", "with", "from", "into", "that", "this", "those", "these",
    "current", "recent", "status", "long", "term", "change", "changes", "pattern",
    "patterns", "spatial", "interannual", "variability", "trend", "trends",
})


def _judge_synthesis(synthesis: str, valid_urls: set[str]) -> list[str]:
    """Return a list of issue descriptions, empty if synthesis passes."""
    issues = []

    # 1. Detect bare URLs
    # Strip out valid markdown links: [Title](URL)
    text_without_md_links = _re.sub(r'\[[^\]]*\]\((https?://[^\s\)]+)\)', '', synthesis)

    # Find any remaining http(s):// strings, cleaning up trailing punctuation
    bare_urls = [
        m.group().rstrip('.,;)"\'')
        for m in _re.finditer(r'https?://\S+', text_without_md_links)
    ]
    bare_urls = [u for u in bare_urls if u]

    if bare_urls:
        issues.append(
            "Bare URLs found (must be wrapped as markdown links [Title](URL)): "
            + ", ".join(bare_urls[:5])
        )

    # 2. Detect hallucinated URLs
    md_link_urls = set(_re.findall(r'\[[^\]]*\]\((https?://[^\s\)]+)\)', synthesis))

    valid_norm = {ResearchTracker._normalize_url(u) for u in valid_urls}

    hallucinated = [u for u in md_link_urls if ResearchTracker._normalize_url(u) not in valid_norm]
    if hallucinated:
        issues.append(
            "URLs cited that are NOT in the available sources (remove or replace): "
            + ", ".join(hallucinated[:5])
        )

    return issues


def _find_next_page_url(content: str, base_url: str) -> Optional[str]:
    """Scan markdown content for a 'next page' link on the same domain as base_url.

    Matches link text (case-insensitive) against: 'next', 'next page', '›', '»'.
    Handles both absolute (https://...) and relative (/path) hrefs.
    Normalizes www. prefix when comparing domains.
    Bare digits are intentionally excluded (too fragile).
    Returns the first matching same-domain URL, or None.
    """
    base_netloc = urlparse(base_url).netloc.lower().removeprefix("www.")

    # Expanded regex: match any href, not just https?:// absolute URLs
    for match in _re.finditer(r'\[([^\]]*)\]\(([^\s\)\#][^\s\)]*)\)', content):
        link_text = match.group(1).strip().lower()
        href_raw = match.group(2)

        # Skip non-navigable schemes
        if href_raw.startswith(("mailto:", "javascript:", "tel:", "data:")):
            continue

        # Resolve relative hrefs against base_url; strip fragment before domain check
        href = urljoin(base_url, href_raw)
        href = href.split("#")[0]  # drop fragment (e.g. /page/2#top → /page/2)
        if not href:
            continue

        if link_text in _NEXT_PAGE_TOKENS:
            href_netloc = urlparse(href).netloc.lower().removeprefix("www.")
            if href_netloc == base_netloc:
                return href
    return None


def _is_same_domain(url: str, domain: str) -> bool:
    netloc = urlparse(url).netloc.lower().split(":", 1)[0].removeprefix("www.")
    return bool(netloc) and (netloc == domain or netloc.endswith("." + domain))


def _looks_like_document_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in _DOCUMENT_EXTENSIONS)


def _query_prefers_data_pages(query: str) -> bool:
    query_lower = query.lower()
    return any(token in query_lower for token in _QUERY_DATA_HINTS)


def _query_prefers_report_pages(query: str) -> bool:
    query_lower = query.lower()
    return any(token in query_lower for token in _QUERY_REPORT_HINTS)


def _extract_query_keywords(query: str) -> set[str]:
    return {
        token
        for token in _re.findall(r"[a-z0-9]{4,}", query.lower())
        if token not in _QUERY_STOPWORDS
    }


def _looks_like_paginated_index_page(url: str) -> bool:
    parsed = urlparse(url)
    segments = [seg.lower() for seg in parsed.path.split("/") if seg]
    query_params = {k.lower() for k, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    has_list_segment = any(seg in _FOLLOWUP_LIST_SEGMENTS for seg in segments)
    has_pagination_param = bool(query_params & {"page", "p", "start", "offset", "tab", "sort"})
    return has_list_segment and has_pagination_param


def _looks_like_identifier_detail_page(path_segments: list[str]) -> bool:
    if not path_segments:
        return False
    terminal = path_segments[-1]
    return (
        any(token in terminal for token in ("10.", "doi", "handle"))
        or any(char.isdigit() for char in terminal)
        or any(token in "/".join(path_segments) for token in ("handle", "record", "item", "bitstream"))
    )


def _score_followup_candidate(query: str, url: str) -> int:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    path_segments = [seg.lower() for seg in path.split("/") if seg and seg.lower() not in {"index", "index.html"}]
    joined = "/".join(path_segments)
    normalized_joined = joined.replace("_", "-")
    terminal = joined.rsplit("/", 1)[-1] if joined else ""
    query_lower = query.lower()
    query_keywords = _extract_query_keywords(query)

    score = 0
    if _looks_like_paginated_index_page(url):
        score -= 12
    if _looks_like_document_url(url):
        score += 3
    if any(token in normalized_joined for token in ("report", "bulletin", "assessment", "analysis", "state-of-the-climate")):
        score += 6
    if any(token in normalized_joined for token in ("publication", "document", "download")):
        score += 4
    if any(token in normalized_joined for token in _FOLLOWUP_DETAIL_TOKENS):
        score += 3
    has_negative_token = any(token in normalized_joined for token in _FOLLOWUP_NEGATIVE_TOKENS)
    if has_negative_token:
        score -= 10
        if _looks_like_document_url(url):
            score -= 6
    if terminal in _FOLLOWUP_GENERIC_SEGMENTS or joined in _FOLLOWUP_GENERIC_SEGMENTS:
        score -= 8
    if terminal in _FOLLOWUP_LIST_SEGMENTS:
        score -= 8
    if any(token in normalized_joined for token in _DATA_PORTAL_TOKENS):
        score += 5 if _query_prefers_data_pages(query) else -6
    if _query_prefers_report_pages(query) and any(token in normalized_joined for token in ("report", "publication", "document")):
        score += 4
    if query_keywords:
        overlap = sum(1 for token in query_keywords if token in normalized_joined)
        score += min(overlap, 3) * 2
    if _looks_like_identifier_detail_page(path_segments):
        score += 2
    if "kenya" in query_lower and "kenya" in normalized_joined:
        score += 2
    return score


def _rank_followup_candidates(query: str, candidates: list[str]) -> list[str]:
    ranked: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for idx, url in enumerate(candidates):
        norm = ResearchTracker._normalize_url(url)
        if norm in seen:
            continue
        seen.add(norm)
        score = _score_followup_candidate(query, url)
        if score > 0:
            ranked.append((score, idx, url))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [url for _, _, url in ranked]


def _is_promising_followup_url(url: str, base_domain: str, query: str = "") -> bool:
    """Heuristic filter for follow-up links discovered inside scraped pages."""
    if not _is_same_domain(url, base_domain):
        return False

    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path and not parsed.query:
        return False

    segments = [seg.lower() for seg in path.split("/") if seg]
    if not segments:
        return False

    terminal = segments[-1]
    non_index_segments = [seg for seg in segments if seg not in {"index", "index.html"}]
    joined = "/".join(non_index_segments)
    normalized_joined = joined.replace("_", "-")

    if terminal in _FOLLOWUP_NEGATIVE_TOKENS:
        return False
    if _looks_like_paginated_index_page(url):
        return False
    if terminal in _FOLLOWUP_GENERIC_SEGMENTS and len(non_index_segments) <= 2:
        return False
    if joined in _FOLLOWUP_GENERIC_SEGMENTS:
        return False
    if non_index_segments and non_index_segments[0] in _FOLLOWUP_NEGATIVE_TOKENS:
        if not any(tok in normalized_joined for tok in _FOLLOWUP_POSITIVE_TOKENS):
            return False
    if any(tok in normalized_joined for tok in _DATA_PORTAL_TOKENS):
        return _query_prefers_data_pages(query)

    # Shallow generic pages are poor follow-up targets unless they clearly
    # expose a document or data endpoint via the query string.
    if len(non_index_segments) <= 2 and not parsed.query:
        return _score_followup_candidate(query, url) > 3

    return _score_followup_candidate(query, url) > 0


def _extract_links_from_markdown(content: str) -> list[str]:
    """Extract all HTTP(S) URLs from markdown content regardless of position or formatting."""
    seen: set[str] = set()
    links: list[str] = []
    # Match [text](url) first, then bare URLs not inside a markdown link
    for m in _re.finditer(r'\]\((https?://[^\s)]+)\)|(https?://\S+)', content):
        url = m.group(1) or m.group(2)
        url = url.rstrip(".,;)>\"'")
        if url and url not in seen:
            seen.add(url)
            links.append(url)
    return links


async def _rerank_followup_urls(
    *,
    query: str,
    parent_url: str,
    parent_content: str,
    candidates: list[str],
    cap: int,
    model: Any,
) -> list[str]:
    """Use a small LLM to choose the most promising follow-up URLs."""
    ranked_candidates = _rank_followup_candidates(query, candidates)
    if not ranked_candidates:
        return []
    shortlist = ranked_candidates[: max(cap * 3, cap)]
    if len(candidates) <= cap or len(shortlist) == 1:
        return shortlist[:cap]

    candidate_norm_map = OrderedDict(
        (ResearchTracker._normalize_url(url), url) for url in shortlist
    )
    parent_excerpt = " ".join(parent_content.split())[:1800]
    prompt = (
        f"Research query: {query}\n"
        f"Parent page: {parent_url}\n"
        f"Parent excerpt:\n{parent_excerpt}\n\n"
        f"Select up to {cap} URLs from this candidate list that are MOST likely to directly answer the query.\n"
        "Prefer report pages, document downloads, and specific detail pages with concrete evidence.\n"
        "Only choose data portals, datasets, or maproom pages when the query explicitly looks data-oriented.\n"
        "Avoid homepages, generic navigation pages, category/list pages, service hubs, and operational forecast/warning pages.\n"
        "Return exact URLs from the list only.\n\n"
        "Candidates:\n" + "\n".join(f"- {url}" for url in shortlist)
    )
    selector = Agent(
        name="followup_selector",
        model=model,
        output_type=FollowupSelection,
        model_settings=ModelSettings(),
        instructions=(
            "You rank same-domain follow-up URLs for a web research pipeline. "
            "Only select from the provided candidates. "
            "Return the few URLs most likely to contain direct evidence for the query."
        ),
    )
    try:
        result = await Runner.run(selector, prompt)
        raw_selected = result.final_output_as(FollowupSelection).selected_urls
    except Exception as e:
        logger.warning("[pipeline] follow-up URL reranker failed for %s: %s", parent_url, e)
        return shortlist[:cap]

    selected: list[str] = []
    for url in raw_selected:
        norm = ResearchTracker._normalize_url(url)
        original = candidate_norm_map.get(norm)
        if original and original not in selected:
            selected.append(original)
        if len(selected) >= cap:
            break

    if not selected:
        return shortlist[:cap]

    logger.info("[pipeline] reranked %d follow-up candidates for %s → %d selected", len(shortlist), parent_url, len(selected))
    return selected


def _normalize_domain(d: str) -> str:
    """Strip scheme, www., path, and trailing whitespace from a domain string.

    Ensures include_domains entries like 'https://www.wocat.net/en/' are treated
    identically to 'wocat.net'.
    """
    d = d.strip().lower()
    if "://" in d:
        d = urlparse(d).netloc
    # Strip any path component
    d = d.split("/")[0]
    # Strip port (keep domain only)
    d = d.split(":")[0]
    return d.removeprefix("www.")


def _build_allowed_domain_set(
    allowed_domains: Optional[List[str]] = None,
    include_domains: Optional[List[str]] = None,
    direct_url: Optional[str] = None,
) -> Optional[frozenset[str]]:
    """Build the effective allow-list for blocked-domain overrides.

    Explicit user intent should override the default blocklist:
    - `allowed_domains` opt specific domains back in manually
    - `include_domains` means domain-restricted mode, so those domains are allowed
    - `direct_url` means the explicitly requested URL's domain is allowed
    """
    effective: set[str] = set()

    if allowed_domains:
        effective.update(_normalize_domain(d) for d in allowed_domains)
    if include_domains:
        effective.update(_normalize_domain(d) for d in include_domains)
    if direct_url:
        effective.add(_normalize_domain(direct_url))

    return frozenset(effective) if effective else None


def _is_domain_mode_candidate(url: str, include_domains: list[str], query: str) -> bool:
    return any(_is_promising_followup_url(url, domain, query=query) for domain in include_domains)


def _build_synth_prompt(
    query: str,
    scraped: list,
    snippet_only: list,
    bot_detected: list,
    blocked_by_policy: list,
    scrape_failed: list,
    source_http_error: list,
    domain_expertise: Optional[str],
) -> str:
    """Build the synthesis prompt from scraped content and failure context.

    Includes a source count, thin-coverage warning when fewer than 3 sources
    were scraped, a list of sources that could not be accessed (so the
    synthesiser knows the limits of its evidence), and the scraped/snippet JSON.
    """
    import json as _json

    scraped_json = [
        {"url": e.url, "title": e.title or e.url, "content": e.content}
        for e in scraped
    ]
    snippet_json = [
        {"url": e.url, "title": e.title or e.url, "snippet": e.content}
        for e in snippet_only
        if e.content
    ]

    prompt = f"Research Query: {query}\n\n"
    if domain_expertise:
        prompt += f"Domain Expertise: {domain_expertise}\n\n"

    # Source count + thin-coverage warning
    n = len(scraped)
    prompt += f"You have {n} successfully scraped source(s) to work with.\n"
    if n < 3:
        prompt += (
            f"⚠ THIN COVERAGE: Only {n} source(s) available. "
            "Synthesize ONLY what these sources contain. "
            "Explicitly state any data the query asks for that is NOT in these sources. "
            "Do NOT fill gaps from training knowledge.\n"
        )
    prompt += "\n"

    # Failure context
    failure_lines: list[str] = []
    for e in bot_detected:
        failure_lines.append(f"  - {e.url} [bot-blocked: content could not be read]")
    for e in blocked_by_policy:
        domain = urlparse(e.url).netloc.lower()
        failure_lines.append(f"  - {domain} [policy-blocked: not attempted]")
    for e in scrape_failed + source_http_error:
        failure_lines.append(f"  - {e.url} [failed: {(e.content or '')[:80]}]")
    if failure_lines:
        prompt += (
            "SOURCES THAT COULD NOT BE ACCESSED"
            " — do NOT cite these, do not assume what they contain:\n"
            + "\n".join(failure_lines[:10])
            + "\n\n"
        )

    # Scraped and snippet content
    if not scraped and not snippet_json:
        prompt += "(No sources were found. You must state that no evidence was found.)\n"
    else:
        if scraped_json:
            prompt += f"Scraped sources (full extracts):\n{_json.dumps(scraped_json, indent=2)}\n\n"
        if snippet_json:
            prompt += f"Additional sources (search snippets only):\n{_json.dumps(snippet_json, indent=2)}\n\n"

    prompt += "Provide the 'synthesis' of the findings directly answering the query.\n"
    return prompt


async def run_web_research(
    query: str,
    models: Dict[str, str],
    include_domains: Optional[List[str]] = None,
    direct_url: Optional[str] = None,
    search_backend: str = "serper",
    domain_expertise: Optional[str] = None,
    research_depth: str = "standard",
    allowed_domains: Optional[List[str]] = None,
    max_pdf_pages: int = 50,
    max_content_chars: int = 30_000,
) -> WebResearchResult:
    """Run deterministic web research pipeline.

    Supports three input modes via argument combinations:

    - **Query-only**: ``query`` only — open web search + extraction.
    - **Domain + query**: ``query`` + ``include_domains`` — domain-
      restricted search + extraction.
    - **Direct URL**: ``query`` + ``direct_url`` — extract the given URL
      directly.

    Args:
        query: The research query.
        models: Model names keyed by role: ``"web_researcher"`` and
            ``"content_extractor"``. You can also pass ``"query_generator"``,
            ``"coverage_evaluator"``, and ``"synthesiser"`` to override the
            ``"web_researcher"`` model for specific steps. Pass
            ``"vision_fallback"`` (e.g. ``"gemini/gemini-2.0-flash"``) to
            enable screenshot-based extraction when text scraping fails
            (scanned PDFs, empty JS pages). Omit to keep the fallback
            disabled.
        include_domains: Restrict search to these domains.
        direct_url: Scrape this URL directly (disables web search). If the
            page is detected as a database/list page (hub), the pipeline
            automatically follows up to ``hub_deepening_cap`` item links
            (10 for ``"standard"``, 15 for ``"deep"``) and performs one
            hop of pagination when a "next page" link is present.
        search_backend: ``"serper"`` (default) or ``"duckduckgo"``.
        domain_expertise: Optional area of expertise (e.g. "biodiversity").
        research_depth: ``"standard"`` (default) or ``"deep"``. Deep mode
            generates more search queries, scrapes more URLs per iteration,
            and runs an extra iteration for better coverage on complex queries.
        allowed_domains: Domains to remove from the default block list (e.g.
            ``["reddit.com"]`` to allow Reddit pages). The block list covers
            social media and video platforms by default. Pass ``None`` (default)
            to use the full block list unchanged.
        max_pdf_pages: Maximum number of pages to extract from PDFs. Defaults
            to 50. Reduce for faster processing of large reports.
        max_content_chars: Maximum characters to return per scraped page.
            Defaults to 30,000. Increase for more complete content at the cost
            of higher token usage.

    Returns:
        ``WebResearchResult`` with URLs grouped by action (scraped,
        scrape_failed, snippet_only) and query metadata.
    """
    from .search_backends import DuckDuckGoBackend, SerperBackend
    from .utils import get_model

    # Resolve depth preset
    if research_depth not in _DEPTH_PRESETS:
        raise ValueError(f"Unknown research_depth={research_depth!r}. Use 'standard' or 'deep'.")
    depth = _DEPTH_PRESETS[research_depth]

    # Normalize include_domains: strip scheme, www., paths so callers can pass
    # "https://wocat.net/en/" or "www.wocat.net" and get correct results.
    if include_domains:
        include_domains = [_normalize_domain(d) for d in include_domains]

    # Explicit user intent should override the default blocked-domain list:
    # - allowed_domains: manual opt-in
    # - include_domains: domain-restricted mode
    # - direct_url: explicitly requested URL
    _allowed = _build_allowed_domain_set(
        allowed_domains=allowed_domains,
        include_domains=include_domains,
        direct_url=direct_url,
    )

    # Create shared tracker
    tracker = ResearchTracker()

    # Allow fallback to "web_researcher" for backward compatibility
    fallback_model = models.get("web_researcher", DEFAULT_WEB_RESEARCH_MODELS["web_researcher"])

    query_gen_model = get_model(models.get("query_generator", fallback_model))
    evaluator_model = get_model(models.get("coverage_evaluator", fallback_model))
    synth_model = get_model(models.get("synthesiser", fallback_model))
    followup_model = get_model(models.get("followup_selector", DEFAULT_WEB_RESEARCH_MODELS["followup_selector"]))
    extractor_model = get_model(models.get("content_extractor", DEFAULT_WEB_RESEARCH_MODELS["content_extractor"]))
    vision_model = models.get("vision_fallback", DEFAULT_WEB_RESEARCH_MODELS["vision_fallback"])

    # Setup Scrape Tool
    scrape_tool = create_scrape_and_extract_tool(
        extractor_model=extractor_model,
        tracker=tracker,
        query=query,
        vision_model=vision_model,
        allowed_domains=_allowed,
        max_pdf_pages=max_pdf_pages,
        max_content_chars=max_content_chars,
    )

    logger.info("[pipeline] start  query=%r  backend=%s mode=%s depth=%s",
                query[:80], search_backend,
                "direct" if direct_url else "domain" if include_domains else "open",
                research_depth)

    if direct_url:
        from .models import SearchQuery
        # --- 1. DIRECT URL MODE ---
        logger.info("[pipeline] scraping direct URL: %s", direct_url)
        # Force a record of the query (since we didn't search) to keep logs clean
        tracker._queries.append(SearchQuery(query=query, num_results_returned=1, domains_restricted=[]))

        content = await scrape_tool(direct_url)

        # Deepen if relevant links are found
        is_hub = "**Page type: list**" in content

        if is_hub:
            # Hub page: collect LLM-ranked item links, one-hop pagination
            candidates = []
            for link in _extract_links_from_markdown(content):
                if link and link not in candidates:
                    candidates.append(link)

            next_page = _find_next_page_url(content, direct_url)
            if next_page:
                logger.info("[pipeline] hub pagination: scraping next page %s", next_page)
                next_content = await scrape_tool(next_page)
                for link in _extract_links_from_markdown(next_content):
                    if link and link not in candidates:
                        candidates.append(link)

            hub_cap = depth["hub_deepening_cap"]
            if candidates:
                chosen = await _rerank_followup_urls(
                    query=query,
                    parent_url=direct_url,
                    parent_content=content,
                    candidates=candidates,
                    cap=hub_cap,
                    model=followup_model,
                )
                logger.info("[pipeline] hub deepening on %d candidate links (cap=%d)", len(chosen), hub_cap)
                tasks = [scrape_tool(link) for link in chosen]
                await _gather_scrapes(tasks)

        else:
            # Non-hub: only deepen HTML pages; explicit document URLs are already
            # the target artifact and usually should not fan out into site chrome.
            if _looks_like_document_url(direct_url):
                links_to_deepen = []
            else:
                links_to_deepen = _extract_links_from_markdown(content)

            # Existing behaviour — follow up to 3 same-domain links
            if links_to_deepen:
                direct_domain = urlparse(direct_url).netloc.lower()
                if direct_domain.startswith("www."):
                    direct_domain = direct_domain[4:]

                same_domain_links = []
                if direct_domain:
                    for link in links_to_deepen:
                        if _is_promising_followup_url(link, direct_domain, query=query):
                            same_domain_links.append(link)

                if same_domain_links:
                    chosen = await _rerank_followup_urls(
                        query=query,
                        parent_url=direct_url,
                        parent_content=content,
                        candidates=same_domain_links,
                        cap=min(3, len(same_domain_links)),
                        model=followup_model,
                    )
                    logger.info("[pipeline] deepening on %d links from direct URL", len(chosen))
                    tasks = [scrape_tool(link) for link in chosen]
                    await _gather_scrapes(tasks)

    else:
        # --- 2. SEARCH MODE (Open Web or Domain Restricted) ---

        # 2a. Setup Search Backend
        if search_backend == "serper":
            key = os.getenv("SERPER_API_KEY", "")
            if not key:
                raise ValueError("search_backend='serper' requires SERPER_API_KEY env var")
            backend = SerperBackend(key)
        elif search_backend == "duckduckgo":
            logger.warning(
                "[pipeline] DuckDuckGo backend selected — this is a development/fallback option only. "
                "Result quality is significantly lower than Serper: no date, rank, PAA, or Knowledge Graph "
                "metadata, and ddgs>=9.0 is known to return irrelevant results. "
                "Set SERPER_API_KEY and use search_backend='serper' for production use."
            )
            backend = DuckDuckGoBackend()
        else:
            raise ValueError(f"Unknown search_backend={search_backend!r}")

        # 2b. Generate Search Queries
        query_gen_agent = Agent(
            name="query_generator",
            model=query_gen_model,
            output_type=SearchQueryGeneration,
            instructions=QUERY_GENERATOR_INSTRUCTIONS + (f"\nDomain Expertise: {domain_expertise}" if domain_expertise else "")
        )

        evaluator_agent = Agent(
            name="coverage_evaluator",
            model=evaluator_model,
            output_type=CoverageEvaluation,
            instructions=COVERAGE_EVALUATOR_INSTRUCTIONS + (f"\nDomain Expertise: {domain_expertise}" if domain_expertise else "")
        )

        # Routing state carried across iterations
        needs_new_searches = True
        promising_urls_from_evaluator: List[str] = []
        missing_info = ""

        for iteration in range(depth["max_iterations"]):
            logger.info("[pipeline] starting search iteration %d", iteration + 1)

            if iteration > 0 and not needs_new_searches:
                # 2b-alt. Evaluator flagged promising unscraped URLs — skip search entirely
                urls_to_scrape = promising_urls_from_evaluator
                logger.info("[pipeline] skipping search; scraping %d evaluator-selected backlog URLs", len(urls_to_scrape))
                extracted_contents = []
                if urls_to_scrape:
                    tasks = [scrape_tool(url) for url in urls_to_scrape]
                    extracted_contents = await _gather_scrapes(tasks)
                    iter_results = list(zip(urls_to_scrape, extracted_contents))
                else:
                    logger.info("[pipeline] no promising backlog URLs to scrape")
                    extracted_contents = []
                    iter_results = []
            else:
                # 2b. Normal path: generate queries, search, triage, scrape
                if iteration == 0:
                    n_queries = depth["queries_first"]
                    prompt = f"Research Query: {query}\nGenerate exactly {n_queries} distinct search queries.\n"
                    if include_domains:
                        prompt += f"Note: We will search exclusively within these domains: {', '.join(include_domains)}\n"
                else:
                    n_queries = depth["queries_followup"]
                    prompt = f"Research Query: {query}\n"
                    prompt += f"We have already scraped some content, but we are missing: {missing_info}\n"
                    prompt += f"Generate exactly {n_queries} new distinct search queries specifically targeting this missing information.\n"
                    if include_domains:
                        prompt += f"Note: We will search exclusively within these domains: {', '.join(include_domains)}\n"

                try:
                    gen_res = await Runner.run(query_gen_agent, prompt)
                    search_queries = gen_res.final_output_as(SearchQueryGeneration).queries
                except Exception as e:
                    logger.error("[pipeline] query generation failed: %s", e)
                    search_queries = []

                if not search_queries:
                    search_queries = [query]

                logger.info("[pipeline] generated %d queries: %s", len(search_queries), search_queries)

                # 2c. Execute Searches in parallel
                async def do_search(q: str):
                    try:
                        resp = await backend.search(q, max_results=10, include_domains=include_domains)
                        tracker.record_search(q, len(resp.results), include_domains, resp.results)
                        return resp
                    except Exception as e:
                        logger.error("[pipeline] search failed for %r: %s", q, e)
                        return None

                search_results = await asyncio.gather(*(do_search(q) for q in search_queries))

                # 2d. Triage URLs
                # Interleave results from the queries to get a diverse set of top URLs
                unique_urls = OrderedDict()
                max_urls_to_scrape = depth["urls_first"] if iteration == 0 else depth["urls_followup"]
                idx = 0

                max_results_len = max([len(resp.results) for resp in search_results if resp] + [0])

                while len(unique_urls) < max_urls_to_scrape and idx < max_results_len:
                    for resp in search_results:
                        if resp and idx < len(resp.results):
                            url = resp.results[idx].url
                            norm = tracker._normalize_url(url)
                            if include_domains and not _is_domain_mode_candidate(url, include_domains, query):
                                continue
                            # We must ensure we don't pick URLs we already scraped/tried
                            if norm not in unique_urls and tracker._actions.get(norm, "snippet_only") == "snippet_only":
                                unique_urls[norm] = url
                            if len(unique_urls) >= max_urls_to_scrape:
                                break
                    idx += 1

                urls_to_scrape = list(unique_urls.values())
                logger.info("[pipeline] selected %d new URLs to scrape", len(urls_to_scrape))

                # 2e. Scrape & Extract in parallel
                extracted_contents = []
                if urls_to_scrape:
                    tasks = [scrape_tool(url) for url in urls_to_scrape]
                    extracted_contents = await _gather_scrapes(tasks)
                    iter_results = list(zip(urls_to_scrape, extracted_contents))
                else:
                    logger.info("[pipeline] no new URLs to scrape")
                    extracted_contents = []
                    iter_results = []

            # 2f. Deepen (Domain Restricted Mode)
            if include_domains:
                count_scraped = len(tracker.build_result_groups()["scraped"])

                # Detect hub pages in this iteration's results
                hub_results = [
                    (url, c) for url, c in iter_results
                    if "**Page type: list**" in c
                ]

                if hub_results:
                    # Hub deepening: collect LLM-ranked item links from all hub pages
                    candidates = []
                    for hub_url, hub_content in hub_results:
                        for link in _extract_links_from_markdown(hub_content):
                            if link:
                                parsed_netloc = urlparse(link).netloc.lower()
                                if any(parsed_netloc == d.lower() or parsed_netloc.endswith("." + d.lower()) for d in include_domains):
                                    if tracker._normalize_url(link) not in tracker._actions and any(
                                        _is_promising_followup_url(link, d.lower(), query=query) for d in include_domains
                                    ):
                                        if link not in candidates:
                                            candidates.append(link)

                        # One-hop pagination per hub
                        next_page = _find_next_page_url(hub_content, hub_url)
                        if next_page and tracker._normalize_url(next_page) not in tracker._actions:
                            logger.info("[pipeline] hub pagination (domain mode): %s", next_page)
                            next_content = await scrape_tool(next_page)
                            for link in _extract_links_from_markdown(next_content):
                                if link:
                                    parsed_netloc = urlparse(link).netloc.lower()
                                    if any(parsed_netloc == d.lower() or parsed_netloc.endswith("." + d.lower()) for d in include_domains):
                                        if tracker._normalize_url(link) not in tracker._actions and any(
                                            _is_promising_followup_url(link, d.lower(), query=query) for d in include_domains
                                        ):
                                            if link not in candidates:
                                                candidates.append(link)

                    hub_cap = depth["hub_deepening_cap"]
                    if candidates:
                        chosen = await _rerank_followup_urls(
                            query=query,
                            parent_url=hub_results[0][0],
                            parent_content=hub_results[0][1],
                            candidates=candidates,
                            cap=hub_cap,
                            model=followup_model,
                        )
                        logger.info("[pipeline] hub deepening (domain mode) on %d candidates (cap=%d)", len(chosen), hub_cap)
                        deep_tasks = [scrape_tool(link) for link in chosen]
                        await _gather_scrapes(deep_tasks)

                elif count_scraped < 2 and extracted_contents:
                    # Existing fallback: no hub detected, thin coverage — follow up to 3 links
                    links_to_deepen = []
                    for content in extracted_contents:
                        for link in _extract_links_from_markdown(content):
                            parsed_netloc = urlparse(link).netloc.lower()
                            if any(parsed_netloc == d.lower() or parsed_netloc.endswith("." + d.lower()) for d in include_domains):
                                if tracker._normalize_url(link) not in tracker._actions and any(
                                    _is_promising_followup_url(link, d.lower(), query=query) for d in include_domains
                                ):
                                    if link not in links_to_deepen:
                                        links_to_deepen.append(link)

                    if links_to_deepen:
                        parent_url = next((url for url, _ in iter_results if url), include_domains[0])
                        parent_content = next((c for _, c in iter_results if c), "")
                        deep_links = await _rerank_followup_urls(
                            query=query,
                            parent_url=parent_url,
                            parent_content=parent_content,
                            candidates=links_to_deepen,
                            cap=min(3, len(links_to_deepen)),
                            model=followup_model,
                        )
                        logger.info("[pipeline] domain restricted deepening on %d links: %s", len(deep_links), deep_links)
                        deep_tasks = [scrape_tool(link) for link in deep_links]
                        await _gather_scrapes(deep_tasks)

            # 2g. Evaluate Coverage
            if iteration < depth["max_iterations"] - 1:
                scraped_entries = tracker.build_result_groups()["scraped"]
                if not scraped_entries:
                    logger.info("[pipeline] 0 successful scrapes, doing another iteration")
                    missing_info = "Everything, no successful scrapes yet."
                    needs_new_searches = True
                    continue

                eval_prompt = f"Research Query: {query}\n\nScraped Content:\n"
                for i, entry in enumerate(scraped_entries, 1):
                    eval_prompt += f"--- Source {i}: {entry.title or entry.url} ---\n{entry.content}\n\n"

                snippet_only_entries = tracker.build_result_groups()["snippet_only"]
                if snippet_only_entries:
                    eval_prompt += "\nUnscraped Candidates (search snippets not yet scraped):\n"
                    for i, entry in enumerate(snippet_only_entries, 1):
                        eval_prompt += f"--- Candidate {i}: {entry.url} ---\n"
                        if entry.title:
                            eval_prompt += f"Title: {entry.title}\n"
                        if entry.content:
                            eval_prompt += f"Snippet: {entry.content}\n"
                        eval_prompt += "\n"

                try:
                    eval_res = await Runner.run(evaluator_agent, eval_prompt)
                    evaluation = eval_res.final_output_as(CoverageEvaluation)
                except Exception as e:
                    logger.error("[pipeline] coverage evaluation failed: %s", e)
                    evaluation = CoverageEvaluation(
                        fully_answered=False, gaps="Evaluator failed", promising_unscraped_urls=[], needs_new_searches=True
                    )

                logger.info(
                    "[pipeline] coverage evaluation: fully_answered=%s, gaps=%r, needs_new_searches=%s, promising_urls=%d",
                    evaluation.fully_answered, evaluation.gaps,
                    evaluation.needs_new_searches, len(evaluation.promising_unscraped_urls),
                )

                if evaluation.fully_answered:
                    logger.info("[pipeline] query fully answered, proceeding to synthesis")
                    break
                else:
                    missing_info = evaluation.gaps
                    needs_new_searches = evaluation.needs_new_searches

                    # Validate evaluator's URL picks against actual snippet_only set (hallucination guard)
                    snippet_norm_map = {
                        tracker._normalize_url(e.url): e.url
                        for e in snippet_only_entries
                    }
                    promising_urls_from_evaluator = [
                        snippet_norm_map[tracker._normalize_url(u)]
                        for u in evaluation.promising_unscraped_urls
                        if tracker._normalize_url(u) in snippet_norm_map
                    ][:depth["urls_followup"]]
                    promising_urls_from_evaluator = [
                        u for u in promising_urls_from_evaluator
                        if not _is_blocked_domain(u, allowed_domains=_allowed)
                    ]
                    if include_domains:
                        promising_urls_from_evaluator = [
                            u for u in promising_urls_from_evaluator
                            if _is_domain_mode_candidate(u, include_domains, query)
                        ]

                    if not needs_new_searches and not promising_urls_from_evaluator:
                        logger.info("[pipeline] evaluator said skip search but gave no valid backlog URLs; falling back to new searches")
                        needs_new_searches = True

    # --- 3. SYNTHESIZE ---
    groups = tracker.build_result_groups()
    scraped = groups["scraped"]
    scrape_failed = groups["scrape_failed"]
    blocked_by_policy = groups["blocked_by_policy"]
    source_http_error = groups["source_http_error"]
    scraped_irrelevant = groups["scraped_irrelevant"]
    bot_detected = groups["bot_detected"]
    snippet_only = groups["snippet_only"]

    synth_prompt = _build_synth_prompt(
        query=query,
        scraped=scraped,
        snippet_only=snippet_only,
        bot_detected=bot_detected,
        blocked_by_policy=blocked_by_policy,
        scrape_failed=scrape_failed,
        source_http_error=source_http_error,
        domain_expertise=domain_expertise,
    )

    synth_agent = Agent(
        name="synthesiser",
        model=synth_model,
        output_type=WebResearchResultRaw,
        instructions=SYNTHESISER_INSTRUCTIONS,
        model_settings=ModelSettings(extra_args={"reasoning_effort": "high"}),
    )

    # Only scraped URLs are valid citation targets. Snippet-only URLs are context
    # in the prompt but the synthesiser is instructed not to create markdown citations
    # for them — this prevents the model from using a real-looking snippet URL as
    # cover for training-data facts that never appeared in any extracted content.
    valid_urls = {entry.url for entry in scraped}

    if bot_detected:
        logger.info(
            "[pipeline] %d URL(s) blocked by bot-protection: %s",
            len(bot_detected), [e.url for e in bot_detected],
        )
    if blocked_by_policy:
        logger.info(
            "[pipeline] %d URL(s) skipped by policy: %s",
            len(blocked_by_policy), [e.url for e in blocked_by_policy],
        )

    try:
        synth_res = await Runner.run(synth_agent, synth_prompt)
        output = synth_res.final_output_as(WebResearchResultRaw)
    except Exception as e:
        logger.error("[pipeline] synthesis failed: %s", e)
        output = WebResearchResultRaw(synthesis=f"Synthesis failed: {e}")

    # --- Judge: check synthesis and ask synthesiser to fix if needed ---
    logger.info("[pipeline] running deterministic synthesis judge")
    issues = _judge_synthesis(output.synthesis, valid_urls)
    if issues and output.synthesis and not output.synthesis.startswith("Synthesis failed"):
        for issue in issues:
            logger.warning("[pipeline] judge issue: %s", issue)
        logger.warning("[pipeline] retrying synthesis due to %d issue(s)", len(issues))

        feedback = (
            "Your synthesis has the following citation issues that must be fixed:\n"
            + "\n".join(f"- {issue}" for issue in issues)
            + "\n\nPlease rewrite the synthesis fixing all issues. "
            "Keep all factual content unchanged."
        )
        retry_prompt = synth_prompt + f"\n\nPrevious attempt:\n{output.synthesis}\n\n{feedback}"
        try:
            synth_res2 = await Runner.run(synth_agent, retry_prompt)
            output = synth_res2.final_output_as(WebResearchResultRaw)
        except Exception as e:
            logger.error("[pipeline] synthesis retry failed: %s", e)
            # keep original output
    elif not issues and output.synthesis and not output.synthesis.startswith("Synthesis failed"):
        logger.info("[pipeline] synthesis passed judge with 0 issues")

    logger.info(
        "[pipeline] done scraped=%d failed=%d blocked=%d source_http=%d irrelevant=%d bot_detected=%d snippet_only=%d queries=%d",
        len(scraped), len(scrape_failed), len(blocked_by_policy), len(source_http_error),
        len(scraped_irrelevant), len(bot_detected), len(snippet_only), len(tracker.queries)
    )

    return WebResearchResult(
        scraped=scraped,
        scrape_failed=scrape_failed,
        blocked_by_policy=blocked_by_policy,
        source_http_error=source_http_error,
        scraped_irrelevant=scraped_irrelevant,
        bot_detected=bot_detected,
        snippet_only=snippet_only,
        queries=tracker.queries,
        synthesis=output.synthesis,
    )


# ---------------------------------------------------------------------------
# Quick manual test — run with: python -m web_scout.agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent.parent / ".env")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    QUERY = "What are the main threats to coral reefs in Australia?"
    MODELS = DEFAULT_WEB_RESEARCH_MODELS

    async def _main():
        result = await run_web_research(
            query=QUERY,
            models=MODELS,
            search_backend="serper",
        )

        print("\n" + "=" * 60)
        print("SYNTHESIS")
        print("=" * 60)
        print(result.synthesis)

        print("\n" + "=" * 60)
        print(f"SOURCES ({len(result.scraped)} scraped)")
        print("=" * 60)
        for s in result.scraped:
            print(f"  - {s.title or s.url}")
            print(f"    {s.url}")

        if result.scrape_failed:
            print(f"\nFailed to scrape {len(result.scrape_failed)} URL(s).")

        print("\n" + "=" * 60)
        print(f"QUERIES ({len(result.queries)} executed)")
        print("=" * 60)
        for q in result.queries:
            print(f"  - {q.query}")

    asyncio.run(_main())
