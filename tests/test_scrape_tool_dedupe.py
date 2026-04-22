import asyncio

import pytest

from web_scout.tools import (
    ResearchTracker,
    _classify_failure_action,
    _ExtractorOutput,
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
