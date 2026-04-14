"""Pluggable search backends for web discovery.

Provides a ``SearchBackend`` ABC with two concrete implementations:

- ``DuckDuckGoBackend`` — zero-config, no API key needed
- ``SerperBackend``     — Google-quality results via serper.dev
                          (requires ``SERPER_API_KEY`` env var)
"""

from __future__ import annotations

import abc
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Single search result from any backend."""

    title: str
    url: str
    snippet: str
    date: str = ""      # publication date when available (Serper only)
    position: int = 0   # Google rank position (Serper only)


@dataclass
class PeopleAlsoAsk:
    """A 'People Also Ask' Q&A pair from Google (Serper only)."""

    question: str
    snippet: str
    link: str = ""


@dataclass
class KnowledgeGraph:
    """Google's entity knowledge card (Serper only)."""

    title: str
    description: str = ""
    entity_type: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)


@dataclass
class SearchResponse:
    """Search results plus optional metadata from the backend."""

    results: List[SearchResult]
    related_searches: List[str]
    people_also_ask: List[PeopleAlsoAsk] = field(default_factory=list)
    knowledge_graph: Optional[KnowledgeGraph] = None


class SearchBackend(abc.ABC):
    """Abstract interface for web search backends."""

    @abc.abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 5,
        include_domains: Optional[List[str]] = None,
    ) -> SearchResponse:
        ...


def _domain_matches(url: str, allowed_domains: List[str]) -> bool:
    """Check if a URL's domain ends with one of the allowed domains."""
    netloc = urlparse(url).netloc.lower()
    return any(netloc == d or netloc.endswith(f".{d}") for d in allowed_domains)


class DuckDuckGoBackend(SearchBackend):
    """Meta-search via the ``duckduckgo-search`` library.

    Zero-config — no API key needed.  Uses the ``site:`` operator for
    domain filtering plus post-filtering to ensure strictness.
    Retries with exponential backoff on rate-limit errors.
    """

    _MAX_RETRIES = 3
    _BASE_DELAY = 1.0  # seconds

    async def search(
        self,
        query: str,
        max_results: int = 5,
        include_domains: Optional[List[str]] = None,
    ) -> SearchResponse:
        from duckduckgo_search import DDGS

        effective_query = query
        if include_domains:
            site_clause = " OR ".join(f"site:{d}" for d in include_domains)
            effective_query = f"({site_clause}) {query}"

        # Request extra results when filtering, since some may be stripped
        fetch_count = max_results * 3 if include_domains else max_results

        def _sync_search() -> list:
            return list(DDGS().text(effective_query, max_results=fetch_count))

        raw: list = []
        for attempt in range(self._MAX_RETRIES):
            try:
                raw = await asyncio.to_thread(_sync_search)
                break
            except Exception as exc:
                is_rate_limit = "ratelimit" in type(exc).__name__.lower()
                if is_rate_limit and attempt < self._MAX_RETRIES - 1:
                    delay = self._BASE_DELAY * (2**attempt)
                    logger.warning(
                        "DuckDuckGo rate-limited (attempt %d/%d), "
                        "retrying in %.1fs",
                        attempt + 1,
                        self._MAX_RETRIES,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error("DuckDuckGo search failed: %s", exc)
                    raise

        results = [
            SearchResult(
                title=r.get("title", "Untitled"),
                url=r.get("href", ""),
                snippet=r.get("body", ""),
            )
            for r in raw
            if r.get("href")
        ]

        if include_domains:
            domains_lower = [d.lower() for d in include_domains]
            results = [r for r in results if _domain_matches(r.url, domains_lower)]

        # Quality guard: warn when results look off-topic (no query keyword overlaps).
        # ddgs>=9.x ("Dux Distributed") is known to return irrelevant results from
        # aggregated engines when rate-limited or when Bing result parsing fails.
        if results and query:
            query_words = {w.lower() for w in query.split() if len(w) > 3}
            on_topic = [
                r for r in results
                if any(w in (r.title + " " + r.snippet).lower() for w in query_words)
            ]
            if on_topic:
                results = on_topic
            else:
                logger.warning(
                    "[DuckDuckGo] all %d result(s) appear off-topic for query %r — "
                    "this is a known ddgs>=9.0 issue. Consider using search_backend='serper'.",
                    len(results), query[:80],
                )
                results = []

        # DuckDuckGo doesn't provide related searches, PAA, or KG
        return SearchResponse(
            results=results[:max_results],
            related_searches=[],
        )  # people_also_ask and knowledge_graph default to empty


class SerperBackend(SearchBackend):
    """Google Search via Serper.dev API.

    Requires ``SERPER_API_KEY`` environment variable.  Returns Google-quality
    results with rich snippets.  The ``site:`` operator is natively strict
    in Google, so no post-filtering is needed.

    Retries up to ``_MAX_RETRIES`` times on HTTP 429 (rate-limit) or 5xx
    (transient server errors) with exponential backoff.
    """

    _MAX_RETRIES = 3
    _BASE_DELAY = 1.0  # seconds; doubles each retry

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def search(
        self,
        query: str,
        max_results: int = 5,
        include_domains: Optional[List[str]] = None,
    ) -> SearchResponse:
        import httpx

        effective_query = query
        if include_domains:
            site_clause = " OR ".join(f"site:{d}" for d in include_domains)
            effective_query = f"({site_clause}) {query}"

        last_exc: Optional[Exception] = None
        data: dict = {}
        async with httpx.AsyncClient(timeout=15) as client:
            for attempt in range(self._MAX_RETRIES):
                resp = await client.post(
                    "https://google.serper.dev/search",
                    headers={
                        "X-API-KEY": self._api_key,
                        "Content-Type": "application/json",
                    },
                    json={"q": effective_query, "num": max_results},
                )
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self._MAX_RETRIES - 1:
                    delay = self._BASE_DELAY * (2 ** attempt)
                    reason = "rate-limited" if resp.status_code == 429 else f"server error {resp.status_code}"
                    logger.warning(
                        "[Serper] %s (attempt %d/%d), retrying in %.1fs",
                        reason, attempt + 1, self._MAX_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break

        results = [
            SearchResult(
                title=item.get("title", "Untitled"),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
                date=item.get("date", ""),
                position=item.get("position", 0),
            )
            for item in data.get("organic", [])
            if item.get("link")
        ][:max_results]

        # Related searches
        related = [
            item.get("query", "")
            for item in data.get("relatedSearches", [])
            if item.get("query")
        ]

        # People Also Ask
        paa = [
            PeopleAlsoAsk(
                question=item.get("question", ""),
                snippet=item.get("snippet", ""),
                link=item.get("link", ""),
            )
            for item in data.get("peopleAlsoAsk", [])
            if item.get("question")
        ]

        # Knowledge Graph
        kg_raw = data.get("knowledgeGraph")
        kg = None
        if kg_raw:
            kg = KnowledgeGraph(
                title=kg_raw.get("title", ""),
                description=kg_raw.get("description", ""),
                entity_type=kg_raw.get("type", ""),
                attributes={
                    k: str(v)
                    for k, v in kg_raw.get("attributes", {}).items()
                },
            )

        return SearchResponse(
            results=results,
            related_searches=related,
            people_also_ask=paa,
            knowledge_graph=kg,
        )
