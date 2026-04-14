"""Run a mixed matrix of open-web, domain-restricted, and direct-URL probes."""

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

MODELS = {
    "web_researcher": "openai/gpt-5.4-mini",
    "query_generator": "openai/gpt-5.4-mini",
    "coverage_evaluator": "openai/gpt-5.4-mini",
    "synthesiser": "openai/gpt-5.4-mini",
    "content_extractor": "gemini/gemini-3-flash-preview",
    "vision_fallback": "gemini/gemini-3-flash-preview",
}


@dataclass
class ProbeCase:
    name: str
    query: str
    include_domains: Optional[list[str]] = None
    direct_url: Optional[str] = None


@dataclass
class ProbeFailure:
    url: str
    title: str
    error: str


@dataclass
class ProbeSummary:
    name: str
    query: str
    mode: str
    elapsed_seconds: float
    include_domains: list[str] = field(default_factory=list)
    direct_url: Optional[str] = None
    num_queries: int = 0
    num_scraped: int = 0
    num_failed: int = 0
    num_bot_detected: int = 0
    synthesis_chars: int = 0
    error: Optional[str] = None
    search_queries: list[str] = field(default_factory=list)
    scrape_failed: list[ProbeFailure] = field(default_factory=list)
    bot_detected: list[ProbeFailure] = field(default_factory=list)


CASES = [
    ProbeCase(
        name="open_q1",
        query="Kenya interannual variability and long-term trends in precipitation current status and recent trend",
    ),
    ProbeCase(
        name="open_q2",
        query="Kenya spatial patterns of precipitation change current status and recent trend",
    ),
    ProbeCase(
        name="open_q3",
        query="State of the climate Kenya precipitation trends rainfall variability",
    ),
    ProbeCase(
        name="domain_meteo_q1",
        query="Kenya interannual variability and long-term trends in precipitation current status and recent trend",
        include_domains=["meteo.go.ke"],
    ),
    ProbeCase(
        name="domain_open_sources_q2",
        query="Kenya spatial patterns of precipitation change current status and recent trend",
        include_domains=["icpac.net", "pmc.ncbi.nlm.nih.gov", "geog.ucsb.edu", "climatecentre.org"],
    ),
    ProbeCase(
        name="direct_meteo_pdf",
        query="Kenya precipitation current status and recent trend",
        direct_url="https://meteo.go.ke/documents/1353/State_of_the_Climate_Kenya_2024_v1.pdf",
    ),
    ProbeCase(
        name="direct_ucsb_page",
        query="Kenya precipitation current status and long-term trend",
        direct_url="https://legacy.geog.ucsb.edu/current-climate-trend-analysis-of-kenya-by-chris-funk/",
    ),
    ProbeCase(
        name="direct_icpac_pdf",
        query="Kenya rainfall variability and regional drivers",
        direct_url="https://www.icpac.net/documents/829/s43017-023-00397-x_1.pdf",
    ),
]


async def _run_case(case: ProbeCase, search_backend: str, research_depth: str, max_pdf_pages: int) -> ProbeSummary:
    t0 = time.perf_counter()
    mode = "direct" if case.direct_url else "domain" if case.include_domains else "open"
    try:
        result = await run_web_research(
            query=case.query,
            models=MODELS,
            include_domains=case.include_domains,
            direct_url=case.direct_url,
            search_backend=search_backend,
            research_depth=research_depth,
            max_pdf_pages=max_pdf_pages,
        )
        return ProbeSummary(
            name=case.name,
            query=case.query,
            mode=mode,
            elapsed_seconds=round(time.perf_counter() - t0, 2),
            include_domains=case.include_domains or [],
            direct_url=case.direct_url,
            num_queries=len(result.queries),
            num_scraped=len(result.scraped),
            num_failed=len(result.scrape_failed),
            num_bot_detected=len(result.bot_detected),
            synthesis_chars=len(result.synthesis),
            search_queries=[item.query for item in result.queries],
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
        return ProbeSummary(
            name=case.name,
            query=case.query,
            mode=mode,
            elapsed_seconds=round(time.perf_counter() - t0, 2),
            include_domains=case.include_domains or [],
            direct_url=case.direct_url,
            error=f"{type(e).__name__}: {e}",
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run a mixed probe matrix for web-scout-ai.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--search-backend", default="serper", choices=["serper", "duckduckgo"])
    parser.add_argument("--research-depth", default="standard", choices=["standard", "deep"])
    parser.add_argument("--max-pdf-pages", type=int, default=40)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    load_dotenv(args.env_file, override=False)
    configure_logging(logging.INFO)

    cases = CASES[: args.limit] if args.limit else CASES
    report: list[ProbeSummary] = []

    for case in cases:
        logging.getLogger("web_scout.probe").info("matrix case: %s (%s)", case.name, "direct" if case.direct_url else "domain" if case.include_domains else "open")
        report.append(await _run_case(case, args.search_backend, args.research_depth, args.max_pdf_pages))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"matrix_probe_{timestamp}.json"
    output_path.write_text(json.dumps([asdict(item) for item in report], indent=2), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    asyncio.run(main())
