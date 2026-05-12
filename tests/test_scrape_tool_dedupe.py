import asyncio

import pytest

from web_scout._heuristics import EXTRACTOR_HEURISTICS, FOLLOWUP_HEURISTICS, ROUTING_HEURISTICS
from web_scout.tools import (
    ResearchTracker,
    _build_failure_outcome,
    _build_success_outcome,
    _classify_failure_action,
    _ExtractorOutput,
    _is_rendered_list_page,
    _render_failed_extractor_output,
    _render_successful_extractor_output,
    _resolve_scrape_outcome,
    create_scrape_and_extract_tool,
)


class _FakeRunResult:
    def __init__(self, output):
        self._output = output

    def final_output_as(self, _output_type):
        return self._output


@pytest.mark.asyncio
async def test_scrape_tool_reuses_inflight_request(monkeypatch):
    from web_scout import tools

    tracker = ResearchTracker()
    call_count = 0

    async def _fake_run(agent, input_text, max_turns=15):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return _FakeRunResult(
            _ExtractorOutput(
                title="Example",
                relevant_content="Useful extracted content",
                page_type="content",
                relevant_links=[],
            )
        )

    async def _no_cleanup():
        pass

    monkeypatch.setattr(
        tools,
        "_build_extractor_agent",
        lambda *args, **kwargs: (object(), _no_cleanup),
    )
    monkeypatch.setattr(tools.Runner, "run", _fake_run)

    scrape_tool = create_scrape_and_extract_tool(extractor_model="dummy", tracker=tracker, query="test")
    url = "https://example.org/report"

    result1, result2 = await asyncio.gather(scrape_tool(url), scrape_tool(url))

    assert call_count == 1
    assert tracker.scrape_count == 1
    assert "Useful extracted content" in result1
    assert result1 == result2


@pytest.mark.asyncio
async def test_scrape_tool_does_not_retry_bot_detected(monkeypatch):
    from web_scout import tools

    tracker = ResearchTracker()
    url = "https://example.org/protected"
    tracker.record_bot_detection(url, "bot_detected: challenge page")

    async def _unexpected_run(agent, input_text, max_turns=15):
        raise AssertionError("Runner.run should not be called for bot-detected URLs")

    async def _no_cleanup():
        pass

    monkeypatch.setattr(
        tools,
        "_build_extractor_agent",
        lambda *args, **kwargs: (object(), _no_cleanup),
    )
    monkeypatch.setattr(tools.Runner, "run", _unexpected_run)

    scrape_tool = create_scrape_and_extract_tool(extractor_model="dummy", tracker=tracker, query="test")
    result = await scrape_tool(url)

    assert "Already attempted this URL" in result
    assert "bot_detected" in result


def test_classify_failure_action_blocked_domain():
    assert _classify_failure_action("Skipped: blocked domain") == "blocked_by_policy"


def test_classify_failure_action_http_error():
    assert _classify_failure_action("Skipped: HTTP 503") == "source_http_error"


def test_classify_failure_action_irrelevant_page():
    assert _classify_failure_action("[No relevant content found for this query]") == "scraped_irrelevant"


def test_research_tracker_builds_new_failure_groups():
    tracker = ResearchTracker()
    tracker.record_blocked_by_policy("https://example.org/publisher", "Skipped: blocked domain")
    tracker.record_source_http_error("https://example.org/down", "Skipped: HTTP 503")
    tracker.record_scraped_irrelevant("https://example.org/off-topic", "[No relevant content found for this query]")

    groups = tracker.build_result_groups()

    assert [entry.url for entry in groups["blocked_by_policy"]] == ["https://example.org/publisher"]
    assert [entry.url for entry in groups["source_http_error"]] == ["https://example.org/down"]
    assert [entry.url for entry in groups["scraped_irrelevant"]] == ["https://example.org/off-topic"]


def test_research_tracker_exposes_public_lookup_api():
    tracker = ResearchTracker()
    url = "https://example.org/report?utm_source=test"
    tracker.record_scrape(url, "Report", "Useful extracted content")

    assert tracker.action_for(url) == "scraped"
    assert tracker.entry_for(url).title == "Report"
    assert tracker.count_for_action("scraped") == 1
    assert tracker.is_unscraped_candidate(url) is False
    assert "Already scraped" in tracker.cached_scrape_response(url)


def test_research_tracker_records_direct_queries():
    tracker = ResearchTracker()
    tracker.record_direct_query("fish production")

    assert len(tracker.queries) == 1
    assert tracker.queries[0].query == "fish production"
    assert tracker.queries[0].num_results_returned == 1


def test_render_successful_extractor_output_keeps_existing_string_contract():
    rendered = _render_successful_extractor_output(
        url="https://example.org/report",
        title="Example Report",
        content="Useful extracted content",
        page_type="list",
        links=["https://example.org/detail"],
        count_scraped=1,
    )

    assert rendered == (
        "# Example Report\n"
        "Source: https://example.org/report\n\n"
        "Useful extracted content\n"
        "**Page type: list**\n\n"
        "**Relevant Links found on page:**\n"
        "- https://example.org/detail\n\n"
        "⚠ REMINDER: You MUST successfully scrape AT LEAST 2 high-quality sources "
        "before synthesising and finishing. You currently have 1 successful scrape(s)."
    )
    assert _is_rendered_list_page(rendered) is True


def test_build_success_outcome_preserves_typed_and_rendered_views():
    outcome = _build_success_outcome(
        url="https://example.org/report",
        title="Example Report",
        content="Useful extracted content",
        page_type="list",
        links=["https://example.org/detail"],
        count_scraped=1,
    )

    assert outcome.status == "success"
    assert outcome.page_type == "list"
    assert outcome.relevant_links == ["https://example.org/detail"]
    assert outcome.rendered_text == _render_successful_extractor_output(
        url="https://example.org/report",
        title="Example Report",
        content="Useful extracted content",
        page_type="list",
        links=["https://example.org/detail"],
        count_scraped=1,
    )


@pytest.mark.parametrize(
    ("content", "expected_action"),
    [
        ("Skipped: blocked domain", "blocked_by_policy"),
        ("bot_detected: challenge page", "bot_detected"),
        ("Skipped: HTTP 503", "source_http_error"),
        ("[No relevant content found for this query]", "scraped_irrelevant"),
    ],
)
def test_render_failed_extractor_output_keeps_existing_string_contract(content, expected_action):
    rendered = _render_failed_extractor_output(
        url="https://example.org/report",
        content=content,
        count_scraped=1,
    )

    assert _classify_failure_action(content) == expected_action
    assert rendered == (
        "No relevant content found at https://example.org/report: "
        f"{content}\n\n"
        "⚠ REMINDER: You MUST successfully scrape AT LEAST 2 high-quality sources "
        "before synthesising and finishing. You currently have 1 successful scrape(s). "
        "You MUST find other URLs and scrape them!"
    )


def test_build_failure_outcome_preserves_typed_and_rendered_views():
    outcome = _build_failure_outcome(
        url="https://example.org/report",
        content="Skipped: HTTP 503",
        count_scraped=1,
        failure_kind="source_http_error",
    )

    assert outcome.status == "failure"
    assert outcome.failure_kind == "source_http_error"
    assert outcome.rendered_text == _render_failed_extractor_output(
        url="https://example.org/report",
        content="Skipped: HTTP 503",
        count_scraped=1,
    )


def test_resolve_scrape_outcome_reconstructs_typed_outcome_from_legacy_string():
    rendered = _render_successful_extractor_output(
        url="https://example.org/report",
        title="Example Report",
        content="Useful extracted content",
        page_type="list",
        links=["https://example.org/detail"],
        count_scraped=None,
    )

    outcome = _resolve_scrape_outcome(None, "https://example.org/report", rendered)

    assert outcome.status == "success"
    assert outcome.page_type == "list"
    assert outcome.title == "Example Report"
    assert outcome.content == "Useful extracted content"
    assert outcome.relevant_links == ["https://example.org/detail"]


def test_low_level_heuristics_are_frozen_in_private_config_module():
    assert ROUTING_HEURISTICS.short_html_text_chars == 150
    assert EXTRACTOR_HEURISTICS.thin_content_chars == 500
    assert EXTRACTOR_HEURISTICS.max_interactive_clicks == 5
    assert FOLLOWUP_HEURISTICS.shortlist_multiplier == 3


@pytest.mark.asyncio
async def test_scrape_tool_skips_new_urls_from_bot_blocked_domain(monkeypatch):
    from web_scout import tools

    tracker = ResearchTracker()
    tracker.record_bot_detection("https://example.org/protected-a", "bot_detected: challenge page")
    tracker.record_bot_detection("https://example.org/protected-b", "bot_detected: challenge page")

    async def _unexpected_run(agent, input_text, max_turns=15):
        raise AssertionError("Runner.run should not be called for blocked domains")

    async def _no_cleanup():
        pass

    monkeypatch.setattr(
        tools,
        "_build_extractor_agent",
        lambda *args, **kwargs: (object(), _no_cleanup),
    )
    monkeypatch.setattr(tools.Runner, "run", _unexpected_run)

    scrape_tool = create_scrape_and_extract_tool(extractor_model="dummy", tracker=tracker, query="test")
    result = await scrape_tool("https://example.org/new-page")

    assert "domain blocked by bot protection" in result
    assert "example.org" in result
    assert tracker.bot_blocked_domains() == {"example.org"}
