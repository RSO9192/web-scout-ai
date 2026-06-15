"""tools — agent tool factories and research state tracker.

Public API
----------
ResearchTracker               — accumulates URL/query records from tool calls
create_web_search()           — URL discovery via pluggable search backend
create_scrape_and_extract_tool() — scrapes a URL via a dedicated sub-agent

The rendering helpers below are re-exported for pipeline consumers that parse
the legacy rendered-text contract produced by the scrape-and-extract layer.
"""

from .rendering import (
    extract_explicit_rendered_followup_links,
    extract_rendered_followup_links,
    is_rendered_list_page,
    resolve_scrape_outcome,
)
from .scraper import create_scrape_and_extract_tool
from .search import create_web_search
from .tracker import ResearchTracker

__all__ = [
    "ResearchTracker",
    "create_web_search",
    "create_scrape_and_extract_tool",
    "is_rendered_list_page",
    "extract_rendered_followup_links",
    "extract_explicit_rendered_followup_links",
    "resolve_scrape_outcome",
]
