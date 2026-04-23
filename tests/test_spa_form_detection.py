"""Tests for SPA fragment and form-contamination detection in raw_scrape."""

from unittest.mock import patch

import pytest
from agents.tool import ToolContext

from web_scout.tools import (
    _EXTRACTOR_INSTRUCTIONS,
    _build_extractor_agent,
    _has_fragment,
    _is_form_contaminated,
)


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

def _make_ctx():
    return ToolContext(
        context=None, tool_name="raw_scrape",
        tool_call_id="test-id", tool_arguments="{}",
    )


def _make_agent(url="https://example.org/portal"):
    return _build_extractor_agent(model="dummy", query="fish production statistics",
                                   url=url, wait_for=None)


def _fake_scrape_result(content: str, title: str = "Test Page", error=None):
    async def _mock(*args, **kwargs):
        return content, title, error
    return _mock


@pytest.mark.asyncio
async def test_raw_scrape_appends_spa_signal_for_fragment_url():
    """Fragment URL → SPA signal appended to output."""
    agent, cleanup = _make_agent(url="https://fao.org/faostat/en/#data/QCL")
    tool = next(t for t in agent.tools if getattr(t, "name", None) == "raw_scrape")
    rich_content = "Fish data content. " * 40

    with patch("web_scout.scraping.scrape_url", _fake_scrape_result(rich_content)):
        result = await tool.on_invoke_tool(_make_ctx(), "{}")

    assert "[SPA: URL fragment detected" in result
    await cleanup()


@pytest.mark.asyncio
async def test_raw_scrape_appends_form_signal_for_survey_content():
    """Survey content > 500 chars → form signal appended."""
    agent, cleanup = _make_agent(url="https://fao.org/faostat/en/")
    tool = next(t for t in agent.tools if getattr(t, "name", None) == "raw_scrape")
    survey_content = (
        "National Statistical Institutes\n"
        + "* Strongly Agree\n" * 5
        + "Some nav content. " * 30
    )
    assert len(survey_content) >= 500

    with patch("web_scout.scraping.scrape_url", _fake_scrape_result(survey_content)):
        result = await tool.on_invoke_tool(_make_ctx(), "{}")

    assert "[Form/survey content detected" in result
    await cleanup()


@pytest.mark.asyncio
async def test_raw_scrape_appends_both_signals_for_faostat_like_url():
    """Fragment URL + survey content → both signals appear."""
    agent, cleanup = _make_agent(url="https://fao.org/faostat/en/#data/QCL")
    tool = next(t for t in agent.tools if getattr(t, "name", None) == "raw_scrape")
    survey_content = (
        "Crops and livestock products\n"
        + "* Strongly Agree\n" * 5
        + "More content. " * 30
    )

    with patch("web_scout.scraping.scrape_url", _fake_scrape_result(survey_content)):
        result = await tool.on_invoke_tool(_make_ctx(), "{}")

    assert "[SPA: URL fragment detected" in result
    assert "[Form/survey content detected" in result
    await cleanup()


@pytest.mark.asyncio
async def test_raw_scrape_no_signal_for_normal_rich_content():
    """Normal rich content with no fragment → no signals."""
    agent, cleanup = _make_agent(url="https://fao.org/fishery/en")
    tool = next(t for t in agent.tools if getattr(t, "name", None) == "raw_scrape")
    normal_content = (
        "Fish production increased by 3% in 2023 according to FAO. "
        "Aquaculture reached 88 million tonnes globally. "
    ) * 40

    with patch("web_scout.scraping.scrape_url", _fake_scrape_result(normal_content)):
        result = await tool.on_invoke_tool(_make_ctx(), "{}")

    assert "[SPA:" not in result
    assert "[Form/survey" not in result
    await cleanup()


@pytest.mark.asyncio
async def test_raw_scrape_no_form_signal_when_content_under_500_chars():
    """Form detection skipped when content < 500 chars (already thin)."""
    agent, cleanup = _make_agent(url="https://fao.org/fishery/en")
    tool = next(t for t in agent.tools if getattr(t, "name", None) == "raw_scrape")
    thin_survey = "* Strongly Agree\n" * 3  # repeated token but < 500 chars

    with patch("web_scout.scraping.scrape_url", _fake_scrape_result(thin_survey)):
        result = await tool.on_invoke_tool(_make_ctx(), "{}")

    assert "[Form/survey content detected" not in result
    await cleanup()

def test_extractor_instructions_mention_spa_signal():
    """Instructions must tell the LLM to react to the SPA signal string."""
    assert "[SPA: URL fragment detected" in _EXTRACTOR_INSTRUCTIONS


def test_extractor_instructions_mention_form_signal():
    """Instructions must tell the LLM to react to the form signal string."""
    assert "[Form/survey content detected" in _EXTRACTOR_INSTRUCTIONS
