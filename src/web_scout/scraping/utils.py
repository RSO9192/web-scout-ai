import json
import re
from email.message import Message
from typing import Any, Optional
from urllib.parse import urlparse

from .constants import (
    BLOCKED_DOMAINS,
    DOC_EXTENSIONS,
    SUPPORTED_DOC_CONTENT_TYPES,
    SUPPORTED_DOC_EXTENSIONS,
    UNSUPPORTED_LEGACY_DOC_EXTENSIONS,
)


def is_blocked_domain(url: str, allowed_domains: Optional[frozenset] = None) -> bool:
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    effective_blocked = BLOCKED_DOMAINS
    if allowed_domains:
        effective_blocked = BLOCKED_DOMAINS - allowed_domains
    return any(netloc == d or netloc.endswith("." + d) for d in effective_blocked)


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
    if payload[:4] == b"%PDF":
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
