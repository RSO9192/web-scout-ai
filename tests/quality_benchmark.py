"""Quality benchmark: web-scout-ai vs OpenAI web search.

Runs a mixed query set (FAO/ESS-domain + general deep-research) through both
tools and produces a rich diagnostic report with LLM-as-judge scoring.

Usage:
    conda run -p /path/to/env python tests/quality_benchmark.py
    conda run -p /path/to/env python tests/quality_benchmark.py --limit 2

Saves results to tests/benchmark_results/quality_benchmark_YYYYMMDD_HHMMSS.{json,md}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import litellm
from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic import Field as PydanticField

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ENV_FILE = Path("/Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/report/.env")
OUTPUT_DIR = Path(__file__).parent / "benchmark_results"
CONTENT_PREVIEW_CHARS = 250

BENCHMARK_QUERIES = [
    # FAO/ESS-domain
    "Kenya interannual variability and long-term trends in precipitation — current status and recent trend",
    "Global food insecurity trends 2022–2024 — FAO State of Food Security and Nutrition report key findings",
    "Ethiopia crop production statistics 2023 — cereals area harvested and yield data",
    "FAOSTAT deforestation and forest area change Sub-Saharan Africa 2000–2023",
    # General deep-research
    (
        "What are the projected sea level rise impacts on Venice specifically, "
        "including flood frequency projections and MOSE barrier effectiveness "
        "under different IPCC scenarios?"
    ),
    (
        "What specific Total Allowable Catch quotas has ICCAT set for Eastern "
        "Atlantic and Mediterranean bluefin tuna 2022–2026?"
    ),
    (
        "What is the current deforestation rate in the Brazilian Cerrado, "
        "main commodity drivers, and specific IBAMA enforcement actions in "
        "the last two years?"
    ),
    (
        "Latest IPCC AR6 findings on food system vulnerability to climate "
        "change — specific regional projections and adaptation options"
    ),
]

OPENAI_MODEL = "gpt-5.4-mini"
JUDGE_MODEL = "gpt-5.4-mini"
WEB_SCOUT_BACKEND = "serper"

# Default models from web_scout.agent.DEFAULT_WEB_RESEARCH_MODELS
WEB_SCOUT_MODELS = {
    "web_researcher": "gemini/gemini-3-flash-preview",
    "content_extractor": "gemini/gemini-3-flash-preview",
    "vision_fallback": "gemini/gemini-3-flash-preview",
    "followup_selector": "gemini/gemini-3-flash-preview",
}

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class FailureEntry:
    url: str
    error: str
    category: str  # scrape_failed | bot_detected | source_http_error | blocked_by_policy | scraped_irrelevant


@dataclass
class Evaluation:
    url_relevance: int = 0
    url_relevance_rationale: str = ""
    tailored_comprehensiveness: int = 0
    tailored_comprehensiveness_rationale: str = ""
    synthesis_quality: int = 0
    synthesis_quality_rationale: str = ""
    extraction_coverage: int = 0
    extraction_coverage_rationale: str = ""

    @property
    def overall(self) -> float:
        total = (
            self.url_relevance
            + self.tailored_comprehensiveness
            + self.synthesis_quality
            + self.extraction_coverage
        )
        return round(total / 4, 1)


@dataclass
class ToolResult:
    tool: str
    query: str
    synthesis: str = ""
    sources: list = field(default_factory=list)  # [{"url": str, "title": str, "content": str}]
    num_scraped: int = 0
    failures: list = field(default_factory=list)  # list[FailureEntry]
    elapsed_seconds: float = 0.0
    error: Optional[str] = None
    evaluation: Optional[Evaluation] = None
    search_queries: list = field(default_factory=list)  # list[str]

# ---------------------------------------------------------------------------
# Report helpers (pure functions — no API calls)
# ---------------------------------------------------------------------------

def build_failure_table(failures: list) -> str:
    """Return a Markdown table of failed URLs, or empty string if none."""
    if not failures:
        return ""
    lines = [
        "| Category | URL | Error |",
        "|----------|-----|-------|",
    ]
    for f in failures:
        error = f.error.replace("|", "\\|").replace("\n", " ")[:200]
        url = f.url.replace("|", "\\|")
        lines.append(f"| {f.category} | {url} | {error} |")
    return "\n".join(lines)


def build_source_previews(sources: list, preview_chars: int = CONTENT_PREVIEW_CHARS) -> str:
    """Return a bulleted list of source titles with content previews."""
    if not sources:
        return ""
    lines = []
    for s in sources:
        title = s.get("title", "") or "Untitled"
        url = s.get("url", "")
        content = s.get("content", "")
        preview = " ".join(content.split())[:preview_chars]
        if len(content) > preview_chars:
            preview += "…"
        lines.append(f"- **[{title}]({url})**  \n  {preview}")
    return "\n".join(lines)


def avg_content_depth(sources: list) -> int:
    """Return average character count of extracted content across scraped sources."""
    if not sources:
        return 0
    return round(sum(len(s.get("content", "")) for s in sources) / len(sources))


def build_summary_row(result: "ToolResult") -> str:
    """Return a single Markdown table row for the summary table."""
    q_short = result.query[:55] + "…" if len(result.query) > 55 else result.query
    if result.error:
        return (
            f"| {q_short} | {result.tool} | - | - | - | - | {result.elapsed_seconds} | ERROR | - | - | - | - |"
        )
    ev = result.evaluation or Evaluation()
    num_failed = len([f for f in result.failures if f.category in ("scrape_failed", "source_http_error")])
    num_bot = len([f for f in result.failures if f.category == "bot_detected"])
    total_attempted = result.num_scraped + len(result.failures)
    scrape_rate = f"{result.num_scraped}/{total_attempted}" if total_attempted else "-"
    depth = avg_content_depth(result.sources)
    depth_str = f"{depth:,}" if depth else "-"
    return (
        f"| {q_short} | {result.tool} | {scrape_rate} | {num_failed} | {num_bot} "
        f"| {depth_str} | {result.elapsed_seconds} | {ev.url_relevance}/5 | {ev.tailored_comprehensiveness}/5 "
        f"| {ev.synthesis_quality}/5 | {ev.extraction_coverage}/5 | {ev.overall}/5 |"
    )

# ---------------------------------------------------------------------------
# Runner: web-scout-ai
# ---------------------------------------------------------------------------

async def run_web_scout(query: str) -> ToolResult:
    from web_scout import run_web_research

    t0 = time.perf_counter()
    try:
        result = await run_web_research(
            query=query,
            models=WEB_SCOUT_MODELS,
            search_backend=WEB_SCOUT_BACKEND,
        )
        elapsed = time.perf_counter() - t0

        sources = [
            {"url": s.url, "title": s.title, "content": s.content}
            for s in result.scraped
        ]

        failures: list[FailureEntry] = []
        for category, entries in [
            ("scrape_failed", result.scrape_failed),
            ("bot_detected", result.bot_detected),
            ("source_http_error", result.source_http_error),
            ("blocked_by_policy", result.blocked_by_policy),
            ("scraped_irrelevant", result.scraped_irrelevant),
        ]:
            for entry in entries:
                failures.append(
                    FailureEntry(
                        url=entry.url,
                        error=(entry.content or "")[:200],
                        category=category,
                    )
                )

        return ToolResult(
            tool="web-scout-ai",
            query=query,
            synthesis=result.synthesis,
            sources=sources,
            num_scraped=len(sources),
            failures=failures,
            elapsed_seconds=round(elapsed, 1),
            search_queries=[q.query for q in result.queries],
        )
    except Exception as e:
        return ToolResult(
            tool="web-scout-ai",
            query=query,
            elapsed_seconds=round(time.perf_counter() - t0, 1),
            error=str(e),
        )

# ---------------------------------------------------------------------------
# Runner: OpenAI web search
# ---------------------------------------------------------------------------

_OPENAI_SYSTEM_PROMPT = """\
You are an expert web researcher. Use the web search tool to find current, \
authoritative information to answer the user's query.

Rules:
- Search the web thoroughly — use multiple searches if needed to cover the topic.
- For each source you use, extract ALL specific facts, numbers, dates, names, and detailed \
  context relevant to the query. Do NOT just note what the page is about — extract the actual data.
- Provide a coherent, well-structured synthesis of your findings.
- Use inline markdown citations after each claim: [Source Title](URL).
  Every factual statement must be attributed to at least one source.
- If there are contradictions or data gaps across sources, note them.
- Do NOT fabricate information. ONLY report what you found in the search results.
  Do NOT add facts, numbers, or claims from your own training data.
  If the search results do not contain specific information, state that it was not found.
- In the sources list, include the URL, title, and a comprehensive extraction of the \
  relevant content from that source (up to 5000 characters per source).
"""


class _WebSearchSource(BaseModel):
    url: str = PydanticField(description="Source URL")
    title: str = PydanticField(default="", description="Source title")
    relevant_content: str = PydanticField(
        default="",
        description=(
            "Comprehensive extraction of all content from this source relevant to the query. "
            "Include specific facts, numbers, dates, names, statistics, and detailed context. "
            "Up to 5000 characters."
        ),
    )


class _OpenAIWebSearchOutput(BaseModel):
    synthesis: str = PydanticField(
        description="Coherent synthesis answering the query with inline [Source](URL) citations."
    )
    sources: list[_WebSearchSource] = PydanticField(default_factory=list)


async def run_openai_websearch(query: str) -> ToolResult:
    from agents import Agent, ModelSettings, Runner, WebSearchTool

    agent = Agent(
        name="openai_web_researcher",
        model=OPENAI_MODEL,
        tools=[WebSearchTool(search_context_size="high")],
        instructions=_OPENAI_SYSTEM_PROMPT,
        model_settings=ModelSettings(parallel_tool_calls=False),
        output_type=_OpenAIWebSearchOutput,
    )

    t0 = time.perf_counter()
    try:
        result = await Runner.run(agent, query)
        elapsed = time.perf_counter() - t0
        output = result.final_output_as(_OpenAIWebSearchOutput)
        sources = [
            {"url": s.url, "title": s.title, "content": s.relevant_content}
            for s in output.sources
        ]
        return ToolResult(
            tool=f"openai-websearch ({OPENAI_MODEL})",
            query=query,
            synthesis=output.synthesis,
            sources=sources,
            num_scraped=len(sources),
            elapsed_seconds=round(elapsed, 1),
        )
    except Exception as e:
        return ToolResult(
            tool=f"openai-websearch ({OPENAI_MODEL})",
            query=query,
            elapsed_seconds=round(time.perf_counter() - t0, 1),
            error=str(e),
        )

# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM_PROMPT = """\
You are a STRICT, impartial evaluator of web research tool outputs. You must be hard to impress.
Your default assumption is that outputs are mediocre (score 2-3) unless you see clear evidence otherwise.
Scores of 4 or 5 must be earned with specific, verifiable evidence. Never give 5/5 unless the output
is genuinely exceptional with no meaningful gaps.

You will receive:
- The original research QUERY
- PER-SOURCE EXTRACTED CONTENT: the URL, title, and extracted content for each source
- FINAL SYNTHESIS: the tool's synthesized answer

Score the output on FOUR dimensions (1-5 each):

1. **URL Relevance** — Are the found URLs the right primary sources for this specific query?
   Judge whether these URLs would plausibly contain the exact information requested (specific
   data, reports, decisions) — not just whether the site is broadly related to the topic.
   1 = URLs unrelated to the query topic
   2 = URLs about the general topic but unlikely to contain the specific requested data
   3 = Mix: some are genuinely the right source, others are tangential or secondary
   4 = Most URLs are primary, authoritative sources directly relevant to the specific request
   5 = Every URL is a primary, authoritative source for exactly the information requested

2. **Tailored Comprehensiveness** — Does each URL's extracted content actually contain the
   specific facts, numbers, decisions, or data the query asks for?
   Look at what was actually EXTRACTED from each source. Generic topic coverage does NOT count.
   The query demands specifics — if those specifics are absent from the extracts, score low.
   1 = Extracts are empty, generic, or entirely off-topic for the query's specifics
   2 = Extracts discuss the topic but lack the specific facts/numbers/data the query requires
   3 = Some sources have relevant specifics but most extracts are surface-level
   4 = Most sources yield concrete query-specific data (exact numbers, dates, decisions)
   5 = All sources yield precise, query-specific data with no notable gaps
   IMPORTANT: A longer extract is NOT better if it lacks the specific requested data.

3. **Synthesis Quality** — Does the synthesis directly and accurately answer the query using
   ONLY information from the provided extracted content?
   CRITICAL RULE: Before scoring, scan the synthesis for specific facts, numbers, and claims,
   then check whether each one appears in the per-source extracted content above. Any specific
   claim in the synthesis that is NOT present in the extracted content must be flagged as
   training-data fill or hallucination and penalized. A synthesis that is significantly longer
   than what the extracted content would support is a strong RED FLAG for training-data padding.
   1 = Misses the query or is mostly fabricated
   2 = Partially answers but contains multiple unsourced specific claims or major gaps
   3 = Mostly answers from sources but has notable unsourced facts or unnecessary padding
   4 = Directly answers using extracted content, only minor gaps or borderline claims
   5 = Fully answers every aspect of the query; every specific fact is traceable to the
       extracted content; no padding or training-data fill; tight attribution throughout

4. **Extraction Coverage** — How much raw evidence was actually gathered: number of sources
   AND depth of content extracted from each?
   This dimension rewards breadth (many sources) AND depth (substantial content per source).
   Count the sources provided and inspect how long and specific each extract is.
   1 = 0-1 sources, or all extracts are very thin (under 200 chars) / generic metadata
   2 = 2-3 sources with mostly shallow extracts, or 1-2 sources with moderate depth
   3 = 3-5 sources with moderate per-source content, or 2-3 sources with deep extracts
   4 = 5+ sources with substantial per-source content (1,000+ chars of specific material each)
   5 = 6+ sources, most with deep and specific extracts (2,000+ chars of query-relevant material)
   NOTE: This dimension evaluates the raw gathering — not whether the content answers the query
   (that is covered by dimensions 1 and 2). A tool that reads more pages and extracts more text
   from each page scores higher here even if the synthesis later falls short.

Respond ONLY with valid JSON (no markdown fences):
{
  "url_relevance": <1-5>,
  "url_relevance_rationale": "<2-3 sentences citing specific evidence for your score>",
  "tailored_comprehensiveness": <1-5>,
  "tailored_comprehensiveness_rationale": "<2-3 sentences citing specific gaps or strengths>",
  "synthesis_quality": <1-5>,
  "synthesis_quality_rationale": "<2-3 sentences noting any unsourced claims or gaps>",
  "extraction_coverage": <1-5>,
  "extraction_coverage_rationale": "<2-3 sentences on source count and per-source content depth>"
}
"""


async def evaluate_result(result: ToolResult) -> Evaluation:
    """Score a single tool result with the LLM judge."""
    if result.error:
        return Evaluation(
            url_relevance=0, url_relevance_rationale="Tool errored.",
            tailored_comprehensiveness=0, tailored_comprehensiveness_rationale="Tool errored.",
            synthesis_quality=0, synthesis_quality_rationale="Tool errored.",
        )

    source_parts = []
    for i, s in enumerate(result.sources, 1):
        title = s.get("title", "Untitled")
        url = s["url"]
        content = s.get("content", "")
        source_parts.append(f"--- Source {i}: {title} ({url}) ---\n{content}")
    source_block = "\n\n".join(source_parts) if source_parts else "(no sources)"

    user_prompt = (
        f"QUERY: {result.query}\n\n"
        f"PER-SOURCE EXTRACTED CONTENT ({result.num_scraped} sources):\n{source_block}\n\n"
        f"FINAL SYNTHESIS:\n{result.synthesis}"
    )

    try:
        response = await litellm.acompletion(
            model=JUDGE_MODEL,
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        try:
            scores = json.loads(raw)
        except json.JSONDecodeError:
            scores = {}
            for key in ("url_relevance", "tailored_comprehensiveness", "synthesis_quality", "extraction_coverage"):
                m = re.search(rf'"{key}"\s*:\s*([1-5])', raw)
                if m:
                    scores[key] = int(m.group(1))
            if not scores:
                raise
        print(
            f"  [judge] {result.tool}: "
            f"relevance={scores['url_relevance']} "
            f"comprehensiveness={scores['tailored_comprehensiveness']} "
            f"synthesis={scores['synthesis_quality']} "
            f"coverage={scores.get('extraction_coverage', '?')}"
        )
        return Evaluation(
            url_relevance=scores["url_relevance"],
            url_relevance_rationale=scores.get("url_relevance_rationale", ""),
            tailored_comprehensiveness=scores["tailored_comprehensiveness"],
            tailored_comprehensiveness_rationale=scores.get("tailored_comprehensiveness_rationale", ""),
            synthesis_quality=scores["synthesis_quality"],
            synthesis_quality_rationale=scores.get("synthesis_quality_rationale", ""),
            extraction_coverage=scores.get("extraction_coverage", 0),
            extraction_coverage_rationale=scores.get("extraction_coverage_rationale", ""),
        )
    except Exception as e:
        print(f"  [judge] evaluation failed for {result.tool}: {e}")
        return Evaluation()

# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def build_report(results: list, queries: list) -> str:
    lines = [
        "# Quality Benchmark: web-scout-ai vs OpenAI web search",
        f"\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"web-scout-ai backend: {WEB_SCOUT_BACKEND}",
        f"OpenAI model: {OPENAI_MODEL}",
        f"Judge model: {JUDGE_MODEL}",
        "",
    ]

    # Summary table
    lines.append("## Summary\n")
    lines.append(
        "| Query | Tool | Scraped | Failed | Bot | Avg Depth | Time (s) "
        "| URL Rel | Compreh | Synthesis | Coverage | Overall |"
    )
    lines.append("|-------|------|---------|--------|-----|-----------|----------|---------|---------|-----------|----------|---------|")

    by_query: dict = {}
    for r in results:
        by_query.setdefault(r.query, []).append(r)

    for query in queries:
        for r in by_query.get(query, []):
            lines.append(build_summary_row(r))

    lines.append("\n---\n")
    lines.append("## Detailed Results\n")

    for i, query in enumerate(queries, 1):
        lines.append(f"### Query {i}: {query}\n")

        for r in by_query.get(query, []):
            lines.append(f"#### {r.tool}\n")

            if r.error:
                lines.append(f"**ERROR:** {r.error}\n")
                continue

            ev = r.evaluation or Evaluation()

            # web-scout-ai gets the full diagnostic breakdown
            if r.tool == "web-scout-ai":
                num_scrape_failed = len([f for f in r.failures if f.category == "scrape_failed"])
                num_http_error = len([f for f in r.failures if f.category == "source_http_error"])
                num_bot = len([f for f in r.failures if f.category == "bot_detected"])
                num_blocked = len([f for f in r.failures if f.category == "blocked_by_policy"])
                num_irrelevant = len([f for f in r.failures if f.category == "scraped_irrelevant"])
                total_attempted = r.num_scraped + len(r.failures)
                depth = avg_content_depth(r.sources)
                lines.append(
                    f"**Scrape breakdown:** {r.num_scraped}/{total_attempted} scraped "
                    f"({num_scrape_failed} failed / {num_bot} bot-blocked / {num_http_error} http-error / "
                    f"{num_blocked} policy-blocked / {num_irrelevant} irrelevant) — "
                    f"avg content depth: **{depth:,} chars/source**\n"
                )

                if r.search_queries:
                    lines.append("**Search queries issued:**")
                    for q in r.search_queries:
                        lines.append(f"- {q}")
                    lines.append("")

                if r.failures:
                    lines.append("**Failed URLs:**\n")
                    lines.append(build_failure_table(r.failures))
                    lines.append("")

                if r.sources:
                    lines.append("**Source content previews:**\n")
                    lines.append(build_source_previews(r.sources, CONTENT_PREVIEW_CHARS))
                    lines.append("")

            # Scores and rationales (both tools)
            lines.append(
                f"**Scores:** URL Relevance {ev.url_relevance}/5 | "
                f"Tailored Comprehensiveness {ev.tailored_comprehensiveness}/5 | "
                f"Synthesis {ev.synthesis_quality}/5 | "
                f"Extraction Coverage {ev.extraction_coverage}/5 | **Overall {ev.overall}/5**\n"
            )
            if ev.url_relevance_rationale:
                lines.append(f"- **URL Relevance:** {ev.url_relevance_rationale}")
            if ev.tailored_comprehensiveness_rationale:
                lines.append(f"- **Tailored Comprehensiveness:** {ev.tailored_comprehensiveness_rationale}")
            if ev.synthesis_quality_rationale:
                lines.append(f"- **Synthesis Quality:** {ev.synthesis_quality_rationale}")
            if ev.extraction_coverage_rationale:
                lines.append(f"- **Extraction Coverage:** {ev.extraction_coverage_rationale}")
            lines.append("")

            lines.append(f"**Synthesis:**\n\n{r.synthesis}\n")

        lines.append("---\n")

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(description="Quality benchmark: web-scout-ai vs OpenAI web search.")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of queries; 0 = all.")
    parser.add_argument("--env-file", default=str(ENV_FILE), help="Path to dotenv file with API keys.")
    args = parser.parse_args()

    load_dotenv(args.env_file, override=False)

    queries = BENCHMARK_QUERIES[: args.limit] if args.limit else BENCHMARK_QUERIES
    print(f"Running quality benchmark: {len(queries)} queries")
    print(f"  web-scout-ai backend: {WEB_SCOUT_BACKEND}")
    print(f"  OpenAI model: {OPENAI_MODEL}")
    print(f"  Judge model: {JUDGE_MODEL}")
    print()

    all_results: list[ToolResult] = []

    for idx, query in enumerate(queries, 1):
        print(f"[{idx}/{len(queries)}] {query[:80]}")

        ws_result, oai_result = await asyncio.gather(
            run_web_scout(query),
            run_openai_websearch(query),
        )

        print(
            f"  web-scout-ai:     {ws_result.elapsed_seconds}s, "
            f"{ws_result.num_scraped} scraped, {len(ws_result.failures)} failures"
            + (f" ERROR: {ws_result.error}" if ws_result.error else "")
        )
        print(
            f"  openai-websearch: {oai_result.elapsed_seconds}s, {oai_result.num_scraped} sources"
            + (f" ERROR: {oai_result.error}" if oai_result.error else "")
        )

        print(f"  Evaluating with {JUDGE_MODEL}...")
        ws_eval, oai_eval = await asyncio.gather(
            evaluate_result(ws_result),
            evaluate_result(oai_result),
        )
        ws_result.evaluation = ws_eval
        oai_result.evaluation = oai_eval

        print(
            f"  web-scout-ai:     relevance={ws_eval.url_relevance}/5 "
            f"comprehensiveness={ws_eval.tailored_comprehensiveness}/5 "
            f"synthesis={ws_eval.synthesis_quality}/5 overall={ws_eval.overall}/5"
        )
        print(
            f"  openai-websearch: relevance={oai_eval.url_relevance}/5 "
            f"comprehensiveness={oai_eval.tailored_comprehensiveness}/5 "
            f"synthesis={oai_eval.synthesis_quality}/5 overall={oai_eval.overall}/5"
        )
        print()

        all_results.extend([ws_result, oai_result])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON
    json_path = OUTPUT_DIR / f"quality_benchmark_{timestamp}.json"
    serializable = []
    for r in all_results:
        d = asdict(r)
        if r.evaluation:
            d["evaluation"]["overall"] = r.evaluation.overall
        serializable.append(d)
    json_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")

    # Markdown
    md_path = OUTPUT_DIR / f"quality_benchmark_{timestamp}.md"
    report = build_report(all_results, queries)
    md_path.write_text(report, encoding="utf-8")

    print("Results saved to:")
    print(f"  {json_path}")
    print(f"  {md_path}")
    print()
    # Print summary section to terminal
    print(report.split("## Detailed Results")[0])


if __name__ == "__main__":
    asyncio.run(main())
