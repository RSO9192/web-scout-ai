"""End-to-end quality probe for run_web_research across three input modes.

Runs three research tasks (open-web, domain-restricted, direct-URL), scores each
synthesis on five objective criteria, and prints a report.

Usage:
    PYTHONPATH=src python tests/quality_probe.py

Requires: SERPER_API_KEY and GEMINI_API_KEY in environment.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Optional

from web_scout.agent import run_web_research, WebResearchResult, DEFAULT_WEB_RESEARCH_MODELS

MODELS = DEFAULT_WEB_RESEARCH_MODELS

TASKS = [
    {
        "mode":  "open-web",
        "label": "Global fish capture production trends 2022",
        "query": "global fish capture production trends 2022 statistics",
        "kwargs": {},
    },
    {
        "mode":  "domain-restricted",
        "label": "FAO aquaculture production by region",
        "query": "aquaculture production by region 2022",
        "kwargs": {"include_domains": ["fao.org"]},
    },
    {
        "mode":  "direct-url",
        "label": "FAO fishery overview page",
        "query": "fisheries and aquaculture global production overview",
        "kwargs": {"direct_url": "https://www.fao.org/fishery/en"},
    },
]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class QualityScore:
    mode: str
    label: str
    elapsed_s: float
    n_scraped: int
    n_failed: int
    n_snippet_only: int
    synthesis_chars: int
    citation_count: int
    data_density: float   # numbers/stats per 500 chars
    gap_reported: bool    # synthesis explicitly reports a data gap
    score_sources: float  # 0–2
    score_length: float   # 0–2
    score_citations: float  # 0–2
    score_data: float     # 0–2
    score_gap: float      # 0–2
    total: float          # 0–10


def _score(result: WebResearchResult, mode: str, label: str, elapsed: float) -> QualityScore:
    synthesis = result.synthesis or ""
    n_scraped = len(result.scraped)
    n_failed = len(result.scrape_failed) + len(result.source_http_error) + len(result.bot_detected)
    n_snippet = len(result.snippet_only)

    # Citation count: markdown links [text](url)
    citations = re.findall(r'\[[^\]]+\]\(https?://[^\)]+\)', synthesis)
    citation_count = len(citations)

    # Data density: numbers, percentages, years per 500 chars
    numbers = re.findall(r'\b\d[\d,.]*\s*(?:%|million|billion|tonnes|Mt|km|kg|ha)?\b', synthesis)
    data_density = (len(numbers) / max(len(synthesis), 1)) * 500

    # Gap reporting
    gap_reported = any(phrase in synthesis.lower() for phrase in [
        "did not contain", "not available", "no data", "sources did not",
        "not found", "coverage is limited", "could not find",
    ])

    # Score: sources (0–2)
    if n_scraped >= 5:      s_sources = 2.0
    elif n_scraped >= 3:    s_sources = 1.5
    elif n_scraped >= 1:    s_sources = 1.0
    else:                   s_sources = 0.0

    # Score: synthesis length (0–2)
    if len(synthesis) >= 2000:    s_length = 2.0
    elif len(synthesis) >= 1000:  s_length = 1.5
    elif len(synthesis) >= 400:   s_length = 1.0
    else:                         s_length = 0.0

    # Score: citations (0–2)
    if citation_count >= 4:    s_citations = 2.0
    elif citation_count >= 2:  s_citations = 1.5
    elif citation_count >= 1:  s_citations = 1.0
    else:                      s_citations = 0.0

    # Score: data density (0–2)
    if data_density >= 8:    s_data = 2.0
    elif data_density >= 4:  s_data = 1.5
    elif data_density >= 1:  s_data = 1.0
    else:                    s_data = 0.5

    # Score: gap reporting (0–2) — honest synthesis
    s_gap = 1.0 if gap_reported else 0.5   # reward gap reporting, don't penalise absence

    total = s_sources + s_length + s_citations + s_data + s_gap

    return QualityScore(
        mode=mode, label=label, elapsed_s=elapsed,
        n_scraped=n_scraped, n_failed=n_failed, n_snippet_only=n_snippet,
        synthesis_chars=len(synthesis),
        citation_count=citation_count,
        data_density=round(data_density, 1),
        gap_reported=gap_reported,
        score_sources=s_sources, score_length=s_length,
        score_citations=s_citations, score_data=s_data, score_gap=s_gap,
        total=round(total, 1),
    )


def _bar(score: float, max_score: float = 2.0, width: int = 10) -> str:
    filled = round((score / max_score) * width)
    return "█" * filled + "░" * (width - filled)


def _print_report(scores: list[QualityScore], syntheses: list[str]) -> None:
    print("\n" + "=" * 72)
    print("  WEB-SCOUT-AI  END-TO-END QUALITY REPORT")
    print("=" * 72)

    for qs, synthesis in zip(scores, syntheses):
        print(f"\n{'─' * 72}")
        print(f"  MODE: {qs.mode.upper()}")
        print(f"  QUERY: {qs.label}")
        print(f"  Time: {qs.elapsed_s:.0f}s")
        print(f"\n  Sources")
        print(f"    Scraped:      {qs.n_scraped}   Failed: {qs.n_failed}   Snippet-only: {qs.n_snippet_only}")
        print(f"\n  Synthesis")
        print(f"    Length:       {qs.synthesis_chars} chars")
        print(f"    Citations:    {qs.citation_count}")
        print(f"    Data density: {qs.data_density} numbers/500chars")
        print(f"    Gap reported: {'yes' if qs.gap_reported else 'no'}")
        print(f"\n  Scores (each /2.0)")
        print(f"    Sources      {_bar(qs.score_sources)}  {qs.score_sources:.1f}")
        print(f"    Length       {_bar(qs.score_length)}  {qs.score_length:.1f}")
        print(f"    Citations    {_bar(qs.score_citations)}  {qs.score_citations:.1f}")
        print(f"    Data density {_bar(qs.score_data)}  {qs.score_data:.1f}")
        print(f"    Gap honesty  {_bar(qs.score_gap)}  {qs.score_gap:.1f}")
        print(f"\n  TOTAL  {_bar(qs.total, max_score=10, width=20)}  {qs.total:.1f} / 10")
        print(f"\n  Synthesis preview (first 600 chars):")
        print(f"  {synthesis[:600].replace(chr(10), chr(10) + '  ')}")

    print(f"\n{'=' * 72}")
    avg = sum(q.total for q in scores) / len(scores)
    print(f"  AVERAGE SCORE: {avg:.1f} / 10")
    print("=" * 72 + "\n")


async def main() -> None:
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    scores = []
    syntheses = []

    for task in TASKS:
        print(f"\n▶ Running [{task['mode']}] {task['label']} ...")
        t0 = time.perf_counter()
        result = await run_web_research(
            query=task["query"],
            models=MODELS,
            search_backend="serper",
            research_depth="standard",
            **task["kwargs"],
        )
        elapsed = time.perf_counter() - t0
        qs = _score(result, task["mode"], task["label"], elapsed)
        scores.append(qs)
        syntheses.append(result.synthesis or "")
        print(f"  ✓ done in {elapsed:.0f}s — score {qs.total}/10")

    _print_report(scores, syntheses)


if __name__ == "__main__":
    asyncio.run(main())
