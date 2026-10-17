"""Pluggable search backends for web discovery.

Provides a ``SearchBackend`` ABC with concrete implementations:

- ``SerperBackend`` — Google-quality results via serper.dev
                      (requires ``SERPER_API_KEY`` env var)
- ``ExaBackend``    — neural/auto search via exa.ai
                      (requires ``EXA_API_KEY`` env var)

- ``ParallelBackend`` - keyless search via Parallel Search MCP

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
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
    query: str = ""
    country_name: Optional[str] = None
    country_code: Optional[str] = None
    language_code: Optional[str] = None


class SearchBackend(abc.ABC):
    """Abstract interface for web search backends.

    To contribute a new backend: subclass this, implement ``search()``,
    and wire it into the backend-selection block in ``agent.py``.

    ``exclude_domains`` is best-effort: backends with native exclusion
    (e.g. Exa) apply it at the API; backends without (e.g. Serper) may
    ignore it.  Callers must always post-filter result URLs.

    ``country_code`` and ``language_code`` are backend-neutral localization
    hints. Backends apply the capabilities they support and otherwise rely on
    the localized query text.
    """

    @abc.abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 5,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        country_code: Optional[str] = None,
        language_code: Optional[str] = None,
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

    from .scraping._resources import search_client

    started = time.perf_counter()
    client = search_client()
    async with asyncio.timeout(45):
        for attempt in range(_MAX_RETRIES):
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES - 1:
                delay = _BASE_DELAY * (2**attempt)
                retry_after = resp.headers.get("retry-after", "")
                if retry_after:
                    try:
                        seconds = float(retry_after)
                    except ValueError:
                        try:
                            seconds = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                        except (ValueError, TypeError, OverflowError):
                            seconds = 0
                    delay = max(delay, seconds)
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
            from .scraping.utils import log_fetch

            log_fetch(url, status=resp.status_code, via=f"search:{label}", bytes_=len(resp.content))
            logger.debug("[search-timing] backend=%s seconds=%.3f", label, time.perf_counter() - started)
            return resp.json()
    return {}


class SerperBackend(SearchBackend):
    """Google Search via Serper.dev API.

    Requires ``SERPER_API_KEY`` environment variable.  Returns Google-quality
    results with rich snippets.  The ``site:`` operator is natively strict
    in Google, so no post-filtering is needed.  ``exclude_domains`` is
    ignored: Google has no native exclusion filter, and ``-site:`` operators
    would eat into its ~32-term query limit — callers post-filter instead.
    Locale hints map to Serper's ``gl`` and ``hl`` request fields.

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
        country_code: Optional[str] = None,
        language_code: Optional[str] = None,
    ) -> SearchResponse:
        effective_query = query
        if include_domains:
            site_clause = " OR ".join(f"site:{d}" for d in include_domains)
            effective_query = f"({site_clause}) {effective_query}"

        payload: dict = {"q": effective_query, "num": max_results}
        if country_code:
            payload["gl"] = country_code.lower()
        if language_code:
            payload["hl"] = language_code.lower()

        data = await _post_with_retries(
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY": self._api_key,
                "Content-Type": "application/json",
            },
            payload=payload,
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

        return SearchResponse(
            results=results,
            query=query,
            country_code=country_code,
            language_code=language_code,
        )


class ExaBackend(SearchBackend):
    """Neural web search via the exa.ai ``/search`` API.

    Requires ``EXA_API_KEY`` environment variable.  Uses ``type="auto"``
    (Exa picks the best strategy per query) and requests ``highlights`` —
    query-relevant sentences from each page — to fill the ``snippet`` field,
    since bare Exa results carry no snippet.  Domain filters map to Exa's
    native ``includeDomains``/``excludeDomains``.
    ``country_code`` maps to Exa's ``userLocation``; Exa has no separate
    query-language field, so language targeting comes from the query text.

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
        country_code: Optional[str] = None,
        language_code: Optional[str] = None,
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
        if country_code:
            payload["userLocation"] = country_code.upper()

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

        return SearchResponse(
            results=results,
            query=query,
            country_code=country_code,
            language_code=language_code,
        )


class ParallelBackend(SearchBackend):
    """Anonymous web search via Parallel Search MCP (Fast mode).

    Uses the Streamable HTTP client already provided by openai-agents. Domain
    and locale hints guide the search; domain constraints are also enforced
    locally because anonymous MCP connections do not support native filters.
    Source reading remains with the existing scraping pipeline.
    """

    def __init__(self):
        import uuid

        # Reuse the same identity across related searches, including failures.
        self._session_id = uuid.uuid4().hex

    async def search(
        self,
        query: str,
        max_results: int = 5,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        country_code: Optional[str] = None,
        language_code: Optional[str] = None,
    ) -> SearchResponse:
        import json
        from urllib.parse import urlparse

        from agents.mcp import MCPServerStreamableHttp

        if max_results <= 0:
            return SearchResponse(results=[], query=query, country_code=country_code, language_code=language_code)

        objective = query
        if include_domains:
            objective += "\nFind sources within these domains: " + ", ".join(include_domains)
        if exclude_domains:
            objective += "\nAvoid these domains: " + ", ".join(exclude_domains)
        if country_code:
            objective += f"\nPrefer sources relevant to country code {country_code}."
        if language_code:
            objective += f"\nPrefer sources in language code {language_code}."

        async def call_search():
            async with MCPServerStreamableHttp(
                params={
                    "url": "https://search.parallel.ai/mcp",
                    "headers": {"User-Agent": "web-scout-ai (+https://github.com/RSO9192/web-scout-ai)"},
                    "timeout": 15,
                    "sse_read_timeout": 45,
                },
                client_session_timeout_seconds=45,
            ) as server:
                return await server.call_tool(
                    "web_search",
                    {"objective": objective, "search_queries": [query], "session_id": self._session_id},
                )

        # The MCP transport can cancel its owning task on an HTTP failure.
        # Isolate that cancellation while preserving cancellation by our caller.
        try:
            result = await asyncio.create_task(call_search())
        except asyncio.CancelledError as exc:
            if asyncio.current_task().cancelling():
                raise
            raise RuntimeError("Parallel MCP transport cancelled the search") from exc

        text = "\n".join(block.text for block in result.content if block.type == "text")
        if result.isError:
            raise RuntimeError(f"Parallel search failed: {text}")
        data = result.structuredContent
        if data is None:
            data = json.loads(text)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("Parallel search returned an invalid results payload")
        if data.get("errors"):
            raise RuntimeError(f"Parallel search failed: {data['errors']}")

        def matches(host: str, domain: str) -> bool:
            domain = domain.strip().lower().strip(".").removeprefix("www.")
            return bool(domain) and (host == domain or host.endswith("." + domain))

        results = []
        for item in data["results"]:
            url = item.get("url") or ""
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower().rstrip(".")
            if parsed.scheme not in {"http", "https"} or not host:
                continue
            if include_domains and not any(matches(host, d) for d in include_domains):
                continue
            if exclude_domains and any(matches(host, d) for d in exclude_domains):
                continue
            results.append(
                SearchResult(
                    title=item.get("title") or "Untitled",
                    url=url,
                    snippet=" … ".join(excerpt.strip() for excerpt in item.get("excerpts", []) if excerpt.strip()),
                )
            )
            if len(results) >= max_results:
                break

        return SearchResponse(
            results=results,
            query=query,
            country_code=country_code,
            language_code=language_code,
        )
