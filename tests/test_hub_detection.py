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
