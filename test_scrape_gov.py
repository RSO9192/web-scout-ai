"""Quick test: validate + scrape the two Akamai-protected gov sites."""
import asyncio
import logging
import sys
import time

sys.path.insert(0, "src")
from dotenv import load_dotenv
load_dotenv(".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from web_scout.scraping import _validate_url, scrape_url

URLS = [
    "https://www2.gbrmpa.gov.au/learn/threats-great-barrier-reef",
    "https://www.aims.gov.au/research-topics/environmental-issues/coral-bleaching/coral-bleaching-events",
]

QUERY = "threats to coral reefs Australia"


async def main():
    print("\n=== Full scrape (checks bot_detected classification) ===", flush=True)
    for url in URLS:
        print(f"\nScraping: {url}", flush=True)
        t0 = time.time()
        content, title, error = await scrape_url(url, query=QUERY)
        elapsed = time.time() - t0
        if error:
            tag = "BOT_DETECTED" if error.startswith("bot_detected:") else "FAILED"
            print(f"[{elapsed:.1f}s] {tag}: {error[:120]}", flush=True)
        else:
            print(f"[{elapsed:.1f}s] OK — title={title!r}  chars={len(content)}", flush=True)


asyncio.run(main())
