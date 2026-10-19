"""Pass an Akamai Bot Manager interstitial and refetch the same URL.

MDPI and other hosts answer the first request with a small HTML challenge
(``/_sec/verify?provider=interstitial``) instead of the file. The challenge
sets a cookie and asks the client to POST a proof-of-work. After that POST,
the same URL returns the real payload: for an article PDF that is a body
starting with ``%PDF`` and a ``Content-Disposition`` filename, often with no
``.pdf`` suffix on the URL and no ``Content-Type`` header.

The proof is the one in the challenge page. The verify URL is taken from
that page and joined to the request's own origin, so the pass is not limited
to one publisher.
"""

import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

_SAMPLE_BYTES = 32_768
_POW_RE = re.compile(
    r"""var\s+i\s*=\s*(\d+)\s*;\s*var\s+j\s*=\s*i\s*\+\s*Number\(\s*["'](\d+)["']\s*\+\s*["'](\d+)["']\s*\)"""
)
_BM_VERIFY_RE = re.compile(r"""["']bm-verify["']\s*:\s*["']([^"']+)["']""")
_VERIFY_PATH_RE = re.compile(r"""["']([^"']*_sec/verify[^"']*)["']""")


def akamai_interstitial_solution(body: bytes | str) -> dict[str, Any] | None:
    """Return the verify token, proof, and path when ``body`` is an interstitial.

    Returns ``None`` for PDF bytes and for ordinary HTML.
    """
    if isinstance(body, bytes):
        if body.startswith(b"%PDF") or b"bm-verify" not in body[:_SAMPLE_BYTES]:
            return None
        text = body[:_SAMPLE_BYTES].decode("utf-8", "replace")
    else:
        if "bm-verify" not in body[:_SAMPLE_BYTES]:
            return None
        text = body[:_SAMPLE_BYTES]
    if "_sec/verify" not in text:
        return None
    pow_match = _POW_RE.search(text)
    token_match = _BM_VERIFY_RE.search(text)
    path_match = _VERIFY_PATH_RE.search(text)
    if not pow_match or not token_match or not path_match:
        return None
    proof = int(pow_match.group(1)) + int(pow_match.group(2) + pow_match.group(3))
    return {
        "bm-verify": token_match.group(1),
        "pow": proof,
        "verify_path": path_match.group(1),
    }


def _response_bytes(response: Any) -> bytes:
    raw = getattr(response, "body", None)
    if raw is None:
        raw = getattr(response, "content", b"")
    if isinstance(raw, str):
        return raw.encode()
    return bytes(raw or b"")


def _next_url(current: str, verify_body: bytes) -> str:
    try:
        payload = json.loads(verify_body)
    except Exception:
        return current
    if not isinstance(payload, dict):
        return current
    location = payload.get("location")
    if isinstance(location, str) and location:
        return urljoin(current, location)
    return current


async def follow_akamai_interstitial(session: Any, url: str, body: bytes | str, **post_kwargs: Any) -> str | None:
    """POST the interstitial proof.

    Returns the URL to request next, or ``None`` when ``body`` is not a
    challenge or the verify request fails. The caller refetches with its own
    HTTP client so cookies set by the challenge stay on ``session``.
    """
    solution = akamai_interstitial_solution(body)
    if solution is None:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    origin = f"{parsed.scheme}://{parsed.netloc}"
    verify_url = urljoin(origin, solution["verify_path"])
    headers = {
        "referer": url,
        "origin": origin,
        "content-type": "application/json",
    }
    headers.update(post_kwargs.pop("headers", {}) or {})
    try:
        verify = await session.post(
            verify_url,
            json={"bm-verify": solution["bm-verify"], "pow": solution["pow"]},
            headers=headers,
            timeout=post_kwargs.pop("timeout", 30),
            **post_kwargs,
        )
    except Exception as exc:
        logger.debug("[akamai] interstitial verify failed for %s: %s", url, exc)
        return None
    status = getattr(verify, "status", None) or getattr(verify, "status_code", 0) or 0
    if status >= 400:
        logger.debug("[akamai] interstitial verify returned HTTP %s for %s", status, url)
        return None
    next_url = _next_url(url, _response_bytes(verify))
    logger.debug("[akamai] passed interstitial for %s", url)
    return next_url
