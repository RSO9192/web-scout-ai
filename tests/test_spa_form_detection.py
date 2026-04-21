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
