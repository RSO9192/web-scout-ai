"""Pure helpers and prompt builders for the web research pipeline."""

import re as _re
from typing import Optional
from urllib.parse import parse_qsl, urljoin, urlparse

from web_scout.config import FOLLOWUP_HEURISTICS
from web_scout.scraping.constants import BLOCKED_DOMAINS

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
    "monitoring",
    "dataset",
    "download",
    "article",
    "paper",
)
_FOLLOWUP_NEGATIVE_TOKENS: tuple[str, ...] = (
    "home",
    "homepage",
    "contact",
    "about",
    "vision-statement",
    "department-history",
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
        "section",
        "sections",
        "topic",
        "topics",
        "research-topic",
        "research-topics",
        "magazine",
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
_DATA_PORTAL_TOKENS: tuple[str, ...] = (
    "maproom",
    "dataset",
    "data",
    "api",
    "csv",
    "thredds",
)
_FOLLOWUP_HUB_PATH_TOKENS: frozenset[str] = frozenset(
    {
        "section",
        "sections",
        "topic",
        "topics",
        "research-topic",
        "research-topics",
        "magazine",
    }
)
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
    "bulletin",
)
_QUERY_FORECAST_HINTS: tuple[str, ...] = (
    "forecast",
    "outlook",
    "warning",
    "warnings",
    "advisory",
    "seasonal forecast",
    "monthly forecast",
    "weekly forecast",
    "daily forecast",
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
        "trend",
        "trends",
    }
)


# Matches [S1] or [S1, S3] citation tokens; the optional trailing (...) group
# swallows a URL the model wrongly attached so no invented link survives.
_SLOT_CITATION_RE = _re.compile(r"\[(S\d+(?:\s*,\s*S\d+)*)\](?:\([^)\s]*\))?")


def _build_citation_slots(scraped: list) -> dict[str, tuple[str, str]]:
    """Map slot id (``S1``, ``S2``, …) → ``(link text, url)`` for scraped entries.

    Ids are positional and must match the ordering used by ``_build_synth_prompt``.
    """
    return {
        f"S{idx}": (entry.reference or entry.title or entry.url, entry.url)
        for idx, entry in enumerate(scraped, 1)
    }


def _resolve_slot_citations(synthesis: str, slots: dict[str, tuple[str, str]]) -> tuple[str, list[str]]:
    """Replace slot-id citation tokens with markdown links.

    Returns the resolved text and the list of unknown slot ids (deduplicated,
    in order of appearance). Unknown ids are left in place as plain text so
    they are visible but never become a wrong link.
    """
    unknown: list[str] = []

    def _replace(match: "_re.Match[str]") -> str:
        parts: list[str] = []
        for slot_id in (token.strip() for token in match.group(1).split(",")):
            slot = slots.get(slot_id)
            if slot is None:
                if slot_id not in unknown:
                    unknown.append(slot_id)
                parts.append(slot_id)
            else:
                label, url = slot
                parts.append(f"[{label}]({url})")
        return ", ".join(parts)

    return _SLOT_CITATION_RE.sub(_replace, synthesis), unknown


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


def _query_prefers_forecast_pages(query: str) -> bool:
    query_lower = query.lower()
    return any(token in query_lower for token in _QUERY_FORECAST_HINTS)


def _extract_query_keywords(query: str) -> set[str]:
    return {token for token in _re.findall(r"[a-z0-9]{4,}", query.lower()) if token not in _QUERY_STOPWORDS}


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


def _looks_like_operational_forecast_or_warning_url(url: str) -> bool:
    normalized = urlparse(url).path.lower().replace("_", "-")
    return any(token in normalized for token in ("forecast", "warning", "warnings", "outlook", "advisory"))


def _looks_like_topic_or_section_hub_url(url: str) -> bool:
    parsed = urlparse(url)
    path_segments = [seg.lower() for seg in parsed.path.strip("/").split("/") if seg]
    if not path_segments or _looks_like_document_url(url):
        return False
    if not any(seg in _FOLLOWUP_HUB_PATH_TOKENS for seg in path_segments):
        return False
    if _looks_like_identifier_detail_page(path_segments):
        return False
    terminal = path_segments[-1]
    if terminal in _FOLLOWUP_HUB_PATH_TOKENS:
        return True
    joined = "/".join(path_segments)
    return "research-topics/" in joined or "/sections/" in joined


def _score_followup_candidate(query: str, url: str) -> int:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    path_segments = [seg.lower() for seg in path.split("/") if seg and seg.lower() not in {"index", "index.html"}]
    joined = "/".join(path_segments)
    normalized_joined = joined.replace("_", "-")
    terminal = joined.rsplit("/", 1)[-1] if joined else ""
    query_keywords = _extract_query_keywords(query)

    score = 0
    if _looks_like_paginated_index_page(url):
        score += FOLLOWUP_HEURISTICS.paginated_index_penalty
    if _looks_like_document_url(url):
        score += FOLLOWUP_HEURISTICS.document_bonus
    if any(token in normalized_joined for token in ("report", "bulletin", "assessment", "analysis")):
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
    return score


def _rank_followup_candidates(query: str, candidates: list[str]) -> list[str]:
    ranked: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for idx, url in enumerate(candidates):
        norm = ResearchTracker.normalize_url(url)
        if norm in seen:
            continue
        seen.add(norm)
        domain = urlparse(url).netloc.lower().split(":", 1)[0].removeprefix("www.")
        if not domain or not _is_promising_followup_url(url, domain, query=query):
            continue
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
    if _looks_like_topic_or_section_hub_url(url):
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
    if _looks_like_operational_forecast_or_warning_url(url) and not _query_prefers_forecast_pages(query):
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


def _build_exclude_domain_set(
    exclude_domains: Optional[list[str]] = None,
    include_domains: Optional[list[str]] = None,
    direct_url: Optional[str] = None,
) -> frozenset[str]:
    """Build the effective exclude/block list for URL exploration.

    Starts from ``BLOCKED_DOMAINS`` when *exclude_domains* is ``None``, otherwise
    from the caller-supplied list. Hostnames from *include_domains* and
    *direct_url* are subtracted so those targets remain reachable.
    """
    if exclude_domains is None:
        effective: set[str] = set(BLOCKED_DOMAINS)
    else:
        effective = {_normalize_domain(domain) for domain in exclude_domains}
    if include_domains:
        effective -= {_normalize_domain(domain) for domain in include_domains}
    if direct_url:
        effective.discard(_normalize_domain(direct_url))
    return frozenset(effective)


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

    # No URLs in the scraped entries: the model cites slot ids (S1, S2, …) that
    # are resolved to real links mechanically after synthesis, so URL
    # hallucination is impossible by construction. Ids are positional and must
    # match _build_citation_slots.
    scraped_json = [
        {
            "id": f"S{idx}",
            "title": entry.title or urlparse(entry.url).netloc,
            "content": entry.content,
        }
        for idx, entry in enumerate(scraped, 1)
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
            " — do NOT cite these, do not assume what they contain:\n" + "\n".join(failure_lines[:10]) + "\n\n"
        )

    if not scraped and not snippet_json:
        prompt += "(No sources were found. You must state that no evidence was found.)\n"
    else:
        if scraped_json:
            prompt += f"Scraped sources (full extracts):\n{_json.dumps(scraped_json, indent=2)}\n\n"
        if snippet_json:
            prompt += f"Additional sources (search snippets only):\n{_json.dumps(snippet_json, indent=2)}\n\n"

    prompt += (
        "Provide the 'synthesis' of the findings directly answering the query. "
        "Cite scraped sources by their id in square brackets, e.g. [S1].\n"
    )
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
        prompt += f"Note: We will search exclusively within these domains: {', '.join(include_domains)}\n"
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
    "_build_citation_slots",
    "_build_exclude_domain_set",
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
    "_looks_like_document_url",
    "_looks_like_paginated_index_page",
    "_normalize_domain",
    "_query_prefers_data_pages",
    "_query_prefers_report_pages",
    "_rank_followup_candidates",
    "_resolve_slot_citations",
    "_score_followup_candidate",
]
