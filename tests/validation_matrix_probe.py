"""Broad validation matrix: 10 open, 10 domain-restricted, 10 direct-URL cases.

Usage:
    PYTHONPATH=src python tests/validation_matrix_probe.py
    PYTHONPATH=src python tests/validation_matrix_probe.py --env-file /path/to/.env
"""

import argparse
import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from web_scout import DEFAULT_WEB_RESEARCH_MODELS, configure_logging, run_web_research

DEFAULT_ENV_FILE = Path("/Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/report/.env")
OUTPUT_DIR = Path(__file__).parent / "probe_results"
MODELS = DEFAULT_WEB_RESEARCH_MODELS


@dataclass(frozen=True)
class ProbeCase:
    name: str
    mode: str
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
class SourceSummary:
    url: str
    title: str
    content_chars: int
    preview: str
    low_value: bool
    low_value_reason: str = ""


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
    num_snippet_only: int = 0
    synthesis_chars: int = 0
    synthesis_citation_count: int = 0
    total_scraped_chars: int = 0
    avg_scraped_chars: float = 0.0
    num_low_value_scraped: int = 0
    error: Optional[str] = None
    search_queries: list[str] = field(default_factory=list)
    scraped: list[SourceSummary] = field(default_factory=list)
    scrape_failed: list[Failure] = field(default_factory=list)
    bot_detected: list[Failure] = field(default_factory=list)


OPEN_CASES = [
    ProbeCase(
        name="open_kenya_precip_variability",
        mode="open",
        query="Kenya interannual variability and long-term trends in precipitation current status and recent trend",
    ),
    ProbeCase(
        name="open_kenya_precip_spatial",
        mode="open",
        query="Kenya spatial patterns of precipitation change current status and recent trend",
    ),
    ProbeCase(
        name="open_kenya_state_climate",
        mode="open",
        query="State of the climate Kenya precipitation trends rainfall variability",
    ),
    ProbeCase(
        name="open_fsc_procurement",
        mode="open",
        query=(
            "FSC MSC RSPO PEFC certification recognized producers "
            "government procurement rules Turkana, West Pokot, Kenya"
        ),
    ),
    ProbeCase(
        name="open_gmo_gene_flow",
        mode="open",
        query="crop origin centres diversity wild relatives gene flow GMO risk Kenya",
    ),
    ProbeCase(
        name="open_gmo_commercialisation",
        mode="open",
        query="GM crops commercial cultivation field trial crop wild relatives Kenya",
    ),
    ProbeCase(
        name="open_iccat_quotas",
        mode="open",
        query=(
            "What specific Total Allowable Catch quotas has ICCAT set for "
            "Eastern Atlantic and Mediterranean bluefin tuna for each year "
            "from 2022 to 2026, and what were the scientific basis and stock "
            "assessment results behind each decision?"
        ),
        max_pdf_pages=30,
    ),
    ProbeCase(
        name="open_venice_mose",
        mode="open",
        query=(
            "What are the projected sea level rise impacts on Venice "
            "specifically, including flood frequency projections and MOSE "
            "barrier effectiveness under different IPCC scenarios?"
        ),
        max_pdf_pages=30,
    ),
    ProbeCase(
        name="open_cerrado_ibama",
        mode="open",
        query=(
            "Current deforestation rates in the Brazilian Cerrado, main commodity drivers, "
            "and recent IBAMA enforcement actions 2023 2025"
        ),
    ),
    ProbeCase(
        name="open_sofi_hunger",
        mode="open",
        query="Global hunger and food insecurity trends 2022 2024 FAO SOFI report",
    ),
]

DOMAIN_CASES = [
    ProbeCase(
        name="domain_meteo_kenya_precip",
        mode="domain",
        query="Kenya precipitation current status recent trend state of the climate",
        include_domains=["meteo.go.ke"],
    ),
    ProbeCase(
        name="domain_iccat_bluefin",
        mode="domain",
        query="ICCAT Eastern Atlantic Mediterranean bluefin tuna quotas 2022 2026 scientific basis",
        include_domains=["iccat.int"],
        max_pdf_pages=30,
    ),
    ProbeCase(
        name="domain_ipcc_venice",
        mode="domain",
        query="Venice sea level rise impacts flood frequency MOSE IPCC scenarios",
        include_domains=["ipcc.ch"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_fsc_procurement",
        mode="domain",
        query="FSC public procurement Africa Kenya certification guidance",
        include_domains=["fsc.org", "africa.fsc.org"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_openknowledge_sofi",
        mode="domain",
        query="Global hunger food insecurity trends 2022 2024 SOFI report",
        include_domains=["openknowledge.fao.org"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_pmc_kenya_precip",
        mode="domain",
        query="Kenya precipitation variability trend study historical climate",
        include_domains=["pmc.ncbi.nlm.nih.gov"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_worldbank_kenya_climate",
        mode="domain",
        query="Kenya climate trends variability historical precipitation",
        include_domains=["climateknowledgeportal.worldbank.org"],
    ),
    ProbeCase(
        name="domain_scielo_gmo",
        mode="domain",
        query="GM crops Kenya biosafety gene flow wild relatives",
        include_domains=["scielo.org.za"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_frontiers_gmo",
        mode="domain",
        query="Kenya wild edible plants genetic diversity biosafety food systems",
        include_domains=["frontiersin.org"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_ucsb_kenya_climate",
        mode="domain",
        query="Kenya current climate trend analysis rainfall change",
        include_domains=["legacy.geog.ucsb.edu", "geog.ucsb.edu"],
    ),
]

DIRECT_CASES = [
    ProbeCase(
        name="direct_meteo_pdf",
        mode="direct",
        query="Kenya precipitation current status and recent trend",
        direct_url="https://meteo.go.ke/documents/1353/State_of_the_Climate_Kenya_2024_v1.pdf",
        max_pdf_pages=30,
    ),
    ProbeCase(
        name="direct_ucsb_html",
        mode="direct",
        query="Kenya precipitation current status and long-term trend",
        direct_url="https://legacy.geog.ucsb.edu/current-climate-trend-analysis-of-kenya-by-chris-funk/",
    ),
    ProbeCase(
        name="direct_iccat_pdf",
        mode="direct",
        query="ICCAT eastern Atlantic Mediterranean bluefin tuna quotas and scientific basis",
        direct_url="https://www.iccat.int/Documents/Recs/compendiopdf-e/2022-08-e.pdf",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_ipcc_html",
        mode="direct",
        query="Venice sea level rise flood frequency and adaptation context",
        direct_url="https://www.ipcc.ch/report/ar6/wg2/chapter/chapter-9/",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_fsc_kenya_pdf",
        mode="direct",
        query="Kenya FSC forest management standard requirements",
        direct_url="https://fsc.org/sites/default/files/2021-01/FSC-STD-KEN-01-2021%20EN_INS-GFSS%20Kenya.pdf",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_openknowledge_handle",
        mode="direct",
        query="Global hunger and food insecurity trends 2022 2024",
        direct_url="https://openknowledge.fao.org/handle/20.500.14283/am882e",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_nepad_pdf",
        mode="direct",
        query="Kenya agricultural biotechnology biosafety policy overview",
        direct_url="https://www.nepad.org/file-download/download/public/127523",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_scielo_html",
        mode="direct",
        query="GM crops ecological risk and biosafety governance in Africa",
        direct_url="https://scielo.org.za/scielo.php?script=sci_arttext&pid=S2663-323X2022000100014",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_frontiers_html",
        mode="direct",
        query="Wild edible plants genetic diversity food systems Kenya",
        direct_url="https://www.frontiersin.org/journals/sustainable-food-systems/articles/10.3389/fsufs.2023.1113771/full",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_nature_html",
        mode="direct",
        query="Kenya precipitation spatial patterns recent change",
        direct_url="https://www.nature.com/articles/s41598-024-63786-2",
        max_pdf_pages=25,
    ),
]

CASES = OPEN_CASES + DOMAIN_CASES + DIRECT_CASES


DIVERSE_OPEN_CASES = [
    ProbeCase(
        name="open_global_methane_iea",
        mode="open",
        query="Global methane emissions from oil and gas 2023 2024 trends abatement opportunities IEA",
    ),
    ProbeCase(
        name="open_cholera_africa",
        mode="open",
        query="Current cholera hotspots in eastern and southern Africa 2024 2025 response vaccine constraints",
    ),
    ProbeCase(
        name="open_adaptation_gap",
        mode="open",
        query="Global adaptation finance gap 2023 2024 adaptation gap report key findings and priorities",
    ),
    ProbeCase(
        name="open_bluefin_quotas_diverse",
        mode="open",
        query="Eastern Atlantic Mediterranean bluefin tuna quotas 2022 2026 stock assessment and management procedure",
        max_pdf_pages=30,
    ),
    ProbeCase(
        name="open_venice_coastal_risk",
        mode="open",
        query=(
            "Venice sea level rise flood frequency coastal risk projections and MOSE effectiveness under IPCC scenarios"
        ),
        max_pdf_pages=30,
    ),
    ProbeCase(
        name="open_cerrado_deforestation_diverse",
        mode="open",
        query="Brazilian Cerrado deforestation 2023 2025 commodity drivers soy cattle and enforcement actions",
    ),
    ProbeCase(
        name="open_sofi_hunger_diverse",
        mode="open",
        query="Global hunger and food insecurity trends 2022 2024 SOFI report regional patterns",
    ),
    ProbeCase(
        name="open_amr_burden",
        mode="open",
        query="Global antimicrobial resistance mortality burden evidence policy priorities and regional patterns",
    ),
    ProbeCase(
        name="open_green_hydrogen_africa",
        mode="open",
        query="Green hydrogen projects in Namibia Mauritania and Morocco export plans infrastructure constraints",
    ),
    ProbeCase(
        name="open_coral_bleaching",
        mode="open",
        query="Global coral bleaching 2024 2025 marine heatwave impacts reef outlook and scientific assessments",
    ),
]


DIVERSE_DOMAIN_CASES = [
    ProbeCase(
        name="domain_iccat_bluefin_diverse",
        mode="domain",
        query="ICCAT bluefin tuna quotas stock assessment and management procedure 2022 2026",
        include_domains=["iccat.int"],
        max_pdf_pages=30,
    ),
    ProbeCase(
        name="domain_ipcc_coasts",
        mode="domain",
        query="Sea level rise coastal flood risk adaptation and Venice IPCC scenarios",
        include_domains=["ipcc.ch"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_who_cholera",
        mode="domain",
        query="Cholera outbreaks eastern and southern Africa vaccine supply and WHO situation updates",
        include_domains=["who.int"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_fao_sofi",
        mode="domain",
        query="SOFI 2024 hunger food insecurity prevalence and undernourishment trends",
        include_domains=["fao.org", "openknowledge.fao.org"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_pmc_amr",
        mode="domain",
        query="antimicrobial resistance mortality burden global regional evidence policy priorities",
        include_domains=["pmc.ncbi.nlm.nih.gov"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_frontiers_foodsystems",
        mode="domain",
        query="wild edible plants food systems nutrition resilience and biodiversity evidence",
        include_domains=["frontiersin.org"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_worldbank_morocco_climate",
        mode="domain",
        query="Morocco historical climate trends temperature variability drought and precipitation patterns",
        include_domains=["climateknowledgeportal.worldbank.org"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_scielo_biosafety_diverse",
        mode="domain",
        query="GM crops ecological risk biosafety governance Africa case study evidence",
        include_domains=["scielo.org.za"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_nature_precip_diverse",
        mode="domain",
        query="precipitation spatial patterns climate change extremes and hydrometeorology",
        include_domains=["nature.com"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_noaa_coral",
        mode="domain",
        query="coral bleaching marine heatwave reef outlook NOAA scientific updates",
        include_domains=["noaa.gov"],
        max_pdf_pages=25,
    ),
]


DIVERSE_DIRECT_CASES = [
    ProbeCase(
        name="direct_iccat_pdf_diverse",
        mode="direct",
        query="ICCAT bluefin tuna quotas and management recommendation text",
        direct_url="https://www.iccat.int/Documents/Recs/compendiopdf-e/2022-08-e.pdf",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_ipcc_coastal_html",
        mode="direct",
        query="Coastal flood risk sea level rise adaptation and Venice-relevant IPCC context",
        direct_url="https://www.ipcc.ch/report/ar6/wg2/chapter/chapter-13/",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_sofi_pdf_diverse",
        mode="direct",
        query="Global hunger and food insecurity trends 2024 SOFI report key findings",
        direct_url="https://data.unicef.org/wp-content/uploads/2024/07/SOFI2024_Report_EN_web.pdf",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_nepad_pdf_diverse",
        mode="direct",
        query="African agricultural biotechnology biosafety policy overview and regional context",
        direct_url="https://www.nepad.org/file-download/download/public/127523",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_scielo_html_diverse",
        mode="direct",
        query="GM crops ecological risk and biosafety governance in Africa",
        direct_url="https://scielo.org.za/scielo.php?script=sci_arttext&pid=S2663-323X2022000100014",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_frontiers_foodsystems",
        mode="direct",
        query="Wild edible plants food systems resilience and biodiversity evidence",
        direct_url="https://www.frontiersin.org/journals/sustainable-food-systems/articles/10.3389/fsufs.2023.1113771/full",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_nature_precip_diverse",
        mode="direct",
        query="Precipitation spatial patterns climate change and extremes evidence",
        direct_url="https://www.nature.com/articles/s41598-024-63786-2",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_pmc_precip",
        mode="direct",
        query="Kenya precipitation variability and historical trend evidence",
        direct_url="https://pmc.ncbi.nlm.nih.gov/articles/PMC11153539/",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_who_sofi_html",
        mode="direct",
        query="SOFI 2024 hunger and food insecurity summary",
        direct_url="https://www.who.int/publications/m/item/the-state-of-food-security-and-nutrition-in-the-world-2024",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_worldbank_climate",
        mode="direct",
        query="Kenya historical climate trends precipitation variability and extremes summary",
        direct_url="https://climateknowledgeportal.worldbank.org/country/kenya/trends-variability-historical",
        max_pdf_pages=25,
    ),
]


INTERACTION_AUDIT_CASES = [
    ProbeCase(
        name="open_adaptation_gap_audit",
        mode="open",
        query="Global adaptation finance gap 2023 2024 adaptation gap report key findings and priorities",
    ),
    ProbeCase(
        name="open_sofi_hunger_audit",
        mode="open",
        query="Global hunger and food insecurity trends 2022 2024 SOFI report regional patterns",
    ),
    ProbeCase(
        name="open_venice_coastal_risk_audit",
        mode="open",
        query=(
            "Venice sea level rise flood frequency coastal risk projections and MOSE effectiveness under IPCC scenarios"
        ),
        max_pdf_pages=30,
    ),
    ProbeCase(
        name="domain_who_cholera_audit",
        mode="domain",
        query="Cholera outbreaks eastern and southern Africa vaccine supply and WHO situation updates",
        include_domains=["who.int"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_fao_sofi_audit",
        mode="domain",
        query="SOFI 2024 hunger food insecurity prevalence and undernourishment trends",
        include_domains=["fao.org", "openknowledge.fao.org"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="domain_worldbank_morocco_climate_audit",
        mode="domain",
        query="Morocco historical climate trends temperature variability drought and precipitation patterns",
        include_domains=["climateknowledgeportal.worldbank.org"],
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_who_sofi_html_audit",
        mode="direct",
        query="SOFI 2024 hunger and food insecurity summary",
        direct_url="https://www.who.int/publications/m/item/the-state-of-food-security-and-nutrition-in-the-world-2024",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_worldbank_climate_audit",
        mode="direct",
        query="Kenya historical climate trends precipitation variability and extremes summary",
        direct_url="https://climateknowledgeportal.worldbank.org/country/kenya/trends-variability-historical",
        max_pdf_pages=25,
    ),
    ProbeCase(
        name="direct_frontiers_foodsystems_audit",
        mode="direct",
        query="Wild edible plants food systems resilience and biodiversity evidence",
        direct_url="https://www.frontiersin.org/journals/sustainable-food-systems/articles/10.3389/fsufs.2023.1113771/full",
        max_pdf_pages=25,
    ),
]


CASE_PRESETS = {
    "standard": OPEN_CASES + DOMAIN_CASES + DIRECT_CASES,
    "diverse": DIVERSE_OPEN_CASES + DIVERSE_DOMAIN_CASES + DIVERSE_DIRECT_CASES,
    "interaction_audit": INTERACTION_AUDIT_CASES,
}


def _preview(text: str, limit: int = 260) -> str:
    return " ".join(text.split())[:limit]


def _count_citations(text: str) -> int:
    return len(re.findall(r"\[[^\]]+\]\(https?://[^\)]+\)", text or ""))


def _low_value_reason(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) < 500:
        return "very_short"
    lower = normalized.lower()
    if lower.count("http://") + lower.count("https://") >= 8:
        return "link_heavy"
    if "relevant follow-up links:" in lower and len(normalized) < 1200:
        return "link_dump"
    if lower.count("source: ") >= 2:
        return "stacked_sources"
    return ""


async def _run_case(case: ProbeCase, search_backend: str, research_depth: str) -> CaseResult:
    t0 = time.perf_counter()
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
        scraped = [
            SourceSummary(
                url=item.url,
                title=item.title,
                content_chars=len(item.content),
                preview=_preview(item.content),
                low_value=bool(_low_value_reason(item.content)),
                low_value_reason=_low_value_reason(item.content),
            )
            for item in result.scraped
        ]
        total_scraped_chars = sum(item.content_chars for item in scraped)
        return CaseResult(
            name=case.name,
            mode=case.mode,
            query=case.query,
            elapsed_seconds=round(time.perf_counter() - t0, 2),
            include_domains=case.include_domains or [],
            direct_url=case.direct_url,
            num_queries=len(result.queries),
            num_scraped=len(result.scraped),
            num_failed=len(result.scrape_failed),
            num_bot_detected=len(result.bot_detected),
            num_snippet_only=len(result.snippet_only),
            synthesis_chars=len(result.synthesis),
            synthesis_citation_count=_count_citations(result.synthesis),
            total_scraped_chars=total_scraped_chars,
            avg_scraped_chars=round(total_scraped_chars / max(len(scraped), 1), 1) if scraped else 0.0,
            num_low_value_scraped=sum(1 for item in scraped if item.low_value),
            search_queries=[item.query for item in result.queries],
            scraped=scraped,
            scrape_failed=[
                Failure(url=item.url, title=item.title, error=item.content) for item in result.scrape_failed
            ],
            bot_detected=[Failure(url=item.url, title=item.title, error=item.content) for item in result.bot_detected],
        )
    except Exception as exc:
        return CaseResult(
            name=case.name,
            mode=case.mode,
            query=case.query,
            elapsed_seconds=round(time.perf_counter() - t0, 2),
            include_domains=case.include_domains or [],
            direct_url=case.direct_url,
            error=f"{type(exc).__name__}: {exc}",
        )


async def _run_cases(
    cases: list[ProbeCase],
    *,
    search_backend: str,
    research_depth: str,
    concurrency: int,
    case_timeout_s: int,
    partial_output_path: Optional[Path] = None,
) -> list[CaseResult]:
    semaphore = asyncio.Semaphore(max(1, concurrency))
    results: list[Optional[CaseResult]] = [None] * len(cases)
    write_lock = asyncio.Lock()

    async def _flush_partial() -> None:
        if partial_output_path is None:
            return
        partial = [asdict(item) for item in results if item is not None]
        async with write_lock:
            partial_output_path.write_text(json.dumps(partial, indent=2), encoding="utf-8")

    async def _worker(index: int, case: ProbeCase) -> None:
        async with semaphore:
            logging.getLogger("web_scout.probe").info("validation case: %s (%s)", case.name, case.mode)
            try:
                if case_timeout_s > 0:
                    result = await asyncio.wait_for(
                        _run_case(case, search_backend, research_depth),
                        timeout=case_timeout_s,
                    )
                else:
                    result = await _run_case(case, search_backend, research_depth)
            except asyncio.TimeoutError:
                result = CaseResult(
                    name=case.name,
                    mode=case.mode,
                    query=case.query,
                    elapsed_seconds=float(case_timeout_s),
                    include_domains=case.include_domains or [],
                    direct_url=case.direct_url,
                    error=f"TimeoutError: case exceeded {case_timeout_s}s",
                )
            results[index] = result
            await _flush_partial()

    await asyncio.gather(*(_worker(index, case) for index, case in enumerate(cases)))
    return [item for item in results if item is not None]


def _mode_summary(results: list[CaseResult], mode: str) -> str:
    subset = [item for item in results if item.mode == mode]
    cases = len(subset)
    errored = sum(1 for item in subset if item.error)
    scraped = sum(item.num_scraped for item in subset)
    failed = sum(item.num_failed for item in subset)
    bot = sum(item.num_bot_detected for item in subset)
    snippets = sum(item.num_snippet_only for item in subset)
    low_value = sum(item.num_low_value_scraped for item in subset)
    avg_chars = (
        round(
            sum(item.total_scraped_chars for item in subset) / max(scraped, 1),
            1,
        )
        if scraped
        else 0.0
    )
    return (
        f"{mode}: cases={cases} errored={errored} "
        f"scraped={scraped} failed={failed} bot={bot} "
        f"snippets={snippets} low_value={low_value} avg_chars={avg_chars}"
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run a 30-case validation matrix for web-scout-ai.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--search-backend", default="serper", choices=["serper", "duckduckgo"])
    parser.add_argument("--research-depth", default="standard", choices=["standard", "deep"])
    parser.add_argument("--preset", default="standard", choices=sorted(CASE_PRESETS))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--limit", type=int, default=0, help="Optional overall case limit for dry runs.")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--case-timeout-s", type=int, default=240)
    args = parser.parse_args()

    load_dotenv(args.env_file, override=False)
    configure_logging(logging.INFO)

    preset_cases = CASE_PRESETS[args.preset]
    cases = preset_cases[: args.limit] if args.limit else preset_cases
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    partial_output_path = output_dir / f"validation_matrix_probe_{timestamp}.partial.json"
    report = await _run_cases(
        cases,
        search_backend=args.search_backend,
        research_depth=args.research_depth,
        concurrency=args.concurrency,
        case_timeout_s=args.case_timeout_s,
        partial_output_path=partial_output_path,
    )

    output_path = output_dir / f"validation_matrix_probe_{timestamp}.json"
    output_path.write_text(json.dumps([asdict(item) for item in report], indent=2), encoding="utf-8")
    if partial_output_path.exists():
        partial_output_path.unlink()

    print(output_path)
    print(_mode_summary(report, "open"))
    print(_mode_summary(report, "domain"))
    print(_mode_summary(report, "direct"))


if __name__ == "__main__":
    asyncio.run(main())
