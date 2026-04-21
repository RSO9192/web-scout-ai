"""Tests for SPA fragment and form-contamination detection in raw_scrape."""

from web_scout.tools import _has_fragment


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


from web_scout.tools import _is_form_contaminated


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
    content = (
        "Please rate our service.\n"
        "Kindly provide your feedback.\n"
        "Kindly provide details.\n"
        "Thank you.\n"
    )
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
