"""Pydantic output models for the web researcher agent (new)."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    """A search query that was executed."""

    query: str
    num_results_returned: int
    domains_restricted: List[str] = Field(default_factory=list)


class UrlEntry(BaseModel):
    """A single URL with its content.

    The meaning of ``content`` depends on the group it belongs to:

    - **scraped**: comprehensive detailed excerpt extracted by the content extractor sub-agent
    - **snippet_only**: search snippet from the search backend
    """

    url: str
    title: str = ""
    content: str = ""


class WebResearchResultRaw(BaseModel):
    """LLM output type for the main researcher agent.

    Extraction of per-URL content is handled by the ``scrape_and_extract``
    sub-agent tool — the main agent does not need to reproduce it.
    The main agent only produces research notes summarising its process.
    """

    synthesis: str = Field(
        default="",
        description=(
            "A coherent narrative synthesizing the findings to answer the research query. "
            "Highlight key facts, mention data gaps, and note any contradictions or issues "
            "with the search results."
        ),
    )


class WebResearchResult(BaseModel):
    """Final output: URLs grouped by action + query metadata.

    Assembled in post-processing from the ``ResearchTracker`` data
    (populated by tool calls) and the main agent's ``research_notes``.
    """

    scraped: List[UrlEntry] = Field(
        default_factory=list,
        description="URLs scraped. content = comprehensive detailed excerpt from extractor sub-agent.",
    )
    scrape_failed: List[UrlEntry] = Field(
        default_factory=list,
        description="URLs where scraping was attempted but failed. content = error.",
    )
    bot_detected: List[UrlEntry] = Field(
        default_factory=list,
        description="URLs blocked by bot-protection (Akamai, Cloudflare, etc.). content = error.",
    )
    snippet_only: List[UrlEntry] = Field(
        default_factory=list,
        description="URLs from search results, not scraped. content = search snippet.",
    )
    queries: List[SearchQuery] = Field(
        default_factory=list,
        description="All search queries executed during research.",
    )
    synthesis: str = Field(
        default="",
        description="Main agent's synthesized answer and narrative about the research process.",
    )
