"""Benchmark: web-scout-ai vs OpenAI web search.

Runs the same queries through both tools and compares results side by side.

Usage:
    conda run -p /path/to/env python tests/benchmark.py

Requires:
    - OPENAI_API_KEY (for OpenAI web search)
    - At least one LLM provider key for web-scout-ai (GEMINI_API_KEY, etc.)
    - Optional: SERPER_API_KEY (for web-scout-ai Serper backend, otherwise uses DuckDuckGo)
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import litellm
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load API keys from .env in the repo root
load_dotenv(Path(__file__).parent.parent / ".env")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Queries to benchmark — edit or extend as needed
BENCHMARK_QUERIES = [
    # Specific data buried in technical reports/PDFs
    "What are the projected sea level rise impacts on Venice specifically, including flood frequency projections and MOSE barrier effectiveness under different IPCC scenarios?",
    # Requires reading actual legal/policy documents for specific provisions
    "What specific Total Allowable Catch quotas has ICCAT set for Eastern Atlantic and Mediterranean bluefin tuna for each year from 2022 to 2026, and what were the scientific basis and stock assessment results behind each decision?",
    # Cross-referencing multiple technical sources with specific quantitative data
    "What is the current deforestation rate in the Brazilian Cerrado biome, what are the main commodity drivers, and what specific enforcement actions has IBAMA taken in the last two years?",
]

# web-scout-ai models (adjust to your available provider)
WEB_SCOUT_MODELS = {
    "web_researcher": "gemini/gemini-3-flash-preview",
    "content_extractor": "gemini/gemini-3-flash-preview",
    "vision_fallback": "gemini/gemini-3-flash-preview",
}

# web-scout-ai search backend: "serper" (needs SERPER_API_KEY)
WEB_SCOUT_BACKEND = "serper"

# OpenAI model for web search comparison
OPENAI_MODEL = "gpt-5.4"

# LLM judge model (evaluates both outputs)
JUDGE_MODEL = "gpt-5.4"

# Output directory
OUTPUT_DIR = Path(__file__).parent / "benchmark_results"


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class Evaluation:
    url_relevance: int = 0                       # 1-5: how appropriate the URLs are for the query
    url_relevance_rationale: str = ""
    tailored_comprehensiveness: int = 0          # 1-5: how well each URL's content answers the query specifically
    tailored_comprehensiveness_rationale: str = ""
    synthesis_quality: int = 0                   # 1-5: how well the synthesis answers the query in detail
    synthesis_quality_rationale: str = ""

    @property
    def overall(self) -> float:
        return round((self.url_relevance + self.tailored_comprehensiveness + self.synthesis_quality) / 3, 1)


@dataclass
class ToolResult:
    tool: str
    query: str
    synthesis: str = ""
    sources: list = field(default_factory=list)
    num_sources: int = 0
    elapsed_seconds: float = 0.0
    error: Optional[str] = None
    evaluation: Optional[Evaluation] = None


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
        return ToolResult(
            tool="web-scout-ai",
            query=query,
            synthesis=result.synthesis,
            sources=sources,
            num_sources=len(sources),
            elapsed_seconds=round(elapsed, 1),
        )
    except Exception as e:
        return ToolResult(
            tool="web-scout-ai",
            query=query,
            elapsed_seconds=round(time.perf_counter() - t0, 1),
            error=str(e),
        )


# ---------------------------------------------------------------------------
# Runner: OpenAI with WebSearchTool (agents SDK)
# ---------------------------------------------------------------------------

OPENAI_SYSTEM_PROMPT = """\
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


class WebSearchSource(BaseModel):
    url: str = Field(description="Source URL")
    title: str = Field(default="", description="Source title")
    relevant_content: str = Field(
        default="",
        description=(
            "Comprehensive extraction of all content from this source relevant to the query. "
            "Include specific facts, numbers, dates, names, statistics, and detailed context. "
            "Up to 5000 characters."
        ),
    )


class OpenAIWebSearchOutput(BaseModel):
    """Structured output for the OpenAI web search agent."""
    synthesis: str = Field(description="Coherent synthesis answering the query with inline [Source](URL) citations.")
    sources: list[WebSearchSource] = Field(
        default_factory=list,
        description="List of sources used, each with URL, title, and comprehensive relevant content extracted.",
    )


async def run_openai_websearch(query: str) -> ToolResult:
    from agents import Agent, Runner, WebSearchTool, ModelSettings

    agent = Agent(
        name="openai_web_researcher",
        model=OPENAI_MODEL,
        tools=[WebSearchTool(search_context_size="high")],
        instructions=OPENAI_SYSTEM_PROMPT,
        model_settings=ModelSettings(parallel_tool_calls=False),
        output_type=OpenAIWebSearchOutput,
    )

    t0 = time.perf_counter()
    try:
        result = await Runner.run(agent, query)
        elapsed = time.perf_counter() - t0
        output = result.final_output_as(OpenAIWebSearchOutput)
        sources = [{"url": s.url, "title": s.title, "content": s.relevant_content} for s in output.sources]

        return ToolResult(
            tool=f"openai-websearch ({OPENAI_MODEL})",
            query=query,
            synthesis=output.synthesis,
            sources=sources,
            num_sources=len(sources),
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
# LLM-as-Judge evaluation
# ---------------------------------------------------------------------------

JUDGE_SYSTEM_PROMPT = """\
You are a STRICT, impartial evaluator of web research tool outputs. You must be hard to impress.
Your default assumption is that outputs are mediocre (score 2-3) unless you see clear evidence otherwise.
Scores of 4 or 5 must be earned with specific, verifiable evidence. Never give 5/5 unless the output
is genuinely exceptional with no meaningful gaps.

You will receive:
- The original research QUERY
- PER-SOURCE EXTRACTED CONTENT: the URL, title, and extracted content for each source
- FINAL SYNTHESIS: the tool's synthesized answer

Score the output on three dimensions (1-5 each):

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

Respond ONLY with valid JSON (no markdown fences):
{
  "url_relevance": <1-5>,
  "url_relevance_rationale": "<2-3 sentences citing specific evidence for your score>",
  "tailored_comprehensiveness": <1-5>,
  "tailored_comprehensiveness_rationale": "<2-3 sentences citing specific gaps or strengths>",
  "synthesis_quality": <1-5>,
  "synthesis_quality_rationale": "<2-3 sentences noting any unsourced claims or gaps>"
}
"""


async def evaluate_result(result: ToolResult) -> Evaluation:
    """Use an LLM judge to score a single tool result."""
    if result.error:
        return Evaluation(
            url_relevance=0, url_relevance_rationale="Tool errored.",
            tailored_comprehensiveness=0, tailored_comprehensiveness_rationale="Tool errored.",
            synthesis_quality=0, synthesis_quality_rationale="Tool errored.",
        )

    source_parts = []
    for i, s in enumerate(result.sources, 1):
        title = s.get('title', 'Untitled')
        url = s['url']
        content = s.get('content', '')
        source_parts.append(f"--- Source {i}: {title} ({url}) ---\n{content}")
    source_block = "\n\n".join(source_parts) if source_parts else "(no sources)"

    user_prompt = (
        f"QUERY: {result.query}\n\n"
        f"PER-SOURCE EXTRACTED CONTENT ({result.num_sources} sources):\n{source_block}\n\n"
        f"FINAL SYNTHESIS:\n{result.synthesis}"
    )

    try:
        response = await litellm.acompletion(
            model=JUDGE_MODEL,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        try:
            scores = json.loads(raw)
        except json.JSONDecodeError:
            # Rationale strings sometimes contain unescaped characters that break JSON.
            # Fall back to regex to extract the integer scores (they appear before the strings).
            import re
            scores = {}
            for key in ("url_relevance", "tailored_comprehensiveness", "synthesis_quality"):
                m = re.search(rf'"{key}"\s*:\s*([1-5])', raw)
                if m:
                    scores[key] = int(m.group(1))
            if not scores:
                raise
        print(f"  [judge] {result.tool}: relevance={scores['url_relevance']} comprehensiveness={scores['tailored_comprehensiveness']} synthesis={scores['synthesis_quality']}")
        return Evaluation(
            url_relevance=scores["url_relevance"],
            url_relevance_rationale=scores.get("url_relevance_rationale", ""),
            tailored_comprehensiveness=scores["tailored_comprehensiveness"],
            tailored_comprehensiveness_rationale=scores.get("tailored_comprehensiveness_rationale", ""),
            synthesis_quality=scores["synthesis_quality"],
            synthesis_quality_rationale=scores.get("synthesis_quality_rationale", ""),
        )
    except Exception as e:
        print(f"  [judge] evaluation failed for {result.tool}: {e}")
        return Evaluation()


# ---------------------------------------------------------------------------
# Comparison report
# ---------------------------------------------------------------------------

def build_markdown_report(results: list[ToolResult], queries: list[str]) -> str:
    lines = [
        "# Benchmark: web-scout-ai vs OpenAI web search",
        f"\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"\nweb-scout-ai backend: {WEB_SCOUT_BACKEND}",
        f"web-scout-ai models: {WEB_SCOUT_MODELS}",
        f"OpenAI model: {OPENAI_MODEL}",
        "",
    ]

    # Summary table
    lines.append("## Summary\n")
    lines.append("| Query | Tool | Sources | Time (s) | Ans. Len | URL Relevance | Tailored Compreh. | Synthesis | Overall |")
    lines.append("|-------|------|---------|----------|----------|---------------|-------------------|-----------|---------|")

    by_query = {}
    for r in results:
        by_query.setdefault(r.query, []).append(r)

    for query in queries:
        for r in by_query.get(query, []):
            q_short = query[:50] + "..." if len(query) > 50 else query
            if r.error:
                lines.append(f"| {q_short} | {r.tool} | - | {r.elapsed_seconds} | ERROR | - | - | - | - |")
            else:
                ev = r.evaluation or Evaluation()
                lines.append(
                    f"| {q_short} | {r.tool} | {r.num_sources} | {r.elapsed_seconds} "
                    f"| {len(r.synthesis)} | {ev.url_relevance}/5 | {ev.tailored_comprehensiveness}/5 "
                    f"| {ev.synthesis_quality}/5 | {ev.overall}/5 |"
                )

    # Detailed results
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
            lines.append(f"- **Time:** {r.elapsed_seconds}s")
            lines.append(f"- **Sources:** {r.num_sources}")
            lines.append(f"- **Scores:** URL Relevance {ev.url_relevance}/5 | Tailored Comprehensiveness {ev.tailored_comprehensiveness}/5 | Synthesis {ev.synthesis_quality}/5 | **Overall {ev.overall}/5**")
            lines.append(f"- **URL Relevance:** {ev.url_relevance_rationale}")
            lines.append(f"- **Tailored Comprehensiveness:** {ev.tailored_comprehensiveness_rationale}")
            lines.append(f"- **Synthesis Quality:** {ev.synthesis_quality_rationale}")
            if r.sources:
                lines.append("- **Source list:**")
                for s in r.sources:
                    title = s.get("title", "")
                    lines.append(f"  - [{title}]({s['url']})" if title else f"  - {s['url']}")
            lines.append(f"\n**Synthesis:**\n\n{r.synthesis}\n")
        lines.append("---\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print(f"Running benchmark with {len(BENCHMARK_QUERIES)} queries...")
    print(f"  web-scout-ai backend: {WEB_SCOUT_BACKEND}")
    print(f"  OpenAI model: {OPENAI_MODEL}")
    print()

    all_results: list[ToolResult] = []

    for i, query in enumerate(BENCHMARK_QUERIES, 1):
        print(f"[{i}/{len(BENCHMARK_QUERIES)}] {query}")

        # Run both tools in parallel
        ws_task = asyncio.create_task(run_web_scout(query))
        oai_task = asyncio.create_task(run_openai_websearch(query))

        ws_result, oai_result = await asyncio.gather(ws_task, oai_task)

        print(f"  web-scout-ai:     {ws_result.elapsed_seconds}s, {ws_result.num_sources} sources"
              + (f" ERROR: {ws_result.error}" if ws_result.error else ""))
        print(f"  openai-websearch: {oai_result.elapsed_seconds}s, {oai_result.num_sources} sources"
              + (f" ERROR: {oai_result.error}" if oai_result.error else ""))

        # Evaluate both results with LLM judge
        print(f"  Evaluating with {JUDGE_MODEL}...")
        ws_eval, oai_eval = await asyncio.gather(
            evaluate_result(ws_result),
            evaluate_result(oai_result),
        )
        ws_result.evaluation = ws_eval
        oai_result.evaluation = oai_eval

        print(f"  web-scout-ai:     relevance={ws_eval.url_relevance}/5  comprehensiveness={ws_eval.tailored_comprehensiveness}/5  synthesis={ws_eval.synthesis_quality}/5  overall={ws_eval.overall}/5")
        print(f"  openai-websearch: relevance={oai_eval.url_relevance}/5  comprehensiveness={oai_eval.tailored_comprehensiveness}/5  synthesis={oai_eval.synthesis_quality}/5  overall={oai_eval.overall}/5")
        print()

        all_results.extend([ws_result, oai_result])

    # Save results
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON (machine-readable)
    json_path = OUTPUT_DIR / f"benchmark_{timestamp}.json"
    serializable = []
    for r in all_results:
        d = asdict(r)
        if r.evaluation:
            d["evaluation"]["overall"] = r.evaluation.overall
        serializable.append(d)
    with open(json_path, "w") as f:
        json.dump(serializable, f, indent=2)

    # Markdown (human-readable)
    md_path = OUTPUT_DIR / f"benchmark_{timestamp}.md"
    report = build_markdown_report(all_results, BENCHMARK_QUERIES)
    with open(md_path, "w") as f:
        f.write(report)

    print(f"Results saved to:")
    print(f"  {json_path}")
    print(f"  {md_path}")

    # Print summary table to terminal
    print()
    print(report.split("## Detailed Results")[0])


if __name__ == "__main__":
    asyncio.run(main())
