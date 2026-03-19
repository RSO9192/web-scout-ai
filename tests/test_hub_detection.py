"""Unit tests for hub-page detection helpers."""
import pytest
from web_scout.tools import _ExtractorOutput


def test_page_type_default_is_content():
    out = _ExtractorOutput(relevant_content="Some article text.")
    assert out.page_type == "content"


def test_page_type_list_accepted():
    out = _ExtractorOutput(relevant_content="List page.", page_type="list")
    assert out.page_type == "list"


def test_relevant_links_accepts_up_to_15():
    links = [f"https://example.com/{i}" for i in range(15)]
    out = _ExtractorOutput(relevant_content="x", relevant_links=links)
    assert len(out.relevant_links) == 15


from web_scout.agent import _find_next_page_url


def test_find_next_page_url_next_token():
    content = "Some content [Next](https://wocat.net/page/2) more content"
    assert _find_next_page_url(content, "https://wocat.net/page/1") == "https://wocat.net/page/2"


def test_find_next_page_url_right_arrow():
    content = "[›](https://wocat.net/list/?page=2)"
    assert _find_next_page_url(content, "https://wocat.net/list/") == "https://wocat.net/list/?page=2"


def test_find_next_page_url_double_right_arrow():
    content = "[»](https://wocat.net/list/?page=2)"
    assert _find_next_page_url(content, "https://wocat.net/list/") == "https://wocat.net/list/?page=2"


def test_find_next_page_url_case_insensitive():
    content = "[NEXT PAGE](https://wocat.net/page/2)"
    assert _find_next_page_url(content, "https://wocat.net/page/1") == "https://wocat.net/page/2"


def test_find_next_page_url_cross_domain_rejected():
    content = "[Next](https://other.org/page/2)"
    assert _find_next_page_url(content, "https://wocat.net/page/1") is None


def test_find_next_page_url_bare_digit_not_matched():
    content = "[2](https://wocat.net/page/2)"
    assert _find_next_page_url(content, "https://wocat.net/page/1") is None


def test_find_next_page_url_no_pagination_link():
    assert _find_next_page_url("Some content with no next link.", "https://wocat.net/page/1") is None


def test_find_next_page_url_empty_content():
    assert _find_next_page_url("", "https://wocat.net/page/1") is None
