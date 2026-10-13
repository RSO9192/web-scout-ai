"""Private types shared by the web research pipeline."""

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ._extractor_contract import ExtractorOutcome

DEFAULT_WEB_RESEARCH_MODELS = {
    "web_researcher": "bedrock_mantle/openai.gpt-6-luna",
    "content_extractor": "bedrock_mantle/openai.gpt-6-luna",
    "followup_selector": "bedrock_mantle/openai.gpt-6-luna",
    "vision_fallback": "gemini/gemini-3.7-flash",
}

# Backend that chooses which follow-up links to open. "gpt" (the default) uses
# the Agents SDK model named in models["followup_selector"]; "jev" uses Jev.
DEFAULT_FOLLOWUP_BACKEND = "gpt"
FOLLOWUP_BACKENDS = frozenset({"jev", "gpt"})


class PlannedSearchQuery(BaseModel):
    """One backend-neutral search request produced by the query planner."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(description="Natural search query written in the language identified by language_code")
    country_name: str | None = Field(
        default=None,
        description="Canonical English country name for single-country localization, or null for other searches",
    )
    language_code: str | None = Field(
        default=None,
        description="Two-letter language code describing the query language, or null when unknown",
    )


class SearchQueryGeneration(BaseModel):
    """LLM output for generating diverse search queries."""

    queries: list[PlannedSearchQuery] = Field(description="List of localized search queries")

    @field_validator("queries", mode="before")
    @classmethod
    def _accept_legacy_query_strings(cls, value):
        """Keep programmatic callers using the former list[str] shape working."""
        if isinstance(value, list):
            return [{"query": item} if isinstance(item, str) else item for item in value]
        return value


class SearchQueryChecklist(SearchQueryGeneration):
    """Query generation with an explicit evidence checklist for Jev coverage."""

    requirements: list[str] = Field(
        description=(
            "Atomic facts needed to answer the original query; preserve geography, dates, "
            "requested numbers and explicit coverage criteria."
        )
    )


class CoverageEvaluation(BaseModel):
    """LLM output for evaluating coverage and routing the next pipeline step."""

    fully_answered: bool = Field(description="True if the extracted content fully answers the original research query.")
    gaps: str = Field(description="If not fully answered, what specific information is still missing?")
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
    requirements: list[str] = field(default_factory=list)


@dataclass
class SearchIterationResult:
    extracted_contents: list[str]
    iter_results: list[tuple[str, str]]
    outcomes_by_url: dict[str, ExtractorOutcome] = field(default_factory=dict)


__all__ = [
    "DEFAULT_FOLLOWUP_BACKEND",
    "DEFAULT_WEB_RESEARCH_MODELS",
    "FOLLOWUP_BACKENDS",
    "CoverageEvaluation",
    "FollowupSelection",
    "PlannedSearchQuery",
    "SearchIterationResult",
    "SearchLoopState",
    "SearchQueryGeneration",
]
