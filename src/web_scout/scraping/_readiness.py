"""Content readiness independent of long-lived analytics connections."""

import asyncio
import json
import logging
import time

_PROBE = """() => {
    const visible = el => !!el && el.getClientRects().length > 0;
    const body = document.body;
    if (!body) return {ready: false, signature: ''};
    const text = body.innerText || '';
    const challengePattern = new RegExp(
        "^(just a moment|checking your browser|verify you are human|performing security verification)", "i");
    const challenge = challengePattern.test(text.trim());
    const main = [...document.querySelectorAll('main, article, [role="main"]')].find(visible) || body;
    const content = main.cloneNode(true);
    content.querySelectorAll('nav, header, footer, script, style, [aria-busy="true"], .skeleton')
        .forEach(el => el.remove());
    const useful = (content.textContent || '').trim();
    const controls = [...main.querySelectorAll('button, input, select, a[href]')]
        .filter(el => visible(el) && !el.closest('nav, header, footer')).length;
    return {ready: !challenge && !main.closest('[aria-busy="true"], .skeleton')
            && (useful.length >= 40 || controls > 0),
            signature: useful + '|' + controls};
}"""


async def wait_for_content(page, *, selector=None, timeout_ms=15_000, visual=False):
    """Require useful content stable for two observations; never read a known shell."""
    started = time.monotonic()
    async with asyncio.timeout(timeout_ms / 1000):
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        if selector:
            await page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
        previous = None
        probe = _PROBE
        if selector:
            probe = probe.replace(
                "const main = ",
                f"const requested = document.querySelector({json.dumps(selector)}); const main = requested || ",
            ).replace(
                "(useful.length >= 40 || controls > 0)",
                "(visible(requested) && (useful.length > 0 || controls > 0 "
                "|| requested.matches('img, canvas, svg, video') "
                "|| requested.querySelector('img, canvas, svg, video')))",
            )
        while True:
            state = await page.evaluate(probe)
            if state["ready"] and state["signature"] == previous:
                break
            previous = state["signature"] if state["ready"] else None
            await asyncio.sleep(0.15)
        if visual:
            await page.evaluate("""async () => {
                await document.fonts.ready;
                await Promise.all([...document.images].filter(i => i.getClientRects().length)
                    .map(i => i.decode().catch(() => {})));
            }""")
    elapsed = time.monotonic() - started
    logging.getLogger(__name__).debug("[readiness-timing] seconds=%.3f visual=%s", elapsed, visual)
    return elapsed
