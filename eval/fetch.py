"""Fetch cited pages with one neutral fetcher shared by every system.

Deliberately independent of ``web_scout``: claims from both systems are
checked against text that neither system's scraper produced. Pages are
cached on disk so re-scoring does not refetch.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify

from eval.metrics import normalize_url

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
TIMEOUT_SECONDS = 30
MIN_TEXT_CHARS = 200
MAX_PDF_PAGES = 300
_TEXT_TYPES = ("html", "text/", "xml")
_DROPPED_TAGS = ("script", "style", "noscript", "nav", "footer")


def fetch_page(url: str, cache_dir: Path, *, client: httpx.Client | None = None) -> dict:
    """Return ``{url, fetched_at, ok, text, error}`` for ``url``, from cache when present."""
    normalized = normalize_url(url)
    cache_path = Path(cache_dir) / f"{hashlib.sha1(normalized.encode('utf-8')).hexdigest()}.json"
    if cache_path.is_file():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    text, error, transient = _download_text(normalized, client)
    if not error and len(text.strip()) < MIN_TEXT_CHARS:
        error = f"Extracted text shorter than {MIN_TEXT_CHARS} characters."
    page = {
        "url": normalized,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ok": not error,
        "text": text if not error else "",
        "error": error,
    }
    # Rate limits, server errors, and network failures are retried on the next scoring pass.
    if not transient:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
    return page


def is_pdf(content: bytes, content_type: str) -> bool:
    """True when the body is a PDF, by magic bytes or declared type."""
    return content.lstrip()[:5] == b"%PDF-" or "application/pdf" in (content_type or "").lower()


def html_to_text(html: str) -> str:
    """Convert HTML to markdown text without scripts, styles, navigation, or footers."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_DROPPED_TAGS):
        tag.decompose()
    return markdownify(str(soup)).strip()


def pdf_to_text(content: bytes) -> str:
    """Convert PDF bytes to markdown with docling, reading the first ``MAX_PDF_PAGES`` pages."""
    from docling.datamodel.base_models import DocumentStream
    from docling.document_converter import DocumentConverter

    stream = DocumentStream(name="page.pdf", stream=BytesIO(content))
    # page_range truncates; max_num_pages would reject the whole document.
    result = DocumentConverter().convert(stream, page_range=(1, MAX_PDF_PAGES))
    return result.document.export_to_markdown()


def _download_text(url: str, client: httpx.Client | None) -> tuple[str, str, bool]:
    owned = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
    try:
        response = client.get(url)
        if response.status_code >= 400:
            return "", f"HTTP {response.status_code}", response.status_code == 429 or response.status_code >= 500
        content_type = response.headers.get("content-type", "")
        if is_pdf(response.content, content_type):
            return pdf_to_text(response.content), "", False
        if content_type and not any(kind in content_type.lower() for kind in _TEXT_TYPES):
            return "", f"Unsupported content type {content_type}", False
        return html_to_text(response.text), "", False
    except httpx.TransportError as exc:
        return "", f"{type(exc).__name__}: {exc}", True
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}", False
    finally:
        if owned:
            client.close()
