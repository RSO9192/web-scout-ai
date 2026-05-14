"""Tests for pure helper functions in agent.py."""


from web_scout.agent import (
    _extract_links_from_markdown,
    _extract_query_keywords,
    _is_promising_followup_url,
    _is_same_domain,
    _judge_synthesis,
    _looks_like_document_url,
    _looks_like_paginated_index_page,
    _query_prefers_data_pages,
    _query_prefers_report_pages,
    _score_followup_candidate,
)

# ---------------------------------------------------------------------------
# _judge_synthesis
# ---------------------------------------------------------------------------

def test_judge_synthesis_passes_clean_synthesis():
    """No issues returned when all citations point to valid scraped URLs."""
    valid = {"https://fao.org/report", "https://worldbank.org/data"}
    synthesis = (
        "Fish production rose in 2023 [FAO Report](https://fao.org/report). "
        "Poverty data confirms this [World Bank](https://worldbank.org/data)."
    )
    assert _judge_synthesis(synthesis, valid) == []


def test_judge_synthesis_detects_bare_url():
    """A raw https:// URL outside a markdown link is flagged."""
    valid = {"https://fao.org/report"}
    synthesis = "See more at https://fao.org/report for details."
    issues = _judge_synthesis(synthesis, valid)
    assert any("Bare URLs" in issue for issue in issues)


def test_judge_synthesis_detects_hallucinated_citation():
    """A markdown citation to a URL not in valid_urls is flagged."""
    valid = {"https://fao.org/report"}
    synthesis = "Data shows [Fake Source](https://invented.example.com/data)."
    issues = _judge_synthesis(synthesis, valid)
    assert any("NOT in the available sources" in issue for issue in issues)


def test_judge_synthesis_allows_valid_scraped_citation():
    """A markdown link whose URL appears in valid_urls raises no issue."""
    valid = {"https://fao.org/report"}
    synthesis = "Production is up [FAO](https://fao.org/report)."
    assert _judge_synthesis(synthesis, valid) == []


def test_judge_synthesis_flags_both_bare_and_hallucinated():
    """Both bare URL and hallucinated citation issues can appear together."""
    valid = {"https://fao.org/report"}
    synthesis = (
        "See https://fao.org/report raw. "
        "Also [Fake](https://made-up.org/page)."
    )
    issues = _judge_synthesis(synthesis, valid)
    assert len(issues) == 2


def test_judge_synthesis_url_normalization_strips_tracking_params():
    """A citation URL with utm_source still matches the clean valid URL."""
    valid = {"https://fao.org/report"}
    synthesis = "[Report](https://fao.org/report?utm_source=google)."
    # After normalization both should match
    issues = _judge_synthesis(synthesis, valid)
    assert not any("NOT in the available sources" in i for i in issues)


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
