"""Batch probe for the full run_web_research pipeline on manual queries.

Usage:
    PYTHONPATH=src python tests/full_query_probe.py
    PYTHONPATH=src python tests/full_query_probe.py --env-file /path/to/.env

This loads API credentials from a dotenv file, runs a small suite of manual
queries through the full search + scrape + synthesis pipeline, and writes a
JSON report for diagnosis.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from web_scout import configure_logging, run_web_research

DEFAULT_ENV_FILE = Path("/Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/report/.env")
OUTPUT_DIR = Path(__file__).parent / "probe_results"
DEFAULT_QUERIES = [
    "Kenya interannual variability and long-term trends in precipitation current status and recent trend",
    "Kenya spatial patterns of precipitation change current status and recent trend",
    "Kenya precipitation interannual variability long-term trend report",
    "Kenya rainfall spatial patterns climate change recent trend",
    "Kenya precipitation anomalies recent trend report pdf",
    "State of the climate Kenya precipitation trends rainfall variability",
]

MODELS = {
    "web_researcher": "openai/gpt-5.4-mini",
    "query_generator": "openai/gpt-5.4-mini",
    "coverage_evaluator": "openai/gpt-5.4-mini",
    "synthesiser": "openai/gpt-5.4-mini",
    "content_extractor": "gemini/gemini-3-flash-preview",
    "vision_fallback": "gemini/gemini-3-flash-preview",
}


@dataclass
class ProbeSource:
    url: str
    title: str
    content_chars: int
    preview: str


@dataclass
class ProbeFailure:
    url: str
    title: str
    error: str


@dataclass
class FullProbeResult:
    query: str
    elapsed_seconds: float
    num_queries: int = 0
    num_scraped: int = 0
    num_failed: int = 0
    num_bot_detected: int = 0
    synthesis_chars: int = 0
    error: Optional[str] = None
    search_queries: list[str] = field(default_factory=list)
    scraped: list[ProbeSource] = field(default_factory=list)
    scrape_failed: list[ProbeFailure] = field(default_factory=list)
    bot_detected: list[ProbeFailure] = field(default_factory=list)


def _preview(text: str, limit: int = 240) -> str:
    return " ".join(text.split())[:limit]


async def _run_one(query: str, search_backend: str, research_depth: str, max_pdf_pages: int) -> FullProbeResult:
    t0 = time.perf_counter()
    try:
        result = await run_web_research(
            query=query,
            models=MODELS,
            search_backend=search_backend,
            research_depth=research_depth,
            max_pdf_pages=max_pdf_pages,
        )
        return FullProbeResult(
            query=query,
            elapsed_seconds=round(time.perf_counter() - t0, 2),
            num_queries=len(result.queries),
            num_scraped=len(result.scraped),
            num_failed=len(result.scrape_failed),
            num_bot_detected=len(result.bot_detected),
            synthesis_chars=len(result.synthesis),
            search_queries=[item.query for item in result.queries],
            scraped=[
                ProbeSource(
                    url=item.url,
                    title=item.title,
                    content_chars=len(item.content),
                    preview=_preview(item.content),
                )
                for item in result.scraped
            ],
            scrape_failed=[
                ProbeFailure(url=item.url, title=item.title, error=item.content)
                for item in result.scrape_failed
            ],
            bot_detected=[
                ProbeFailure(url=item.url, title=item.title, error=item.content)
                for item in result.bot_detected
            ],
        )
    except Exception as e:
        return FullProbeResult(
            query=query,
            elapsed_seconds=round(time.perf_counter() - t0, 2),
            error=f"{type(e).__name__}: {e}",
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run full web-scout research probes on manual queries.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Path to dotenv file with API keys.")
    parser.add_argument("--search-backend", default="serper", choices=["serper", "duckduckgo"])
    parser.add_argument("--research-depth", default="standard", choices=["standard", "deep"])
    parser.add_argument("--max-pdf-pages", type=int, default=40)
    parser.add_argument("--limit", type=int, default=0, help="Limit number of queries; 0 means all.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    load_dotenv(args.env_file, override=False)
    configure_logging(logging.INFO)

    report: list[FullProbeResult] = []
    queries = DEFAULT_QUERIES[: args.limit] if args.limit else DEFAULT_QUERIES
    for query in queries:
        logging.getLogger("web_scout.probe").info("full-pipeline query: %s", query)
        report.append(await _run_one(query, args.search_backend, args.research_depth, args.max_pdf_pages))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"full_query_probe_{timestamp}.json"
    output_path.write_text(json.dumps([asdict(item) for item in report], indent=2), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    asyncio.run(main())
