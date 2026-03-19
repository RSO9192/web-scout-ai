"""Unit tests for URL/domain utility helpers."""
from web_scout.agent import _normalize_domain, _find_next_page_url
from web_scout.scraping import _is_blocked_domain
from web_scout.tools import ResearchTracker


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


def test_normalize_url_strips_utm_source():
    base = "https://example.com/page"
    with_utm = "https://example.com/page?utm_source=google"
    assert ResearchTracker._normalize_url(with_utm) == ResearchTracker._normalize_url(base)


def test_normalize_url_strips_multiple_tracking_params():
    url = "https://example.com/page?utm_source=google&utm_medium=email&utm_campaign=spring"
    assert ResearchTracker._normalize_url(url) == "https://example.com/page"


def test_normalize_url_preserves_non_tracking_params():
    url = "https://wocat.net/en/database/list/?type=technology&country=ke"
    normalized = ResearchTracker._normalize_url(url)
    assert "type=technology" in normalized
    assert "country=ke" in normalized


def test_normalize_url_strips_fbclid():
    url = "https://example.com/article?fbclid=IwAR123"
    assert ResearchTracker._normalize_url(url) == "https://example.com/article"


def test_normalize_url_mixed_tracking_and_real_params():
    url = "https://example.com/search?q=test&utm_source=google&page=2"
    normalized = ResearchTracker._normalize_url(url)
    assert "q=test" in normalized
    assert "page=2" in normalized
    assert "utm_source" not in normalized


def test_normalize_url_http_to_https():
    assert ResearchTracker._normalize_url("http://example.com/page") == "https://example.com/page"


def test_normalize_url_trailing_slash_stripped():
    assert ResearchTracker._normalize_url("https://example.com/page/") == "https://example.com/page"


def test_is_blocked_domain_reddit_blocked_by_default():
    assert _is_blocked_domain("https://reddit.com/r/MachineLearning") is True


def test_is_blocked_domain_reddit_allowed_when_in_allowed_set():
    allowed = frozenset({"reddit.com"})
    assert _is_blocked_domain("https://reddit.com/r/MachineLearning", allowed_domains=allowed) is False


def test_is_blocked_domain_unrelated_domain_not_blocked():
    assert _is_blocked_domain("https://wocat.net/en/database/") is False


def test_is_blocked_domain_subdomain_blocked():
    assert _is_blocked_domain("https://m.youtube.com/watch?v=abc") is True


def test_is_blocked_domain_www_reddit_blocked_by_default():
    assert _is_blocked_domain("https://www.reddit.com/r/science") is True


def test_is_blocked_domain_allowed_set_empty_uses_full_blocklist():
    assert _is_blocked_domain("https://twitter.com/user", allowed_domains=frozenset()) is True
