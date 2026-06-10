"""Ad-hoc probe for GMO/crop-diversity queries to diagnose Max turns issue.

Usage:
    PYTHONPATH=src python tests/gmo_probe.py
    PYTHONPATH=src python tests/gmo_probe.py --env-file /path/to/.env
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

from web_scout import DEFAULT_WEB_RESEARCH_MODELS, configure_logging, run_web_research

ENV_FILE = Path("/Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/report/.env")
OUTPUT_DIR = Path(__file__).parent / "probe_results"

QUERIES = [
    "crop origin centres diversity wild relatives gene flow GMO risk Kenya",
    "GM crops commercial cultivation field trial crop wild relatives Kenya",
]

MODELS = DEFAULT_WEB_RESEARCH_MODELS


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


async def _run_one(query: str) -> FullProbeResult:
    t0 = time.perf_counter()
    try:
        result = await run_web_research(
            query=query,
            models=MODELS,
            search_backend="serper",
            research_depth="standard",
            max_pdf_pages=40,
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
                ProbeFailure(url=item.url, title=item.title, error=item.content) for item in result.scrape_failed
            ],
            bot_detected=[
                ProbeFailure(url=item.url, title=item.title, error=item.content) for item in result.bot_detected
            ],
        )
    except Exception as e:
        return FullProbeResult(
            query=query,
            elapsed_seconds=round(time.perf_counter() - t0, 2),
            error=f"{type(e).__name__}: {e}",
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the targeted GMO/max-turn regression probe.")
    parser.add_argument("--env-file", default=str(ENV_FILE), help="Path to dotenv file with API keys.")
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR),
        help="Directory where the JSON report is written.",
    )
    args = parser.parse_args()

    load_dotenv(args.env_file, override=False)
    configure_logging(logging.INFO)

    report: list[FullProbeResult] = []
    for query in QUERIES:
        logging.getLogger("web_scout.probe").info("probing: %s", query)
        report.append(await _run_one(query))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"gmo_probe_{timestamp}.json"
    output_path.write_text(json.dumps([asdict(item) for item in report], indent=2), encoding="utf-8")
    print(f"\nReport saved: {output_path}")

    # Print a quick summary to stdout
    for r in report:
        print(f"\n--- {r.query[:70]} ---")
        print(
            "  elapsed: "
            f"{r.elapsed_seconds}s | scraped: {r.num_scraped} | "
            f"failed: {r.num_failed} | bot: {r.num_bot_detected}"
        )
        if r.error:
            print(f"  ERROR: {r.error}")
        for f in r.scrape_failed:
            print(f"  FAILED: {f.url}")
            print(f"    {f.error[:120]}")


if __name__ == "__main__":
    asyncio.run(main())
