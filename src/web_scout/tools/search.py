"""Web search tool factory.

``create_web_search`` returns a ``@function_tool`` that wraps a pluggable
search backend and enforces circuit-breaker limits via ``ResearchTracker``.
"""

import logging
from typing import List, Optional

from agents import function_tool

from .rendering import snippet_quality
from .tracker import ResearchTracker

logger = logging.getLogger(__name__)


def create_web_search(
    backend=None,
    tracker: Optional[ResearchTracker] = None,
    force_open_web: bool = False,
):
    """Create a web search ``@function_tool`` using a pluggable backend."""
    from web_scout.search_backends import SearchBackend

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
                    logger.info("[search] scrape-nudge (searches=%d, scrapes=0)", tracker.search_count)
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

            if response.knowledge_graph:
                kg = response.knowledge_graph
                kg_lines = [f"\n**Knowledge Graph: {kg.title}**"]
                if kg.entity_type:
                    kg_lines.append(f"Type: {kg.entity_type}")
                if kg.description:
                    kg_lines.append(kg.description)
                if kg.attributes:
                    attrs = ", ".join(f"{k}: {v}" for k, v in list(kg.attributes.items())[:6])
                    kg_lines.append(f"Attributes: {attrs}")
                parts.append("\n".join(kg_lines))

            parts.append("\n**Sources:**")
            for i, r in enumerate(response.results, 1):
                quality = snippet_quality(r.snippet)
                date_tag = f" · {r.date}" if r.date else ""
                rank_tag = f" · rank #{r.position}" if r.position else ""
                parts.append(f"\n[{i}] **{r.title}** {quality}{date_tag}{rank_tag}\nURL: {r.url}\n{r.snippet}")

            if response.people_also_ask:
                parts.append("\n**People Also Ask:**")
                for paa in response.people_also_ask[:4]:
                    parts.append(f"\nQ: {paa.question}")
                    if paa.snippet:
                        parts.append(f"A: {paa.snippet}")
                    if paa.link:
                        parts.append(f"Source: {paa.link}")

            if response.related_searches:
                parts.append("\n**Related searches:**")
                for rs in response.related_searches[:5]:
                    parts.append(f"- {rs}")

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
