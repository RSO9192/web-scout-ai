"""Tests for the run_web_research orchestration pipeline.

Covers: input validation, direct-URL mode flow, hub detection,
synthesis judge retry, and _rerank_followup_urls edge cases.
All LLM and network calls are replaced with synchronous fakes via monkeypatch.
"""

import pytest

from web_scout import agent as _agent_module
from web_scout.agent import (
    _rerank_followup_urls,
    FollowupSelection,
    _build_synth_prompt,
    run_web_research,
)
from web_scout.models import WebResearchResultRaw, UrlEntry


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _make_entry(url: str, content: str = "") -> UrlEntry:
    return UrlEntry(url=url, content=content)


class _FakeRunResult:
    """Minimal stand-in for openai-agents Runner.run() result."""

    def __init__(self, output):
        self._output = output

    def final_output_as(self, _output_type):
        return self._output


def _patch_scrape_tool(monkeypatch, return_value: str = "Some scraped content " * 30):
    """Replace create_scrape_and_extract_tool with one that returns a fixed string."""
    calls = []

    async def _fake_scrape(url: str) -> str:
        calls.append(url)
        return return_value

    monkeypatch.setattr(
        _agent_module,
        "create_scrape_and_extract_tool",
        lambda **kwargs: _fake_scrape,
    )
    return calls


def _patch_runner(monkeypatch, output):
    """Replace Runner.run with a fake that returns output for every call."""

    async def _fake_run(agent_obj, prompt, **kwargs):
        return _FakeRunResult(output)

    monkeypatch.setattr(_agent_module.Runner, "run", _fake_run)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_web_research_raises_for_invalid_research_depth():
    """`research_depth` must be 'standard' or 'deep'; anything else raises ValueError."""
    with pytest.raises(ValueError, match="Unknown research_depth"):
        await run_web_research(
            query="fish",
            models={"web_researcher": "dummy"},
            research_depth="turbo",
        )


@pytest.mark.asyncio
async def test_run_web_research_raises_for_unknown_search_backend(monkeypatch):
    """`search_backend` must be 'serper'; anything else raises ValueError."""
    _patch_scrape_tool(monkeypatch)
    _patch_runner(monkeypatch, WebResearchResultRaw(synthesis="ok"))

    with pytest.raises(ValueError, match="Unknown search_backend"):
        await run_web_research(
            query="fish",
            models={"web_researcher": "dummy", "content_extractor": "dummy"},
            search_backend="duckduckgo",
        )


@pytest.mark.asyncio
async def test_run_web_research_raises_for_missing_serper_api_key(monkeypatch):
    """search_backend='serper' without SERPER_API_KEY raises ValueError."""
    _patch_scrape_tool(monkeypatch)
    _patch_runner(monkeypatch, WebResearchResultRaw(synthesis="ok"))
    monkeypatch.delenv("SERPER_API_KEY", raising=False)

    with pytest.raises(ValueError, match="SERPER_API_KEY"):
        await run_web_research(
            query="fish",
            models={"web_researcher": "dummy", "content_extractor": "dummy"},
            search_backend="serper",
        )


# ---------------------------------------------------------------------------
# Direct URL mode — happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_direct_url_mode_scrapes_url_and_synthesizes(monkeypatch):
    """In direct-URL mode the pipeline scrapes exactly the given URL and returns a synthesis."""
    scrape_calls = _patch_scrape_tool(
        monkeypatch,
        return_value="Fish production report content " * 20,
    )
    _patch_runner(monkeypatch, WebResearchResultRaw(synthesis="Fish rose 5% in 2023."))

    result = await run_web_research(
        query="fish production 2023",
        models={"web_researcher": "dummy", "content_extractor": "dummy"},
        direct_url="https://fao.org/fishery/static/report.pdf",
    )

    assert "https://fao.org/fishery/static/report.pdf" in scrape_calls
    assert result.synthesis == "Fish rose 5% in 2023."


@pytest.mark.asyncio
async def test_direct_url_mode_document_url_skips_follow_up_scraping(monkeypatch):
    """A .pdf direct URL must not trigger follow-up link scraping."""
    scrape_calls = _patch_scrape_tool(
        monkeypatch,
        # Even if content contains links, they must be ignored for document URLs
        return_value=(
            "Report content. "
            "[Related report](https://fao.org/fishery/other-report.pdf) "
        ) * 20,
    )
    _patch_runner(monkeypatch, WebResearchResultRaw(synthesis="Done."))

    await run_web_research(
        query="fish production",
        models={"web_researcher": "dummy", "content_extractor": "dummy"},
        direct_url="https://fao.org/fishery/report.pdf",
    )

    # Only the direct URL itself should be scraped — no follow-ups
    assert scrape_calls == ["https://fao.org/fishery/report.pdf"]


# ---------------------------------------------------------------------------
# Direct URL mode — hub detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_direct_url_hub_page_triggers_deepening(monkeypatch):
    """When the scraped page is a hub (contains the hub marker), follow-up links are scraped."""
    hub_content = (
        "**Page type: list**\n"
        "[Fish Report 2023](https://fao.org/fishery/report-2023)\n"
        "[Fish Report 2022](https://fao.org/fishery/report-2022)\n"
    ) * 5

    scrape_calls = []
    call_count = [0]

    async def _fake_scrape(url: str) -> str:
        scrape_calls.append(url)
        call_count[0] += 1
        if call_count[0] == 1:
            return hub_content  # first call: the hub page
        return "Individual report content " * 20

    monkeypatch.setattr(
        _agent_module,
        "create_scrape_and_extract_tool",
        lambda **kwargs: _fake_scrape,
    )

    # Reranker returns the same candidates (first URL)
    async def _fake_run(agent_obj, prompt, **kwargs):
        return _FakeRunResult(
            FollowupSelection(selected_urls=["https://fao.org/fishery/report-2023"])
            if "FollowupSelection" in str(type(agent_obj.output_type))
            else WebResearchResultRaw(synthesis="Hub deepened synthesis.")
        )

    monkeypatch.setattr(_agent_module.Runner, "run", _fake_run)

    await run_web_research(
        query="FAO fish production reports",
        models={"web_researcher": "dummy", "content_extractor": "dummy"},
        direct_url="https://fao.org/fishery/hub",
    )

    # At least the hub URL AND one follow-up URL were scraped
    assert "https://fao.org/fishery/hub" in scrape_calls
    assert len(scrape_calls) >= 2


# ---------------------------------------------------------------------------
# Direct URL mode — synthesis judge retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesis_judge_retries_when_hallucinated_citation(monkeypatch):
    """When the synthesiser produces a hallucinated citation, Runner.run is called a second time."""
    _patch_scrape_tool(monkeypatch, return_value="Solid report content " * 20)

    run_calls = []

    async def _fake_run(agent_obj, prompt, **kwargs):
        run_calls.append(prompt[:60])
        if len(run_calls) == 1:
            # First synthesis: contains a hallucinated URL not in valid_urls
            return _FakeRunResult(
                WebResearchResultRaw(
                    synthesis="Some fact [Hallucinated](https://invented.example.com/data)."
                )
            )
        # Retry synthesis: clean
        return _FakeRunResult(
            WebResearchResultRaw(synthesis="Clean synthesis with no citations.")
        )

    monkeypatch.setattr(_agent_module.Runner, "run", _fake_run)

    result = await run_web_research(
        query="fish",
        models={"web_researcher": "dummy", "content_extractor": "dummy"},
        direct_url="https://fao.org/fishery/report.pdf",
    )

    # Runner should have been called twice (initial + retry)
    assert len(run_calls) == 2
    assert result.synthesis == "Clean synthesis with no citations."


# ---------------------------------------------------------------------------
# _build_synth_prompt — domain expertise
# ---------------------------------------------------------------------------

def test_build_synth_prompt_includes_domain_expertise():
    """domain_expertise appears in the prompt so the synthesiser can use it."""
    scraped = [_make_entry("https://fao.org", "fish content")]
    prompt = _build_synth_prompt(
        query="fish production",
        scraped=scraped,
        snippet_only=[],
        bot_detected=[],
        blocked_by_policy=[],
        scrape_failed=[],
        source_http_error=[],
        domain_expertise="Marine biology and aquaculture",
    )
    assert "Marine biology and aquaculture" in prompt


def test_build_synth_prompt_omits_domain_expertise_section_when_none():
    """Without domain_expertise the prompt does not contain a Domain Expertise line."""
    scraped = [_make_entry("https://fao.org", "fish content")]
    prompt = _build_synth_prompt(
        query="fish production",
        scraped=scraped,
        snippet_only=[],
        bot_detected=[],
        blocked_by_policy=[],
        scrape_failed=[],
        source_http_error=[],
        domain_expertise=None,
    )
    assert "Domain Expertise" not in prompt


# ---------------------------------------------------------------------------
# _rerank_followup_urls — edge cases not covered in test_followup_reranker.py
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rerank_followup_urls_skips_llm_when_candidates_lte_cap(monkeypatch):
    """When candidates count ≤ cap, the LLM is never called."""
    run_called = []

    async def _fake_run(*args, **kwargs):
        run_called.append(True)
        return _FakeRunResult(FollowupSelection(selected_urls=[]))

    monkeypatch.setattr(_agent_module.Runner, "run", _fake_run)

    result = await _rerank_followup_urls(
        query="fish production report",
        parent_url="https://fao.org/hub",
        parent_content="Hub content",
        candidates=[
            "https://fao.org/fishery/annual-report-2023",
            "https://fao.org/fishery/annual-report-2022",
        ],
        cap=3,  # cap > len(candidates)
        model="dummy",
    )

    assert not run_called, "LLM should not be called when candidates ≤ cap"


@pytest.mark.asyncio
async def test_rerank_followup_urls_falls_back_to_heuristic_on_llm_exception(monkeypatch):
    """When Runner.run raises an exception, heuristic ranking is used as fallback."""

    async def _fake_run(*args, **kwargs):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(_agent_module.Runner, "run", _fake_run)

    candidates = [
        "https://fao.org/fishery/annual-report-2023",
        "https://fao.org/fishery/annual-report-2022",
        "https://fao.org/fishery/annual-report-2021",
        "https://fao.org/fishery/annual-report-2020",
    ]

    result = await _rerank_followup_urls(
        query="fish production report",
        parent_url="https://fao.org/hub",
        parent_content="Hub content",
        candidates=candidates,
        cap=2,
        model="dummy",
    )

    # Should fall back to heuristic ranking, returning up to cap results
    assert len(result) <= 2
    assert all(url in candidates for url in result)


@pytest.mark.asyncio
async def test_rerank_followup_urls_deduplicates_by_normalized_url(monkeypatch):
    """Duplicate URLs (differing only by tracking params) appear only once in output."""

    async def _fake_run(agent_obj, prompt, **kwargs):
        return _FakeRunResult(
            FollowupSelection(selected_urls=[
                "https://fao.org/fishery/report-2023",
                "https://fao.org/fishery/report-2022",
            ])
        )

    monkeypatch.setattr(_agent_module.Runner, "run", _fake_run)

    candidates = [
        "https://fao.org/fishery/report-2023",
        "https://fao.org/fishery/report-2023?utm_source=twitter",  # duplicate
        "https://fao.org/fishery/report-2022",
    ]

    result = await _rerank_followup_urls(
        query="fish production report",
        parent_url="https://fao.org/hub",
        parent_content="Hub content",
        candidates=candidates,
        cap=3,
        model="dummy",
    )

    result_normalized = [url.split("?")[0] for url in result]
    assert result_normalized.count("https://fao.org/fishery/report-2023") == 1
