"""Shared data types for the tools package."""

from dataclasses import dataclass
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from web_scout.scraping._data_sources import DashboardKind, DataSource
from web_scout.scraping.types import PdfDocumentLayout


@dataclass(frozen=True)
class SourceCacheKey:
    """Stable cache key for a query-agnostic source artifact."""

    url: str
    wait_for: str = ""
    max_pdf_pages: int = 0


@dataclass(frozen=True)
class CachedSourceArtifact:
    """Query-agnostic source artifact persisted by the URL cache."""

    url: str
    title: str
    artifact_kind: Literal["text", "binary"]
    text_content: str = ""
    binary_bytes: bytes = b""
    mime_type: str = ""
    layout: Optional[PdfDocumentLayout] = None
    raw_html: str = ""
    error: str = ""
    # Defaults keep artifacts pickled before dashboard capture loadable.
    dashboard_kind: DashboardKind = ""
    data_sources: tuple[DataSource, ...] = ()


class DataQuote(BaseModel):
    """A fact the extractor took from one of the page's data files, citable as that file."""

    source_id: str = Field(
        description='ID of the data source the fact came from, as listed in the catalog (e.g. "D2").'
    )
    quote: str = Field(
        description=(
            "The fact with its figures exactly as stored in that data source, including the labels, "
            "period and units needed to read it."
        )
    )


class ExtractorOutput(BaseModel):
    """Structured output from the content extractor sub-agent."""

    title: str = Field(
        default="",
        description="Title of the page or document.",
    )
    has_evidence: bool = Field(
        default=True,
        description=(
            "False when the page contains no facts that answer the research query. "
            "When the query lists research requirements, true if the page satisfies at least one of them. "
            "Statements about where the answer might be found, or descriptions of "
            "what the page links to, are NOT evidence. relevant_links may still be "
            "returned when has_evidence is false."
        ),
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
    data_quotes: List[DataQuote] = Field(
        default_factory=list,
        description=(
            "Facts taken from the page's data sources via open_data_source, one per fact, each naming "
            "the source_id it came from. Leave empty when the page lists no data sources."
        ),
    )
