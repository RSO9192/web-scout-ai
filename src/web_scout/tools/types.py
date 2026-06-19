"""Shared data types for the tools package."""

from dataclasses import dataclass
from typing import List, Literal

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class SourceCacheKey:
    """Stable cache key for a query-agnostic source artifact."""

    url: str
    wait_for: str = ""
    max_pdf_pages: int = 0


@dataclass(frozen=True)
class CachedSourceArtifact:
    """Query-agnostic source artifact stored for the current Python process."""

    url: str
    title: str
    artifact_kind: Literal["text", "binary"]
    text_content: str = ""
    binary_bytes: bytes = b""
    mime_type: str = ""


class ExtractorOutput(BaseModel):
    """Structured output from the content extractor sub-agent."""

    title: str = Field(
        default="",
        description="Title of the page or document.",
    )
    relevant_content: str = Field(
        description=(
            "Comprehensive extraction from the page that directly answers the research query. "
            "Include ALL specific facts, numbers, dates, regulations, quotes, species names, location names, and detailed context. "
            "Do NOT summarize what the page is about; explicitly extract the actual data and facts from the page. "
            "If the page is an article or report, extract the specific findings, not just a table of contents or structural overview. "
            "Exclude boilerplate, navigation, ads, and completely off-topic content. "
            "Maximum 5,000 characters."
        )
    )
    page_type: Literal["list", "content"] = Field(
        default="content",
        description=(
            'Set to "list" if this page is a database view, search results page, '
            "index, or any page whose primary purpose is listing many items with links "
            'to detail pages. Set to "content" for articles, reports, and detail pages.'
        ),
    )
    relevant_links: List[str] = Field(
        default_factory=list,
        description=(
            "Up to 15 absolute URLs found in the page that are highly likely to contain "
            "additional specific information for the research query. "
            "If page_type is 'list', treat each visible item's detail-page link as a candidate "
            "and rank by relevance to the query. Return up to 15."
        ),
    )
