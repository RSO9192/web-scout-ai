"""Batch probe for search + scrape behaviour on a set of manual queries.

Usage:
    PYTHONPATH=src python tests/query_probe.py
    PYTHONPATH=src python tests/query_probe.py --max-results 8 --max-scrapes 4

This intentionally exercises the search backend and low-level scraper without
requiring LLM API keys. It is meant for diagnosing URL discovery, routing,
timeouts, and extraction failures across a family of related queries.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import os

from web_scout import configure_logging
from web_scout.scraping import _validate_url, scrape_url
from web_scout.search_backends import SerperBackend


DEFAULT_QUERIES = [
    "Kenya interannual variability and long-term trends in precipitation current status and recent trend",
    "Kenya spatial patterns of precipitation change current status and recent trend",
    "Kenya precipitation interannual variability long-term trend report",
    "Kenya rainfall spatial patterns climate change recent trend",
    "Kenya precipitation anomalies trends pdf site:meteo.go.ke",
    "State of the climate Kenya precipitation trends rainfall variability",
]

OUTPUT_DIR = Path(__file__).parent / "probe_results"


@dataclass
class ProbeUrlResult:
    rank: int
    title: str
    url: str
    snippet: str
    validate_verdict: str = ""
    validate_detail: str = ""
    scrape_title: str = ""
    scrape_error: Optional[str] = None
    content_chars: int = 0
    elapsed_seconds: float = 0.0
    preview: str = ""


@dataclass
class ProbeQueryResult:
    query: str
    num_results: int
    elapsed_seconds: float
    urls: list[ProbeUrlResult]


def _safe_preview(text: str, limit: int = 220) -> str:
    text = " ".join(text.split())
    return text[:limit]


async def _probe_query(query: str, max_results: int, max_scrapes: int) -> ProbeQueryResult:
    backend = SerperBackend(os.environ["SERPER_API_KEY"])
    t0 = time.perf_counter()
    response = await backend.search(query, max_results=max_results)
    urls: list[ProbeUrlResult] = []

    for idx, result in enumerate(response.results[:max_scrapes], start=1):
        u0 = time.perf_counter()
        verdict, detail = await _validate_url(result.url)
        content, title, error = await scrape_url(result.url, query=query)
        elapsed = time.perf_counter() - u0
        urls.append(
            ProbeUrlResult(
                rank=idx,
                title=result.title,
                url=result.url,
                snippet=result.snippet,
                validate_verdict=verdict,
                validate_detail=detail,
                scrape_title=title,
                scrape_error=error,
                content_chars=len(content),
                elapsed_seconds=round(elapsed, 2),
                preview=_safe_preview(content),
            )
        )

    return ProbeQueryResult(
        query=query,
        num_results=len(response.results),
        elapsed_seconds=round(time.perf_counter() - t0, 2),
        urls=urls,
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Probe search + scraping behaviour for manual queries.")
    parser.add_argument("--max-results", type=int, default=6, help="Search results to request per query.")
    parser.add_argument("--max-scrapes", type=int, default=4, help="Top URLs to scrape per query.")
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR),
        help="Directory where the JSON report is written.",
    )
    args = parser.parse_args()

    configure_logging(logging.INFO)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    report: list[ProbeQueryResult] = []
    for query in DEFAULT_QUERIES:
        logging.getLogger("web_scout.probe").info("probing query: %s", query)
        try:
            report.append(await _probe_query(query, args.max_results, args.max_scrapes))
        except Exception as e:
            report.append(
                ProbeQueryResult(
                    query=query,
                    num_results=0,
                    elapsed_seconds=0.0,
                    urls=[
                        ProbeUrlResult(
                            rank=0,
                            title="",
                            url="",
                            snippet="",
                            scrape_error=f"probe failed: {type(e).__name__}: {e}",
                        )
                    ],
                )
            )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"query_probe_{timestamp}.json"
    output_path.write_text(
        json.dumps([asdict(item) for item in report], indent=2),
        encoding="utf-8",
    )
    print(output_path)


if __name__ == "__main__":
    asyncio.run(main())
