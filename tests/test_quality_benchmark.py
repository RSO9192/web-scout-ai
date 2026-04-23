"""Unit tests for pure report-builder helpers in quality_benchmark."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from quality_benchmark import (
    Evaluation,
    FailureEntry,
    ToolResult,
    avg_content_depth,
    build_failure_table,
    build_source_previews,
    build_summary_row,
)

# ---------------------------------------------------------------------------
# avg_content_depth
# ---------------------------------------------------------------------------

def test_avg_content_depth_empty():
    assert avg_content_depth([]) == 0


def test_avg_content_depth_single():
    sources = [{"url": "https://a.com", "content": "x" * 1000}]
    assert avg_content_depth(sources) == 1000


def test_avg_content_depth_multiple():
    sources = [
        {"url": "https://a.com", "content": "x" * 2000},
        {"url": "https://b.com", "content": "x" * 4000},
    ]
    assert avg_content_depth(sources) == 3000


def test_avg_content_depth_missing_content_key():
    sources = [{"url": "https://a.com"}]  # no "content" key
    assert avg_content_depth(sources) == 0


# ---------------------------------------------------------------------------
# build_failure_table
# ---------------------------------------------------------------------------

def test_build_failure_table_empty():
    assert build_failure_table([]) == ""


def test_build_failure_table_single():
    failures = [FailureEntry(url="https://example.com/a", error="403 Forbidden", category="bot_detected")]
    out = build_failure_table(failures)
    assert "https://example.com/a" in out
    assert "bot_detected" in out
    assert "403 Forbidden" in out


def test_build_failure_table_multiple():
    failures = [
        FailureEntry(url="https://a.com", error="err1", category="scrape_failed"),
        FailureEntry(url="https://b.com", error="err2", category="source_http_error"),
    ]
    out = build_failure_table(failures)
    assert "https://a.com" in out
    assert "https://b.com" in out
    assert "scrape_failed" in out
    assert "source_http_error" in out


def test_build_source_previews_empty():
    assert build_source_previews([], preview_chars=250) == ""


def test_build_source_previews_truncates():
    sources = [{"url": "https://x.com", "title": "X", "content": "A" * 500}]
    out = build_source_previews(sources, preview_chars=100)
    assert "A" * 100 in out
    assert "A" * 101 not in out


def test_build_source_previews_includes_title_and_url():
    sources = [{"url": "https://x.com/page", "title": "My Title", "content": "Some content here"}]
    out = build_source_previews(sources, preview_chars=250)
    assert "My Title" in out
    assert "https://x.com/page" in out
    assert "Some content here" in out


def test_build_summary_row_with_scores():
    ev = Evaluation(
        url_relevance=4,
        tailored_comprehensiveness=3,
        synthesis_quality=4,
        extraction_coverage=4,
    )
    result = ToolResult(
        tool="web-scout-ai",
        query="test query that is quite long and should be truncated for display",
        synthesis="answer",
        num_scraped=7,
        failures=[
            FailureEntry(url="u", error="e", category="bot_detected"),
            FailureEntry(url="u2", error="e2", category="scrape_failed"),
        ],
        elapsed_seconds=42.5,
        evaluation=ev,
    )
    row = build_summary_row(result)
    assert "web-scout-ai" in row
    assert "7/9" in row  # scrape_rate: 7 scraped out of 9 attempted
    assert "42.5" in row
    assert "4/5" in row
    assert "3.8" in row  # overall = (4+3+4+4)/4 = 3.75 → 3.8
    assert row.count("|") == 13  # 12 columns → 13 pipes


def test_build_summary_row_error():
    result = ToolResult(tool="web-scout-ai", query="q", error="timeout", elapsed_seconds=5.0)
    row = build_summary_row(result)
    assert "ERROR" in row
    assert row.count("|") == 13  # 12 columns → 13 pipes
