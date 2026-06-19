"""Scraping package — Fetcher → Parser → Crawler pipeline.

Public API
----------
Orchestrator / config::

    Orchestrator, OrchestratorConfig

Components (ABCs + default implementations)::

    Fetcher, ScraplingFetcher
    Parser, DefaultParser
    Crawler, Crawl4AICrawler

Per-URL context::

    URLContext

Data types::

    FetchResult, ParseResult, SourceArtifact

Page-shape classifiers (used by tools and tests)::

    PageShapeAssessment, classify_html_page_shape, classify_prefetched_page_shape

Utilities::

    fetch_and_parse_url      — fetch + parse a single URL (ScraplingFetcher + DefaultParser)
    materialize_parse_result — convert ParseResult → (text, error)
"""

from ._crawler import Crawl4AICrawler, Crawler
from ._fetch_parse import fetch_and_parse_url
from ._fetcher import Fetcher, ScraplingFetcher
from ._parser import DefaultParser, Parser, materialize_parse_result
from .context import URLContext
from .orchestrator import Orchestrator, OrchestratorConfig
from .page_classifier import PageShapeAssessment, classify_html_page_shape, classify_prefetched_page_shape
from .types import FetchResult, ParseResult, SourceArtifact

__all__ = [
    # Orchestrator
    "Orchestrator",
    "OrchestratorConfig",
    # Components
    "Fetcher",
    "ScraplingFetcher",
    "Parser",
    "DefaultParser",
    "Crawler",
    "Crawl4AICrawler",
    # Context
    "URLContext",
    # Data types
    "FetchResult",
    "ParseResult",
    "SourceArtifact",
    # Classifiers
    "PageShapeAssessment",
    "classify_html_page_shape",
    "classify_prefetched_page_shape",
    # Utilities
    "fetch_and_parse_url",
    "materialize_parse_result",
]
