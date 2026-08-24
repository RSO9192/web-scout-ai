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

    try:
        return await fetch_via_session(url, **kwargs)
    except TypeError as exc:
        if "solve_cloudflare" in str(exc):
            raise RuntimeError(
                "Scrapling >= 0.4.9 is required for solve_cloudflare support. "
                "Run: pip install 'scrapling[fetchers]>=0.4.9'"
            ) from exc
        raise
