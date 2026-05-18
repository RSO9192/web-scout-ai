"""Web researcher deterministic pipeline facade.

Uses pluggable search backends (Serper, and community-contributed backends). A dedicated
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

import logging
from typing import Any, Dict, List, Optional

from agents import Runner

from ._pipeline_flow import (
    _build_query_agents as _build_query_agents_impl,
)
from ._pipeline_flow import (
    _build_search_backend as _build_search_backend_impl,
)
from ._pipeline_flow import (
    _evaluate_search_coverage as _evaluate_search_coverage_impl,
)
from ._pipeline_flow import (
    _rerank_followup_urls,
    _run_direct_url_mode,
    _run_search_mode_impl,
    _select_search_urls,
    _synthesise_result,
)
from ._pipeline_flow import (
    _search_and_scrape_iteration as _search_and_scrape_iteration_impl,
)
from ._pipeline_rules import (
    _build_allowed_domain_set,
    _build_coverage_prompt,
    _build_synth_prompt,
    _diversify_search_urls,
    _extract_links_from_markdown,
    _extract_query_keywords,
    _find_next_page_url,
    _is_promising_followup_url,
    _is_same_domain,
    _judge_synthesis,
    _looks_like_document_url,
    _looks_like_paginated_index_page,
    _normalize_domain,
    _query_prefers_data_pages,
    _query_prefers_report_pages,
    _rank_followup_candidates,
    _score_followup_candidate,
)
from ._pipeline_types import (
    DEFAULT_WEB_RESEARCH_MODELS,
    CoverageEvaluation,
    FollowupSelection,
    SearchIterationResult,
    SearchLoopState,
    SearchQueryGeneration,
)
from ._prompts import (
    COVERAGE_EVALUATOR_INSTRUCTIONS,
    QUERY_GENERATOR_INSTRUCTIONS,
    SYNTHESISER_INSTRUCTIONS,
)
from .models import WebResearchResult
from .tools import ResearchTracker, create_scrape_and_extract_tool

logger = logging.getLogger(__name__)

# Suppress verbose LiteLLM INFO logs (keep WARNING+ only)
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("litellm").setLevel(logging.WARNING)


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


def _build_search_backend(search_backend: str):
    """Compatibility wrapper kept on the public module for tests and monkeypatching."""
    return _build_search_backend_impl(search_backend)


def _build_query_agents(
    query_gen_model: Any,
    evaluator_model: Any,
    domain_expertise: Optional[str],
):
    """Compatibility wrapper kept on the public module for tests and monkeypatching."""
    return _build_query_agents_impl(
        query_gen_model=query_gen_model,
        evaluator_model=evaluator_model,
        domain_expertise=domain_expertise,
    )


async def _search_and_scrape_iteration(
    *,
    query: str,
    include_domains: Optional[list[str]],
    depth: dict[str, int],
    iteration: int,
    missing_info: str,
    query_gen_agent: Any,
    backend: Any,
    tracker: ResearchTracker,
    scrape_tool: Any,
) -> SearchIterationResult:
    """Compatibility wrapper kept on the public module for tests and monkeypatching."""
    return await _search_and_scrape_iteration_impl(
        query=query,
        include_domains=include_domains,
        depth=depth,
        iteration=iteration,
        missing_info=missing_info,
        query_gen_agent=query_gen_agent,
        backend=backend,
        tracker=tracker,
        scrape_tool=scrape_tool,
    )


async def _evaluate_search_coverage(
    *,
    query: str,
    include_domains: Optional[list[str]],
    depth: dict[str, int],
    evaluator_agent: Any,
    tracker: ResearchTracker,
    allowed_domains: Optional[frozenset[str]],
    state: SearchLoopState,
) -> bool:
    """Compatibility wrapper kept on the public module for tests and monkeypatching."""
    return await _evaluate_search_coverage_impl(
        query=query,
        include_domains=include_domains,
        depth=depth,
        evaluator_agent=evaluator_agent,
        tracker=tracker,
        allowed_domains=allowed_domains,
        state=state,
    )


async def _run_search_mode(
    *,
    query: str,
    include_domains: Optional[list[str]],
    search_backend: str,
    domain_expertise: Optional[str],
    depth: dict[str, int],
    query_gen_model: Any,
    evaluator_model: Any,
    followup_model: Any,
    tracker: ResearchTracker,
    scrape_tool: Any,
    allowed_domains: Optional[frozenset[str]],
) -> None:
    """Run the iterative search loop using agent-module symbols for compatibility.

    This wrapper preserves the current monkeypatch surface used by the test suite
    while delegating the actual implementation details to the private flow module.
    """
    await _run_search_mode_impl(
        query=query,
        include_domains=include_domains,
        search_backend=search_backend,
        domain_expertise=domain_expertise,
        depth=depth,
        query_gen_model=query_gen_model,
        evaluator_model=evaluator_model,
        followup_model=followup_model,
        tracker=tracker,
        scrape_tool=scrape_tool,
        allowed_domains=allowed_domains,
        build_search_backend=_build_search_backend,
        build_query_agents=_build_query_agents,
        search_and_scrape_iteration=_search_and_scrape_iteration,
        evaluate_search_coverage=_evaluate_search_coverage,
    )


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
    cache: bool = False,
) -> WebResearchResult:
    """Run deterministic web research pipeline."""
    from .utils import get_model

    if research_depth not in _DEPTH_PRESETS:
        raise ValueError(
            f"Unknown research_depth={research_depth!r}. Use 'standard' or 'deep'."
        )
    depth = _DEPTH_PRESETS[research_depth]

    if include_domains:
        include_domains = [_normalize_domain(domain) for domain in include_domains]

    _allowed = _build_allowed_domain_set(
        allowed_domains=allowed_domains,
        include_domains=include_domains,
        direct_url=direct_url,
    )

    tracker = ResearchTracker()

    fallback_model = models.get(
        "web_researcher",
        DEFAULT_WEB_RESEARCH_MODELS["web_researcher"],
    )
    query_gen_model = get_model(models.get("query_generator", fallback_model))
    evaluator_model = get_model(models.get("coverage_evaluator", fallback_model))
    synth_model = get_model(models.get("synthesiser", fallback_model))
    followup_model = get_model(
        models.get(
            "followup_selector",
            DEFAULT_WEB_RESEARCH_MODELS["followup_selector"],
        )
    )
    extractor_model = get_model(
        models.get(
            "content_extractor",
            DEFAULT_WEB_RESEARCH_MODELS["content_extractor"],
        )
    )
    vision_model = models.get(
        "vision_fallback",
        DEFAULT_WEB_RESEARCH_MODELS["vision_fallback"],
    )

    scrape_tool = create_scrape_and_extract_tool(
        extractor_model=extractor_model,
        tracker=tracker,
        query=query,
        vision_model=vision_model,
        allowed_domains=_allowed,
        max_pdf_pages=max_pdf_pages,
        max_content_chars=max_content_chars,
        use_session_cache=cache,
    )

    logger.info(
        "[pipeline] start  query=%r  backend=%s mode=%s depth=%s",
        query[:80],
        search_backend,
        "direct" if direct_url else "domain" if include_domains else "open",
        research_depth,
    )

    if direct_url:
        await _run_direct_url_mode(
            query=query,
            direct_url=direct_url,
            tracker=tracker,
            scrape_tool=scrape_tool,
            depth=depth,
            followup_model=followup_model,
            allowed_domains=_allowed,
        )
    else:
        await _run_search_mode(
            query=query,
            include_domains=include_domains,
            search_backend=search_backend,
            domain_expertise=domain_expertise,
            depth=depth,
            query_gen_model=query_gen_model,
            evaluator_model=evaluator_model,
            followup_model=followup_model,
            tracker=tracker,
            scrape_tool=scrape_tool,
            allowed_domains=_allowed,
        )

    return await _synthesise_result(
        query=query,
        tracker=tracker,
        synth_model=synth_model,
        domain_expertise=domain_expertise,
    )


__all__ = [
    "COVERAGE_EVALUATOR_INSTRUCTIONS",
    "DEFAULT_WEB_RESEARCH_MODELS",
    "CoverageEvaluation",
    "FollowupSelection",
    "QUERY_GENERATOR_INSTRUCTIONS",
    "Runner",
    "SYNTHESISER_INSTRUCTIONS",
    "SearchIterationResult",
    "SearchLoopState",
    "SearchQueryGeneration",
    "_build_allowed_domain_set",
    "_build_coverage_prompt",
    "_build_query_agents",
    "_build_search_backend",
    "_build_synth_prompt",
    "_diversify_search_urls",
    "_evaluate_search_coverage",
    "_extract_links_from_markdown",
    "_extract_query_keywords",
    "_find_next_page_url",
    "_is_promising_followup_url",
    "_is_same_domain",
    "_judge_synthesis",
    "_looks_like_document_url",
    "_looks_like_paginated_index_page",
    "_normalize_domain",
    "_query_prefers_data_pages",
    "_query_prefers_report_pages",
    "_rank_followup_candidates",
    "_rerank_followup_urls",
    "_run_search_mode",
    "_score_followup_candidate",
    "_search_and_scrape_iteration",
    "_select_search_urls",
    "run_web_research",
]


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
        for source in result.scraped:
            print(f"  - {source.title or source.url}")
            print(f"    {source.url}")

        if result.scrape_failed:
            print(f"\nFailed to scrape {len(result.scrape_failed)} URL(s).")

        print("\n" + "=" * 60)
        print(f"QUERIES ({len(result.queries)} executed)")
        print("=" * 60)
        for query_entry in result.queries:
            print(f"  - {query_entry.query}")

    import asyncio

    asyncio.run(_main())
