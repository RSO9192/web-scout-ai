"""End-to-end trace of one unrestricted run_web_research call.

Usage: python tmp/e2e_trace_run.py <slug> "<query>" "<country>"

Mimics the report project's figure_web_search_agent configuration
(models, depth, guidance template) and dumps a JSON trace of the result.
"""
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
# Silence the noisiest third-party loggers; keep web_scout + scrapling INFO.
for noisy in ("httpx", "httpcore", "LiteLLM", "litellm", "openai", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

MODELS = {
    "web_researcher": "bedrock_mantle/openai.gpt-5.6-luna",
    "content_extractor": "bedrock_mantle/openai.gpt-5.6-luna",
    "followup_selector": "bedrock_mantle/openai.gpt-5.6-luna",
    "vision_fallback": "gemini/gemini-3.7-flash",
}

GUIDANCE = """\
Country of interest: {country}

- Keep findings explicitly about {country}.
- Also keep regional findings whose stated scope includes {country}.
- Preserve exact figures, dates, units, trend directions, locations, and data
  provenance as the source gives them.
"""


async def main() -> None:
    slug, query, country = sys.argv[1], sys.argv[2], sys.argv[3]
    from web_scout import run_web_research

    t0 = time.time()
    result = await run_web_research(
        query=query,
        models=MODELS,
        research_depth="standard",
        max_pdf_pages=80,
        max_content_chars=60_000,
        cache=True,
        extractor_guidance=GUIDANCE.format(country=country),
    )
    elapsed = time.time() - t0

    trace = {
        "slug": slug,
        "query": query,
        "elapsed_s": round(elapsed, 1),
        "searches": [q.query for q in result.queries],
        "buckets": {
            name: [
                {"url": e.url, "title": e.title, "reference": e.reference, "content_chars": len(e.content or "")}
                for e in getattr(result, name)
            ]
            for name in (
                "scraped", "scraped_irrelevant", "scrape_failed", "blocked_by_policy",
                "source_http_error", "bot_detected", "snippet_only",
            )
        },
        "synthesis": result.synthesis,
    }
    out = Path(__file__).parent / f"e2e_{slug}.result.json"
    out.write_text(json.dumps(trace, indent=2, ensure_ascii=False))
    print(f"[e2e] {slug} done in {elapsed:.0f}s -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
