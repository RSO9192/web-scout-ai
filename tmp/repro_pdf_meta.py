"""Repro: does the PDF path still pass no-country-evidence meta-summaries as citable?

Exact query + PDF URL from the observed Somalia case, report-project config.
"""
import asyncio
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
for noisy in ("httpx", "httpcore", "LiteLLM", "litellm", "openai", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

QUERY = "Somalia access to livestock processing and animal health services current status and recent trend"
URL = "https://openknowledge.fao.org/server/api/core/bitstreams/3323e50e-1875-491c-b4c0-3e47cbaae123/content"
GUIDANCE = "Country of interest: Somalia\n- Keep findings explicitly about Somalia."


async def main() -> None:
    from web_scout.tools.scraper import create_scrape_and_extract_tool
    from web_scout.tools.tracker import ResearchTracker
    from web_scout.utils import get_model

    tracker = ResearchTracker()
    tool = create_scrape_and_extract_tool(
        extractor_model=get_model("bedrock_mantle/openai.gpt-5.6-luna"),
        tracker=tracker,
        query=QUERY,
        extractor_guidance=GUIDANCE,
        max_pdf_pages=80,
        max_content_chars=60_000,
    )
    await tool(URL)
    outcome = getattr(tool, "_outcome_cache").get(ResearchTracker.normalize_url(URL))
    groups = tracker.build_result_groups()
    bucket = next((name for name, entries in groups.items() if entries), "none")
    row = {
        "bucket": bucket,
        "status": outcome.status if outcome else None,
        "failure_kind": outcome.failure_kind if outcome else None,
        "reference": outcome.reference if outcome else None,
        "content": outcome.content if outcome else None,
    }
    print(f"\nbucket={bucket} status={row['status']} reference={row['reference']!r}")
    print(f"content:\n{row['content']}")
    Path(__file__).with_suffix(".result.json").write_text(json.dumps(row, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
