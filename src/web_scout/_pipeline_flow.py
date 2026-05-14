"""Pipeline orchestration helpers for deterministic web research."""

from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from typing import Any, Optional
from urllib.parse import urlparse

from agents import Agent, ModelSettings, Runner

from ._extractor_contract import ExtractorOutcome
from ._heuristics import FOLLOWUP_HEURISTICS
from ._pipeline_rules import (
    _build_coverage_prompt,
    _build_query_generation_prompt,
    _build_synth_prompt,
    _diversify_search_urls,
    _filter_blocked_domain_backlog_urls,
    _find_next_page_url,
    _is_domain_mode_candidate,
    _is_promising_followup_url,
    _judge_synthesis,
    _normalize_domain,
    _rank_followup_candidates,
)
from ._pipeline_types import (
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
from .models import WebResearchResult, WebResearchResultRaw
from .scraping import ScrapeStrategy, _build_scrape_plan, _is_blocked_domain
from .tools import (
    ResearchTracker,
    _extract_rendered_followup_links,
    _is_rendered_list_page,
    _resolve_scrape_outcome,
)

logger = logging.getLogger("web_scout.agent")


async def _gather_scrapes(tasks: list) -> list:
    """Run scrape coroutines concurrently; concurrency is bounded inside the scrape tool."""
    return await asyncio.gather(*tasks)


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
    shortlist = ranked_candidates[: max(cap * FOLLOWUP_HEURISTICS.shortlist_multiplier, cap)]
    if len(candidates) <= cap or len(shortlist) == 1:
        return shortlist[:cap]

    candidate_norm_map = OrderedDict(
        (ResearchTracker.normalize_url(url), url) for url in shortlist
    )
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
    except Exception as exc:
        logger.warning("[pipeline] follow-up URL reranker failed for %s: %s", parent_url, exc)
        return shortlist[:cap]

    selected: list[str] = []
    for url in raw_selected:
        norm = ResearchTracker.normalize_url(url)
        original = candidate_norm_map.get(norm)
        if original and original not in selected:
            selected.append(original)
        if len(selected) >= cap:
            break

    if not selected:
        return shortlist[:cap]

    logger.info(
        "[pipeline] reranked %d follow-up candidates for %s → %d selected",
        len(shortlist),
        parent_url,
        len(selected),
    )
    return selected


def _build_search_backend(search_backend: str):
    from .search_backends import SerperBackend

    if search_backend == "serper":
        key = os.getenv("SERPER_API_KEY", "")
        if not key:
            raise ValueError("search_backend='serper' requires SERPER_API_KEY env var")
        return SerperBackend(key)

    raise ValueError(
        f"Unknown search_backend={search_backend!r}. "
        "Currently supported: 'serper'. "
        "See SearchBackend in search_backends.py to add a new backend."
    )


def _build_query_agents(
    query_gen_model: Any,
    evaluator_model: Any,
    domain_expertise: Optional[str],
) -> tuple[Agent, Agent]:
    suffix = f"\nDomain Expertise: {domain_expertise}" if domain_expertise else ""
    query_gen_agent = Agent(
        name="query_generator",
        model=query_gen_model,
        output_type=SearchQueryGeneration,
        instructions=QUERY_GENERATOR_INSTRUCTIONS + suffix,
    )
    evaluator_agent = Agent(
        name="coverage_evaluator",
        model=evaluator_model,
        output_type=CoverageEvaluation,
        instructions=COVERAGE_EVALUATOR_INSTRUCTIONS + suffix,
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
            url: _resolve_scrape_outcome(scrape_tool, url, content)
            for url, content in zip(urls, extracted_contents)
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
) -> list[str]:
    n_queries = depth["queries_first"] if iteration == 0 else depth["queries_followup"]
    prompt = _build_query_generation_prompt(
        query=query,
        n_queries=n_queries,
        include_domains=include_domains,
        missing_info=missing_info if iteration > 0 else "",
    )
    try:
        gen_res = await Runner.run(query_gen_agent, prompt)
        search_queries = gen_res.final_output_as(SearchQueryGeneration).queries
    except Exception as exc:
        logger.error("[pipeline] query generation failed: %s", exc)
        search_queries = []

    if not search_queries:
        search_queries = [query]

    logger.info("[pipeline] generated %d queries: %s", len(search_queries), search_queries)
    return search_queries


async def _execute_searches(
    *,
    backend: Any,
    search_queries: list[str],
    include_domains: Optional[list[str]],
    tracker: ResearchTracker,
) -> list[Any]:
    async def do_search(query: str):
        try:
            response = await backend.search(query, max_results=10, include_domains=include_domains)
            tracker.record_search(query, len(response.results), include_domains, response.results)
            return response
        except Exception as exc:
            logger.error("[pipeline] search failed for %r: %s", query, exc)
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
                if tracker.is_domain_bot_blocked(url):
                    continue
                if norm not in unique_urls and tracker.is_unscraped_candidate(url):
                    unique_urls[norm] = url
                if len(unique_urls) >= max_urls_to_scrape:
                    break
        idx += 1

    urls_to_scrape = _diversify_search_urls(list(unique_urls.values()), max_urls_to_scrape)
    logger.info("[pipeline] selected %d new URLs to scrape", len(urls_to_scrape))
    return urls_to_scrape


async def _search_and_scrape_iteration(
    *,
    query: str,
    include_domains: Optional[list[str]],
    depth: dict[str, int],
    iteration: int,
    missing_info: str,
    query_gen_agent: Agent,
    backend: Any,
    tracker: ResearchTracker,
    scrape_tool: Any,
) -> SearchIterationResult:
    search_queries = await _generate_search_queries(
        query=query,
        include_domains=include_domains,
        depth=depth,
        iteration=iteration,
        missing_info=missing_info,
        query_gen_agent=query_gen_agent,
    )
    search_results = await _execute_searches(
        backend=backend,
        search_queries=search_queries,
        include_domains=include_domains,
        tracker=tracker,
    )
    urls_to_scrape = _select_search_urls(
        query=query,
        include_domains=include_domains,
        depth=depth,
        iteration=iteration,
        tracker=tracker,
        search_results=search_results,
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
        and any(
            _is_promising_followup_url(link, domain.lower(), query=query)
            for domain in include_domains
        )
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


def _collect_hub_results(iteration_result: SearchIterationResult) -> list[tuple[str, ExtractorOutcome]]:
    if not iteration_result.outcomes_by_url:
        return [
            (url, _resolve_scrape_outcome(None, url, content))
            for url, content in iteration_result.iter_results
            if _is_rendered_list_page(content)
        ]
    return [
        (url, outcome)
        for url, outcome in iteration_result.outcomes_by_url.items()
        if outcome.page_type == "list"
    ]


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
            next_outcome = _resolve_scrape_outcome(scrape_tool, next_page, next_content)
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
            links=outcome.relevant_links or _extract_rendered_followup_links(outcome.rendered_text),
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
    tracker: ResearchTracker,
    scrape_tool: Any,
    iteration_result: SearchIterationResult,
) -> None:
    links_to_deepen = _collect_non_hub_links_to_deepen(
        query=query,
        include_domains=include_domains,
        tracker=tracker,
        outcomes=list(iteration_result.outcomes_by_url.values())
        or [
            _resolve_scrape_outcome(scrape_tool, url, content)
            for url, content in iteration_result.iter_results
        ],
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
    )
    logger.info("[pipeline] domain restricted deepening on %d links: %s", len(deep_links), deep_links)
    await _gather_scrapes([scrape_tool(link) for link in deep_links])


async def _deepen_domain_iteration(
    *,
    query: str,
    include_domains: list[str],
    depth: dict[str, int],
    followup_model: Any,
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
    allowed_domains: Optional[frozenset[str]],
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
        eval_res = await Runner.run(evaluator_agent, eval_prompt)
        evaluation = eval_res.final_output_as(CoverageEvaluation)
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

    snippet_norm_map = {
        tracker.normalize_url(entry.url): entry.url for entry in snippet_only_entries
    }
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
        if not _is_blocked_domain(url, allowed_domains=allowed_domains)
    ]
    if include_domains:
        state.promising_urls_from_evaluator = [
            url
            for url in state.promising_urls_from_evaluator
            if _is_domain_mode_candidate(url, include_domains, query)
        ]

    if not state.needs_new_searches and not state.promising_urls_from_evaluator:
        logger.info(
            "[pipeline] evaluator said skip search but gave no valid backlog URLs; falling back to new searches"
        )
        state.needs_new_searches = True
    return False


def _extract_rendered_followup_candidates(content: str) -> list[str]:
    """Read follow-up candidates from the rendered scrape output."""
    return _extract_rendered_followup_links(content)


def _outcome_followup_candidates(outcome: ExtractorOutcome) -> list[str]:
    """Read follow-up candidates from a typed outcome with legacy fallback."""
    return outcome.relevant_links or _extract_rendered_followup_candidates(outcome.rendered_text)


async def _run_direct_url_mode(
    *,
    query: str,
    direct_url: str,
    tracker: ResearchTracker,
    scrape_tool: Any,
    depth: dict[str, int],
    followup_model: Any,
    allowed_domains: Optional[frozenset[str]] = None,
) -> None:
    """Handle direct-URL mode, including optional hub or same-domain deepening."""
    logger.info("[pipeline] scraping direct URL: %s", direct_url)
    tracker.record_direct_query(query)

    content = await scrape_tool(direct_url)
    outcome = _resolve_scrape_outcome(scrape_tool, direct_url, content)
    logger.info(
        "[pipeline] direct_url_mode status=%s page_type=%s links=%d url=%s",
        outcome.status,
        outcome.page_type,
        len(outcome.relevant_links),
        direct_url,
    )
    if outcome.page_type == "list":
        candidates: list[str] = []
        for link in _outcome_followup_candidates(outcome):
            if link and link not in candidates:
                candidates.append(link)

        next_page = _find_next_page_url(outcome.rendered_text, direct_url)
        if next_page:
            logger.info("[pipeline] hub pagination: scraping next page %s", next_page)
            next_content = await scrape_tool(next_page)
            next_outcome = _resolve_scrape_outcome(scrape_tool, next_page, next_content)
            for link in _outcome_followup_candidates(next_outcome):
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
            )
            logger.info("[pipeline] hub deepening on %d candidate links (cap=%d)", len(chosen), hub_cap)
            await _gather_scrapes([scrape_tool(link) for link in chosen])
        return

    direct_plan = await _build_scrape_plan(direct_url, allowed_domains=allowed_domains)
    if direct_plan.strategy == ScrapeStrategy.DOCUMENT:
        links_to_deepen: list[str] = []
    else:
        links_to_deepen = _outcome_followup_candidates(outcome)

    if not links_to_deepen:
        return

    direct_domain = _normalize_domain(direct_url)
    same_domain_links = [
        link
        for link in links_to_deepen
        if direct_domain and _is_promising_followup_url(link, direct_domain, query=query)
    ]
    if not same_domain_links:
        return

    chosen = await _rerank_followup_urls(
        query=query,
        parent_url=direct_url,
        parent_content=outcome.rendered_text,
        candidates=same_domain_links,
        cap=min(3, len(same_domain_links)),
        model=followup_model,
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
    tracker: ResearchTracker,
    scrape_tool: Any,
    allowed_domains: Optional[frozenset[str]],
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
    )
    state = SearchLoopState()

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
                depth=depth,
                iteration=iteration,
                missing_info=state.missing_info,
                query_gen_agent=query_gen_agent,
                backend=backend,
                tracker=tracker,
                scrape_tool=scrape_tool,
            )

        if include_domains:
            await _deepen_domain_iteration(
                query=query,
                include_domains=include_domains,
                depth=depth,
                followup_model=followup_model,
                tracker=tracker,
                scrape_tool=scrape_tool,
                iteration_result=iteration_result,
            )

        if iteration >= depth["max_iterations"] - 1:
            continue

        if await evaluate_search_coverage(
            query=query,
            include_domains=include_domains,
            depth=depth,
            evaluator_agent=evaluator_agent,
            tracker=tracker,
            allowed_domains=allowed_domains,
            state=state,
        ):
            break


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
    valid_urls = {entry.url for entry in scraped}

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
        synth_res = await Runner.run(synth_agent, synth_prompt)
        output = synth_res.final_output_as(WebResearchResultRaw)
    except Exception as exc:
        logger.error("[pipeline] synthesis failed: %s", exc)
        output = WebResearchResultRaw(synthesis=f"Synthesis failed: {exc}")

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
        except Exception as exc:
            logger.error("[pipeline] synthesis retry failed: %s", exc)
    elif not issues and output.synthesis and not output.synthesis.startswith("Synthesis failed"):
        logger.info("[pipeline] synthesis passed judge with 0 issues")

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
        synthesis=output.synthesis,
    )


__all__ = [
    "_build_query_agents",
    "_build_search_backend",
    "_collect_hub_results",
    "_deepen_domain_iteration",
    "_evaluate_search_coverage",
    "_gather_scrapes",
    "_rerank_followup_urls",
    "_run_direct_url_mode",
    "_run_search_mode_impl",
    "_search_and_scrape_iteration",
    "_select_search_urls",
    "_synthesise_result",
]
