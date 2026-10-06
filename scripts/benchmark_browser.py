import asyncio
import json
import os
import resource
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil
from playwright.async_api import TimeoutError, async_playwright

from web_scout.scraping._readiness import wait_for_content

PAGE = b"""<html><body><main aria-busy="true">Loading</main><script>
setTimeout(() => {
    let m = document.querySelector('main');
    m.removeAttribute('aria-busy');
    m.textContent = 'Ready report: the data has arrived with enough useful substantive content to extract.';
}, 400);
setInterval(() => fetch('/analytics'), 100);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/analytics":
            time.sleep(0.25)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(PAGE if self.path == "/" else b"ok")

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()


async def main():
    rows = []
    async with async_playwright() as pw:
        start = time.perf_counter()
        browser = await pw.chromium.launch(
            headless=True,
            **(
                {"executable_path": os.environ["BROWSER_EXECUTABLE_PATH"]}
                if os.getenv("BROWSER_EXECUTABLE_PATH")
                else {}
            ),
        )
        launch = time.perf_counter() - start
        for mode in ("network_idle", "readiness", "readiness"):
            start = time.perf_counter()
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
            ready_start = time.perf_counter()
            timed_out = False
            if mode == "network_idle":
                try:
                    await page.wait_for_load_state("networkidle", timeout=2000)
                except TimeoutError:
                    timed_out = True
            else:
                await wait_for_content(page, timeout_ms=2000)
            text = await page.locator("main").inner_text()
            rss = sum(p.memory_info().rss for p in psutil.Process().children(recursive=True) if p.is_running())
            rows.append(
                {
                    "mode": mode,
                    "seconds": time.perf_counter() - start,
                    "ready_seconds": time.perf_counter() - ready_start,
                    "timed_out": timed_out,
                    "ready_text": text.startswith("Ready report"),
                    "child_rss_bytes": rss,
                }
            )
            await context.close()
        await browser.close()
    print(
        json.dumps(
            {
                "launch_seconds": launch,
                "runs": rows,
                "parent_peak_rss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
    server.shutdown()
