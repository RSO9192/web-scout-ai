"""Private typed outcomes for the scrape-and-extract layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ExtractorStatus = Literal["success", "failure"]
ExtractorFailureKind = Literal[
    "bot_detected",
    "blocked_by_policy",
    "source_http_error",
    "scraped_irrelevant",
    "scrape_failed",
    "subagent_failed",
]
ExtractorPageType = Literal["list", "content"]


@dataclass(frozen=True)
class ExtractorOutcome:
    url: str
    status: ExtractorStatus
    rendered_text: str
    title: str = ""
    content: str = ""
    page_type: ExtractorPageType = "content"
    relevant_links: list[str] = field(default_factory=list)
    failure_kind: ExtractorFailureKind | None = None


__all__ = ["ExtractorOutcome"]
