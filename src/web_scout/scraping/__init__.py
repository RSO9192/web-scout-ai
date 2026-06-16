"""Unified web scraping via Scrapling, docling, and vision fallbacks.

Public API summary
------------------
``scrape_url``
    One-shot scrape: validates → routes → extracts → truncates.

``build_scrape_plan``
    Cheap GET routing (via Scrapling AsyncFetcher) without running any extractor.

``execute_strategy``
    Execute a pre-built ``ScrapePlan`` and return a ``SourceArtifact``.

``fetch_query_agnostic_source_artifact`` / ``materialize_source_artifact``
    Two-phase cache API: fetch once, reuse across queries.

Types: ``ScrapePlan``, ``ScrapeStrategy``, ``SourceArtifact``

Classifiers: ``PageShapeAssessment``, ``classify_html_page_shape``,
             ``classify_prefetched_page_shape``
"""

from ._scrape_url import MAX_CONTENT_CHARS, scrape_url
from .executor import (
    execute_strategy,
    fetch_query_agnostic_source_artifact,
    materialize_source_artifact,
    scrape_document,
)
from .page_classifier import PageShapeAssessment, classify_html_page_shape, classify_prefetched_page_shape
from .plan import build_scrape_plan
from .types import ScrapePlan, ScrapeStrategy, SourceArtifact

__all__ = [
    "MAX_CONTENT_CHARS",
    "scrape_url",
    "build_scrape_plan",
    "execute_strategy",
    "scrape_document",
    "fetch_query_agnostic_source_artifact",
    "materialize_source_artifact",
    "ScrapePlan",
    "ScrapeStrategy",
    "SourceArtifact",
    "PageShapeAssessment",
    "classify_html_page_shape",
    "classify_prefetched_page_shape",
]
