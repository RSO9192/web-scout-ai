"""Pluggable search backends for web discovery.

Provides a ``SearchBackend`` ABC with two concrete implementations:

- ``SerperBackend`` — Google-quality results via serper.dev
                      (requires ``SERPER_API_KEY`` env var)
- ``ExaBackend``    — neural/auto search via exa.ai
                      (requires ``EXA_API_KEY`` env var)

Adding a new backend
--------------------
1. Subclass ``SearchBackend`` and implement the ``search()`` coroutine.
2. Return a ``SearchResponse`` with normalized ``SearchResult`` items
   (title, url, snippet).
3. Add a branch for ``search_backend="your_name"`` in
   ``_build_search_backend()`` (_pipeline_flow.py).
4. Open a pull request — contributions welcome!
"""

import abc
import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Single search result from any backend."""

    title: str
    url: str
    snippet: str


@dataclass
class SearchResponse:
    """Search results from a backend."""

    results: List[SearchResult]


class SearchBackend(abc.ABC):
    """Abstract interface for web search backends.

    To contribute a new backend: subclass this, implement ``search()``,
    and wire it into the backend-selection block in ``agent.py``.

    ``exclude_domains`` is best-effort: backends with native exclusion
    (e.g. Exa) apply it at the API; backends without (e.g. Serper) may
    ignore it.  Callers must always post-filter result URLs.
    """

    @abc.abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 5,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> SearchResponse: ...


# Transient statuses shared by all HTTP backends: rate-limit + server errors.
_RETRY_STATUSES = (429, 500, 502, 503, 504)
_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds; doubles each retry


async def _post_with_retries(url: str, headers: Dict[str, str], payload: dict, label: str) -> dict:
    """POST ``payload`` to ``url``, retrying transient failures with backoff.

    Retries up to ``_MAX_RETRIES`` times on HTTP 429 or 5xx, raises on any
    other error status, and returns the decoded JSON body.
    """
    import httpx

    async with httpx.AsyncClient(timeout=15) as client:
        for attempt in range(_MAX_RETRIES):
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES - 1:
                delay = _BASE_DELAY * (2**attempt)
                reason = "rate-limited" if resp.status_code == 429 else f"server error {resp.status_code}"
                logger.warning(
                    "[%s] %s (attempt %d/%d), retrying in %.1fs",
                    label,
                    reason,
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            return resp.json()
    return {}


class SerperBackend(SearchBackend):
    """Google Search via Serper.dev API.

    Requires ``SERPER_API_KEY`` environment variable.  Returns Google-quality
    results with rich snippets.  The ``site:`` operator is natively strict
    in Google, so no post-filtering is needed.  ``exclude_domains`` is
    ignored: Google has no native exclusion filter, and ``-site:`` operators
    would eat into its ~32-term query limit — callers post-filter instead.

    Retries transient failures via ``_post_with_retries``.
    """

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def search(
        self,
        query: str,
        max_results: int = 5,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> SearchResponse:
        effective_query = query
        if include_domains:
            site_clause = " OR ".join(f"site:{d}" for d in include_domains)
            effective_query = f"({site_clause}) {effective_query}"

        data = await _post_with_retries(
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY": self._api_key,
                "Content-Type": "application/json",
            },
            payload={"q": effective_query, "num": max_results},
            label="Serper",
        )

        results = [
            SearchResult(
                title=item.get("title", "Untitled"),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
            )
            for item in data.get("organic", [])
            if item.get("link")
        ][:max_results]

        return SearchResponse(results=results)


class ExaBackend(SearchBackend):
    """Neural web search via the exa.ai ``/search`` API.

    Requires ``EXA_API_KEY`` environment variable.  Uses ``type="auto"``
    (Exa picks the best strategy per query) and requests ``highlights`` —
    query-relevant sentences from each page — to fill the ``snippet`` field,
    since bare Exa results carry no snippet.  Domain filters map to Exa's
    native ``includeDomains``/``excludeDomains``.

    Google SERP extras (related searches, People Also Ask, knowledge graph)
    do not exist in Exa and are returned empty.

    Retries transient failures via ``_post_with_retries``.
    """

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def search(
        self,
        query: str,
        max_results: int = 5,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> SearchResponse:
        payload: dict = {
            "query": query,
            "numResults": max_results,
            "type": "auto",
            # Richer than a Google snippet (feeds the coverage evaluator's
            # unscraped-candidate list) but capped: uncapped highlights were
            # observed at ~8k chars per result, bloating evaluator prompts.
            "contents": {"highlights": {"maxCharacters": 1000}},
        }
        if include_domains:
            payload["includeDomains"] = include_domains
        if exclude_domains:
            payload["excludeDomains"] = exclude_domains

        data = await _post_with_retries(
            "https://api.exa.ai/search",
            headers={
                "x-api-key": self._api_key,
                "Content-Type": "application/json",
            },
            payload=payload,
            label="Exa",
        )

        results = [
            SearchResult(
                title=item.get("title") or "Untitled",
                url=item.get("url", ""),
                snippet=" … ".join(h.strip() for h in item.get("highlights", []) if h and h.strip()),
            )
            for item in data.get("results", [])
            if item.get("url")
        ][:max_results]

        return SearchResponse(results=results)
