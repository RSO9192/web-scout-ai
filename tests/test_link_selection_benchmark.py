"""Unit tests for link-selection benchmark scoring. No network or model calls."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from link_selection_benchmark import (
    CASES,
    CaseResult,
    SelectionScore,
    build_report,
    normalize_benchmark_url,
    score_selection,
    unique_urls,
)


def test_cases_match_the_hand_picked_sets():
    by_name = {case.name: case for case in CASES}
    assert list(by_name) == ["el-nino", "forestry", "biodiversity"]
    assert by_name["el-nino"].query == "el nino"
    assert by_name["forestry"].query == "forestry"
    assert by_name["biodiversity"].query == "biodiversity"
    assert len(unique_urls(by_name["el-nino"].relevant_urls)) == 16
    assert len(unique_urls(by_name["forestry"].relevant_urls)) == 5
    assert len(unique_urls(by_name["biodiversity"].relevant_urls)) == 10


def test_normalize_benchmark_url_treats_encoding_and_www_as_the_same_page():
    encoded = (
        "https://www.fao.org/newsroom/detail/bracing-for-el-ni%C3%B1o--fao-and-wfp-launch-joint-appeal/en"
    )
    decoded = "http://fao.org/newsroom/detail/bracing-for-el-niño--fao-and-wfp-launch-joint-appeal/en/"
    assert normalize_benchmark_url(encoded) == normalize_benchmark_url(decoded)


def test_score_selection_counts_extra_urls_as_false_positives():
    gold = [
        "https://www.fao.org/climate-change/",
        "https://doi.org/10.4060/cd5248en",
        "https://doi.org/10.4060/cd5248en",
    ]
    selected = [
        "https://fao.org/climate-change",
        "https://www.fao.org/about/en",
    ]
    candidates = selected + ["https://doi.org/10.4060/cd5248en"]

    score = score_selection(gold, selected, candidates)

    assert score.true_positives == ["https://www.fao.org/climate-change/"]
    assert score.false_positives == ["https://www.fao.org/about/en"]
    assert score.false_negatives == ["https://doi.org/10.4060/cd5248en"]
    assert score.not_in_candidates == []
    assert score.precision == 0.5
    assert score.recall == 0.5


def test_score_selection_marks_gold_urls_the_fetcher_did_not_extract():
    gold = ["https://news.un.org/en/story/2026/09/1168404"]
    score = score_selection(gold, [], ["https://www.fao.org/climate-change/"])

    assert score.true_positives == []
    assert score.false_positives == []
    assert score.false_negatives == gold
    assert score.not_in_candidates == gold
    assert score.precision == 0.0
    assert score.recall == 0.0


def test_report_includes_selector_time():
    result = CaseResult(
        name="el-nino",
        query="el nino",
        url="https://www.fao.org/el-nino/en",
        selector="jev",
        model="jev-1.13.0",
        candidate_count=3,
        score=SelectionScore(true_positives=["https://example.org/a"]),
        elapsed_seconds=1.25,
    )

    report = build_report([result])

    assert "Selection time: 1.25s" in report
    assert "`jev-1.13.0`" in report
    assert "### jev" in report
