"""Tests for pipeline speed optimisations."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from agents.tool import ToolContext
from web_scout.tools import _build_extractor_agent, create_scrape_and_extract_tool


def _make_ctx(tool_name="scrape_linked_document"):
    return ToolContext(
        context=None, tool_name=tool_name,
        tool_call_id="test-id", tool_arguments="{}",
    )


def _find_tool(agent, name):
    for t in agent.tools:
        if getattr(t, "name", None) == name:
            return t
    raise AssertionError(f"Tool '{name}' not found")


# ---------------------------------------------------------------------------
# Task 1: shared document cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_linked_document_uses_cache_on_second_call():
    """scrape_linked_document fetches the document once; second call returns cached."""
    doc_cache: dict = {}
    agent, cleanup = _build_extractor_agent(
        model="dummy", query="fish production", url="https://example.org/page",
        wait_for=None, doc_cache=doc_cache,
    )
    tool = _find_tool(agent, "scrape_linked_document")

    doc_content = "Full report content about fish production. " * 30

    call_count = 0
    async def _fake_scrape_doc(url, **kwargs):
        nonlocal call_count
        call_count += 1
        return doc_content, "Fish Report 2024", None

    with (
        patch("web_scout.scraping._validate_url", AsyncMock(return_value=("SCRAPE_DOC", "document-by-url"))),
        patch("web_scout.scraping._scrape_document", _fake_scrape_doc),
    ):
        result1 = await tool.on_invoke_tool(_make_ctx(), '{"document_url": "https://fao.org/report.pdf"}')
        result2 = await tool.on_invoke_tool(_make_ctx(), '{"document_url": "https://fao.org/report.pdf"}')

    assert call_count == 1, "Document should be fetched only once"
    assert result1 == result2
    assert "fish production" in result1.lower() or "Fish Report" in result1

    await cleanup()


@pytest.mark.asyncio
async def test_scrape_linked_document_cache_shared_across_agents():
    """Two extractor agents sharing the same cache fetch a common doc only once."""
    doc_cache: dict = {}

    agent1, cleanup1 = _build_extractor_agent(
        model="dummy", query="fish production", url="https://example.org/page1",
        wait_for=None, doc_cache=doc_cache,
    )
    agent2, cleanup2 = _build_extractor_agent(
        model="dummy", query="fish production", url="https://example.org/page2",
        wait_for=None, doc_cache=doc_cache,
    )

    tool1 = _find_tool(agent1, "scrape_linked_document")
    tool2 = _find_tool(agent2, "scrape_linked_document")

    doc_content = "SOFIA 2024 report content. " * 30
    call_count = 0

    async def _fake_scrape_doc(url, **kwargs):
        nonlocal call_count
        call_count += 1
        return doc_content, "SOFIA 2024", None

    with (
        patch("web_scout.scraping._validate_url", AsyncMock(return_value=("SCRAPE_DOC", "document-by-url"))),
        patch("web_scout.scraping._scrape_document", _fake_scrape_doc),
    ):
        result1 = await tool1.on_invoke_tool(_make_ctx(), '{"document_url": "https://fao.org/sofia.pdf"}')
        result2 = await tool2.on_invoke_tool(_make_ctx(), '{"document_url": "https://fao.org/sofia.pdf"}')

    assert call_count == 1, "Two agents sharing cache must fetch the document only once"
    assert result1 == result2

    await cleanup1()
    await cleanup2()


@pytest.mark.asyncio
async def test_scrape_linked_document_no_cache_by_default():
    """_build_extractor_agent with no doc_cache still works (backward compatible)."""
    agent, cleanup = _build_extractor_agent(
        model="dummy", query="fish", url="https://example.org/page",
        wait_for=None,
    )
    tool = _find_tool(agent, "scrape_linked_document")

    async def _fake_scrape_doc(url, **kwargs):
        return "content " * 40, "Doc", None

    with (
        patch("web_scout.scraping._validate_url", AsyncMock(return_value=("SCRAPE_DOC", "ok"))),
        patch("web_scout.scraping._scrape_document", _fake_scrape_doc),
    ):
        result = await tool.on_invoke_tool(_make_ctx(), '{"document_url": "https://fao.org/doc.pdf"}')

    assert "content" in result
    await cleanup()
