"""Tests for SPA fragment and form-contamination detection in fetched content."""

from web_scout.tools.extractor import _EXTRACTOR_INSTRUCTIONS
from web_scout.tools.page_analysis import _has_fragment, _is_form_contaminated, render_cached_page_text as _render_cached_page_text


def test_has_fragment_detects_hash_fragment():
    assert _has_fragment("https://www.fao.org/faostat/en/#data/QCL") is True


def test_has_fragment_false_for_plain_url():
    assert _has_fragment("https://fao.org/fishery/en") is False


def test_has_fragment_false_for_empty_fragment():
    assert _has_fragment("https://fao.org/page#") is False


def test_has_fragment_false_for_query_string_only():
    assert _has_fragment("https://fao.org/search?q=fish") is False


def test_has_fragment_detects_spa_anchor():
    assert _has_fragment("https://example.com/app#/dashboard/stats") is True


def test_is_form_contaminated_detects_strongly_agree_repetition():
    content = (
        "National Statistical Institutes\n"
        "* Strongly Agree\n"
        "* Strongly Agree\n"
        "* Strongly Agree\n"
        "Kindly provide details on your response.\n"
    )
    assert _is_form_contaminated(content) is True


def test_is_form_contaminated_detects_kindly_provide_repetition():
    content = "Please rate our service.\nKindly provide your feedback.\nKindly provide details.\nThank you.\n"
    assert _is_form_contaminated(content) is True


def test_is_form_contaminated_false_for_normal_article():
    content = (
        "Fish production increased by 3% in 2023 according to FAO data.\n"
        "The report covers 180 countries and includes aquaculture statistics.\n"
        "Global capture fisheries reached 91 million tonnes.\n"
        "Aquaculture contributed an additional 88 million tonnes.\n"
    ) * 5
    assert _is_form_contaminated(content) is False


def test_is_form_contaminated_detects_nav_only_bullet_page():
    # >20 lines, >75% bullet points — navigation dump
    lines = ["* " + f"Nav link {i}" for i in range(25)]
    content = "\n".join(lines)
    assert _is_form_contaminated(content) is True


def test_is_form_contaminated_false_for_mixed_content_with_bullets():
    lines = (
        ["Fish production rose in 2023."] * 8
        + ["* " + f"Related link {i}" for i in range(5)]
        + ["The data covers 180 countries and includes aquaculture."] * 7
    )
    content = "\n".join(lines)
    assert _is_form_contaminated(content) is False


def test_is_form_contaminated_false_for_short_survey_content():
    # Under 20 lines — bullet-ratio rule does not apply
    lines = ["* " + f"Bullet {i}" for i in range(10)]
    content = "\n".join(lines)
    assert _is_form_contaminated(content) is False


def test_render_cached_page_appends_spa_signal_for_fragment_url():
    """Fragment URL → SPA signal appended to output."""
    rich_content = "Fish data content. " * 40

    result = _render_cached_page_text(
        "https://fao.org/faostat/en/#data/QCL",
        "Test Page",
        rich_content,
    )

    assert "[SPA: URL fragment detected" in result


def test_render_cached_page_appends_form_signal_for_survey_content():
    """Survey content > 500 chars → form signal appended."""
    survey_content = "National Statistical Institutes\n" + "* Strongly Agree\n" * 5 + "Some nav content. " * 30
    assert len(survey_content) >= 500

    result = _render_cached_page_text("https://fao.org/faostat/en/", "Test Page", survey_content)

    assert "[Form/survey content detected" in result


def test_render_cached_page_appends_both_signals_for_faostat_like_url():
    """Fragment URL + survey content → both signals appear."""
    survey_content = "Crops and livestock products\n" + "* Strongly Agree\n" * 5 + "More content. " * 30

    result = _render_cached_page_text(
        "https://fao.org/faostat/en/#data/QCL",
        "Test Page",
        survey_content,
    )

    assert "[SPA: URL fragment detected" in result
    assert "[Form/survey content detected" in result


def test_render_cached_page_no_signal_for_normal_rich_content():
    """Normal rich content with no fragment → no signals."""
    normal_content = (
        "Fish production increased by 3% in 2023 according to FAO. Aquaculture reached 88 million tonnes globally. "
    ) * 40

    result = _render_cached_page_text("https://fao.org/fishery/en", "Test Page", normal_content)

    assert "[SPA:" not in result
    assert "[Form/survey" not in result


def test_render_cached_page_no_form_signal_when_content_under_500_chars():
    """Form detection skipped when content < 500 chars (already thin)."""
    thin_survey = "* Strongly Agree\n" * 3  # repeated token but < 500 chars

    result = _render_cached_page_text("https://fao.org/fishery/en", "Test Page", thin_survey)

    assert "[Form/survey content detected" not in result


def test_extractor_instructions_mention_spa_signal():
    """Instructions must tell the LLM to react to the SPA signal string."""
    assert "[SPA: URL fragment detected" in _EXTRACTOR_INSTRUCTIONS


def test_extractor_instructions_mention_form_signal():
    """Instructions must tell the LLM to react to the form signal string."""
    assert "[Form/survey content detected" in _EXTRACTOR_INSTRUCTIONS
