"""Agentic web research — smarter than search, faster than deep research.

Uses pluggable search backends (Serper, and community-contributed backends). A dedicated
content extractor sub-agent (Scrapling / Docling) scrapes and summarises
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
- ``fetch(url, ...)`` — fetch one URL and return a ``FetchResult``
- ``fetch_pdf(url, ...)`` — fetch one URL and return PDF bytes
- ``FetchResult`` — raw fetch payload (``body`` bytes for PDFs, ``html_content`` for pages)
- ``PDF_MAGIC_BYTES`` — ``b"%PDF"`` header used to detect a PDF body
- ``WebResearchResult``, ``WebResearchResultRaw``, etc. — output models
- ``ResearchTracker`` — URL/query bookkeeping
"""

__version__ = "1.9.1"

import logging as _logging
import os as _os

# Avoid LiteLLM's import-time network probe and fallback warning.
_os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")

# Suppress import-time WARNING noise from LiteLLM (missing botocore etc.)
# before litellm is pulled in transitively by .agent.
_logging.getLogger("LiteLLM").setLevel(_logging.ERROR)
_logging.getLogger("litellm").setLevel(_logging.ERROR)
# openai-agents traces export to OpenAI by default; without OPENAI_API_KEY this
# emits a noisy WARNING on every run. Disable tracing and silence the logger.
_logging.getLogger("agents.tracing.processors").setLevel(_logging.ERROR)


def _configure_third_party_runtime() -> None:
    """Silence third-party progress bars and debug spam that leak into CLI output."""
    # Docling pulls in Hugging Face / Transformers models whose first-load path
    # emits tqdm bars like "Loading weights: 100%|...|". Treat those as noise.
    _os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    try:
        from transformers.utils import logging as _transformers_logging
    except Exception:
        pass
    else:
        try:
            _transformers_logging.disable_progress_bar()
        except Exception:
            pass

    try:
        from agents import set_tracing_disabled

        set_tracing_disabled(True)
    except Exception:
        pass

    try:
        import litellm

        # Prevents the printed "Give Feedback / Get Help" + turn_on_debug banner
        # on mapped provider exceptions.
        litellm.suppress_debug_info = True
    except Exception:
        pass


_configure_third_party_runtime()


def configure_logging(level: int = _logging.INFO) -> None:
    """Configure clean logging for web_scout.

    Call this once at application startup to get structured log output from
    the ``web_scout.*`` loggers with timestamps and level names. Safe to call
    repeatedly; handlers are only attached once.

    Third-party loggers (httpx, litellm, docling) are kept at
    WARNING regardless of the requested level.

    Args:
        level: Log level for ``web_scout.*`` loggers (default ``INFO``).
    """
    pkg_logger = _logging.getLogger("web_scout")
    pkg_logger.setLevel(level)
    if not pkg_logger.handlers:
        handler = _logging.StreamHandler()
        # Match scrapling's shell-friendly format so notebook / CLI output lines up.
        handler.setFormatter(
            _logging.Formatter(
                fmt="[%(asctime)s] %(levelname)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        pkg_logger.addHandler(handler)
    pkg_logger.propagate = False


from .agent import (  # noqa: E402
    DEFAULT_WEB_RESEARCH_MODELS,
    run_web_research,
)
from .models import (  # noqa: E402
    SearchQuery,
    UrlEntry,
    WebResearchResult,
    WebResearchResultRaw,
)
from .scraping._fetcher import fetch, fetch_pdf  # noqa: E402
from .scraping.constants import PDF_MAGIC_BYTES, RECOMMENDED_EXCLUDE_DOMAINS  # noqa: E402
from .scraping.types import FetchResult  # noqa: E402
from .tools import ResearchTracker  # noqa: E402

__all__ = [
    "__version__",
    "configure_logging",
    "DEFAULT_WEB_RESEARCH_MODELS",
    "fetch",
    "fetch_pdf",
    "FetchResult",
    "PDF_MAGIC_BYTES",
    "RECOMMENDED_EXCLUDE_DOMAINS",
    "run_web_research",
    "ResearchTracker",
    "SearchQuery",
    "UrlEntry",
    "WebResearchResult",
    "WebResearchResultRaw",
]
