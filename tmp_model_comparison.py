"""One-off comparison: default models (gemini-3-flash) vs bedrock_mantle gpt-5.6-luna.

Runs the same queries through run_web_research with both model configs and
saves each result to JSON for side-by-side comparison. Delete after use.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from web_scout import run_web_research  # noqa: E402

OUT_DIR = Path(__file__).parent / "tmp_comparison_results"
OUT_DIR.mkdir(exist_ok=True)

LUNA = "bedrock_mantle/openai.gpt-5.6-luna"

CONFIGS = {
    "default_gemini": None,  # use package defaults
    "gpt56_luna": {
        "web_researcher": LUNA,
        "content_extractor": LUNA,
        "followup_selector": LUNA,
        "vision_fallback": LUNA,
    },
}

QUERIES = [
    {
        "name": "kenya_climate",
        "query": "What are the main climate risks for Kenya's agricultural sector?",
        "domain_expertise": "climate risk and agriculture",
    },
    {
        "name": "senegal_rice",
        "query": "Rice production trends in Senegal over the last 5 years, with production volumes in tonnes",
        "domain_expertise": "agricultural statistics",
    },
]


async def main():
    for q in QUERIES:
        for config_name, models in CONFIGS.items():
            out_file = OUT_DIR / f"{q['name']}__{config_name}.json"
            if out_file.exists():
                print(f"SKIP (exists): {out_file.name}")
                continue
            print(f"\n===== RUN {q['name']} / {config_name} =====", flush=True)
            start = time.time()
            try:
                result = await run_web_research(
                    query=q["query"],
                    models=models,
                    domain_expertise=q["domain_expertise"],
                )
                payload = {
                    "config": config_name,
                    "models": models or "DEFAULT",
                    "query": q["query"],
                    "elapsed_s": round(time.time() - start, 1),
                    "synthesis": result.synthesis,
                    "synthesis_chars": len(result.synthesis),
                    "n_scraped": len(result.scraped),
                    "n_scrape_failed": len(result.scrape_failed),
                    "n_queries": len(result.queries),
                    "scraped_urls": [e.url for e in result.scraped],
                    "executed_queries": [sq.query for sq in result.queries],
                }
            except Exception as e:  # capture failures so one run doesn't kill the rest
                payload = {
                    "config": config_name,
                    "query": q["query"],
                    "elapsed_s": round(time.time() - start, 1),
                    "error": f"{type(e).__name__}: {e}",
                }
            out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
            print(f"SAVED {out_file.name}  elapsed={payload['elapsed_s']}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
