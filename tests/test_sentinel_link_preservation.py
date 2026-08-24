"""Hub pages with no direct evidence: sentinel content must not become citable
evidence, while their relevant_links must survive for crawl deepening."""

import pytest
from unittest.mock import AsyncMock

from agents import Runner

import web_scout.tools.scraper as _tools_scraper
from web_scout.scraping import DefaultParser, FetchResult, ParseResult, ScraplingFetcher
from web_scout.scraping.types import SourceArtifact
from web_scout.tools import ResearchTracker, create_scrape_and_extract_tool, resolve_scrape_outcome
from web_scout.tools.outcomes import build_failure_outcome
from web_scout.tools.rendering import infer_rendered_outcome
from web_scout.tools.types import ExtractorOutput

SENTINEL = "[No relevant content found for this query]"
HUB_URL = "https://example.org/database/"
HUB_LINKS = [
    "https://example.org/database/item-1",
    "https://example.org/database/item-2",
]


class _FakeRunResult:
    def __init__(self, output):
        self._output = output

    def final_output_as(self, _output_type):
        return self._output


def _patch_scrape_stack(monkeypatch, extractor_output):
    async def _fake_run(agent, input_text, max_turns=15):
        return _FakeRunResult(extractor_output)

    async def _no_cleanup():
        pass

    artifact = SourceArtifact(kind="text", title="Hub", text_content="Hub page content " * 80)
    parse_result = ParseResult(
        url=HUB_URL, title="Hub", text_content=artifact.text_content, links=[], artifact=artifact
    )
    monkeypatch.setattr(_tools_scraper, "build_extractor_agent", lambda *args, **kwargs: (object(), _no_cleanup))
    monkeypatch.setattr(DefaultParser, "dispatch", AsyncMock(return_value=parse_result))
    monkeypatch.setattr(ScraplingFetcher, "fetch", AsyncMock(return_value=FetchResult(
        url=HUB_URL, status=200, content_type="text/html",
        content_disposition="", html_content="<html>...</html>", body=None, headers={}, used_browser=False,
    )))
    monkeypatch.setattr(Runner, "run", _fake_run)


@pytest.mark.asyncio
async def test_sentinel_with_links_is_irrelevant_but_keeps_links(monkeypatch):
    tracker = ResearchTracker()
    _patch_scrape_stack(
        monkeypatch,
        ExtractorOutput(
            title="Hub",
            relevant_content=SENTINEL,
            page_type="list",
            relevant_links=HUB_LINKS,
        ),
    )

    scrape_tool = create_scrape_and_extract_tool(extractor_model="dummy", tracker=tracker, query="test")
    rendered = await scrape_tool(HUB_URL)

    assert tracker.count_for_action("scraped") == 0
    assert tracker.count_for_action("scraped_irrelevant") == 1

    outcome = resolve_scrape_outcome(scrape_tool, HUB_URL, rendered)
    assert outcome.status == "failure"
    assert outcome.failure_kind == "scraped_irrelevant"
    assert outcome.page_type == "list"
    assert outcome.relevant_links == HUB_LINKS
    # Links must appear in the rendered text so followup candidate mining works.
    for link in HUB_LINKS:
        assert link in rendered


@pytest.mark.asyncio
async def test_has_evidence_false_overrides_meta_summary_content(monkeypatch):
    """The structured flag must win even when the model writes a hedged
    meta-summary instead of the sentinel string."""
    tracker = ResearchTracker()
    _patch_scrape_stack(
        monkeypatch,
        ExtractorOutput(
            title="Hub",
            has_evidence=False,
            relevant_content="The page does not display query-specific values.",
            page_type="list",
            relevant_links=HUB_LINKS,
        ),
    )

    scrape_tool = create_scrape_and_extract_tool(extractor_model="dummy", tracker=tracker, query="test")
    rendered = await scrape_tool(HUB_URL)

    assert tracker.count_for_action("scraped") == 0
    assert tracker.count_for_action("scraped_irrelevant") == 1

    outcome = resolve_scrape_outcome(scrape_tool, HUB_URL, rendered)
    assert outcome.status == "failure"
    assert outcome.failure_kind == "scraped_irrelevant"
    assert outcome.relevant_links == HUB_LINKS


@pytest.mark.asyncio
async def test_sentinel_without_links_keeps_existing_behavior(monkeypatch):
    tracker = ResearchTracker()
    _patch_scrape_stack(
        monkeypatch,
        ExtractorOutput(title="Hub", relevant_content=SENTINEL, page_type="content", relevant_links=[]),
    )

    scrape_tool = create_scrape_and_extract_tool(extractor_model="dummy", tracker=tracker, query="test")
    rendered = await scrape_tool(HUB_URL)

    assert tracker.count_for_action("scraped_irrelevant") == 1
    outcome = resolve_scrape_outcome(scrape_tool, HUB_URL, rendered)
    assert outcome.status == "failure"
    assert outcome.relevant_links == []


def test_failure_outcome_with_links_round_trips_through_rendered_text():
    outcome = build_failure_outcome(
        url=HUB_URL,
        content=SENTINEL,
        count_scraped=None,
        failure_kind="scraped_irrelevant",
        page_type="list",
        links=HUB_LINKS,
    )
    assert outcome.page_type == "list"
    assert outcome.relevant_links == HUB_LINKS

    reparsed = infer_rendered_outcome(HUB_URL, outcome.rendered_text)
    assert reparsed.status == "failure"
    assert reparsed.failure_kind == "scraped_irrelevant"
    assert reparsed.page_type == "list"
    assert reparsed.relevant_links == HUB_LINKS
