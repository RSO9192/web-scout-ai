"""Pipeline orchestration helpers for deterministic web research."""

import asyncio
import json
import logging
import os
from collections import OrderedDict
from datetime import date
from typing import Any, Optional
from urllib.parse import urlparse

from agents import Agent, ModelRetrySettings, ModelSettings, Runner, retry_policies
from openai.types.shared.reasoning import Reasoning

from web_scout.config import FOLLOWUP_HEURISTICS

from ._classification import ClassificationError
from ._classification import backend as classification_backend
from ._classification import coverage as jev_coverage
from ._classification import search_results as jev_search_results
from ._country_codes import resolve_country_code
from ._extractor_contract import ExtractorOutcome
from ._pipeline_rules import (
    _build_citation_slots,
    _build_coverage_prompt,
    _build_query_generation_prompt,
    _build_synth_prompt,
    _diversify_search_urls,
    _filter_blocked_domain_backlog_urls,
    _find_next_page_url,
    _is_domain_mode_candidate,
    _is_promising_followup_url,
    _normalize_domain,
    _rank_followup_candidates,
    _resolve_slot_citations,
    _strip_site_operators,
)
from ._pipeline_types import (
    DEFAULT_FOLLOWUP_BACKEND,
    FOLLOWUP_BACKENDS,
    CoverageEvaluation,
    FollowupSelection,
    PlannedSearchQuery,
    SearchIterationResult,
    SearchLoopState,
    SearchQueryChecklist,
    SearchQueryGeneration,
)
from ._prompts import (
    COVERAGE_EVALUATOR_INSTRUCTIONS,
    QUERY_GENERATOR_INSTRUCTIONS,
    SEARCH_RESULT_SELECTOR_INSTRUCTIONS,
    SYNTHESISER_INSTRUCTIONS,
)
from .jev_link_selector import select_links_with_jev
from .models import WebResearchResult, WebResearchResultRaw
from .scraping._resources import model_admission
from .scraping.page_classifier import looks_like_document_resource
from .scraping.utils import is_blocked_domain, looks_like_document_link
from .tools import (
    ResearchTracker,
    extract_explicit_rendered_followup_links,
    extract_rendered_followup_links,
    is_rendered_list_page,
    resolve_scrape_outcome,
)

logger = logging.getLogger("web_scout.agent")

# Same yes-probability cutoff as coverage's candidate routing.
JEV_SEARCH_RESULT_MIN = 0.5


def _research_model_settings(*, reasoning: Reasoning | None = None) -> ModelSettings:
    """Retry transient model failures for short, single-turn research agents."""
    return ModelSettings(
        reasoning=reasoning,
        retry=ModelRetrySettings(
            max_retries=3,
            backoff={"initial_delay": 1.0, "max_delay": 8.0, "multiplier": 2.0, "jitter": True},
            policy=retry_policies.any(
                retry_policies.provider_suggested(),
                retry_policies.retry_after(),
                retry_policies.network_error(),
                retry_policies.http_status([408, 409, 429, 500, 502, 503, 504]),
            ),
        ),
    )


def _normalize_planned_code(value: Optional[str], *, label: str) -> Optional[str]:
    """Normalize a planner-produced two-letter locale code, ignoring malformed values."""
    if value is None:
        return None
    normalized = value.strip().lower()
    if len(normalized) == 2 and normalized.isalpha():
        return normalized
    logger.warning("[pipeline] ignoring invalid %s from query planner: %r", label, value)
    return None


async def _gather_scrapes(tasks: list) -> list:
    """Run scrape coroutines concurrently; concurrency is bounded inside the scrape tool."""
    return await asyncio.gather(*tasks)


def _looks_like_document_followup(url: str) -> bool:
    lower = url.lower()
    path = lower.split("?", 1)[0].split("#", 1)[0]
    if path.endswith((".pdf", ".docx", ".pptx", ".xlsx")):
        return True
    return any(
        marker in lower
        for marker in (
            "/download",
            "/bitstream/",
            "/bitstreams/",
            "download?",
            "file-download",
        )
    )


def _finalize_followup_selection(
    selected: list[str],
    *,
    shortlist: list[str],
    cap: int,
    parent_url: str,
) -> list[str]:
    """Keep selected URLs that appear in the shortlist, capped and in order."""
    candidate_norm_map = OrderedDict((ResearchTracker.normalize_url(url), url) for url in shortlist)
    chosen: list[str] = []
    for url in selected:
        original = candidate_norm_map.get(ResearchTracker.normalize_url(url))
        if original and original not in chosen:
            chosen.append(original)
        if len(chosen) >= cap:
            break

    if not chosen:
        return shortlist[:cap]

    logger.info(
        "[pipeline] reranked %d follow-up candidates for %s → %d selected",
        len(shortlist),
        parent_url,
        len(chosen),
    )
    return chosen


async def _select_followup_urls_with_luna(
    *,
    query: str,
    parent_url: str,
    parent_content: str,
    shortlist: list[str],
    cap: int,
    model: Any,
) -> list[str]:
    """Ask the Luna Agents SDK model which shortlisted URLs to follow."""
    parent_excerpt = " ".join(parent_content.split())[:1800]
    prompt = (
        f"Research query: {query}\n"
        f"Parent page: {parent_url}\n"
        f"Parent excerpt:\n{parent_excerpt}\n\n"
        f"Select up to {cap} URLs from this candidate list that are MOST likely to directly answer the query.\n"
        "Prefer report pages, document downloads, and specific detail pages with concrete evidence.\n"
        "Only choose data portals, datasets, or maproom pages when the query explicitly looks data-oriented.\n"
        "Avoid homepages, generic navigation pages, category/list pages, service hubs, "
        "and operational forecast/warning pages.\n"
        "Return exact URLs from the list only.\n\n"
        "Candidates:\n" + "\n".join(f"- {url}" for url in shortlist)
    )
    selector = Agent(
        name="followup_selector",
        model=model,
        output_type=FollowupSelection,
        model_settings=_research_model_settings(),
        instructions=(
            "You rank same-domain follow-up URLs for a web research pipeline. "
            "Only select from the provided candidates. "
            "Return the few URLs most likely to contain direct evidence for the query."
        ),
    )
    try:
        async with model_admission():
            result = await Runner.run(selector, prompt)
        raw_selected = result.final_output_as(FollowupSelection).selected_urls
    except Exception as exc:
        logger.warning("[pipeline] follow-up URL reranker failed for %s: %s", parent_url, exc)
        return shortlist[:cap]

    return _finalize_followup_selection(
        raw_selected,
        shortlist=shortlist,
        cap=cap,
        parent_url=parent_url,
    )


async def _select_followup_urls_with_jev(
    *,
    query: str,
    parent_url: str,
    parent_content: str,
    shortlist: list[str],
    cap: int,
) -> list[str]:
    """Ask TypeSafe Jev which shortlisted URLs to follow."""
    selected, error = await select_links_with_jev(
        query=query,
        parent_url=parent_url,
        parent_content=parent_content,
        candidates=shortlist,
    )
    if error:
        logger.warning("[pipeline] follow-up URL reranker failed for %s: %s", parent_url, error)
        return shortlist[:cap]

    return _finalize_followup_selection(
        selected,
        shortlist=shortlist,
        cap=cap,
        parent_url=parent_url,
    )


async def _rerank_followup_urls(
    *,
    query: str,
    parent_url: str,
    parent_content: str,
    candidates: list[str],
    cap: int,
    model: Any = None,
    selector: str = DEFAULT_FOLLOWUP_BACKEND,
) -> list[str]:
    """Choose follow-up URLs with Jev or GPT, falling back to the heuristic shortlist."""
    if selector not in FOLLOWUP_BACKENDS:
        raise ValueError(f"Unknown followup_backend={selector!r}. Supported: {', '.join(sorted(FOLLOWUP_BACKENDS))}.")

    ranked_candidates = _rank_followup_candidates(query, candidates)
    if not ranked_candidates:
        return []
    shortlist = ranked_candidates[: max(cap * FOLLOWUP_HEURISTICS.shortlist_multiplier, cap)]
    if len(candidates) <= cap or len(shortlist) == 1:
        return shortlist[:cap]

    if selector == "gpt":
        if model is None:
            logger.warning(
                "[pipeline] followup_backend='gpt' needs a model; using heuristic shortlist for %s",
                parent_url,
            )
            return shortlist[:cap]
        return await _select_followup_urls_with_luna(
            query=query,
            parent_url=parent_url,
            parent_content=parent_content,
            shortlist=shortlist,
            cap=cap,
            model=model,
        )

    return await _select_followup_urls_with_jev(
        query=query,
        parent_url=parent_url,
        parent_content=parent_content,
        shortlist=shortlist,
        cap=cap,
    )


def _build_search_backend(search_backend: str):
    from .search_backends import ExaBackend, ParallelBackend, SerperBackend

    if search_backend == "serper":
        key = os.getenv("SERPER_API_KEY", "")
        if not key:
            raise ValueError("search_backend='serper' requires SERPER_API_KEY env var")
        return SerperBackend(key)

    if search_backend == "exa":
        key = os.getenv("EXA_API_KEY", "")
        if not key:
            raise ValueError("search_backend='exa' requires EXA_API_KEY env var")
        return ExaBackend(key)

    if search_backend == "parallel":
        return ParallelBackend()

    raise ValueError(
        f"Unknown search_backend={search_backend!r}. "
        "Currently supported: 'serper', 'exa', 'parallel'. "
        "See SearchBackend in search_backends.py to add a new backend."
    )


def _build_query_agents(
    query_gen_model: Any,
    evaluator_model: Any,
    domain_expertise: Optional[str],
    evaluator_extra_prompt: Optional[str] = None,
) -> tuple[Agent, Agent]:
    suffix = f"\nDomain Expertise: {domain_expertise}" if domain_expertise else ""

    # Append the extra quality sentence for the evaluator
    eval_instructions = COVERAGE_EVALUATOR_INSTRUCTIONS + suffix
    if evaluator_extra_prompt:
        eval_instructions += f"\n\nQuality Guideline: {evaluator_extra_prompt}"

    query_gen_agent = Agent(
        name="query_generator",
        model=query_gen_model,
        model_settings=_research_model_settings(),
        # The first round also lists the query's requirements, used by every backend's extractors;
        # _generate_search_queries switches to plain query planning afterwards.
        output_type=SearchQueryChecklist,
        instructions=QUERY_GENERATOR_INSTRUCTIONS
        + suffix
        + (
            "\nAlso list every atomic requirement needed to answer the ORIGINAL query. Preserve all "
            "requested geography, dates, units, entities, comparisons and numerical details; do not "
            "invent extra requirements. Each requirement names facts a source must state (figures, names, "
            "dates or findings); do not list instructions about citing sources, naming the data provider or "
            "monitoring system, or stating units, unless the original query itself asks for them. Make each "
            "requirement self-contained, so that a source can satisfy it on its own. Split a compound question "
            "into separate requirements: the reporting period or latest year to identify, each requested set "
            "of entities, and each requested figure or value. Never return the whole question as a single "
            "requirement unless it asks for exactly one fact."
        )
        + ("\nCoverage criteria: " + evaluator_extra_prompt if evaluator_extra_prompt else ""),
    )
    evaluator_agent = Agent(
        name="coverage_evaluator",
        model=evaluator_model,
        model_settings=_research_model_settings(),
        output_type=CoverageEvaluation,
        instructions=eval_instructions,
    )
    return query_gen_agent, evaluator_agent


async def _scrape_urls(
    scrape_tool: Any,
    urls: list[str],
    *,
    empty_log_message: str,
) -> SearchIterationResult:
    if urls:
        extracted_contents = await _gather_scrapes([scrape_tool(url) for url in urls])
        outcomes_by_url = {
            url: resolve_scrape_outcome(scrape_tool, url, content) for url, content in zip(urls, extracted_contents)
        }
        return SearchIterationResult(
            extracted_contents=extracted_contents,
            iter_results=list(zip(urls, extracted_contents)),
            outcomes_by_url=outcomes_by_url,
        )

    logger.info(empty_log_message)
    return SearchIterationResult(extracted_contents=[], iter_results=[])


async def _scrape_backlog_urls(scrape_tool: Any, urls: list[str]) -> SearchIterationResult:
    logger.info(
        "[pipeline] skipping search; scraping %d evaluator-selected backlog URLs",
        len(urls),
    )
    return await _scrape_urls(
        scrape_tool,
        urls,
        empty_log_message="[pipeline] no promising backlog URLs to scrape",
    )


async def _generate_search_queries(
    *,
    query: str,
    include_domains: Optional[list[str]],
    depth: dict[str, int],
    iteration: int,
    missing_info: str,
    query_gen_agent: Agent,
) -> list[PlannedSearchQuery]:
    n_queries = depth["queries_first"] if iteration == 0 else depth["queries_followup"]
    prompt = _build_query_generation_prompt(
        query=query,
        n_queries=n_queries,
        include_domains=include_domains,
        missing_info=missing_info if iteration > 0 else "",
    )
    try:
        async with model_admission():
            gen_res = await Runner.run(query_gen_agent, prompt)
        generated = gen_res.final_output_as(SearchQueryGeneration)
        search_queries = generated.queries
        if iteration == 0:
            query_gen_agent._research_requirements = list(getattr(generated, "requirements", []) or [query])
            query_gen_agent.output_type = SearchQueryGeneration
            query_gen_agent.instructions = query_gen_agent.instructions.split("\nAlso list", 1)[0]
    except Exception as exc:
        logger.error("[pipeline] query generation failed: %s", exc)
        search_queries = []

    if not search_queries:
        search_queries = [
            PlannedSearchQuery(
                query=query,
            )
        ]

    logger.info("[pipeline] generated %d queries: %s", len(search_queries), search_queries)
    return search_queries


async def _execute_searches(
    *,
    backend: Any,
    search_queries: list[PlannedSearchQuery | str],
    include_domains: Optional[list[str]],
    exclude_domains: Optional[list[str]] = None,
    tracker: ResearchTracker,
) -> list[Any]:
    async def do_search(planned: PlannedSearchQuery | str):
        if isinstance(planned, str):
            planned = PlannedSearchQuery(query=planned)
        if include_domains:
            # The backend applies include_domains itself; planner-written site: operators would duplicate it.
            planned = planned.model_copy(update={"query": _strip_site_operators(planned.query)})
        country_code = resolve_country_code(planned.country_name)
        if planned.country_name and country_code is None:
            logger.warning("[pipeline] ignoring unrecognized country from query planner: %r", planned.country_name)
        language_code = _normalize_planned_code(planned.language_code, label="language code")
        try:
            response = await backend.search(
                planned.query,
                max_results=10,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                country_code=country_code,
                language_code=language_code,
            )
            response.query = planned.query
            response.country_name = planned.country_name
            response.country_code = country_code
            response.language_code = language_code
            tracker.record_search(
                planned.query,
                len(response.results),
                include_domains,
                response.results,
                country_code=country_code,
                language_code=language_code,
            )
            return response
        except Exception as exc:
            logger.error("[pipeline] search failed for %r: %s", planned.query, exc)
            return None

    return await asyncio.gather(*(do_search(query) for query in search_queries))


def _select_search_urls(
    *,
    query: str,
    include_domains: Optional[list[str]],
    depth: dict[str, int],
    iteration: int,
    tracker: ResearchTracker,
    search_results: list[Any],
    exclude_domains: Optional[list[str]] = None,
) -> list[str]:
    unique_urls = OrderedDict()
    max_urls_to_scrape = depth["urls_first"] if iteration == 0 else depth["urls_followup"]
    idx = 0
    max_results_len = max([len(resp.results) for resp in search_results if resp] + [0])

    while len(unique_urls) < max_urls_to_scrape and idx < max_results_len:
        for response in search_results:
            if response and idx < len(response.results):
                url = response.results[idx].url
                norm = tracker.normalize_url(url)
                if include_domains and not _is_domain_mode_candidate(url, include_domains, query):
                    continue
                if tracker.is_domain_bot_blocked(url) or is_blocked_domain(url, exclude_domains=exclude_domains):
                    continue
                if norm not in unique_urls and tracker.is_unscraped_candidate(url):
                    unique_urls[norm] = url
                if len(unique_urls) >= max_urls_to_scrape:
                    break
        idx += 1

    urls_to_scrape = _diversify_search_urls(list(unique_urls.values()), max_urls_to_scrape)
    logger.info("[pipeline] selected %d new URLs to scrape", len(urls_to_scrape))
    return urls_to_scrape


def _search_result_candidates(
    *,
    include_domains: Optional[list[str]],
    tracker: ResearchTracker,
    search_results: list[Any],
    exclude_domains: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Build a deduplicated, round-robin candidate pool with locale provenance."""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    max_results_len = max([len(response.results) for response in search_results if response] + [0])

    for rank in range(max_results_len):
        for response in search_results:
            if not response or rank >= len(response.results):
                continue
            result = response.results[rank]
            url = result.url
            norm = tracker.normalize_url(url)
            if include_domains and not _is_domain_mode_candidate(url, include_domains, response.query or ""):
                continue
            if tracker.is_domain_bot_blocked(url) or norm in seen or not tracker.is_unscraped_candidate(url):
                continue
            if is_blocked_domain(url, exclude_domains=exclude_domains):
                continue
            seen.add(norm)
            candidates.append(
                {
                    "url": url,
                    "title": result.title,
                    "snippet": " ".join(result.snippet.split())[:600],
                    "backend_rank": rank + 1,
                    "search_query": response.query,
                    "country_name": response.country_name,
                    "country_code": response.country_code,
                    "language_code": response.language_code,
                }
            )
    return candidates


async def _select_search_urls_with_model(
    *,
    query: str,
    include_domains: Optional[list[str]],
    depth: dict[str, int],
    iteration: int,
    tracker: ResearchTracker,
    search_results: list[Any],
    selector_model: Any,
    exclude_domains: Optional[list[str]] = None,
) -> list[str]:
    """Rank search results with Jev (``DISABLE_JEV=false``) or a structured GPT selector.

    Selections are padded with the deterministic round-robin order up to the URL budget.
    A GPT failure falls back to that order; a Jev failure raises ``ClassificationError``.
    """
    fallback = _select_search_urls(
        query=query,
        include_domains=include_domains,
        depth=depth,
        iteration=iteration,
        tracker=tracker,
        search_results=search_results,
        exclude_domains=exclude_domains,
    )
    candidates = _search_result_candidates(
        include_domains=include_domains,
        tracker=tracker,
        search_results=search_results,
        exclude_domains=exclude_domains,
    )
    use_jev = classification_backend() == "jev"
    if (selector_model is None and not use_jev) or len(candidates) <= 1:
        return fallback

    cap = depth["urls_first"] if iteration == 0 else depth["urls_followup"]
    if use_jev:
        selected = await _select_search_urls_with_jev(query=query, candidates=candidates)
    else:
        selected = await _select_search_urls_with_gpt(
            query=query, candidates=candidates, cap=cap, selector_model=selector_model
        )
        if selected is None:
            return fallback

    allowed = {tracker.normalize_url(item["url"]): item["url"] for item in candidates}
    chosen: list[str] = []
    for url in selected:
        original = allowed.get(tracker.normalize_url(url))
        if original and original not in chosen:
            chosen.append(original)
        if len(chosen) >= cap:
            break
    for url in fallback:
        if url not in chosen:
            chosen.append(url)
        if len(chosen) >= cap:
            break

    chosen = _diversify_search_urls(chosen, cap)
    logger.info("[pipeline] %s search-result selector chose %d URLs", "jev" if use_jev else "gpt", len(chosen))
    return chosen


async def _select_search_urls_with_jev(*, query: str, candidates: list[dict[str, Any]]) -> list[str]:
    """Return candidate URLs Jev judges relevant, most probable first."""
    probabilities = await jev_search_results(
        query,
        date.today().isoformat(),
        [{key: item[key] for key in ("url", "title", "snippet")} for item in candidates],
    )
    ranked = sorted(zip(probabilities, candidates), key=lambda pair: pair[0], reverse=True)
    return [item["url"] for probability, item in ranked if probability >= JEV_SEARCH_RESULT_MIN]


async def _select_search_urls_with_gpt(
    *, query: str, candidates: list[dict[str, Any]], cap: int, selector_model: Any
) -> Optional[list[str]]:
    """Return GPT-selected candidate URLs best-first, or None when the call fails."""
    prompt = (
        f"Original research question: {query}\n"
        f"Current date: {date.today().isoformat()}\n"
        f"Select up to {cap} candidates to scrape.\n"
        "Candidate country names, country codes, and language codes describe the search that produced each result; "
        "they do not prove that the result itself matches that geography.\n"
        "If the question has no specific geographic scope, do not invent one.\n\n"
        f"Candidates:\n{json.dumps(candidates, ensure_ascii=False, indent=2)}"
    )
    selector = Agent(
        name="search_result_selector",
        model=selector_model,
        output_type=FollowupSelection,
        model_settings=_research_model_settings(),
        instructions=SEARCH_RESULT_SELECTOR_INSTRUCTIONS,
    )
    try:
        async with model_admission():
            result = await Runner.run(selector, prompt)
        return result.final_output_as(FollowupSelection).selected_urls
    except Exception as exc:
        logger.warning("[pipeline] search-result selector failed: %s", exc)
        return None


async def _search_and_scrape_iteration(
    *,
    query: str,
    include_domains: Optional[list[str]],
    exclude_domains: Optional[list[str]] = None,
    depth: dict[str, int],
    iteration: int,
    missing_info: str,
    query_gen_agent: Agent,
    backend: Any,
    tracker: ResearchTracker,
    scrape_tool: Any,
    selector_model: Any = None,
) -> SearchIterationResult:
    search_queries = await _generate_search_queries(
        query=query,
        include_domains=include_domains,
        depth=depth,
        iteration=iteration,
        missing_info=missing_info,
        query_gen_agent=query_gen_agent,
    )
    requirements = getattr(query_gen_agent, "_research_requirements", None)
    if tracker is not None and requirements:
        tracker.requirements = list(requirements)
    search_results = await _execute_searches(
        backend=backend,
        search_queries=search_queries,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        tracker=tracker,
    )
    urls_to_scrape = await _select_search_urls_with_model(
        query=query,
        include_domains=include_domains,
        depth=depth,
        iteration=iteration,
        tracker=tracker,
        search_results=search_results,
        selector_model=selector_model,
        exclude_domains=exclude_domains,
    )
    return await _scrape_urls(
        scrape_tool,
        urls_to_scrape,
        empty_log_message="[pipeline] no new URLs to scrape",
    )


def _domain_candidate_from_link(
    *,
    link: str,
    include_domains: list[str],
    query: str,
    tracker: ResearchTracker,
) -> bool:
    parsed_netloc = urlparse(link).netloc.lower()
    return (
        any(
            parsed_netloc == domain.lower() or parsed_netloc.endswith("." + domain.lower())
            for domain in include_domains
        )
        and not tracker.has_attempted_url(link)
        and any(_is_promising_followup_url(link, domain.lower(), query=query) for domain in include_domains)
    )


def _append_domain_candidates(
    *,
    candidates: list[str],
    links: list[str],
    include_domains: list[str],
    query: str,
    tracker: ResearchTracker,
) -> None:
    for link in links:
        if (
            link
            and _domain_candidate_from_link(
                link=link,
                include_domains=include_domains,
                query=query,
                tracker=tracker,
            )
            and link not in candidates
        ):
            candidates.append(link)


def _collect_hub_results(
    iteration_result: SearchIterationResult,
) -> list[tuple[str, ExtractorOutcome]]:
    if not iteration_result.outcomes_by_url:
        return [
            (url, resolve_scrape_outcome(None, url, content))
            for url, content in iteration_result.iter_results
            if is_rendered_list_page(content)
        ]
    return [(url, outcome) for url, outcome in iteration_result.outcomes_by_url.items() if outcome.page_type == "list"]


async def _collect_hub_candidates(
    *,
    query: str,
    include_domains: list[str],
    tracker: ResearchTracker,
    scrape_tool: Any,
    hub_results: list[tuple[str, ExtractorOutcome]],
) -> list[str]:
    candidates: list[str] = []
    for hub_url, hub_outcome in hub_results:
        _append_domain_candidates(
            candidates=candidates,
            links=hub_outcome.relevant_links,
            include_domains=include_domains,
            query=query,
            tracker=tracker,
        )

        next_page = _find_next_page_url(hub_outcome.rendered_text, hub_url)
        if next_page and not tracker.has_attempted_url(next_page):
            logger.info("[pipeline] hub pagination (domain mode): %s", next_page)
            next_content = await scrape_tool(next_page)
            next_outcome = resolve_scrape_outcome(scrape_tool, next_page, next_content)
            _append_domain_candidates(
                candidates=candidates,
                links=next_outcome.relevant_links,
                include_domains=include_domains,
                query=query,
                tracker=tracker,
            )
    return candidates


async def _deepen_hub_results(
    *,
    query: str,
    include_domains: list[str],
    depth: dict[str, int],
    followup_model: Any,
    followup_backend: str,
    tracker: ResearchTracker,
    scrape_tool: Any,
    hub_results: list[tuple[str, ExtractorOutcome]],
) -> None:
    candidates = await _collect_hub_candidates(
        query=query,
        include_domains=include_domains,
        tracker=tracker,
        scrape_tool=scrape_tool,
        hub_results=hub_results,
    )
    if not candidates:
        return

    hub_cap = depth["hub_deepening_cap"]
    chosen = await _rerank_followup_urls(
        query=query,
        parent_url=hub_results[0][0],
        parent_content=hub_results[0][1].rendered_text,
        candidates=candidates,
        cap=hub_cap,
        model=followup_model,
        selector=followup_backend,
    )
    logger.info(
        "[pipeline] hub deepening (domain mode) on %d candidates (cap=%d)",
        len(chosen),
        hub_cap,
    )
    await _gather_scrapes([scrape_tool(link) for link in chosen])


def _collect_non_hub_links_to_deepen(
    *,
    query: str,
    include_domains: list[str],
    tracker: ResearchTracker,
    outcomes: list[ExtractorOutcome],
) -> list[str]:
    links_to_deepen: list[str] = []
    for outcome in outcomes:
        _append_domain_candidates(
            candidates=links_to_deepen,
            links=outcome.relevant_links or extract_rendered_followup_links(outcome.rendered_text),
            include_domains=include_domains,
            query=query,
            tracker=tracker,
        )
    return links_to_deepen


async def _deepen_non_hub_results(
    *,
    query: str,
    include_domains: list[str],
    followup_model: Any,
    followup_backend: str,
    tracker: ResearchTracker,
    scrape_tool: Any,
    iteration_result: SearchIterationResult,
) -> None:
    links_to_deepen = _collect_non_hub_links_to_deepen(
        query=query,
        include_domains=include_domains,
        tracker=tracker,
        outcomes=list(iteration_result.outcomes_by_url.values())
        or [resolve_scrape_outcome(scrape_tool, url, content) for url, content in iteration_result.iter_results],
    )
    if not links_to_deepen:
        return

    parent_url = next((url for url, _ in iteration_result.iter_results if url), include_domains[0])
    parent_content = next(
        (outcome.rendered_text for outcome in iteration_result.outcomes_by_url.values() if outcome.rendered_text),
        next((content for _, content in iteration_result.iter_results if content), ""),
    )
    deep_links = await _rerank_followup_urls(
        query=query,
        parent_url=parent_url,
        parent_content=parent_content,
        candidates=links_to_deepen,
        cap=min(3, len(links_to_deepen)),
        model=followup_model,
        selector=followup_backend,
    )
    logger.info(
        "[pipeline] domain restricted deepening on %d links: %s",
        len(deep_links),
        deep_links,
    )
    await _gather_scrapes([scrape_tool(link) for link in deep_links])


async def _deepen_domain_iteration(
    *,
    query: str,
    include_domains: list[str],
    depth: dict[str, int],
    followup_model: Any,
    followup_backend: str,
    tracker: ResearchTracker,
    scrape_tool: Any,
    iteration_result: SearchIterationResult,
) -> None:
    """Deepen same-domain coverage after the initial scrape pass when needed."""
    count_scraped = tracker.count_for_action("scraped")
    hub_results = _collect_hub_results(iteration_result)

    if hub_results:
        await _deepen_hub_results(
            query=query,
            include_domains=include_domains,
            depth=depth,
            followup_model=followup_model,
            followup_backend=followup_backend,
            tracker=tracker,
            scrape_tool=scrape_tool,
            hub_results=hub_results,
        )
        return

    if count_scraped >= 2 or not iteration_result.extracted_contents:
        return

    await _deepen_non_hub_results(
        query=query,
        include_domains=include_domains,
        followup_model=followup_model,
        followup_backend=followup_backend,
        tracker=tracker,
        scrape_tool=scrape_tool,
        iteration_result=iteration_result,
    )


async def _evaluate_search_coverage(
    *,
    query: str,
    include_domains: Optional[list[str]],
    depth: dict[str, int],
    evaluator_agent: Agent,
    tracker: ResearchTracker,
    exclude_domains: Optional[frozenset[str]],
    state: SearchLoopState,
) -> bool:
    """Decide whether to stop, reuse backlog URLs, or trigger new searches."""
    scraped_entries = tracker.entries_for_action("scraped")
    if not scraped_entries:
        logger.info("[pipeline] 0 successful scrapes, doing another iteration")
        state.missing_info = "Everything, no successful scrapes yet."
        state.needs_new_searches = True
        return False

    eval_prompt = _build_coverage_prompt(query, tracker)
    snippet_only_entries = tracker.entries_for_action("snippet_only")
    try:
        if classification_backend() == "jev":
            sources = [
                {"url": item.url, "text": tracker._source_text.get(tracker.normalize_url(item.url), item.content)}
                for item in scraped_entries
            ]
            candidates = [
                {"url": item.url, "title": item.title, "snippet": item.content} for item in snippet_only_entries
            ]
            answered, gaps, promising = await jev_coverage(query, state.requirements or [query], sources, candidates)
            evaluation = CoverageEvaluation(
                fully_answered=answered, gaps=gaps, promising_unscraped_urls=promising, needs_new_searches=not promising
            )
        else:
            async with model_admission():
                eval_res = await Runner.run(evaluator_agent, eval_prompt)
            evaluation = eval_res.final_output_as(CoverageEvaluation)
    except ClassificationError:
        raise
    except Exception as exc:
        logger.error("[pipeline] coverage evaluation failed: %s", exc)
        evaluation = CoverageEvaluation(
            fully_answered=False,
            gaps="Evaluator failed",
            promising_unscraped_urls=[],
            needs_new_searches=True,
        )

    logger.info(
        "[pipeline] coverage evaluation: fully_answered=%s, gaps=%r, needs_new_searches=%s, promising_urls=%d",
        evaluation.fully_answered,
        evaluation.gaps,
        evaluation.needs_new_searches,
        len(evaluation.promising_unscraped_urls),
    )

    if evaluation.fully_answered:
        logger.info("[pipeline] query fully answered, proceeding to synthesis")
        return True

    state.missing_info = evaluation.gaps
    state.needs_new_searches = evaluation.needs_new_searches

    snippet_norm_map = {tracker.normalize_url(entry.url): entry.url for entry in snippet_only_entries}
    state.promising_urls_from_evaluator = [
        snippet_norm_map[tracker.normalize_url(url)]
        for url in evaluation.promising_unscraped_urls
        if tracker.normalize_url(url) in snippet_norm_map
    ][: depth["urls_followup"]]
    state.promising_urls_from_evaluator = _filter_blocked_domain_backlog_urls(
        state.promising_urls_from_evaluator,
        tracker,
    )
    state.promising_urls_from_evaluator = [
        url
        for url in state.promising_urls_from_evaluator
        if not is_blocked_domain(url, exclude_domains=exclude_domains)
    ]
    if include_domains:
        state.promising_urls_from_evaluator = [
            url for url in state.promising_urls_from_evaluator if _is_domain_mode_candidate(url, include_domains, query)
        ]

    if not state.needs_new_searches and not state.promising_urls_from_evaluator:
        logger.info(
            "[pipeline] evaluator said skip search but gave no valid backlog URLs; falling back to new searches"
        )
        state.needs_new_searches = True
    return False


def _extract_rendered_followup_candidates(content: str) -> list[str]:
    """Read follow-up candidates from the rendered scrape output."""
    return extract_rendered_followup_links(content)


def _sanitize_followup_candidates(
    links: list[str],
    *,
    parent_url: Optional[str] = None,
) -> list[str]:
    """Deduplicate follow-up candidates and optionally drop the parent URL itself."""
    seen: set[str] = set()
    sanitized: list[str] = []
    parent_norm = ResearchTracker.normalize_url(parent_url) if parent_url else None

    for link in links:
        if not link:
            continue
        norm = ResearchTracker.normalize_url(link)
        if norm in seen or (parent_norm and norm == parent_norm):
            continue
        seen.add(norm)
        sanitized.append(link)

    return sanitized


def _outcome_followup_candidates(
    outcome: ExtractorOutcome,
    *,
    parent_url: Optional[str] = None,
) -> list[str]:
    """Read follow-up candidates from an outcome without mining failure boilerplate."""
    if outcome.relevant_links:
        links = outcome.relevant_links
    elif outcome.status == "failure":
        links = extract_explicit_rendered_followup_links(outcome.rendered_text)
    else:
        links = _extract_rendered_followup_candidates(outcome.rendered_text)
    return _sanitize_followup_candidates(links, parent_url=parent_url)


async def _follow_extractor_links(
    *,
    query: str,
    iteration_result: SearchIterationResult,
    depth: dict[str, int],
    followup_model: Any,
    followup_backend: str,
    tracker: ResearchTracker,
    scrape_tool: Any,
    exclude_domains: Optional[frozenset[str]],
) -> SearchIterationResult:
    """Scrape one capped wave of links the extractor flagged on this round's pages (open search mode).

    Candidates are pooled from every outcome in ``iteration_result``, including rejected pages, which
    keep their links. Already-attempted, excluded-domain and parent URLs are dropped; the rest are ranked
    with ``_rerank_followup_urls`` (cap ``depth["urls_followup"]``) against the first contributing page.
    Returns the follow-up scrape result, empty when nothing was left to follow.
    """
    parent_urls = {ResearchTracker.normalize_url(url) for url in iteration_result.outcomes_by_url}
    candidates: list[str] = []
    seen: set[str] = set()
    parent: Optional[ExtractorOutcome] = None
    for url, outcome in iteration_result.outcomes_by_url.items():
        contributed = False
        for link in _outcome_followup_candidates(outcome, parent_url=url):
            norm = ResearchTracker.normalize_url(link)
            if (
                norm in seen
                or norm in parent_urls
                or tracker.has_attempted_url(link)
                or is_blocked_domain(link, exclude_domains=exclude_domains)
            ):
                continue
            seen.add(norm)
            candidates.append(link)
            contributed = True
        if contributed and parent is None:
            parent = outcome

    if parent is None:
        return SearchIterationResult(extracted_contents=[], iter_results=[])

    chosen = await _rerank_followup_urls(
        query=query,
        parent_url=parent.url,
        parent_content=parent.rendered_text,
        candidates=candidates,
        cap=depth["urls_followup"],
        model=followup_model,
        selector=followup_backend,
    )
    if not chosen:
        return SearchIterationResult(extracted_contents=[], iter_results=[])

    logger.info("[pipeline] open-mode follow-up on %d links: %s", len(chosen), chosen)
    return await _scrape_urls(scrape_tool, chosen, empty_log_message="")


async def _run_direct_url_mode(
    *,
    query: str,
    direct_url: str,
    tracker: ResearchTracker,
    scrape_tool: Any,
    depth: dict[str, int],
    followup_model: Any,
    followup_backend: str = DEFAULT_FOLLOWUP_BACKEND,
) -> None:
    """Handle direct-URL mode, including optional hub or same-domain deepening."""
    logger.info("[pipeline] scraping direct URL: %s", direct_url)
    tracker.record_direct_query(query)

    content = await scrape_tool(direct_url)
    outcome = resolve_scrape_outcome(scrape_tool, direct_url, content)
    logger.info(
        "[pipeline] direct_url_mode status=%s page_type=%s links=%d url=%s",
        outcome.status,
        outcome.page_type,
        len(outcome.relevant_links),
        direct_url,
    )
    if outcome.page_type == "list":
        candidates: list[str] = []
        for link in _outcome_followup_candidates(outcome, parent_url=direct_url):
            if link and link not in candidates:
                candidates.append(link)

        next_page = _find_next_page_url(outcome.rendered_text, direct_url)
        if next_page:
            logger.info("[pipeline] hub pagination: scraping next page %s", next_page)
            next_content = await scrape_tool(next_page)
            next_outcome = resolve_scrape_outcome(scrape_tool, next_page, next_content)
            for link in _outcome_followup_candidates(next_outcome, parent_url=next_page):
                if link and link not in candidates:
                    candidates.append(link)

        hub_cap = depth["hub_deepening_cap"]
        if candidates:
            chosen = await _rerank_followup_urls(
                query=query,
                parent_url=direct_url,
                parent_content=outcome.rendered_text,
                candidates=candidates,
                cap=hub_cap,
                model=followup_model,
                selector=followup_backend,
            )
            logger.info(
                "[pipeline] hub deepening on %d candidate links (cap=%d)",
                len(chosen),
                hub_cap,
            )
            await _gather_scrapes([scrape_tool(link) for link in chosen])
        return

    if looks_like_document_resource(direct_url) or looks_like_document_link(direct_url):
        links_to_deepen: list[str] = []
    else:
        links_to_deepen = _outcome_followup_candidates(outcome, parent_url=direct_url)

    if not links_to_deepen:
        return

    direct_domain = _normalize_domain(direct_url)
    high_value_document_links = [link for link in links_to_deepen if _looks_like_document_followup(link)]
    same_domain_links = [
        link
        for link in links_to_deepen
        if direct_domain and _is_promising_followup_url(link, direct_domain, query=query)
    ]
    candidate_links: list[str] = []
    for link in high_value_document_links + same_domain_links:
        if link not in candidate_links:
            candidate_links.append(link)
    if not candidate_links:
        return

    chosen = await _rerank_followup_urls(
        query=query,
        parent_url=direct_url,
        parent_content=outcome.rendered_text,
        candidates=candidate_links,
        cap=min(3, len(candidate_links)),
        model=followup_model,
        selector=followup_backend,
    )
    logger.info("[pipeline] deepening on %d links from direct URL", len(chosen))
    await _gather_scrapes([scrape_tool(link) for link in chosen])


async def _run_search_mode_impl(
    *,
    query: str,
    include_domains: Optional[list[str]],
    search_backend: str,
    domain_expertise: Optional[str],
    depth: dict[str, int],
    query_gen_model: Any,
    evaluator_model: Any,
    followup_model: Any,
    followup_backend: str = DEFAULT_FOLLOWUP_BACKEND,
    search_selector_model: Any = None,
    tracker: ResearchTracker,
    scrape_tool: Any,
    exclude_domains: Optional[frozenset[str]],
    evaluator_extra_prompt: Optional[str] = None,
    build_search_backend: Any,
    build_query_agents: Any,
    search_and_scrape_iteration: Any,
    evaluate_search_coverage: Any,
) -> None:
    backend = build_search_backend(search_backend)
    query_gen_agent, evaluator_agent = build_query_agents(
        query_gen_model=query_gen_model,
        evaluator_model=evaluator_model,
        domain_expertise=domain_expertise,
        evaluator_extra_prompt=evaluator_extra_prompt,
    )
    state = SearchLoopState()
    from .scraping._stealth_session import prewarm_host

    warmups = [
        asyncio.create_task(prewarm_host("https://" + _normalize_domain(host))) for host in (include_domains or [])[:3]
    ]
    for task in warmups:
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)

    for iteration in range(depth["max_iterations"]):
        logger.info("[pipeline] starting search iteration %d", iteration + 1)

        if iteration > 0 and not state.needs_new_searches:
            iteration_result = await _scrape_backlog_urls(
                scrape_tool,
                state.promising_urls_from_evaluator,
            )
        else:
            iteration_result = await search_and_scrape_iteration(
                query=query,
                include_domains=include_domains,
                # Sorted for determinism; backends without native exclusion
                # ignore this and the post-search URL filter covers them.
                exclude_domains=sorted(exclude_domains) if exclude_domains else None,
                depth=depth,
                iteration=iteration,
                missing_info=state.missing_info,
                query_gen_agent=query_gen_agent,
                backend=backend,
                tracker=tracker,
                scrape_tool=scrape_tool,
                selector_model=search_selector_model,
            )

        if classification_backend() == "jev":
            state.requirements = getattr(query_gen_agent, "_research_requirements", None) or [query]
        if include_domains:
            await _deepen_domain_iteration(
                query=query,
                include_domains=include_domains,
                depth=depth,
                followup_model=followup_model,
                followup_backend=followup_backend,
                tracker=tracker,
                scrape_tool=scrape_tool,
                iteration_result=iteration_result,
            )
        else:
            # Pages from this wave are not mined again: one follow-up wave per round.
            await _follow_extractor_links(
                query=query,
                iteration_result=iteration_result,
                depth=depth,
                followup_model=followup_model,
                followup_backend=followup_backend,
                tracker=tracker,
                scrape_tool=scrape_tool,
                exclude_domains=exclude_domains,
            )

        if iteration >= depth["max_iterations"] - 1:
            continue

        if await evaluate_search_coverage(
            query=query,
            include_domains=include_domains,
            depth=depth,
            evaluator_agent=evaluator_agent,
            tracker=tracker,
            exclude_domains=exclude_domains,
            state=state,
        ):
            break


def _assemble_research_result(tracker: ResearchTracker, *, synthesis: str) -> WebResearchResult:
    """Copy tracker URL groups and queries onto a ``WebResearchResult``."""
    groups = tracker.build_result_groups()
    scraped = groups["scraped"]
    scrape_failed = groups["scrape_failed"]
    blocked_by_policy = groups["blocked_by_policy"]
    source_http_error = groups["source_http_error"]
    scraped_irrelevant = groups["scraped_irrelevant"]
    bot_detected = groups["bot_detected"]
    snippet_only = groups["snippet_only"]

    logger.info(
        "[pipeline] done scraped=%d failed=%d blocked=%d source_http=%d irrelevant=%d "
        "bot_detected=%d snippet_only=%d queries=%d",
        len(scraped),
        len(scrape_failed),
        len(blocked_by_policy),
        len(source_http_error),
        len(scraped_irrelevant),
        len(bot_detected),
        len(snippet_only),
        len(tracker.queries),
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
        synthesis=synthesis,
    )


async def _synthesise_result(
    *,
    query: str,
    tracker: ResearchTracker,
    synth_model: Any,
    domain_expertise: Optional[str],
) -> WebResearchResult:
    groups = tracker.build_result_groups()
    scraped = groups["scraped"]
    scrape_failed = groups["scrape_failed"]
    blocked_by_policy = groups["blocked_by_policy"]
    source_http_error = groups["source_http_error"]
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
        model_settings=_research_model_settings(reasoning=Reasoning(effort="high")),
    )
    slots = _build_citation_slots(scraped)

    if bot_detected:
        logger.info(
            "[pipeline] %d URL(s) blocked by bot-protection: %s",
            len(bot_detected),
            [entry.url for entry in bot_detected],
        )
    if blocked_by_policy:
        logger.info(
            "[pipeline] %d URL(s) skipped by policy: %s",
            len(blocked_by_policy),
            [entry.url for entry in blocked_by_policy],
        )

    try:
        async with model_admission():
            synth_res = await Runner.run(synth_agent, synth_prompt)
        output = synth_res.final_output_as(WebResearchResultRaw)
    except Exception as exc:
        logger.error("[pipeline] synthesis failed: %s", exc)
        output = WebResearchResultRaw(synthesis=f"Synthesis failed: {exc}")

    resolved, unknown_ids = _resolve_slot_citations(output.synthesis, slots)
    if unknown_ids and output.synthesis and not output.synthesis.startswith("Synthesis failed"):
        logger.warning("[pipeline] retrying synthesis: unknown source id(s) cited: %s", unknown_ids)
        valid_ids = ", ".join(slots) if slots else "(none — no scraped sources, cite nothing)"
        feedback = (
            "Your synthesis cites unknown source ids: " + ", ".join(unknown_ids) + ". "
            f"The only valid source ids are: {valid_ids}.\n"
            "Rewrite the synthesis citing only valid ids. Keep all factual content unchanged."
        )
        retry_prompt = synth_prompt + f"\n\nPrevious attempt:\n{output.synthesis}\n\n{feedback}"
        try:
            async with model_admission():
                synth_res2 = await Runner.run(synth_agent, retry_prompt)
            output = synth_res2.final_output_as(WebResearchResultRaw)
        except Exception as exc:
            logger.error("[pipeline] synthesis retry failed: %s", exc)
        resolved, unknown_ids = _resolve_slot_citations(output.synthesis, slots)
        if unknown_ids:
            logger.warning(
                "[pipeline] unknown source id(s) still cited after retry, left as plain text: %s",
                unknown_ids,
            )
    output.synthesis = resolved

    return _assemble_research_result(tracker, synthesis=output.synthesis)


__all__ = [
    "_assemble_research_result",
    "_build_query_agents",
    "_build_search_backend",
    "_collect_hub_results",
    "_deepen_domain_iteration",
    "_evaluate_search_coverage",
    "_follow_extractor_links",
    "_gather_scrapes",
    "_rerank_followup_urls",
    "_run_direct_url_mode",
    "_run_search_mode_impl",
    "_search_and_scrape_iteration",
    "_select_followup_urls_with_jev",
    "_select_followup_urls_with_luna",
    "_select_search_urls",
    "_select_search_urls_with_model",
    "_synthesise_result",
]
