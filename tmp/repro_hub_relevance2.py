"""Repro round 2: consistency + is extractor_guidance a factor?

Runs the FAOSTAT homepage extraction 2x with the report project's Kenya
extractor_guidance and 2x without it (fresh tracker each run, no cache reuse).
Records page_type, bucket, sentinel usage, and content for each run.
"""
import asyncio
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

QUERY = (
    "Kenya trends in per capita supply of cereals fruits pulses starchy roots "
    "and vegetables current status and recent trend"
)
URL = "https://www.fao.org/faostat/en/"

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


async def one_run(label: str, guidance: str | None) -> dict:
    from web_scout.tools.scraper import create_scrape_and_extract_tool
    from web_scout.tools.tracker import ResearchTracker

    from web_scout.utils import get_model

    model = get_model("bedrock_mantle/openai.gpt-5.6-luna")
    tracker = ResearchTracker()
    tool = create_scrape_and_extract_tool(
        extractor_model=model,
        tracker=tracker,
        query=QUERY,
        extractor_guidance=guidance,
    )
    await tool(URL)
    outcome = getattr(tool, "_outcome_cache").get(ResearchTracker.normalize_url(URL))
    groups = tracker.build_result_groups()
    bucket = next((name for name, entries in groups.items() if entries), "none")
    row = {
        "label": label,
        "bucket": bucket,
        "status": outcome.status if outcome else None,
        "page_type": outcome.page_type if outcome else None,
        "sentinel": bool(outcome and outcome.content.startswith("[No relevant content")),
        "n_links": len(outcome.relevant_links) if outcome else 0,
        "content": outcome.content if outcome else "",
    }
    print(f"{label}: bucket={row['bucket']} page_type={row['page_type']} "
          f"sentinel={row['sentinel']} links={row['n_links']}")
    return row


async def main() -> None:
    rows = []
    for i in (1, 2):
        rows.append(await one_run(f"with_guidance_{i}", EXTRACTOR_GUIDANCE))
    for i in (1, 2):
        rows.append(await one_run(f"no_guidance_{i}", None))
    out = Path(__file__).with_suffix(".result.json")
    out.write_text(json.dumps(rows, indent=2))
    print(f"Saved: {out}")


if __name__ == "__main__":
    asyncio.run(main())
