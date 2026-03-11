"""Web researcher deterministic pipeline.

Uses pluggable search backends (Serper / DuckDuckGo). A dedicated
content extractor sub-agent (crawl4ai / docling) scrapes and summarises
each URL so the main researcher only sees focused excerpts.

Three input modes — all share a single linear pipeline:

1. **Query-only** — open web search -> triage -> parallel scrape -> synthesis.
2. **Domain + query** — domain-restricted search -> parallel scrape -> optional deep scrape -> synthesis.
3. **Direct URL** — skip search, extract a given URL directly -> optional deep scrape -> synthesis.

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
    "vision_fallback": "gemini/gemini-2.0-flash",
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
Be strict: if key specific data (like species names, numbers, thresholds) is requested but missing, it is not fully answered.\
"""

SYNTHESISER_INSTRUCTIONS = """\
You are a web research synthesiser. Your job is to read the extracted contents
from various web pages and produce a coherent narrative `synthesis` answering the query.

Your output must address the query directly, mention any data gaps, and do not 
fabricate information. If there are issues, caveats, or biases with the search 
results that the user should know about (e.g., contradictory sources, outdated data), 
mention them clearly in your synthesis.
"""


class SearchQueryGeneration(BaseModel):
    """LLM output for generating diverse search queries."""
    queries: List[str] = Field(description="List of search queries")

class CoverageEvaluation(BaseModel):
    """LLM output for evaluating if the scraped content answers the query."""
    fully_answered: bool = Field(description="True if the extracted content fully answers the original research query.")
    gaps: str = Field(description="If not fully answered, what specific information is still missing?")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

async def run_web_research(
    query: str,
    models: Dict[str, str],
    include_domains: Optional[List[str]] = None,
    direct_url: Optional[str] = None,
    search_backend: str = "serper",
    domain_expertise: Optional[str] = None,
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

    Returns:
        ``WebResearchResult`` with URLs grouped by action (scraped,
        scrape_failed, snippet_only) and query metadata.
    """
    from .utils import get_model
    from .search_backends import DuckDuckGoBackend, SerperBackend

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

    logger.info("[pipeline] start  query=%r  backend=%s mode=%s", 
                query[:80], search_backend, 
                "direct" if direct_url else "domain" if include_domains else "open")

    if direct_url:
        # --- 1. DIRECT URL MODE ---
        logger.info("[pipeline] scraping direct URL: %s", direct_url)
        # Force a record of the query (since we didn't search) to keep logs clean
        # but in direct mode we don't have search results
        content = await scrape_tool(direct_url)
        
        # Deepen if relevant links are found
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

        MAX_ITERATIONS = 2
        for iteration in range(MAX_ITERATIONS):
            logger.info("[pipeline] starting search iteration %d", iteration + 1)

            if iteration == 0:
                prompt = f"Research Query: {query}\nGenerate exactly 3 distinct search queries.\n"
                if include_domains:
                    prompt += f"Note: We will search exclusively within these domains: {', '.join(include_domains)}\n"
            else:
                prompt = f"Research Query: {query}\n"
                prompt += f"We have already scraped some content, but we are missing: {missing_info}\n"
                prompt += "Generate exactly 2 new distinct search queries specifically targeting this missing information.\n"
                if include_domains:
                    prompt += f"Note: We will search exclusively within these domains: {', '.join(include_domains)}\n"

            gen_res = await Runner.run(query_gen_agent, prompt, max_turns=1)
            search_queries = gen_res.final_output_as(SearchQueryGeneration).queries
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
            max_urls_to_scrape = 6 if iteration == 0 else 4
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
            if iteration < MAX_ITERATIONS - 1:
                scraped_entries = tracker.build_result_groups()["scraped"]
                if not scraped_entries:
                    logger.info("[pipeline] 0 successful scrapes, doing another iteration")
                    missing_info = "Everything, no successful scrapes yet."
                    continue

                eval_prompt = f"Research Query: {query}\n\nScraped Content:\n"
                for i, entry in enumerate(scraped_entries, 1):
                    eval_prompt += f"--- Source {i}: {entry.title or entry.url} ---\n{entry.content}\n\n"
                    
                eval_res = await Runner.run(evaluator_agent, eval_prompt, max_turns=1)
                evaluation = eval_res.final_output_as(CoverageEvaluation)
                
                logger.info("[pipeline] coverage evaluation: fully_answered=%s, gaps=%r", 
                            evaluation.fully_answered, evaluation.gaps)
                
                if evaluation.fully_answered:
                    logger.info("[pipeline] query fully answered, proceeding to synthesis")
                    break
                else:
                    missing_info = evaluation.gaps

    # --- 3. SYNTHESIZE ---
    groups = tracker.build_result_groups()
    scraped = groups["scraped"]
    scrape_failed = groups["scrape_failed"]
    snippet_only = groups["snippet_only"]
    
    synth_prompt = f"Research Query: {query}\n\n"
    if domain_expertise:
        synth_prompt += f"Domain Expertise: {domain_expertise}\n\n"
        
    synth_prompt += "Here are the sources we successfully scraped and extracted:\n\n"
    if not scraped:
        synth_prompt += "(No sources were successfully scraped. You must state that no evidence was found.)\n"
    else:
        for i, entry in enumerate(scraped, 1):
            synth_prompt += f"--- Source {i}: {entry.title or entry.url} ---\n{entry.content}\n\n"
            
    synth_prompt += "\n\nProvide the 'synthesis' of the findings directly answering the query.\n"
            
    synth_agent = Agent(
        name="synthesiser",
        model=synth_model,
        output_type=WebResearchResultRaw,
        instructions=SYNTHESISER_INSTRUCTIONS,
        model_settings=ModelSettings(extra_args={"reasoning_effort": "high"}),
    )
    
    synth_res = await Runner.run(synth_agent, synth_prompt, max_turns=1)
    output = synth_res.final_output_as(WebResearchResultRaw)

    logger.info(
        "[pipeline] done scraped=%d failed=%d snippet_only=%d queries=%d",
        len(scraped), len(scrape_failed), len(snippet_only), len(tracker.queries)
    )
    
    return WebResearchResult(
        scraped=scraped,
        scrape_failed=scrape_failed,
        snippet_only=snippet_only,
        queries=tracker.queries,
        synthesis=output.synthesis,
    )
