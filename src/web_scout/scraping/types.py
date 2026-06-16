from dataclasses import dataclass
from enum import Enum
from typing import Literal


class ScrapeStrategy(str, Enum):
    """Normalized scrape strategies chosen during URL routing."""

    SKIP = "SKIP"
    HTML_FAST = "SCRAPE_HTML"
    HTML_BROWSER = "SCRAPE_JS"
    DOCUMENT = "SCRAPE_DOC"
    JSON = "SCRAPE_JSON"
    IMAGE = "SCRAPE_IMAGE"


@dataclass(frozen=True)
class SourceArtifact:
    """Query-agnostic source artifact that can be reused across queries."""

    kind: Literal["text", "binary"]
    title: str
    text_content: str = ""
    binary_bytes: bytes = b""
    mime_type: str = ""


@dataclass(frozen=True)
class ScrapePlan:
    """Routing plan produced by URL validation before executing a scraper."""

    strategy: ScrapeStrategy
    reason: str
    content_type: str = ""
    content_disposition: str = ""
    needs_browser: bool = False
    """True when the browser had to be used during planning (e.g. CloudFlare bypass).
    The executor should skip the fast HTTP path and go straight to the browser."""

    @property
    def likely_bot_detected(self) -> bool:
        return self.needs_browser or (self.strategy == ScrapeStrategy.HTML_BROWSER and "GET timed out" in self.reason)
