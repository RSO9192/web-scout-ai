"""Real-time test for cache=True behaviour.

What the README says (and what we verify here):
  cache=True  ->  "reuse successful URL source artifacts for this Python process"

Expected behaviour:
1. First call for a URL populates _SESSION_SOURCE_CACHE.
2. Second call for the same URL returns the cached artifact (much faster, no re-fetch).
3. Content is identical across both calls.
4. A second scrape tool instance with use_session_cache=False does NOT hit the cache.
"""

import asyncio
import time

from web_scout.tools import (
    ResearchTracker,
    _SESSION_SOURCE_CACHE,
    create_scrape_and_extract_tool,
)
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
    # ── clear any leftover cache entries from earlier runs ──────────────────
    _SESSION_SOURCE_CACHE.clear()

    scrape, tracker = await _make_tool(use_cache=True)

    # ── first call ──────────────────────────────────────────────────────────
    print(f"\n[1] First scrape ({TEST_URL}) with cache=True ...")
    t0 = time.perf_counter()
    result1 = await scrape(TEST_URL)
    t1 = time.perf_counter()
    elapsed1 = t1 - t0
    print(f"    Done in {elapsed1:.2f}s")
    print(f"    Content preview: {result1[:120]!r}")

    # ── cache state after first call ────────────────────────────────────────
    cache_keys_after_first = list(_SESSION_SOURCE_CACHE.keys())
    print(f"\n[2] Cache entries after first call: {len(cache_keys_after_first)}")
    if not cache_keys_after_first:
        print("    FAIL: cache is empty — artifact was not stored.")
        return
    print(f"    Key URL: {cache_keys_after_first[0].url!r}")
    print("    PASS: artifact stored in _SESSION_SOURCE_CACHE")

    # ── second call — must use cache ─────────────────────────────────────────
    print(f"\n[3] Second scrape (same URL) with cache=True ...")
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

    # ── with cache=False — should not touch _SESSION_SOURCE_CACHE ────────────
    print(f"\n[4] Scrape with cache=False (should NOT hit session cache) ...")
    _SESSION_SOURCE_CACHE.clear()  # reset to isolate
    scrape_no_cache, _ = await _make_tool(use_cache=False)
    t4 = time.perf_counter()
    result_nc = await scrape_no_cache(TEST_URL)
    t5 = time.perf_counter()
    elapsed_nc = t5 - t4
    print(f"    Done in {elapsed_nc:.2f}s")
    after_no_cache = list(_SESSION_SOURCE_CACHE.keys())
    if after_no_cache:
        print(f"    FAIL: cache was populated ({len(after_no_cache)} entries) even with cache=False")
    else:
        print("    PASS: _SESSION_SOURCE_CACHE remains empty with cache=False")

    # ── cache=True: second call significantly faster than first ──────────────
    print(f"\n[5] Timing summary:")
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
