from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True)
class SourceArtifact:
    """Query-agnostic source artifact that can be reused across queries."""

    kind: Literal["text", "binary"]
    title: str
    text_content: str = ""
    binary_bytes: bytes = b""
    mime_type: str = ""


class FetchResult(BaseModel):
    """Raw result from the Fetcher — a single network round-trip."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    url: str
    status: int
    content_type: str           # normalised (no params, lowercase)
    content_disposition: str
    html_content: str | None    # None for binary responses (PDFs, images)
    body: bytes | None          # None for text responses; raw bytes for PDFs, images
    headers: dict[str, str]
    used_browser: bool
    page: Any = None            # Scrapling page object (for CSS selector access in Parser)
    error: str | None = None    # set when fetch failed or URL was blocked before network call


class ParseResult(BaseModel):
    """Result of parsing a FetchResult into structured text content."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    url: str
    title: str
    text_content: str
    links: list[str]            # absolute URLs extracted from the page
    artifact: SourceArtifact    # full artifact for vision/binary use downstream
    raw_html: str | None = None # original HTML from Fetcher (for Crawler; avoids re-fetch)
    error: str | None = None
