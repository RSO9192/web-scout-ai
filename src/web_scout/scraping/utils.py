import json
import logging
import re
from email.message import Message
from typing import Any, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .constants import (
    DOC_EXTENSIONS,
    PDF_MAGIC_BYTES,
    SUPPORTED_DOC_CONTENT_TYPES,
    SUPPORTED_DOC_EXTENSIONS,
    UNSUPPORTED_LEGACY_DOC_EXTENSIONS,
)

logger = logging.getLogger(__name__)


def log_fetch(
    url: str,
    *,
    status: int | str,
    via: str,
    bytes_: int | None = None,
    error: str | None = None,
) -> None:
    """Emit a consistent DEBUG line for every network URL web-scout retrieves."""
    # Prefer the package logger so lines show after ``configure_logging(DEBUG)``
    # even when the root logger is quiet (common in notebooks).
    fetch_logger = logging.getLogger("web_scout.fetch")
    if error:
        fetch_logger.debug("[fetch] %s %s via=%s error=%s", status, url, via, error)
    elif bytes_ is not None:
        fetch_logger.debug("[fetch] %s %s via=%s bytes=%d", status, url, via, bytes_)
    else:
        fetch_logger.debug("[fetch] %s %s via=%s", status, url, via)

_NETWORK_ERROR_MARKERS = (
    "certificate verify failed",
    "certificateverifyerror",
    "could not resolve host",
    "err_connection_",
    "err_name_not_resolved",
    "err_network_",
    "network is unreachable",
    "connection refused",
    "connection reset",
    "connecterror",
    "connectionerror",
    "timed out",
    "timeouterror",
)


def invalid_http_url_reason(url: str) -> str:
    """Return a reason when *url* is not an absolute HTTP(S) URL."""
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        return str(exc)
    if parsed.scheme not in ("http", "https"):
        return "URL must use http or https"
    if not parsed.netloc:
        return "URL must include a hostname"
    return ""


def is_network_error(value: object) -> bool:
    """Return True when an exception or message represents a transport failure."""
    message = str(value).lower()
    return any(marker in message for marker in _NETWORK_ERROR_MARKERS)


def is_blocked_domain(url: str, exclude_domains: Optional[frozenset] = None) -> bool:
    """Return True when *url*'s host is in the caller-supplied exclude list.

    Nothing is blocked implicitly: ``None`` or an empty set blocks nothing.
    """
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return any(netloc == d or netloc.endswith("." + d) for d in exclude_domains or ())


def is_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped[0] not in ("{", "["):
        return False
    try:
        json.loads(stripped)
        return True
    except Exception:
        return False


def sniff_document_payload(
    payload: bytes,
    *,
    content_type: str = "",
    content_disposition: str = "",
) -> bool:
    if not payload:
        return False
    if payload[:4] == PDF_MAGIC_BYTES:
        return True
    if payload[:4] == b"PK\x03\x04":
        ct = normalize_content_type(content_type)
        filename = filename_from_content_disposition(content_disposition).lower()
        if any(ct.startswith(t) for t in SUPPORTED_DOC_CONTENT_TYPES):
            return True
        return any(filename.endswith(ext) for ext in SUPPORTED_DOC_EXTENSIONS)
    return False


def extract_text_from_html(html: str) -> str:
    """Strip script/style blocks then all tags; return plain text."""
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()


def normalize_content_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def filename_from_content_disposition(value: str) -> str:
    if not value:
        return ""
    msg = Message()
    msg["content-disposition"] = value
    return msg.get_filename() or ""


_DOCUMENT_LINK_MARKERS = (
    "/download",
    "/bitstream/",
    "/bitstreams/",
    "download?",
    "file-download",
)

# Highwire Press / Google Scholar meta used by DSpace and many repositories.
_CITATION_PDF_META_RE = re.compile(
    r"<meta\b[^>]*\bname\s*=\s*[\"']citation_pdf_url[\"'][^>]*>",
    flags=re.I,
)
_META_CONTENT_RE = re.compile(r"\bcontent\s*=\s*[\"']([^\"']+)[\"']", flags=re.I)

# DSpace Angular frontend download route → binary REST content endpoint.
_DSPACE_BITSTREAM_DOWNLOAD_RE = re.compile(
    r"^(https?://[^/]+)/bitstreams/([0-9a-fA-F-]{36})/download/?$",
    flags=re.I,
)
_DSPACE_ITEM_RE = re.compile(
    r"^(https?://[^/]+)/items/([0-9a-fA-F-]{36})/?$",
    flags=re.I,
)


def resolve_document_download_url(url: str) -> str:
    """Rewrite repository frontend download URLs to a binary-fetchable endpoint.

    Open Knowledge / DSpace ``citation_pdf_url`` values look like
    ``/bitstreams/{uuid}/download``. That path is an Angular route and returns
    HTML over plain HTTP. The actual PDF is at
    ``/server/api/core/bitstreams/{uuid}/content``.
    """
    if not url:
        return url
    match = _DSPACE_BITSTREAM_DOWNLOAD_RE.match(url.strip())
    if match:
        return f"{match.group(1)}/server/api/core/bitstreams/{match.group(2)}/content"
    return url


def extract_citation_pdf_urls(html: str) -> list[str]:
    """Return absolute ``citation_pdf_url`` meta values from HTML.

    DSpace / Open Knowledge item pages often expose the primary PDF only via
    this head meta tag while the visible "Download PDF" control is a button.
    Returned URLs are rewritten to binary-fetchable endpoints when needed.
    """
    if not html:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for tag_match in _CITATION_PDF_META_RE.finditer(html):
        content_match = _META_CONTENT_RE.search(tag_match.group(0))
        if not content_match:
            continue
        raw = content_match.group(1).strip()
        if not raw:
            continue
        # Prefer https for scheme-relative / http repository links.
        if raw.startswith("//"):
            raw = "https:" + raw
        elif raw.startswith("http://"):
            raw = "https://" + raw[len("http://"):]
        if not raw.startswith(("http://", "https://")):
            continue
        raw = resolve_document_download_url(raw)
        if raw not in seen:
            seen.add(raw)
            found.append(raw)
    return found


def _dspace_bundles_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    embedded = payload.get("_embedded") or {}
    bundles = embedded.get("bundles")
    if isinstance(bundles, list):
        return [bundle for bundle in bundles if isinstance(bundle, dict)]
    if isinstance(bundles, dict):
        nested = (bundles.get("_embedded") or {}).get("bundles")
        if isinstance(nested, list):
            return [bundle for bundle in nested if isinstance(bundle, dict)]
    return []


def _dspace_bitstreams_from_bundle(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    embedded = bundle.get("_embedded") or {}
    bitstreams = embedded.get("bitstreams")
    if isinstance(bitstreams, list):
        return [bit for bit in bitstreams if isinstance(bit, dict)]
    if isinstance(bitstreams, dict):
        nested = (bitstreams.get("_embedded") or {}).get("bitstreams")
        if isinstance(nested, list):
            return [bit for bit in nested if isinstance(bit, dict)]
    return []


def primary_pdf_url_from_dspace_item_payload(payload: dict[str, Any]) -> Optional[str]:
    """Pick the ORIGINAL-bundle PDF content URL from a DSpace item JSON payload."""
    bundles = _dspace_bundles_from_payload(payload)
    if not bundles:
        return None
    ordered = sorted(bundles, key=lambda bundle: 0 if bundle.get("name") == "ORIGINAL" else 1)
    for bundle in ordered:
        for bitstream in _dspace_bitstreams_from_bundle(bundle):
            name = str(bitstream.get("name") or "").lower()
            content_href = ((bitstream.get("_links") or {}).get("content") or {}).get("href")
            if content_href and name.endswith(".pdf"):
                return str(content_href)
    return None


def resolve_dspace_item_primary_pdf_url(item_url: str, *, timeout: float = 20.0) -> Optional[str]:
    """Resolve a DSpace/Open Knowledge ``/items/{uuid}`` page to its primary PDF content URL.

    Fast HTTP shells for these pages often omit ``citation_pdf_url``. The public
    REST API still exposes the ORIGINAL bitstream content endpoint.
    """
    match = _DSPACE_ITEM_RE.match((item_url or "").strip())
    if not match:
        return None
    api_url = f"{match.group(1)}/server/api/core/items/{match.group(2)}?embed=bundles/bitstreams"
    try:
        request = Request(
            api_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
            },
        )
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = getattr(response, "status", 200)
            log_fetch(api_url, status=status, via="api", bytes_=len(raw))
            payload = json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:
        log_fetch(api_url, status="error", via="api", error=f"{type(exc).__name__}: {exc}")
        logger.debug("[dspace] item API lookup failed for %s: %s", item_url, exc)
        return None
    if not isinstance(payload, dict):
        return None
    return primary_pdf_url_from_dspace_item_payload(payload)


def resolve_primary_pdf_url(url: str, html: str = "") -> Optional[str]:
    """Best-effort primary PDF URL for a page: citation meta, then DSpace item API."""
    citation_pdfs = extract_citation_pdf_urls(html)
    if citation_pdfs:
        return citation_pdfs[0]
    return resolve_dspace_item_primary_pdf_url(url)


def looks_like_document_link(url: str) -> bool:
    """Return True when a URL likely points at a downloadable document."""
    lower = url.lower()
    path = lower.split("?", 1)[0].split("#", 1)[0]
    if path.endswith(DOC_EXTENSIONS):
        return True
    return any(marker in lower for marker in _DOCUMENT_LINK_MARKERS)


def detect_document_extension(url: str, content_disposition: str = "") -> str:
    url_path = url.lower().split("?", 1)[0].split("#", 1)[0]
    for ext in DOC_EXTENSIONS:
        if url_path.endswith(ext):
            return ext
    filename = filename_from_content_disposition(content_disposition).lower()
    for ext in DOC_EXTENSIONS:
        if filename.endswith(ext):
            return ext
    return ""


def unsupported_legacy_document_reason(url: str, content_type: str = "", content_disposition: str = "") -> str:
    ct = normalize_content_type(content_type)
    ext = detect_document_extension(url, content_disposition)
    if ext in SUPPORTED_DOC_EXTENSIONS:
        return ""
    if ext in UNSUPPORTED_LEGACY_DOC_EXTENSIONS:
        return f"unsupported legacy Office document format ({ext})"
    if ct == "application/msword":
        return "unsupported legacy Office document format (.doc)"
    if ct.startswith("application/vnd.ms-"):
        suffix = f" ({ext})" if ext in UNSUPPORTED_LEGACY_DOC_EXTENSIONS else f" ({ct})"
        return f"unsupported legacy Office document format{suffix}"
    return ""


def truncate_content(content: str, max_chars: int) -> str:
    """Clip content to max_chars and append a truncation notice."""
    if len(content) > max_chars:
        return content[:max_chars] + f"\n\n[Truncated at {max_chars:,} chars]"
    return content


def trim_json_value(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int = 4,
    max_items: int = 20,
    max_string_chars: int = 500,
) -> Any:
    if depth >= max_depth:
        if isinstance(value, list):
            return f"[list truncated: {len(value)} items]"
        if isinstance(value, dict):
            return f"{{object truncated: {len(value)} keys}}"
        return value

    if isinstance(value, dict):
        items = list(value.items())
        trimmed = {
            str(k): trim_json_value(
                v,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_string_chars=max_string_chars,
            )
            for k, v in items[:max_items]
        }
        if len(items) > max_items:
            trimmed["..."] = f"{len(items) - max_items} more keys omitted"
        return trimmed

    if isinstance(value, list):
        trimmed = [
            trim_json_value(
                v,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_string_chars=max_string_chars,
            )
            for v in value[:max_items]
        ]
        if len(value) > max_items:
            trimmed.append(f"... {len(value) - max_items} more items omitted")
        return trimmed

    if isinstance(value, str) and len(value) > max_string_chars:
        return value[:max_string_chars] + f"... [truncated, original length {len(value)}]"

    return value
