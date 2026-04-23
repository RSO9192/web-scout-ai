"""Mixed probe suite with varied topics and modes for monitoring package behaviour."""

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
    max_pdf_pages: int = 40


@dataclass
class Failure:
    url: str
    title: str
    error: str


@dataclass
class CaseResult:
    name: str
    mode: str
    query: str
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
    scrape_failed: list[Failure] = field(default_factory=list)
    bot_detected: list[Failure] = field(default_factory=list)


CASES = [
    ProbeCase(
        name="open_kenya_precip",
        query="Kenya spatial patterns of precipitation change current status and recent trend",
    ),
    ProbeCase(
        name="open_iccat_policy",
        query=(
            "What specific Total Allowable Catch quotas has ICCAT set for "
            "Eastern Atlantic and Mediterranean bluefin tuna for each year "
            "from 2022 to 2026, and what were the scientific basis and stock "
            "assessment results behind each decision?"
        ),
        max_pdf_pages=30,
    ),
    ProbeCase(
        name="open_venice_climate",
        query=(
            "What are the projected sea level rise impacts on Venice "
            "specifically, including flood frequency projections and MOSE "
            "barrier effectiveness under different IPCC scenarios?"
        ),
        max_pdf_pages=30,
    ),
    ProbeCase(
        name="domain_iccat",
        query="ICCAT Eastern Atlantic Mediterranean bluefin tuna quotas 2022 2026 scientific basis",
        include_domains=["iccat.int"],
        max_pdf_pages=30,
    ),
    ProbeCase(
        name="domain_ipcc",
        query="Venice sea level rise impacts flood frequency MOSE IPCC scenarios",
        include_domains=["ipcc.ch"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_meteo",
        query="Kenya precipitation current status recent trend state of the climate",
        include_domains=["meteo.go.ke"],
    ),
    ProbeCase(
        name="direct_iccat_pdf",
        query="ICCAT eastern Atlantic Mediterranean bluefin tuna quotas and scientific basis",
        direct_url="https://www.iccat.int/Documents/Recs/compendiopdf-e/2022-08-e.pdf",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_ipcc_html",
        query="Venice sea level rise flood frequency and adaptation context",
        direct_url="https://www.ipcc.ch/report/ar6/wg2/chapter/chapter-9/",
        max_pdf_pages=25,
    ),
]


async def _run_case(case: ProbeCase, search_backend: str, research_depth: str) -> CaseResult:
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
            max_pdf_pages=case.max_pdf_pages,
        )
        return CaseResult(
            name=case.name,
            mode=mode,
            query=case.query,
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
                Failure(url=item.url, title=item.title, error=item.content)
                for item in result.scrape_failed
            ],
            bot_detected=[
                Failure(url=item.url, title=item.title, error=item.content)
                for item in result.bot_detected
            ],
        )
    except Exception as e:
        return CaseResult(
            name=case.name,
            mode=mode,
            query=case.query,
            elapsed_seconds=round(time.perf_counter() - t0, 2),
            include_domains=case.include_domains or [],
            direct_url=case.direct_url,
            error=f"{type(e).__name__}: {e}",
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run a diverse monitoring suite for web-scout-ai.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--search-backend", default="serper", choices=["serper", "duckduckgo"])
    parser.add_argument("--research-depth", default="standard", choices=["standard", "deep"])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    load_dotenv(args.env_file, override=False)
    configure_logging(logging.INFO)

    cases = CASES[: args.limit] if args.limit else CASES
    report: list[CaseResult] = []

    for case in cases:
        mode = "direct" if case.direct_url else "domain" if case.include_domains else "open"
        logging.getLogger("web_scout.probe").info("diverse case: %s (%s)", case.name, mode)
        report.append(await _run_case(case, args.search_backend, args.research_depth))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"diverse_matrix_probe_{timestamp}.json"
    output_path.write_text(json.dumps([asdict(item) for item in report], indent=2), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    asyncio.run(main())
