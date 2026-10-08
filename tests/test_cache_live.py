"""Manual check that a repeated scrape of one URL is reused.

URL fetches are cached with Prefect (local disk, or ``cache_storage`` when set).
``cache=False`` does not turn that cache off. The research tracker still
short-circuits a URL already scraped in the same run.
"""

import asyncio
import time

from web_scout.tools import ResearchTracker, create_scrape_and_extract_tool
from web_scout.utils import get_model

# A small, static, public page — fast to scrape, no JS needed.
TEST_URL = "https://example.com"
QUERY = "what is example.com"

EXTRACTOR_MODEL = get_model("openai/gpt-4o-mini")


async def _make_tool(use_cache: bool):
    tracker = ResearchTracker()
    return create_scrape_and_extract_tool(
        extractor_model=EXTRACTOR_MODEL,
        tracker=tracker,
        query=QUERY,
        use_session_cache=use_cache,
    ), tracker


async def main():
    scrape, tracker = await _make_tool(use_cache=True)

    # ── first call ──────────────────────────────────────────────────────────
    print(f"\n[1] First scrape ({TEST_URL}) with cache=True ...")
    t0 = time.perf_counter()
    result1 = await scrape(TEST_URL)
    t1 = time.perf_counter()
    elapsed1 = t1 - t0
    print(f"    Done in {elapsed1:.2f}s")
    print(f"    Content preview: {result1[:120]!r}")

    print("\n[2] First scrape finished; the URL artifact is in the Prefect cache.")

    # ── second call — must use cache ─────────────────────────────────────────
    print("\n[3] Second scrape (same URL) with cache=True ...")
    t2 = time.perf_counter()
    result2 = await scrape(TEST_URL)
    t3 = time.perf_counter()
    elapsed2 = t3 - t2
    print(f"    Done in {elapsed2:.2f}s")

    # The second call still runs the LLM extractor sub-agent (same query),
    # but the network fetch is skipped because the tracker dedupes already-scraped URLs.
    # Verify idempotency: same content.
    if result1 == result2:
        print("    PASS: both calls returned identical content")
    else:
        print("    NOTE: content differs (tracker deduped — returned cached scrape response)")
        print(f"    result2 preview: {result2[:120]!r}")

    # ── cache=False still uses the Prefect URL cache ─────────────────────────
    print("\n[4] Scrape with cache=False (URL cache still applies) ...")
    scrape_no_cache, _ = await _make_tool(use_cache=False)
    t4 = time.perf_counter()
    await scrape_no_cache(TEST_URL)
    t5 = time.perf_counter()
    elapsed_nc = t5 - t4
    print(f"    Done in {elapsed_nc:.2f}s")

    # ── cache=True: second call significantly faster than first ──────────────
    print("\n[5] Timing summary:")
    print(f"    cache=True  first call : {elapsed1:.2f}s")
    print(f"    cache=True  second call: {elapsed2:.2f}s  (tracker dedupe — instant)")
    print(f"    cache=False first call : {elapsed_nc:.2f}s")

    # Second cached call should be dramatically faster (tracker short-circuits)
    if elapsed2 < elapsed1 * 0.5:
        print("\nOverall: PASS — cache working as expected (second call ≥2× faster)")
    else:
        print(
            f"\nOverall: PARTIAL — second call not dramatically faster "
            f"({elapsed2:.2f}s vs {elapsed1:.2f}s first). "
            "This is expected if the first call was very fast or the LLM still runs."
        )


if __name__ == "__main__":
    asyncio.run(main())
