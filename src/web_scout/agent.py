"""Web researcher deterministic pipeline.

Uses pluggable search backends (Serper / DuckDuckGo). A dedicated
content extractor sub-agent (crawl4ai / docling) scrapes and summarises
each URL so the main researcher only sees focused excerpts.

Three input modes — all share a single linear pipeline:

1. **Query-only** — open web search -> triage -> parallel scrape -> synthesis.
2. **Domain + query** — domain-restricted search -> parallel scrape -> optional deep scrape -> synthesis.
3. **Direct URL** — skip search, extract a given URL directly -> optional deep scrape -> synthesis.

Two research depth presets control how aggressively the pipeline searches:

- ``"standard"`` (default) — 2 iterations, up to ~10 sources.
- ``"deep"`` — 3 iterations, more search queries, up to ~28 sources.

Public API
----------
- ``run_web_research(query, models, ...)`` — full pipeline
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from collections import OrderedDict

from pydantic import BaseModel, Field

from agents import Agent, ModelSettings, Runner
from .models import WebResearchResult, WebResearchResultRaw
from .tools import ResearchTracker, create_scrape_and_extract_tool

logger = logging.getLogger(__name__)

# Suppress verbose LiteLLM INFO logs (keep WARNING+ only)
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("litellm").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Default models
# ---------------------------------------------------------------------------

DEFAULT_WEB_RESEARCH_MODELS = {
    "web_researcher": "gemini/gemini-3-flash-preview",
    "content_extractor": "gemini/gemini-3-flash-preview",
    "vision_fallback": "gemini/gemini-3-flash-preview",
}

# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------

QUERY_GENERATOR_INSTRUCTIONS = """\
You are an expert web researcher.
Generate highly effective search queries to find evidence for the user's research query.\
"""

COVERAGE_EVALUATOR_INSTRUCTIONS = """\
You evaluate whether the provided extracted web content fully answers the research query.
Be strict: if key specific data (like species names, numbers, thresholds) is requested but missing, it is not fully answered.
If the query is NOT fully answered, review the provided "Unscraped Candidates" (search result snippets not yet scraped).
- If any candidates look likely to contain the missing information, list their exact URLs in `promising_unscraped_urls` and set `needs_new_searches` to false.
- If the candidates look useless or unrelated to the missing information, set `needs_new_searches` to true and leave `promising_unscraped_urls` empty.\
"""

SYNTHESISER_INSTRUCTIONS = """\
You are a web research synthesiser. Your job is to read the extracted contents
from various web pages and produce a coherent narrative `synthesis` answering the query.

Rules:
- ONLY use information that appears in the provided scraped sources. Do NOT add facts,
  numbers, dates, or claims from your own training data. If the sources do not contain
  the information, state that it was not found rather than filling in from memory.
- Address the query directly, mention any data gaps, do not fabricate information.
- Use inline markdown citations after each claim: [Source Title](URL).
  Every factual statement must be attributed to at least one source.
- If there are contradictions, caveats, or outdated data across sources, note them.
"""


class SearchQueryGeneration(BaseModel):
    """LLM output for generating diverse search queries."""
    queries: List[str] = Field(description="List of search queries")

class CoverageEvaluation(BaseModel):
    """LLM output for evaluating coverage and routing the next pipeline step.

    After inspecting scraped content, the evaluator decides whether to scrape
    promising URLs already in the backlog or run new web searches.
    """
    fully_answered: bool = Field(description="True if the extracted content fully answers the original research query.")
    gaps: str = Field(description="If not fully answered, what specific information is still missing?")
    promising_unscraped_urls: List[str] = Field(
        default_factory=list,
        description="If not fully answered, list exact URLs from the Unscraped Candidates that likely contain the missing information. Leave empty if none are promising.",
    )
    needs_new_searches: bool = Field(
        default=True,
        description="True if the unscraped candidates are insufficient and new web searches must be run. False if promising_unscraped_urls candidates are enough to try first.",
    )


# ---------------------------------------------------------------------------
# Research depth presets
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

import re as _re

_NEXT_PAGE_TOKENS: frozenset = frozenset({"next", "next page", "›", "»"})


def _judge_synthesis(synthesis: str, valid_urls: set[str]) -> list[str]:
    """Return a list of issue descriptions, empty if synthesis passes."""
    issues = []

    # 1. Detect bare URLs
    # Strip out valid markdown links: [Title](URL)
    text_without_md_links = _re.sub(r'\[[^\]]*\]\((https?://[^\s\)]+)\)', '', synthesis)
    
    # Find any remaining http(s):// strings, cleaning up trailing punctuation
    bare_urls = [
        m.group().rstrip('.,;)"\'')
        for m in _re.finditer(r'https?://\S+', text_without_md_links)
    ]
    bare_urls = [u for u in bare_urls if u]

    if bare_urls:
        issues.append(
            "Bare URLs found (must be wrapped as markdown links [Title](URL)): "
            + ", ".join(bare_urls[:5])
        )

    # 2. Detect hallucinated URLs
    md_link_urls = set(_re.findall(r'\[[^\]]*\]\((https?://[^\s\)]+)\)', synthesis))
    
    valid_norm = {ResearchTracker._normalize_url(u) for u in valid_urls}
    
    hallucinated = [u for u in md_link_urls if ResearchTracker._normalize_url(u) not in valid_norm]
    if hallucinated:
        issues.append(
            "URLs cited that are NOT in the available sources (remove or replace): "
            + ", ".join(hallucinated[:5])
        )

    return issues


def _find_next_page_url(content: str, base_url: str) -> Optional[str]:
    """Scan markdown content for a 'next page' link on the same domain as base_url.

    Matches link text (case-insensitive) against: 'next', 'next page', '›', '»'.
    Bare digits are intentionally excluded (too fragile).
    Returns the first matching same-domain URL, or None.
    """
    base_netloc = urlparse(base_url).netloc.lower()

    for match in _re.finditer(r'\[([^\]]*)\]\((https?://[^\s\)]+)\)', content):
        link_text = match.group(1).strip().lower()
        href = match.group(2)
        if link_text in _NEXT_PAGE_TOKENS:
            href_netloc = urlparse(href).netloc.lower()
            if href_netloc == base_netloc:
                return href
    return None


async def run_web_research(
    query: str,
    models: Dict[str, str],
    include_domains: Optional[List[str]] = None,
    direct_url: Optional[str] = None,
    search_backend: str = "serper",
    domain_expertise: Optional[str] = None,
    research_depth: str = "standard",
) -> WebResearchResult:
    """Run deterministic web research pipeline.

    Supports three input modes via argument combinations:

    - **Query-only**: ``query`` only — open web search + extraction.
    - **Domain + query**: ``query`` + ``include_domains`` — domain-
      restricted search + extraction.
    - **Direct URL**: ``query`` + ``direct_url`` — extract the given URL
      directly.

    Args:
        query: The research query.
        models: Model names keyed by role: ``"web_researcher"`` and
            ``"content_extractor"``. You can also pass ``"query_generator"``,
            ``"coverage_evaluator"``, and ``"synthesiser"`` to override the
            ``"web_researcher"`` model for specific steps. Pass
            ``"vision_fallback"`` (e.g. ``"gemini/gemini-2.0-flash"``) to
            enable screenshot-based extraction when text scraping fails
            (scanned PDFs, empty JS pages). Omit to keep the fallback
            disabled.
        include_domains: Restrict search to these domains.
        direct_url: Scrape this URL directly (disables web search).
        search_backend: ``"serper"`` (default) or ``"duckduckgo"``.
        domain_expertise: Optional area of expertise (e.g. "biodiversity").
        research_depth: ``"standard"`` (default) or ``"deep"``. Deep mode
            generates more search queries, scrapes more URLs per iteration,
            and runs an extra iteration for better coverage on complex queries.

    Returns:
        ``WebResearchResult`` with URLs grouped by action (scraped,
        scrape_failed, snippet_only) and query metadata.
    """
    from .utils import get_model
    from .search_backends import DuckDuckGoBackend, SerperBackend

    # Resolve depth preset
    if research_depth not in _DEPTH_PRESETS:
        raise ValueError(f"Unknown research_depth={research_depth!r}. Use 'standard' or 'deep'.")
    depth = _DEPTH_PRESETS[research_depth]

    # Create shared tracker
    tracker = ResearchTracker()
    
    # Allow fallback to "web_researcher" for backward compatibility
    fallback_model = models.get("web_researcher", DEFAULT_WEB_RESEARCH_MODELS["web_researcher"])
    
    query_gen_model = get_model(models.get("query_generator", fallback_model))
    evaluator_model = get_model(models.get("coverage_evaluator", fallback_model))
    synth_model = get_model(models.get("synthesiser", fallback_model))
    extractor_model = get_model(models.get("content_extractor", DEFAULT_WEB_RESEARCH_MODELS["content_extractor"]))
    vision_model = models.get("vision_fallback", DEFAULT_WEB_RESEARCH_MODELS["vision_fallback"])

    # Setup Scrape Tool
    scrape_tool = create_scrape_and_extract_tool(
        extractor_model=extractor_model,
        tracker=tracker,
        query=query,
        vision_model=vision_model,
    )

    logger.info("[pipeline] start  query=%r  backend=%s mode=%s depth=%s",
                query[:80], search_backend,
                "direct" if direct_url else "domain" if include_domains else "open",
                research_depth)

    if direct_url:
        from .models import SearchQuery
        # --- 1. DIRECT URL MODE ---
        logger.info("[pipeline] scraping direct URL: %s", direct_url)
        # Force a record of the query (since we didn't search) to keep logs clean
        tracker._queries.append(SearchQuery(query=query, num_results_returned=1, domains_restricted=[]))
        
        content = await scrape_tool(direct_url)
        
        # Deepen if relevant links are found
        is_hub = "**Page type: list**" in content

        if is_hub:
            # Hub page: collect LLM-ranked item links, one-hop pagination
            candidates = []
            if "**Relevant Links found on page:**" in content:
                for line in content.split("\n"):
                    if line.startswith("- http") or (line.startswith("- [") and "http" in line):
                        l = line.split("](", 1)[-1].split(")", 1)[0] if "](" in line else line.replace("- ", "").strip()
                        if l and l not in candidates:
                            candidates.append(l)

            next_page = _find_next_page_url(content, direct_url)
            if next_page:
                logger.info("[pipeline] hub pagination: scraping next page %s", next_page)
                next_content = await scrape_tool(next_page)
                for line in next_content.split("\n"):
                    if line.startswith("- http") or (line.startswith("- [") and "http" in line):
                        l = line.split("](", 1)[-1].split(")", 1)[0] if "](" in line else line.replace("- ", "").strip()
                        if l and l not in candidates:
                            candidates.append(l)

            hub_cap = depth["hub_deepening_cap"]
            if candidates:
                logger.info("[pipeline] hub deepening on %d candidate links (cap=%d)", len(candidates), hub_cap)
                tasks = [scrape_tool(link) for link in candidates[:hub_cap]]
                await asyncio.gather(*tasks)

        else:
            # Non-hub: existing behaviour — follow up to 3 same-domain links
            links_to_deepen = []
            if "**Relevant Links found on page:**" in content:
                for line in content.split("\n"):
                    if line.startswith("- http") or (line.startswith("- [") and "http" in line):
                        l = line.split("](", 1)[-1].split(")", 1)[0] if "](" in line else line.replace("- ", "").strip()
                        links_to_deepen.append(l)

            if links_to_deepen:
                direct_domain = urlparse(direct_url).netloc.lower()
                if direct_domain.startswith("www."):
                    direct_domain = direct_domain[4:]

                same_domain_links = []
                if direct_domain:
                    for l in links_to_deepen:
                        link_domain = urlparse(l).netloc.lower()
                        if link_domain == direct_domain or link_domain.endswith("." + direct_domain):
                            same_domain_links.append(l)

                same_domain_links = same_domain_links[:3]
                if same_domain_links:
                    logger.info("[pipeline] deepening on %d links from direct URL", len(same_domain_links))
                    tasks = [scrape_tool(link) for link in same_domain_links]
                    await asyncio.gather(*tasks)

    else:
        # --- 2. SEARCH MODE (Open Web or Domain Restricted) ---
        
        # 2a. Setup Search Backend
        if search_backend == "serper":
            key = os.getenv("SERPER_API_KEY", "")
            if not key:
                raise ValueError("search_backend='serper' requires SERPER_API_KEY env var")
            backend = SerperBackend(key)
        elif search_backend == "duckduckgo":
            backend = DuckDuckGoBackend()
        else:
            raise ValueError(f"Unknown search_backend={search_backend!r}")

        # 2b. Generate Search Queries
        query_gen_agent = Agent(
            name="query_generator",
            model=query_gen_model,
            output_type=SearchQueryGeneration,
            instructions=QUERY_GENERATOR_INSTRUCTIONS + (f"\nDomain Expertise: {domain_expertise}" if domain_expertise else "")
        )

        evaluator_agent = Agent(
            name="coverage_evaluator",
            model=evaluator_model,
            output_type=CoverageEvaluation,
            instructions=COVERAGE_EVALUATOR_INSTRUCTIONS + (f"\nDomain Expertise: {domain_expertise}" if domain_expertise else "")
        )

        # Routing state carried across iterations
        needs_new_searches = True
        promising_urls_from_evaluator: List[str] = []
        missing_info = ""

        for iteration in range(depth["max_iterations"]):
            logger.info("[pipeline] starting search iteration %d", iteration + 1)

            if iteration > 0 and not needs_new_searches:
                # 2b-alt. Evaluator flagged promising unscraped URLs — skip search entirely
                urls_to_scrape = promising_urls_from_evaluator
                logger.info("[pipeline] skipping search; scraping %d evaluator-selected backlog URLs", len(urls_to_scrape))
                extracted_contents = []
                if urls_to_scrape:
                    tasks = [scrape_tool(url) for url in urls_to_scrape]
                    extracted_contents = await asyncio.gather(*tasks)
                else:
                    logger.info("[pipeline] no promising backlog URLs to scrape")
            else:
                # 2b. Normal path: generate queries, search, triage, scrape
                if iteration == 0:
                    n_queries = depth["queries_first"]
                    prompt = f"Research Query: {query}\nGenerate exactly {n_queries} distinct search queries.\n"
                    if include_domains:
                        prompt += f"Note: We will search exclusively within these domains: {', '.join(include_domains)}\n"
                else:
                    n_queries = depth["queries_followup"]
                    prompt = f"Research Query: {query}\n"
                    prompt += f"We have already scraped some content, but we are missing: {missing_info}\n"
                    prompt += f"Generate exactly {n_queries} new distinct search queries specifically targeting this missing information.\n"
                    if include_domains:
                        prompt += f"Note: We will search exclusively within these domains: {', '.join(include_domains)}\n"

                try:
                    gen_res = await Runner.run(query_gen_agent, prompt, max_turns=1)
                    search_queries = gen_res.final_output_as(SearchQueryGeneration).queries
                except Exception as e:
                    logger.error("[pipeline] query generation failed: %s", e)
                    search_queries = []
                    
                if not search_queries:
                    search_queries = [query]

                logger.info("[pipeline] generated %d queries: %s", len(search_queries), search_queries)

                # 2c. Execute Searches in parallel
                async def do_search(q: str):
                    try:
                        resp = await backend.search(q, max_results=10, include_domains=include_domains)
                        tracker.record_search(q, len(resp.results), include_domains, resp.results)
                        return resp
                    except Exception as e:
                        logger.error("[pipeline] search failed for %r: %s", q, e)
                        return None

                search_results = await asyncio.gather(*(do_search(q) for q in search_queries))

                # 2d. Triage URLs
                # Interleave results from the queries to get a diverse set of top URLs
                unique_urls = OrderedDict()
                max_urls_to_scrape = depth["urls_first"] if iteration == 0 else depth["urls_followup"]
                idx = 0

                max_results_len = max([len(resp.results) for resp in search_results if resp] + [0])

                while len(unique_urls) < max_urls_to_scrape and idx < max_results_len:
                    for resp in search_results:
                        if resp and idx < len(resp.results):
                            url = resp.results[idx].url
                            norm = tracker._normalize_url(url)
                            # We must ensure we don't pick URLs we already scraped/tried
                            if norm not in unique_urls and tracker._actions.get(norm, "snippet_only") == "snippet_only":
                                unique_urls[norm] = url
                            if len(unique_urls) >= max_urls_to_scrape:
                                break
                    idx += 1

                urls_to_scrape = list(unique_urls.values())
                logger.info("[pipeline] selected %d new URLs to scrape", len(urls_to_scrape))

                # 2e. Scrape & Extract in parallel
                extracted_contents = []
                if urls_to_scrape:
                    tasks = [scrape_tool(url) for url in urls_to_scrape]
                    extracted_contents = await asyncio.gather(*tasks)
                else:
                    logger.info("[pipeline] no new URLs to scrape")

            # 2f. Deepen (Domain Restricted Mode)
            # If domain restricted, we might have hit index pages.
            # If we didn't get enough good content, let's deepen on internal links
            if include_domains:
                count_scraped = len(tracker.build_result_groups()["scraped"])
                if count_scraped < 2 and extracted_contents:
                    links_to_deepen = []
                    for content in extracted_contents:
                        if "**Relevant Links found on page:**" in content:
                            for line in content.split("\n"):
                                if line.startswith("- http") or (line.startswith("- [") and "http" in line):
                                    # Use string manipulation to get clean URL
                                    l = line.split("](", 1)[-1].split(")", 1)[0] if "](" in line else line.replace("- ", "").strip()
                                    parsed_netloc = urlparse(l).netloc.lower()
                                    if any(parsed_netloc == d.lower() or parsed_netloc.endswith("." + d.lower()) for d in include_domains):
                                        if tracker._normalize_url(l) not in tracker._actions:
                                            if l not in links_to_deepen:
                                                links_to_deepen.append(l)

                    if links_to_deepen:
                        deep_links = links_to_deepen[:3]
                        logger.info("[pipeline] domain restricted deepening on %d links: %s", len(deep_links), deep_links)
                        deep_tasks = [scrape_tool(link) for link in deep_links]
                        await asyncio.gather(*deep_tasks)

            # 2g. Evaluate Coverage
            if iteration < depth["max_iterations"] - 1:
                scraped_entries = tracker.build_result_groups()["scraped"]
                if not scraped_entries:
                    logger.info("[pipeline] 0 successful scrapes, doing another iteration")
                    missing_info = "Everything, no successful scrapes yet."
                    needs_new_searches = True
                    continue

                eval_prompt = f"Research Query: {query}\n\nScraped Content:\n"
                for i, entry in enumerate(scraped_entries, 1):
                    eval_prompt += f"--- Source {i}: {entry.title or entry.url} ---\n{entry.content}\n\n"

                snippet_only_entries = tracker.build_result_groups()["snippet_only"]
                if snippet_only_entries:
                    eval_prompt += "\nUnscraped Candidates (search snippets not yet scraped):\n"
                    for i, entry in enumerate(snippet_only_entries, 1):
                        eval_prompt += f"--- Candidate {i}: {entry.url} ---\n"
                        if entry.title:
                            eval_prompt += f"Title: {entry.title}\n"
                        if entry.content:
                            eval_prompt += f"Snippet: {entry.content}\n"
                        eval_prompt += "\n"

                try:
                    eval_res = await Runner.run(evaluator_agent, eval_prompt, max_turns=1)
                    evaluation = eval_res.final_output_as(CoverageEvaluation)
                except Exception as e:
                    logger.error("[pipeline] coverage evaluation failed: %s", e)
                    evaluation = CoverageEvaluation(
                        fully_answered=False, gaps="Evaluator failed", promising_unscraped_urls=[], needs_new_searches=True
                    )

                logger.info(
                    "[pipeline] coverage evaluation: fully_answered=%s, gaps=%r, needs_new_searches=%s, promising_urls=%d",
                    evaluation.fully_answered, evaluation.gaps,
                    evaluation.needs_new_searches, len(evaluation.promising_unscraped_urls),
                )

                if evaluation.fully_answered:
                    logger.info("[pipeline] query fully answered, proceeding to synthesis")
                    break
                else:
                    missing_info = evaluation.gaps
                    needs_new_searches = evaluation.needs_new_searches

                    # Validate evaluator's URL picks against actual snippet_only set (hallucination guard)
                    snippet_norm_map = {
                        tracker._normalize_url(e.url): e.url
                        for e in snippet_only_entries
                    }
                    promising_urls_from_evaluator = [
                        snippet_norm_map[tracker._normalize_url(u)]
                        for u in evaluation.promising_unscraped_urls
                        if tracker._normalize_url(u) in snippet_norm_map
                    ][:depth["urls_followup"]]

                    if not needs_new_searches and not promising_urls_from_evaluator:
                        logger.info("[pipeline] evaluator said skip search but gave no valid backlog URLs; falling back to new searches")
                        needs_new_searches = True

    # --- 3. SYNTHESIZE ---
    groups = tracker.build_result_groups()
    scraped = groups["scraped"]
    scrape_failed = groups["scrape_failed"]
    bot_detected = groups["bot_detected"]
    snippet_only = groups["snippet_only"]

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

    synth_prompt = f"Research Query: {query}\n\n"
    if domain_expertise:
        synth_prompt += f"Domain Expertise: {domain_expertise}\n\n"

    if not scraped and not snippet_json:
        synth_prompt += "(No sources were found. You must state that no evidence was found.)\n"
    else:
        if scraped_json:
            synth_prompt += f"Scraped sources (full extracts):\n{_json.dumps(scraped_json, indent=2)}\n\n"
        if snippet_json:
            synth_prompt += f"Additional sources (search snippets only):\n{_json.dumps(snippet_json, indent=2)}\n\n"

    synth_prompt += "Provide the 'synthesis' of the findings directly answering the query.\n"
            
    synth_agent = Agent(
        name="synthesiser",
        model=synth_model,
        output_type=WebResearchResultRaw,
        instructions=SYNTHESISER_INSTRUCTIONS,
        model_settings=ModelSettings(extra_args={"reasoning_effort": "high"}),
    )
    
    valid_urls = {entry.url for entry in scraped + snippet_only}

    if bot_detected:
        logger.info(
            "[pipeline] %d URL(s) blocked by bot-protection: %s",
            len(bot_detected), [e.url for e in bot_detected],
        )

    try:
        synth_res = await Runner.run(synth_agent, synth_prompt, max_turns=1)
        output = synth_res.final_output_as(WebResearchResultRaw)
    except Exception as e:
        logger.error("[pipeline] synthesis failed: %s", e)
        output = WebResearchResultRaw(synthesis=f"Synthesis failed: {e}")

    # --- Judge: check synthesis and ask synthesiser to fix if needed ---
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
            synth_res2 = await Runner.run(synth_agent, retry_prompt, max_turns=1)
            output = synth_res2.final_output_as(WebResearchResultRaw)
        except Exception as e:
            logger.error("[pipeline] synthesis retry failed: %s", e)
            # keep original output
    elif not issues and output.synthesis and not output.synthesis.startswith("Synthesis failed"):
        logger.info("[pipeline] synthesis passed judge with 0 issues")

    logger.info(
        "[pipeline] done scraped=%d failed=%d bot_detected=%d snippet_only=%d queries=%d",
        len(scraped), len(scrape_failed), len(bot_detected), len(snippet_only), len(tracker.queries)
    )

    return WebResearchResult(
        scraped=scraped,
        scrape_failed=scrape_failed,
        bot_detected=bot_detected,
        snippet_only=snippet_only,
        queries=tracker.queries,
        synthesis=output.synthesis,
    )


# ---------------------------------------------------------------------------
# Quick manual test — run with: python -m web_scout.agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging
    from dotenv import load_dotenv
    from pathlib import Path

    load_dotenv(Path(__file__).parent.parent.parent / ".env")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    QUERY = "What are the main threats to coral reefs in Australia?"
    MODELS = DEFAULT_WEB_RESEARCH_MODELS

    async def _main():
        result = await run_web_research(
            query=QUERY,
            models=MODELS,
            search_backend="serper",  # free, no API key needed
        )

        print("\n" + "=" * 60)
        print("SYNTHESIS")
        print("=" * 60)
        print(result.synthesis)

        print("\n" + "=" * 60)
        print(f"SOURCES ({len(result.scraped)} scraped)")
        print("=" * 60)
        for s in result.scraped:
            print(f"  - {s.title or s.url}")
            print(f"    {s.url}")

        if result.scrape_failed:
            print(f"\nFailed to scrape {len(result.scrape_failed)} URL(s).")

        print("\n" + "=" * 60)
        print(f"QUERIES ({len(result.queries)} executed)")
        print("=" * 60)
        for q in result.queries:
            print(f"  - {q.query}")

    asyncio.run(_main())
