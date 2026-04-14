"""Agentic web research — smarter than search, faster than deep research.

Uses pluggable search backends (Serper / DuckDuckGo). A dedicated
content extractor sub-agent (crawl4ai / docling) scrapes and summarises
each URL so the main researcher only sees focused excerpts.

The pipeline generates search queries, evaluates coverage, iterates if
needed, and produces a synthesised answer with full source attribution.

Three input modes (all share a single unified pipeline):

1. **Query-only** — open web search + extract promising URLs.
2. **Domain + query** — domain-restricted search + extraction.
3. **Direct URL** — extract a given URL directly (search skipped).

Supports any LLM provider via LiteLLM (OpenAI, Anthropic, Google,
Mistral, local models, etc.).

Quick start::

    from web_scout import run_web_research

    result = await run_web_research(
        query="What are the main threats to coral reefs?",
        models={
            "web_researcher": "openai/gpt-4o",
            "content_extractor": "gemini/gemini-2.0-flash",
        },
    )
    print(result.synthesis)

Public API
----------
- ``run_web_research(query, models, ...)`` — full pipeline
- ``WebResearchResult``, ``WebResearchResultRaw``, etc. — output models
- ``ResearchTracker`` — URL/query bookkeeping
"""

__version__ = "1.0.0"

import logging as _logging


def configure_logging(level: int = _logging.INFO) -> None:
    """Configure clean logging for web_scout.

    Call this once at application startup to get structured log output from
    the ``web_scout.*`` loggers with timestamps and level names.

    Third-party loggers (httpx, crawl4ai, litellm, docling) are kept at
    WARNING regardless of the requested level.

    Args:
        level: Log level for ``web_scout.*`` loggers (default ``INFO``).
    """
    handler = _logging.StreamHandler()
    handler.setFormatter(
        _logging.Formatter(
            fmt="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    pkg_logger = _logging.getLogger("web_scout")
    pkg_logger.setLevel(level)
    if not pkg_logger.handlers:
        pkg_logger.addHandler(handler)
    pkg_logger.propagate = False


from .agent import (
    DEFAULT_WEB_RESEARCH_MODELS,
    run_web_research,
)
from .models import (
    SearchQuery,
    UrlEntry,
    WebResearchResult,
    WebResearchResultRaw,
)
from .tools import ResearchTracker

__all__ = [
    "__version__",
    "configure_logging",
    "DEFAULT_WEB_RESEARCH_MODELS",
    "run_web_research",
    "ResearchTracker",
    "SearchQuery",
    "UrlEntry",
    "WebResearchResult",
    "WebResearchResultRaw",
]
