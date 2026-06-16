"""Thin wrappers around Scrapling fetchers (private).

Centralises ``StealthyFetcher.async_fetch`` calls with ``solve_cloudflare=True``
always enabled.  Requires Scrapling ≥ 0.4.9; raises ``RuntimeError`` on older
installs that do not support the ``solve_cloudflare`` keyword.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def stealthy_fetch(url: str, **kwargs: Any):
    """Call ``StealthyFetcher.async_fetch`` with ``solve_cloudflare=True``.

    Raises ``RuntimeError`` when the installed Scrapling version does not
    support ``solve_cloudflare`` (requires ≥ 0.4.9).
    """
    from scrapling.fetchers import StealthyFetcher  # type: ignore[import]

    kwargs.setdefault("solve_cloudflare", True)

    try:
        return await StealthyFetcher.async_fetch(url, **kwargs)
    except TypeError as exc:
        if "solve_cloudflare" in str(exc):
            raise RuntimeError(
                "Scrapling ≥ 0.4.9 is required for solve_cloudflare support. "
                "Run: pip install 'scrapling[fetchers]>=0.4.9'"
            ) from exc
        raise
