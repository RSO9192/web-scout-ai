"""Tests for synthesis grounding — instruction content and prompt enrichment."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from web_scout.agent import SYNTHESISER_INSTRUCTIONS, _build_synth_prompt, _judge_synthesis
from web_scout.models import UrlEntry


def _make_entry(url: str, content: str = "") -> UrlEntry:
    return UrlEntry(url=url, content=content)


def test_instructions_forbid_training_data():
    lower = SYNTHESISER_INSTRUCTIONS.lower()
    assert "training" in lower
    assert "do not" in lower or "no training data" in lower


def test_instructions_require_gap_reporting():
    lower = SYNTHESISER_INSTRUCTIONS.lower()
    assert "gap" in lower or "not found" in lower or "did not contain" in lower


def test_instructions_mention_thin_coverage():
    lower = SYNTHESISER_INSTRUCTIONS.lower()
    assert "thin" in lower or "few source" in lower or "limited" in lower


def test_instructions_restrict_citations_to_scraped_sources():
    """Must tell the model not to cite snippet-only URLs with markdown links."""
    lower = SYNTHESISER_INSTRUCTIONS.lower()
    assert "snippet" in lower
    assert "only cite scraped" in lower or "only permitted for" in lower or "scraped sources" in lower


def test_synth_prompt_includes_source_count():
    scraped = [_make_entry("https://a.com", "content a")]
    prompt = _build_synth_prompt(
        query="test query",
        scraped=scraped,
        snippet_only=[],
        bot_detected=[],
        blocked_by_policy=[],
        scrape_failed=[],
        source_http_error=[],
        domain_expertise=None,
    )
    assert "You have 1 successfully scraped source" in prompt


def test_synth_prompt_includes_failure_context_when_failures_exist():
    scraped = [_make_entry("https://a.com", "content")]
    bot = [_make_entry("https://bot.com", "[bot detection: blocked]")]
    blocked = [_make_entry("https://nature.com/paper", "[blocked by policy]")]
    prompt = _build_synth_prompt(
        query="test",
        scraped=scraped,
        snippet_only=[],
        bot_detected=bot,
        blocked_by_policy=blocked,
        scrape_failed=[],
        source_http_error=[],
        domain_expertise=None,
    )
    assert "bot.com" in prompt
    assert "nature.com" in prompt
    assert "could not" in prompt.lower() or "not be accessed" in prompt.lower()


def test_synth_prompt_thin_coverage_warning_when_few_sources():
    scraped = [_make_entry("https://a.com", "content")]
    prompt = _build_synth_prompt(
        query="test",
        scraped=scraped,
        snippet_only=[],
        bot_detected=[],
        blocked_by_policy=[],
        scrape_failed=[],
        source_http_error=[],
        domain_expertise=None,
    )
    lower = prompt.lower()
    assert "thin" in lower or "only 1" in lower or "limited" in lower


def test_synth_prompt_no_thin_warning_when_enough_sources():
    scraped = [
        _make_entry("https://a.com", "content a"),
        _make_entry("https://b.com", "content b"),
        _make_entry("https://c.com", "content c"),
    ]
    prompt = _build_synth_prompt(
        query="test",
        scraped=scraped,
        snippet_only=[],
        bot_detected=[],
        blocked_by_policy=[],
        scrape_failed=[],
        source_http_error=[],
        domain_expertise=None,
    )
    assert "thin" not in prompt.lower()


def test_synth_prompt_no_failure_section_when_no_failures():
    scraped = [_make_entry("https://a.com", "content")]
    prompt = _build_synth_prompt(
        query="test",
        scraped=scraped,
        snippet_only=[],
        bot_detected=[],
        blocked_by_policy=[],
        scrape_failed=[],
        source_http_error=[],
        domain_expertise=None,
    )
    assert "could not be accessed" not in prompt.lower()
    assert "bot-blocked" not in prompt.lower()


# ---------------------------------------------------------------------------
# _judge_synthesis — citation validation
# ---------------------------------------------------------------------------

def test_judge_passes_scraped_url_citation():
    """A URL that was scraped may be cited without flagging."""
    synthesis = "Some fact. [Title](https://scraped.com/page)"
    valid_urls = {"https://scraped.com/page"}
    issues = _judge_synthesis(synthesis, valid_urls)
    assert not any("scraped.com" in i for i in issues)


def test_judge_flags_snippet_only_url_citation():
    """A URL that only appeared as a snippet (not scraped) must be flagged."""
    synthesis = "Some fact [Title](https://snippet-only.com/article)"
    valid_urls = {"https://scraped.com/page"}  # snippet-only URL not included
    issues = _judge_synthesis(synthesis, valid_urls)
    assert any("snippet-only.com" in i for i in issues), (
        "Judge should flag citation to URL not in scraped set"
    )


def test_judge_flags_fully_hallucinated_url():
    """A URL invented from thin air that never appeared anywhere must be flagged."""
    synthesis = "Ethiopia corn area is 2.6M ha [FAS](https://www.fas.usda.gov/data/production/et)"
    valid_urls = {"https://fao.org/faostat/", "https://tradingeconomics.com/ethiopia/"}
    issues = _judge_synthesis(synthesis, valid_urls)
    assert any("fas.usda.gov" in i for i in issues), (
        "Judge should flag hallucinated USDA URL not in scraped set"
    )


def test_judge_passes_synthesis_with_no_urls():
    """A synthesis with no citations raises no issues."""
    issues = _judge_synthesis("No specific sources found.", {"https://a.com"})
    assert issues == []
