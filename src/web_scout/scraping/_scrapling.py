"""Thin wrappers around Scrapling fetchers (private).

Centralises stealth browser fetches with ``solve_cloudflare=True`` always
enabled.  Fetches are routed through per-host shared browser sessions
(``_stealth_session``) instead of launching one browser per call.
Requires Scrapling >= 0.4.9; raises ``RuntimeError`` on older installs
that do not support the ``solve_cloudflare`` keyword.
"""

import logging
from typing import Any

from ._stealth_session import fetch_via_session

logger = logging.getLogger(__name__)


async def stealthy_fetch(url: str, **kwargs: Any):
    """Fetch *url* via the host's shared stealth session, ``solve_cloudflare=True``.

    Raises ``RuntimeError`` when the installed Scrapling version does not
    support ``solve_cloudflare`` (requires >= 0.4.9).
    """
    kwargs.setdefault("solve_cloudflare", True)
    kwargs["network_idle"] = False
    kwargs["wait"] = 0
    kwargs.setdefault("disable_resources", True)
    selector = kwargs.pop("wait_selector", None)
    action = kwargs.get("page_action")
    from ._readiness import wait_for_content

    async def ready(page):
        await wait_for_content(page, selector=selector,
                               timeout_ms=kwargs.get("timeout", 15_000),
                               visual=not kwargs.get("disable_resources", True))
        if action is not None:
            await action(page)

    kwargs["page_action"] = ready

    try:
        return await fetch_via_session(url, **kwargs)
    except TypeError as exc:
        if "solve_cloudflare" in str(exc):
            raise RuntimeError(
                "Scrapling >= 0.4.9 is required for solve_cloudflare support. "
                "Run: pip install 'scrapling[fetchers]>=0.4.9'"
            ) from exc
        raise
