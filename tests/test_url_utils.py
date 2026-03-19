"""Unit tests for URL/domain utility helpers."""
from web_scout.agent import _normalize_domain


def test_normalize_domain_plain():
    assert _normalize_domain("wocat.net") == "wocat.net"


def test_normalize_domain_strips_scheme():
    assert _normalize_domain("https://wocat.net") == "wocat.net"


def test_normalize_domain_strips_www():
    assert _normalize_domain("www.wocat.net") == "wocat.net"


def test_normalize_domain_strips_scheme_and_www():
    assert _normalize_domain("https://www.wocat.net/en/database/") == "wocat.net"


def test_normalize_domain_strips_path():
    assert _normalize_domain("wocat.net/en/database") == "wocat.net"


def test_normalize_domain_trailing_slash():
    assert _normalize_domain("wocat.net/") == "wocat.net"


def test_normalize_domain_uppercase():
    assert _normalize_domain("WOCAT.NET") == "wocat.net"
