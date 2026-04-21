"""Pluggable search backends for web discovery.

Provides a ``SearchBackend`` ABC with one concrete implementation:

- ``SerperBackend`` — Google-quality results via serper.dev
                      (requires ``SERPER_API_KEY`` env var)

Adding a new backend
--------------------
1. Subclass ``SearchBackend`` and implement the ``search()`` coroutine.
2. Return a ``SearchResponse`` (results, related_searches, and optionally
   people_also_ask / knowledge_graph).
3. Accept ``search_backend="your_name"`` in ``run_research_pipeline()``
   (agent.py) and instantiate your class in the backend-selection block.
4. Open a pull request — contributions welcome!
"""

from __future__ import annotations

import abc
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

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
    """Abstract interface for web search backends.

    To contribute a new backend: subclass this, implement ``search()``,
    and wire it into the backend-selection block in ``agent.py``.
    """

    @abc.abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 5,
        include_domains: Optional[List[str]] = None,
    ) -> SearchResponse:
        ...


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
