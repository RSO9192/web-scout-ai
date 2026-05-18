"""Pure helpers and prompt builders for the web research pipeline."""

from __future__ import annotations

import re as _re
from typing import Optional
from urllib.parse import parse_qsl, urljoin, urlparse

from ._heuristics import FOLLOWUP_HEURISTICS
from .tools import ResearchTracker

_NEXT_PAGE_TOKENS: frozenset[str] = frozenset({"next", "next page", "›", "»"})
_DOCUMENT_EXTENSIONS: tuple[str, ...] = (
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
)
_FOLLOWUP_POSITIVE_TOKENS: tuple[str, ...] = (
    "report",
    "document",
    "publication",
    "bulletin",
    "factsheet",
    "assessment",
    "recommendation",
    "summary",
    "execsum",
    "study",
    "analysis",
    "trend",
    "state-of-the-climate",
    "climatology",
    "monitoring",
    "dataset",
    "download",
    "article",
    "paper",
)
_FOLLOWUP_NEGATIVE_TOKENS: tuple[str, ...] = (
    "service",
    "services",
    "forecast",
    "daily-forecast",
    "weekly-forecast",
    "seasonal-forecast",
    "weather-warning",
    "weather-warnings",
    "warning",
    "warnings",
    "home",
    "homepage",
    "contact",
    "about",
    "vision-statement",
    "department-history",
    "geography-research",
    "mapviewer",
)
_FOLLOWUP_GENERIC_SEGMENTS: frozenset[str] = frozenset(
    {
        "publications",
        "publication",
        "our-products",
        "products",
        "services",
        "service",
        "weather",
        "climate",
        "resources",
        "library",
        "documents",
    }
)
_FOLLOWUP_LIST_SEGMENTS: frozenset[str] = frozenset(
    {
        "search",
        "results",
        "list",
        "listing",
        "archive",
        "archives",
        "browse",
        "catalog",
        "catalogue",
        "collection",
        "collections",
        "publications",
        "publications-full",
        "database",
    }
)
_FOLLOWUP_DETAIL_TOKENS: tuple[str, ...] = (
    "report",
    "document",
    "publication",
    "article",
    "paper",
    "brief",
    "factsheet",
    "assessment",
    "analysis",
    "countrybrief",
    "record",
    "item",
    "handle",
    "bitstream",
)
_DATA_PORTAL_TOKENS: tuple[str, ...] = ("maproom", "dataset", "data", "api", "csv", "thredds")
_QUERY_DATA_HINTS: tuple[str, ...] = (
    "dataset",
    "data portal",
    "maproom",
    "api",
    "csv",
    "download data",
    "timeseries",
    "time series",
    "gridded",
    "grid",
    "raster",
)
_QUERY_REPORT_HINTS: tuple[str, ...] = (
    "report",
    "trend",
    "variability",
    "assessment",
    "analysis",
    "current status",
    "recent trend",
    "state of the climate",
    "bulletin",
)
_QUERY_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "that",
        "this",
        "those",
        "these",
        "current",
        "recent",
        "status",
        "long",
        "term",
        "change",
        "changes",
        "pattern",
        "patterns",
        "spatial",
        "interannual",
        "variability",
        "trend",
        "trends",
    }
)


def _judge_synthesis(synthesis: str, valid_urls: set[str]) -> list[str]:
    """Return a list of issue descriptions, empty if synthesis passes."""
    issues = []
    text_without_md_links = _re.sub(r"\[[^\]]*\]\((https?://[^\s\)]+)\)", "", synthesis)
    bare_urls = [
        match.group().rstrip('.,;)"\'')
        for match in _re.finditer(r"https?://\S+", text_without_md_links)
    ]
    bare_urls = [url for url in bare_urls if url]
    if bare_urls:
        issues.append(
            "Bare URLs found (must be wrapped as markdown links [Title](URL)): "
            + ", ".join(bare_urls[:5])
        )

    md_link_urls = set(_re.findall(r"\[[^\]]*\]\((https?://[^\s\)]+)\)", synthesis))
    valid_norm = {ResearchTracker.normalize_url(url) for url in valid_urls}
    hallucinated = [
        url for url in md_link_urls if ResearchTracker.normalize_url(url) not in valid_norm
    ]
    if hallucinated:
        issues.append(
            "URLs cited that are NOT in the available sources (remove or replace): "
            + ", ".join(hallucinated[:5])
        )

    return issues


def _find_next_page_url(content: str, base_url: str) -> Optional[str]:
    """Scan markdown content for a same-domain next-page link."""
    base_netloc = urlparse(base_url).netloc.lower().removeprefix("www.")
    for match in _re.finditer(r"\[([^\]]*)\]\(([^\s\)\#][^\s\)]*)\)", content):
        link_text = match.group(1).strip().lower()
        href_raw = match.group(2)
        if href_raw.startswith(("mailto:", "javascript:", "tel:", "data:")):
            continue

        href = urljoin(base_url, href_raw).split("#")[0]
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
        or any(
            token in "/".join(path_segments)
            for token in ("handle", "record", "item", "bitstream")
        )
    )


def _score_followup_candidate(query: str, url: str) -> int:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    path_segments = [
        seg.lower()
        for seg in path.split("/")
        if seg and seg.lower() not in {"index", "index.html"}
    ]
    joined = "/".join(path_segments)
    normalized_joined = joined.replace("_", "-")
    terminal = joined.rsplit("/", 1)[-1] if joined else ""
    query_lower = query.lower()
    query_keywords = _extract_query_keywords(query)

    score = 0
    if _looks_like_paginated_index_page(url):
        score += FOLLOWUP_HEURISTICS.paginated_index_penalty
    if _looks_like_document_url(url):
        score += FOLLOWUP_HEURISTICS.document_bonus
    if any(
        token in normalized_joined
        for token in ("report", "bulletin", "assessment", "analysis", "state-of-the-climate")
    ):
        score += FOLLOWUP_HEURISTICS.report_bonus
    if any(token in normalized_joined for token in ("publication", "document", "download")):
        score += FOLLOWUP_HEURISTICS.publication_bonus
    if any(token in normalized_joined for token in _FOLLOWUP_DETAIL_TOKENS):
        score += FOLLOWUP_HEURISTICS.detail_token_bonus
    has_negative_token = any(token in normalized_joined for token in _FOLLOWUP_NEGATIVE_TOKENS)
    if has_negative_token:
        score += FOLLOWUP_HEURISTICS.negative_token_penalty
        if _looks_like_document_url(url):
            score += FOLLOWUP_HEURISTICS.negative_document_penalty
    if terminal in _FOLLOWUP_GENERIC_SEGMENTS or joined in _FOLLOWUP_GENERIC_SEGMENTS:
        score += FOLLOWUP_HEURISTICS.generic_segment_penalty
    if terminal in _FOLLOWUP_LIST_SEGMENTS:
        score += FOLLOWUP_HEURISTICS.list_segment_penalty
    if any(token in normalized_joined for token in _DATA_PORTAL_TOKENS):
        score += (
            FOLLOWUP_HEURISTICS.data_portal_bonus_for_data_query
            if _query_prefers_data_pages(query)
            else FOLLOWUP_HEURISTICS.data_portal_penalty_for_non_data_query
        )
    if _query_prefers_report_pages(query) and any(
        token in normalized_joined for token in ("report", "publication", "document")
    ):
        score += FOLLOWUP_HEURISTICS.report_query_bonus
    if query_keywords:
        overlap = sum(1 for token in query_keywords if token in normalized_joined)
        score += (
            min(overlap, FOLLOWUP_HEURISTICS.max_keyword_overlap_bonus_terms)
            * FOLLOWUP_HEURISTICS.keyword_overlap_bonus
        )
    if _looks_like_identifier_detail_page(path_segments):
        score += FOLLOWUP_HEURISTICS.identifier_detail_bonus
    if "kenya" in query_lower and "kenya" in normalized_joined:
        score += FOLLOWUP_HEURISTICS.kenya_bonus
    return score


def _rank_followup_candidates(query: str, candidates: list[str]) -> list[str]:
    ranked: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for idx, url in enumerate(candidates):
        norm = ResearchTracker.normalize_url(url)
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
    if len(non_index_segments) <= 2 and not parsed.query:
        return _score_followup_candidate(query, url) > FOLLOWUP_HEURISTICS.shallow_page_min_score
    return _score_followup_candidate(query, url) > 0


def _extract_links_from_markdown(content: str) -> list[str]:
    """Extract all HTTP(S) URLs from markdown content regardless of position or formatting."""
    seen: set[str] = set()
    links: list[str] = []
    for match in _re.finditer(r"\]\((https?://[^\s)]+)\)|(https?://\S+)", content):
        url = match.group(1) or match.group(2)
        url = url.rstrip(".,;)>\"'")
        if url and url not in seen:
            seen.add(url)
            links.append(url)
    return links


def _normalize_domain(value: str) -> str:
    """Normalize a hostname or URL down to its canonical domain."""
    value = value.strip().lower()
    if "://" in value:
        value = urlparse(value).netloc
    value = value.split("/")[0]
    value = value.split(":")[0]
    return value.removeprefix("www.")


def _build_allowed_domain_set(
    allowed_domains: Optional[list[str]] = None,
    include_domains: Optional[list[str]] = None,
    direct_url: Optional[str] = None,
) -> Optional[frozenset[str]]:
    """Build the effective allow-list for blocked-domain overrides."""
    effective: set[str] = set()
    if allowed_domains:
        effective.update(_normalize_domain(domain) for domain in allowed_domains)
    if include_domains:
        effective.update(_normalize_domain(domain) for domain in include_domains)
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
    """Build the synthesis prompt from scraped content and failure context."""
    import json as _json

    scraped_json = [
        {"url": entry.url, "title": entry.title or entry.url, "content": entry.content}
        for entry in scraped
    ]
    snippet_json = [
        {"url": entry.url, "title": entry.title or entry.url, "snippet": entry.content}
        for entry in snippet_only
        if entry.content
    ]

    prompt = f"Research Query: {query}\n\n"
    if domain_expertise:
        prompt += f"Domain Expertise: {domain_expertise}\n\n"

    count = len(scraped)
    prompt += f"You have {count} successfully scraped source(s) to work with.\n"
    if count < 3:
        prompt += (
            f"⚠ THIN COVERAGE: Only {count} source(s) available. "
            "Synthesize ONLY what these sources contain. "
            "Explicitly state any data the query asks for that is NOT in these sources. "
            "Do NOT fill gaps from training knowledge.\n"
        )
    prompt += "\n"

    failure_lines: list[str] = []
    for entry in bot_detected:
        failure_lines.append(f"  - {entry.url} [bot-blocked: content could not be read]")
    for entry in blocked_by_policy:
        domain = urlparse(entry.url).netloc.lower()
        failure_lines.append(f"  - {domain} [policy-blocked: not attempted]")
    for entry in scrape_failed + source_http_error:
        failure_lines.append(f"  - {entry.url} [failed: {(entry.content or '')[:80]}]")
    if failure_lines:
        prompt += (
            "SOURCES THAT COULD NOT BE ACCESSED"
            " — do NOT cite these, do not assume what they contain:\n"
            + "\n".join(failure_lines[:10])
            + "\n\n"
        )

    if not scraped and not snippet_json:
        prompt += "(No sources were found. You must state that no evidence was found.)\n"
    else:
        if scraped_json:
            prompt += f"Scraped sources (full extracts):\n{_json.dumps(scraped_json, indent=2)}\n\n"
        if snippet_json:
            prompt += (
                f"Additional sources (search snippets only):\n{_json.dumps(snippet_json, indent=2)}\n\n"
            )

    prompt += "Provide the 'synthesis' of the findings directly answering the query.\n"
    return prompt


def _build_query_generation_prompt(
    query: str,
    n_queries: int,
    include_domains: Optional[list[str]],
    missing_info: str = "",
) -> str:
    if missing_info:
        prompt = (
            f"Research Query: {query}\n"
            f"We have already scraped some content, but we are missing: {missing_info}\n"
            f"Generate exactly {n_queries} new distinct search queries specifically "
            "targeting this missing information.\n"
        )
    else:
        prompt = f"Research Query: {query}\nGenerate exactly {n_queries} distinct search queries.\n"
    if include_domains:
        prompt += (
            "Note: We will search exclusively within these domains: "
            f"{', '.join(include_domains)}\n"
        )
    return prompt


def _build_coverage_prompt(query: str, tracker: ResearchTracker) -> str:
    scraped_entries = tracker.entries_for_action("scraped")
    prompt = (
        f"Research Query: {query}\n"
        f"Successful scraped sources available as evidence: {len(scraped_entries)}\n\n"
        "Important:\n"
        "- Only the 'Scraped Content' section counts as evidence.\n"
        "- Do not use prior knowledge.\n"
        "- Do not treat titles/snippets from unscraped candidates as if they were extracted facts.\n\n"
        "Scraped Content:\n"
    )
    for idx, entry in enumerate(scraped_entries, 1):
        prompt += f"--- Source {idx}: {entry.title or entry.url} ---\n{entry.content}\n\n"

    snippet_only_entries = tracker.entries_for_action("snippet_only")
    if snippet_only_entries:
        prompt += "\nUnscraped Candidates (search snippets not yet scraped):\n"
        for idx, entry in enumerate(snippet_only_entries, 1):
            prompt += f"--- Candidate {idx}: {entry.url} ---\n"
            if entry.title:
                prompt += f"Title: {entry.title}\n"
            if entry.content:
                prompt += f"Snippet: {entry.content}\n"
            prompt += "\n"
    return prompt


def _filter_blocked_domain_backlog_urls(urls: list[str], tracker: ResearchTracker) -> list[str]:
    """Drop backlog URLs from domains already deemed bot-blocked this run."""
    return [url for url in urls if not tracker.is_domain_bot_blocked(url)]


def _diversify_search_urls(urls: list[str], max_urls: int) -> list[str]:
    """Prefer breadth across domains before taking multiple URLs per host."""
    if len(urls) <= 1:
        return urls[:max_urls]

    selected: list[str] = []
    seen_domains: set[str] = set()

    for url in urls:
        domain = _normalize_domain(url)
        if domain and domain not in seen_domains:
            selected.append(url)
            seen_domains.add(domain)
            if len(selected) >= max_urls:
                return selected

    for url in urls:
        if url in selected:
            continue
        selected.append(url)
        if len(selected) >= max_urls:
            break

    return selected


__all__ = [
    "_build_allowed_domain_set",
    "_build_coverage_prompt",
    "_build_query_generation_prompt",
    "_build_synth_prompt",
    "_diversify_search_urls",
    "_extract_links_from_markdown",
    "_extract_query_keywords",
    "_filter_blocked_domain_backlog_urls",
    "_find_next_page_url",
    "_is_domain_mode_candidate",
    "_is_promising_followup_url",
    "_is_same_domain",
    "_judge_synthesis",
    "_looks_like_document_url",
    "_looks_like_paginated_index_page",
    "_normalize_domain",
    "_query_prefers_data_pages",
    "_query_prefers_report_pages",
    "_rank_followup_candidates",
    "_score_followup_candidate",
]
