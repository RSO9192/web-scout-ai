"""Live check: does gpt-5.6-luna set has_evidence=false on the FAOSTAT hub?

Wraps run_with_retry to capture the raw ExtractorOutput (the outcome object
does not carry the flag), then runs the Kenya query twice with the report
project's guidance.
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
GUIDANCE = "Country of interest: Kenya\n- Keep findings explicitly about Kenya."


async def one_run(label: str) -> dict:
    import web_scout.tools.scraper as scraper_mod
    from web_scout.tools.scraper import create_scrape_and_extract_tool
    from web_scout.tools.tracker import ResearchTracker
    from web_scout.tools.types import ExtractorOutput
    from web_scout.utils import get_model

    captured: dict = {}
    original = scraper_mod.run_with_retry

    async def _capturing(agent, input_text, max_turns=30):
        result = await original(agent, input_text, max_turns=max_turns)
        output = result.final_output_as(ExtractorOutput)
        captured["has_evidence"] = output.has_evidence
        captured["page_type"] = output.page_type
        captured["content"] = output.relevant_content
        captured["n_links"] = len(output.relevant_links)
        return result

    scraper_mod.run_with_retry = _capturing
    try:
        tracker = ResearchTracker()
        tool = create_scrape_and_extract_tool(
            extractor_model=get_model("bedrock_mantle/openai.gpt-5.6-luna"),
            tracker=tracker,
            query=QUERY,
            extractor_guidance=GUIDANCE,
        )
        await tool(URL)
    finally:
        scraper_mod.run_with_retry = original

    groups = tracker.build_result_groups()
    bucket = next((name for name, entries in groups.items() if entries), "none")
    row = {"label": label, "bucket": bucket, **captured}
    print(
        f"{label}: has_evidence={row.get('has_evidence')} page_type={row.get('page_type')} "
        f"links={row.get('n_links')} bucket={bucket}"
    )
    return row


async def main() -> None:
    rows = [await one_run(f"run_{i}") for i in (1, 2)]
    Path(__file__).with_suffix(".result.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
