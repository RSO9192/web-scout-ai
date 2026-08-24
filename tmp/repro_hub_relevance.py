"""Repro: does the extractor pass hub-page meta-summaries as citable evidence?

Mimics the report project's figure_web_search_agent usage:
- same content_extractor model (bedrock_mantle/openai.gpt-5.6-luna)
- same extractor_guidance (country scoping for Kenya)
- same query template "{country} {theme} current status and recent trend"

Calls scrape_and_extract directly on hub pages and one control content page,
then reports which tracker bucket each URL landed in and what the extractor
returned (page_type, content). Hypothesis confirmed if a hub page whose
extraction contains no Kenya facts lands in the "scraped" (citable) bucket.

Run:
    bash -c 'set -a && source .env && set +a && \
    conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout \
    python tmp/repro_hub_relevance.py'
"""
import asyncio
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

QUERY = (
    "Kenya trends in per capita supply of cereals fruits pulses starchy roots "
    "and vegetables current status and recent trend"
)

# Copy of report/fao_ess_lib/agentic/agents/figure_web_search_agent.py
# WEB_SOURCE_EXTRACTOR_GUIDANCE formatted for Kenya.
EXTRACTOR_GUIDANCE = """\
Country of interest: Kenya

- Keep findings explicitly about Kenya.
- Also keep regional findings whose stated scope includes Kenya, even when
  the source never names the country — for example East Africa, NENA, Sahel,
  North Africa, or sub-Saharan Africa when that region covers Kenya.
  Always label those findings with the regional scope the source uses
  (e.g. "East Africa:", "NENA:", "North Africa:").
- You may use your knowledge of geography only to judge whether a named region
  includes Kenya. Do not invent regional membership the source does not
  imply, and do not present that membership judgment as a source-derived claim.
- Discard purely global findings and findings about other countries or regions that
  do not include Kenya, however interesting.
- Preserve exact figures, dates, units, trend directions, locations, and data
  provenance as the source gives them.
"""

URLS = [
    # The reported case: FAOSTAT homepage, a pure hub/navigation page.
    "https://www.fao.org/faostat/en/",
    # Second hub: FAOSTAT data-domains view (SPA fragment), also no Kenya facts.
    "https://www.fao.org/faostat/en/#data",
    # Control: a content page that does carry Kenya-specific food data.
    "https://www.fao.org/faostat/en/#country/114",
]


async def main() -> None:
    from web_scout.tools.scraper import create_scrape_and_extract_tool
    from web_scout.tools.tracker import ResearchTracker
    from web_scout.utils import get_model

    model = get_model("bedrock_mantle/openai.gpt-5.6-luna")
    tracker = ResearchTracker()
    scrape_and_extract = create_scrape_and_extract_tool(
        extractor_model=model,
        tracker=tracker,
        query=QUERY,
        extractor_guidance=EXTRACTOR_GUIDANCE,
    )
    outcome_cache = getattr(scrape_and_extract, "_outcome_cache")

    for url in URLS:
        print(f"\n{'=' * 80}\nURL: {url}")
        await scrape_and_extract(url)
        norm = ResearchTracker.normalize_url(url)
        outcome = outcome_cache.get(norm)
        if outcome is None:
            print("  no outcome cached (unexpected)")
            continue
        print(f"  status       : {outcome.status}")
        print(f"  failure_kind : {outcome.failure_kind}")
        print(f"  page_type    : {outcome.page_type}")
        print(f"  title        : {outcome.title!r}")
        print(f"  links ({len(outcome.relevant_links)}): {outcome.relevant_links[:5]}")
        print(f"  content      :\n{outcome.content}")

    groups = tracker.build_result_groups()
    summary = {
        bucket: [entry.url for entry in entries]
        for bucket, entries in groups.items()
        if entries
    }
    print(f"\n{'=' * 80}\nTracker buckets (scraped = citable evidence):")
    print(json.dumps(summary, indent=2))

    out = Path(__file__).with_suffix(".result.json")
    out.write_text(
        json.dumps(
            {
                "query": QUERY,
                "buckets": summary,
                "outcomes": {
                    url: {
                        "status": o.status,
                        "failure_kind": o.failure_kind,
                        "page_type": o.page_type,
                        "content": o.content,
                        "links": list(o.relevant_links),
                    }
                    for url, o in (
                        (u, outcome_cache.get(ResearchTracker.normalize_url(u)))
                        for u in URLS
                    )
                    if o is not None
                },
            },
            indent=2,
        )
    )
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    asyncio.run(main())
