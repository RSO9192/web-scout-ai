"""Tests for coverage-evaluator grounding and prompt framing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from web_scout.agent import COVERAGE_EVALUATOR_INSTRUCTIONS, _build_coverage_prompt
from web_scout.tools import ResearchTracker


def test_coverage_instructions_forbid_training_data():
    lower = COVERAGE_EVALUATOR_INSTRUCTIONS.lower()
    assert "training" in lower
    assert "do not use your own" in lower or "do not use prior knowledge" in lower


def test_coverage_instructions_mark_snippets_as_routing_only():
    lower = COVERAGE_EVALUATOR_INSTRUCTIONS.lower()
    assert "routing" in lower
    assert "do not count as evidence" in lower


def test_coverage_prompt_marks_unscraped_candidates_as_non_evidence():
    tracker = ResearchTracker()
    tracker.record_scrape("https://example.com/a", "A", "Useful extracted content.")
    tracker.record_search(
        query="reef threats",
        num_results=1,
        domains=None,
        results=[type("R", (), {
            "url": "https://example.com/b",
            "title": "Candidate B",
            "snippet": "This snippet mentions other threats.",
        })()],
    )

    prompt = _build_coverage_prompt("reef threats", tracker)
    lower = prompt.lower()

    assert "successful scraped sources available as evidence: 1" in lower
    assert "only the 'scraped content' section counts as evidence" in lower
    assert "do not use prior knowledge" in lower
    assert "unscraped candidates" in lower
