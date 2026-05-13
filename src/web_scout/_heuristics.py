"""Private heuristics and thresholds for low-level pipeline behavior."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx


@dataclass(frozen=True)
class RoutingHeuristics:
    min_pdf_text_chars: int = 300
    pdf_max_pages_default: int = 50
    validation_timeout: httpx.Timeout = field(
        default_factory=lambda: httpx.Timeout(20.0, connect=15.0)
    )
    document_download_timeout: httpx.Timeout = field(
        default_factory=lambda: httpx.Timeout(45.0, connect=20.0)
    )
    urllib_download_timeout: int = 45
    pdf_download_retries: int = 2
    short_html_text_chars: int = 150
    short_html_size_chars: int = 8000
    short_html_spa_script_count: int = 2
    low_text_spa_chars: int = 300
    low_text_spa_script_count: int = 3
    heavy_spa_script_count: int = 15
    heavy_spa_text_density: float = 0.06
    soft_404_text_chars: int = 1000
    html_fast_thin_content_chars: int = 200
    bm25_threshold: float = 1.0
    browser_page_timeout_ms: int = 45_000
    browser_delay_before_return_html_s: float = 1.0
    vision_goto_timeout_ms: int = 45_000
    vision_settle_wait_ms: int = 8000
    browser_download_timeout_ms: int = 60_000
    image_json_timeout_s: int = 20


@dataclass(frozen=True)
class ExtractorHeuristics:
    thin_content_chars: int = 500
    max_interactive_clicks: int = 5
    interactive_page_goto_timeout_ms: int = 30_000
    interactive_wait_timeout_ms: int = 3_000
    form_token_min_repeat: int = 2
    nav_dump_min_lines: int = 20
    nav_dump_bullet_ratio: float = 0.75
    failure_short_content_chars: int = 400
    max_rendered_relevant_links: int = 15


@dataclass(frozen=True)
class FollowupHeuristics:
    shortlist_multiplier: int = 3
    max_keyword_overlap_bonus_terms: int = 3
    paginated_index_penalty: int = -12
    document_bonus: int = 3
    report_bonus: int = 6
    publication_bonus: int = 4
    detail_token_bonus: int = 3
    negative_token_penalty: int = -10
    negative_document_penalty: int = -6
    generic_segment_penalty: int = -8
    list_segment_penalty: int = -8
    data_portal_bonus_for_data_query: int = 5
    data_portal_penalty_for_non_data_query: int = -6
    report_query_bonus: int = 4
    keyword_overlap_bonus: int = 2
    identifier_detail_bonus: int = 2
    kenya_bonus: int = 2
    shallow_page_min_score: int = 3


ROUTING_HEURISTICS = RoutingHeuristics()
EXTRACTOR_HEURISTICS = ExtractorHeuristics()
FOLLOWUP_HEURISTICS = FollowupHeuristics()


__all__ = [
    "EXTRACTOR_HEURISTICS",
    "FOLLOWUP_HEURISTICS",
    "ROUTING_HEURISTICS",
]
