"""Private types shared by the web research pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from ._extractor_contract import ExtractorOutcome

DEFAULT_WEB_RESEARCH_MODELS = {
    "web_researcher": "gemini/gemini-3-flash-preview",
    "content_extractor": "gemini/gemini-3-flash-preview",
    "vision_fallback": "gemini/gemini-3-flash-preview",
    "followup_selector": "gemini/gemini-3-flash-preview",
}


class SearchQueryGeneration(BaseModel):
    """LLM output for generating diverse search queries."""

    queries: list[str] = Field(description="List of search queries")


class CoverageEvaluation(BaseModel):
    """LLM output for evaluating coverage and routing the next pipeline step."""

    fully_answered: bool = Field(
        description="True if the extracted content fully answers the original research query."
    )
    gaps: str = Field(
        description="If not fully answered, what specific information is still missing?"
    )
    promising_unscraped_urls: list[str] = Field(
        default_factory=list,
        description=(
            "If not fully answered, list exact URLs from the Unscraped Candidates "
            "that likely contain the missing information. Leave empty if none are promising."
        ),
    )
    needs_new_searches: bool = Field(
        default=True,
        description=(
            "True if the unscraped candidates are insufficient and new web searches "
            "must be run. False if promising_unscraped_urls candidates are enough to try first."
        ),
    )


class FollowupSelection(BaseModel):
    """LLM output for selecting the best follow-up URLs from a same-domain set."""

    selected_urls: list[str] = Field(
        default_factory=list,
        description="Exact URLs chosen from the provided candidate list only, ordered best-first.",
    )


@dataclass
class SearchLoopState:
    needs_new_searches: bool = True
    promising_urls_from_evaluator: list[str] = field(default_factory=list)
    missing_info: str = ""


@dataclass
class SearchIterationResult:
    extracted_contents: list[str]
    iter_results: list[tuple[str, str]]
    outcomes_by_url: dict[str, ExtractorOutcome] = field(default_factory=dict)


__all__ = [
    "DEFAULT_WEB_RESEARCH_MODELS",
    "CoverageEvaluation",
    "FollowupSelection",
    "SearchIterationResult",
    "SearchLoopState",
    "SearchQueryGeneration",
]
