"""Normalise each system's output into one record shape.

A record holds the final answer, the URLs it cites, and system-specific
extras. Cited URLs come from the markdown links in the answer for both
systems, so both sides are read the same way.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_LINK_START = re.compile(r"\]\((?=https?://)")


def normalize_url(url: str | None) -> str:
    """Strip whitespace, ``utm_*`` query parameters, and one trailing slash.

    Other query bytes are kept exactly: the normalised URL is the one fetched,
    and document servers rely on forms like ``?MjAyNA==`` or ``?a=1;b=2``.
    """
    if not url or not url.strip():
        return ""
    parts = urlsplit(url.strip())
    query = "&".join(
        segment
        for segment in parts.query.split("&")
        if segment and not segment.split("=", 1)[0].lower().startswith("utm_")
    )
    cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    if len(cleaned) > 1 and cleaned.endswith("/"):
        cleaned = cleaned[:-1]
    return cleaned


def markdown_link_urls(text: str) -> list[str]:
    """Return the normalised targets of ``[label](url)`` links, deduplicated in order."""
    urls: list[str] = []
    for start, end in _link_spans(text or ""):
        _append_unique(urls, normalize_url(text[start:end]))
    return urls


def strip_tracking(text: str) -> str:
    """Rewrite every markdown link target to its normalised form.

    Used before blind judging: ``utm_source=openai`` would name the system.
    """
    pieces = []
    cursor = 0
    for start, end in _link_spans(text or ""):
        pieces.append(text[cursor:start])
        pieces.append(normalize_url(text[start:end]))
        cursor = end
    pieces.append((text or "")[cursor:])
    return "".join(pieces)


def _link_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for match in _LINK_START.finditer(text):
        start = match.end()
        depth = 0
        end = start
        # Balance parentheses so URLs like .../Petén_(department) stay whole.
        while end < len(text) and not text[end].isspace():
            char = text[end]
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            end += 1
        spans.append((start, end))
    return spans


def record_error(message: str) -> dict:
    """Record for a run that produced no answer."""
    return {"answer": "", "cited_urls": [], "extras": {}, "error": message}


def record_web_scout(result: dict) -> dict:
    """Build a record from ``WebResearchResult.model_dump()``."""
    answer = result.get("synthesis") or ""
    if not answer.strip():
        return record_error("Web Scout returned an empty synthesis.")
    return {
        "answer": answer,
        "cited_urls": markdown_link_urls(answer),
        "extras": {"queries": list(result.get("queries") or [])},
        "error": None,
    }


def record_openai_response(response: dict) -> dict:
    """Build a record from a Responses API payload."""
    consulted_urls: list[str] = []
    open_page_urls: list[str] = []
    annotation_urls: list[str] = []
    texts: list[str] = []

    for item in response.get("output") or []:
        if item.get("type") == "web_search_call":
            action = item.get("action") or {}
            if action.get("type") == "open_page":
                _append_unique(open_page_urls, normalize_url(action.get("url")))
                _append_unique(consulted_urls, normalize_url(action.get("url")))
            for source in action.get("sources") or []:
                _append_unique(consulted_urls, normalize_url(source.get("url")))
            continue
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") != "output_text":
                continue
            if part.get("text"):
                texts.append(part["text"])
            for annotation in part.get("annotations") or []:
                if annotation.get("type") == "url_citation":
                    _append_unique(annotation_urls, normalize_url(annotation.get("url")))

    answer = "\n\n".join(texts)
    if not answer.strip():
        return record_error("OpenAI returned no answer text.")
    cited_urls = markdown_link_urls(answer)
    for url in annotation_urls:
        _append_unique(cited_urls, url)
    return {
        "answer": answer,
        "cited_urls": cited_urls,
        "extras": {"consulted_urls": consulted_urls, "open_page_urls": open_page_urls},
        "error": None,
    }


def _append_unique(urls: list[str], url: str) -> None:
    if url and url not in urls:
        urls.append(url)
