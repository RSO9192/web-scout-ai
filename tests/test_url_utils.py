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


# --- Additional _normalize_url coverage ---

def test_normalize_url_preserves_empty_query():
    url = "https://example.com/page?"
    # Trailing ? should not leave a hanging separator
    normalized = ResearchTracker._normalize_url(url)
    assert normalized == "https://example.com/page"


def test_normalize_url_idempotent():
    url = "https://example.com/page?type=tech&country=ke"
    assert ResearchTracker._normalize_url(url) == ResearchTracker._normalize_url(
        ResearchTracker._normalize_url(url)
    )


def test_normalize_url_fragment_stripped():
    # urlunparse already omits fragment (last component is "")
    url = "https://example.com/page"
    assert "#" not in ResearchTracker._normalize_url(url)


# --- Additional _is_blocked_domain coverage ---

def test_is_blocked_domain_linkedin_blocked():
    assert _is_blocked_domain("https://linkedin.com/in/someone") is True


def test_is_blocked_domain_allowed_set_only_removes_specified():
    allowed = frozenset({"reddit.com"})
    # twitter.com is still blocked even when reddit is allowed
    assert _is_blocked_domain("https://twitter.com/user", allowed_domains=allowed) is True
    assert _is_blocked_domain("https://reddit.com/r/foo", allowed_domains=allowed) is False


# --- Additional _find_next_page_url realistic content ---

def test_find_next_page_url_in_real_markdown_table():
    content = (
        "| Technology | Country |\n"
        "| Agroforestry | KE |\n"
        "\n"
        "Page 1 of 3 — [Next](https://wocat.net/en/database/list/?page=2&type=technology)\n"
    )
    result = _find_next_page_url(content, "https://wocat.net/en/database/list/")
    assert result == "https://wocat.net/en/database/list/?page=2&type=technology"


def test_find_next_page_url_ignores_other_links_with_same_domain():
    content = (
        "[Home](https://wocat.net/) "
        "[About](https://wocat.net/about) "
        "[Next](https://wocat.net/list/?page=2)"
    )
    result = _find_next_page_url(content, "https://wocat.net/list/")
    assert result == "https://wocat.net/list/?page=2"
