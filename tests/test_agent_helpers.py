"""Tests for pure helper functions in agent.py."""

from web_scout.agent import (
    _build_citation_slots,
    _extract_links_from_markdown,
    _extract_query_keywords,
    _is_promising_followup_url,
    _is_same_domain,
    _looks_like_document_url,
    _looks_like_paginated_index_page,
    _query_prefers_data_pages,
    _query_prefers_report_pages,
    _resolve_slot_citations,
    _score_followup_candidate,
)

# ---------------------------------------------------------------------------
# _build_citation_slots / _resolve_slot_citations
# ---------------------------------------------------------------------------


def _entry(url, title="", reference=""):
    from web_scout.models import UrlEntry

    return UrlEntry(url=url, title=title, reference=reference, content="x")


def test_build_citation_slots_assigns_positional_ids():
    slots = _build_citation_slots([_entry("https://a.org", title="A"), _entry("https://b.org", title="B")])
    assert slots == {"S1": ("A", "https://a.org"), "S2": ("B", "https://b.org")}


def test_build_citation_slots_prefers_reference_over_title():
    slots = _build_citation_slots([_entry("https://a.org/r.pdf", title="A", reference="Crop Prospects, pp. 3–7")])
    assert slots["S1"] == ("Crop Prospects, pp. 3–7", "https://a.org/r.pdf")


def test_build_citation_slots_falls_back_to_url():
    slots = _build_citation_slots([_entry("https://a.org/page")])
    assert slots["S1"] == ("https://a.org/page", "https://a.org/page")


def test_resolve_slot_citations_single_id():
    slots = {"S1": ("FAO Report", "https://fao.org/report")}
    resolved, unknown = _resolve_slot_citations("Production rose 4% [S1].", slots)
    assert resolved == "Production rose 4% [FAO Report](https://fao.org/report)."
    assert unknown == []


def test_resolve_slot_citations_multiple_ids_in_one_bracket():
    slots = {
        "S1": ("FAO", "https://fao.org/report"),
        "S3": ("WB", "https://worldbank.org/data"),
    }
    resolved, unknown = _resolve_slot_citations("Fact [S1, S3].", slots)
    assert resolved == "Fact [FAO](https://fao.org/report), [WB](https://worldbank.org/data)."
    assert unknown == []


def test_resolve_slot_citations_unknown_id_left_as_text_and_reported():
    slots = {"S1": ("FAO", "https://fao.org/report")}
    resolved, unknown = _resolve_slot_citations("Fact [S9].", slots)
    assert resolved == "Fact S9."
    assert unknown == ["S9"]


def test_resolve_slot_citations_mixed_known_and_unknown():
    slots = {"S1": ("FAO", "https://fao.org/report")}
    resolved, unknown = _resolve_slot_citations("Fact [S1, S9].", slots)
    assert resolved == "Fact [FAO](https://fao.org/report), S9."
    assert unknown == ["S9"]


def test_resolve_slot_citations_drops_model_attached_url():
    """If the model wrongly writes [S1](url), the invented URL is discarded."""
    slots = {"S1": ("FAO", "https://fao.org/report")}
    resolved, unknown = _resolve_slot_citations("Fact [S1](https://invented.example.com).", slots)
    assert resolved == "Fact [FAO](https://fao.org/report)."
    assert unknown == []


def test_resolve_slot_citations_leaves_non_slot_brackets_untouched():
    slots = {"S1": ("FAO", "https://fao.org/report")}
    resolved, unknown = _resolve_slot_citations("In [2023] output fell [see note].", slots)
    assert resolved == "In [2023] output fell [see note]."
    assert unknown == []


def test_resolve_slot_citations_deduplicates_unknown_ids():
    resolved, unknown = _resolve_slot_citations("Fact [S9]. Other fact [S9].", {})
    assert unknown == ["S9"]


# ---------------------------------------------------------------------------
# _is_same_domain
# ---------------------------------------------------------------------------


def test_is_same_domain_exact_match():
    assert _is_same_domain("https://fao.org/path", "fao.org") is True


def test_is_same_domain_subdomain_match():
    assert _is_same_domain("https://data.fao.org/portal", "fao.org") is True


def test_is_same_domain_www_prefix_stripped():
    assert _is_same_domain("https://www.fao.org/page", "fao.org") is True


def test_is_same_domain_different_domain_rejected():
    assert _is_same_domain("https://worldbank.org/page", "fao.org") is False


def test_is_same_domain_empty_host_rejected():
    assert _is_same_domain("not-a-url", "fao.org") is False


# ---------------------------------------------------------------------------
# _looks_like_document_url
# ---------------------------------------------------------------------------


def test_looks_like_document_url_pdf():
    assert _looks_like_document_url("https://fao.org/files/report.pdf") is True


def test_looks_like_document_url_docx():
    assert _looks_like_document_url("https://example.com/doc.docx") is True


def test_looks_like_document_url_xlsx():
    assert _looks_like_document_url("https://example.com/data.xlsx") is True


def test_looks_like_document_url_legacy_doc_is_false():
    assert _looks_like_document_url("https://example.com/legacy.doc") is False


def test_looks_like_document_url_html_is_false():
    assert _looks_like_document_url("https://fao.org/page.html") is False


def test_looks_like_document_url_no_extension():
    assert _looks_like_document_url("https://fao.org/report") is False


# ---------------------------------------------------------------------------
# _looks_like_paginated_index_page
# ---------------------------------------------------------------------------


def test_paginated_index_page_detected_with_list_segment_and_page_param():
    url = "https://fao.org/publications?page=2"
    assert _looks_like_paginated_index_page(url) is True


def test_paginated_index_page_detected_with_search_and_offset():
    url = "https://fao.org/search?offset=20"
    assert _looks_like_paginated_index_page(url) is True


def test_paginated_index_page_false_for_detail_page_without_list_segment():
    url = "https://fao.org/report/2024-fish-assessment"
    assert _looks_like_paginated_index_page(url) is False


def test_paginated_index_page_false_for_list_segment_without_pagination_param():
    url = "https://fao.org/publications"
    assert _looks_like_paginated_index_page(url) is False


# ---------------------------------------------------------------------------
# _query_prefers_data_pages / _query_prefers_report_pages
# ---------------------------------------------------------------------------


def test_query_prefers_data_pages_on_dataset_keyword():
    assert _query_prefers_data_pages("download the dataset for fish catch") is True


def test_query_prefers_data_pages_on_api_keyword():
    assert _query_prefers_data_pages("access fish catch via API") is True


def test_query_prefers_data_pages_false_for_report_query():
    assert _query_prefers_data_pages("fish production trend assessment 2023") is False


def test_query_prefers_report_pages_on_trend():
    assert _query_prefers_report_pages("fish production trend in East Africa") is True


def test_query_prefers_report_pages_on_assessment():
    assert _query_prefers_report_pages("climate assessment of fisheries") is True


def test_query_prefers_report_pages_false_for_data_query():
    assert _query_prefers_report_pages("download fish catch csv timeseries") is False


# ---------------------------------------------------------------------------
# _extract_query_keywords
# ---------------------------------------------------------------------------


def test_extract_query_keywords_removes_stopwords():
    keywords = _extract_query_keywords("the current trend of fish production")
    assert "the" not in keywords
    assert "current" not in keywords
    assert "fish" in keywords
    assert "production" in keywords


def test_extract_query_keywords_removes_short_tokens():
    keywords = _extract_query_keywords("cod and tuna catch")
    # "and" and "cod" are ≤3 chars — should be excluded
    assert "and" not in keywords
    assert "cod" not in keywords
    assert "tuna" in keywords
    assert "catch" in keywords


def test_extract_query_keywords_lowercases():
    keywords = _extract_query_keywords("Global Fish Production")
    assert "global" in keywords
    assert "fish" in keywords
    assert "production" in keywords


# ---------------------------------------------------------------------------
# _score_followup_candidate
# ---------------------------------------------------------------------------


def test_score_followup_candidate_pdf_report_scores_high():
    score = _score_followup_candidate(
        "fish production report",
        "https://fao.org/fishery/docs/annual-report-2023.pdf",
    )
    assert score > 5


def test_score_followup_candidate_paginated_index_scores_negative():
    score = _score_followup_candidate(
        "fish production report",
        "https://fao.org/publications?page=3",
    )
    assert score < 0


def test_score_followup_candidate_homepage_scores_low():
    score = _score_followup_candidate(
        "fish production trend",
        "https://fao.org/home",
    )
    # "home" is in _FOLLOWUP_NEGATIVE_TOKENS → big penalty
    assert score < 0


def test_score_followup_candidate_dataset_url_for_data_query():
    score_data = _score_followup_candidate(
        "download fish catch dataset",
        "https://fao.org/fishery/data/csv",
    )
    score_report = _score_followup_candidate(
        "annual report fisheries trend",
        "https://fao.org/fishery/data/csv",
    )
    # A data URL should score better for data queries than for report queries
    assert score_data > score_report


# ---------------------------------------------------------------------------
# _extract_links_from_markdown
# ---------------------------------------------------------------------------


def test_extract_links_from_markdown_finds_markdown_links():
    content = "See [FAO](https://fao.org/report) and [WB](https://worldbank.org/data)."
    links = _extract_links_from_markdown(content)
    assert "https://fao.org/report" in links
    assert "https://worldbank.org/data" in links


def test_extract_links_from_markdown_deduplicates():
    content = "[A](https://fao.org) and [B](https://fao.org)."
    links = _extract_links_from_markdown(content)
    assert links.count("https://fao.org") == 1


def test_extract_links_from_markdown_strips_trailing_punctuation_from_bare_urls():
    content = "Visit https://fao.org/report."
    links = _extract_links_from_markdown(content)
    assert "https://fao.org/report" in links
    assert "https://fao.org/report." not in links


# ---------------------------------------------------------------------------
# _is_promising_followup_url
# ---------------------------------------------------------------------------


def test_is_promising_followup_url_rejects_off_domain():
    assert _is_promising_followup_url("https://other.org/report.pdf", "fao.org") is False


def test_is_promising_followup_url_rejects_homepage():
    assert _is_promising_followup_url("https://fao.org/", "fao.org") is False


def test_is_promising_followup_url_rejects_paginated_index():
    url = "https://fao.org/publications?page=2"
    assert _is_promising_followup_url(url, "fao.org") is False


def test_is_promising_followup_url_allows_document_download():
    url = "https://fao.org/fishery/docs/annual-fish-report-2023.pdf"
    assert _is_promising_followup_url(url, "fao.org", query="fish production report") is True


def test_is_promising_followup_url_rejects_service_page():
    url = "https://fao.org/services"
    assert _is_promising_followup_url(url, "fao.org") is False
