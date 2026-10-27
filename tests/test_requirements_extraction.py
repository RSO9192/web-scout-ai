"""Research requirements: planned once, carried by the tracker, and used by the page and PDF extractors."""

from types import SimpleNamespace

from test_dashboard_extraction import ARTICLE_URL, QUERY, cache_artifact, install_fake_extractor, make_tool

import web_scout._pipeline_flow as _flow
import web_scout.tools.scraper as _tools_scraper
from web_scout._pipeline_flow import _build_query_agents, _search_and_scrape_iteration
from web_scout._pipeline_types import PlannedSearchQuery, SearchQueryChecklist, SearchQueryGeneration
from web_scout.scraping.types import PdfDocumentLayout, PdfPageSpan
from web_scout.tools import ResearchTracker, pdf_extractor
from web_scout.tools.extractor import _BASE_EXTRACTOR_INSTRUCTIONS, format_research_query
from web_scout.tools.types import CachedSourceArtifact, ExtractorOutput

PDF_URL = "https://www.gov.example.org/prodes/presentation.pdf"
REQUIREMENTS_BLOCK = "Research requirements:\n1. A\n2. B"


def test_query_agent_always_lists_requirements(monkeypatch):
    monkeypatch.setenv("DISABLE_JEV", "true")
    qa, _ = _build_query_agents(query_gen_model="model", evaluator_model="model", domain_expertise=None)
    assert qa.output_type is SearchQueryChecklist and "atomic requirement" in qa.instructions


def test_coverage_criteria_reach_planner_only_on_jev(monkeypatch):
    strict = "Be extremely strict."
    monkeypatch.setenv("DISABLE_JEV", "true")
    qa, ev = _build_query_agents("model", "model", None, evaluator_extra_prompt=strict)
    assert strict not in qa.instructions and "atomic requirement" in qa.instructions
    assert strict in ev.instructions
    monkeypatch.setenv("DISABLE_JEV", "false")
    qa, _ = _build_query_agents("model", "model", None, evaluator_extra_prompt=strict)
    assert "Coverage criteria: " + strict in qa.instructions


async def test_requirements_reach_tracker(monkeypatch):
    monkeypatch.setenv("DISABLE_JEV", "true")
    calls = []

    async def _fake_run(agent, prompt):
        calls.append(agent.output_type)
        output = SearchQueryChecklist(queries=[PlannedSearchQuery(query="q1")], requirements=["A", "B"])
        return SimpleNamespace(final_output_as=lambda _type: output)

    async def _fake_execute(**_kwargs):
        return []

    async def _fake_select(**_kwargs):
        return []

    async def _fake_scrape(_scrape_tool, _urls, *, empty_log_message):
        return None

    monkeypatch.setattr(_flow.Runner, "run", _fake_run)
    monkeypatch.setattr(_flow, "_execute_searches", _fake_execute)
    monkeypatch.setattr(_flow, "_select_search_urls_with_model", _fake_select)
    monkeypatch.setattr(_flow, "_scrape_urls", _fake_scrape)

    qa, _ = _build_query_agents(query_gen_model="model", evaluator_model="model", domain_expertise=None)
    tracker = ResearchTracker()
    kwargs = dict(
        query=QUERY,
        include_domains=None,
        depth={"queries_first": 2, "queries_followup": 2},
        missing_info="",
        query_gen_agent=qa,
        backend=None,
        tracker=tracker,
        scrape_tool=None,
    )
    await _search_and_scrape_iteration(iteration=0, **kwargs)
    assert tracker.requirements == ["A", "B"]
    # Later rounds plan queries only and keep the first round's requirements.
    await _search_and_scrape_iteration(iteration=1, **kwargs)
    assert calls == [SearchQueryChecklist, SearchQueryGeneration]
    assert "atomic requirement" not in qa.instructions
    assert tracker.requirements == ["A", "B"]


def test_tracker_requirements_default_empty():
    assert ResearchTracker().requirements == []


def test_format_research_query():
    assert format_research_query("Q", []) == "Q"
    assert format_research_query("Q", ["Q"]) == "Q"
    assert format_research_query("Q", ["A", "B"]) == "Q\n" + REQUIREMENTS_BLOCK


async def test_extractor_input_carries_requirements(monkeypatch):
    seen = install_fake_extractor(monkeypatch, output=ExtractorOutput(title="t", relevant_content="Fact. " * 80))
    cache_artifact(monkeypatch)
    tracker = ResearchTracker()
    tracker.requirements = ["A", "B"]
    await make_tool(tracker)(ARTICLE_URL)
    assert REQUIREMENTS_BLOCK in seen.input_text


async def test_extractor_input_without_requirements_is_unchanged(monkeypatch):
    seen = install_fake_extractor(monkeypatch, output=ExtractorOutput(title="t", relevant_content="Fact. " * 80))
    cache_artifact(monkeypatch)
    await make_tool(ResearchTracker())(ARTICLE_URL)
    assert f"Research query: {QUERY}\nURL: {ARTICLE_URL}" in seen.input_text
    assert "Research requirements" not in seen.input_text


def test_instructions_contain_requirement_rule():
    assert "at least one requirement" in _BASE_EXTRACTOR_INSTRUCTIONS
    assert "at least one requirement" in pdf_extractor._NO_EVIDENCE_RULE


def test_metadata_exclusions_yield_to_what_the_query_asks():
    qa, _ = _build_query_agents(query_gen_model="model", evaluator_model="model", domain_expertise=None)
    assert "unless the original query itself asks for them" in qa.instructions
    assert "unless the requirement explicitly asks for that name" in _BASE_EXTRACTOR_INSTRUCTIONS
    assert "unless the requirement explicitly asks for that name" in pdf_extractor._NO_EVIDENCE_RULE


def test_planner_splits_compound_questions():
    qa, _ = _build_query_agents(query_gen_model="model", evaluator_model="model", domain_expertise=None)
    assert "Split a compound question into separate requirements" in qa.instructions
    assert "Never return the whole question as a single requirement unless it asks for exactly one fact" in (
        qa.instructions
    )


def test_pdf_rule_treats_uncovered_regions_as_unsatisfied_requirements():
    rule = pdf_extractor._NO_EVIDENCE_RULE
    assert "A requirement about a country or region the document does not cover is unsatisfied" in rule
    assert "not a reason to reject the document" in rule
    assert "When the query lists no requirements" in rule
    assert "methodology" in rule


async def test_linked_document_path_receives_requirements(monkeypatch):
    install_fake_extractor(monkeypatch, output=ExtractorOutput(title="t", relevant_content="Fact. " * 80))
    cache_artifact(monkeypatch)
    seen = SimpleNamespace(query=None)
    real_build = _tools_scraper.build_extractor_agent

    def _recording_build(model, query, *args, **kwargs):
        # This query also drives scrape_linked_document's PDF extraction inside the agent.
        seen.query = query
        return real_build(model, query, *args, **kwargs)

    monkeypatch.setattr(_tools_scraper, "build_extractor_agent", _recording_build)
    tracker = ResearchTracker()
    tracker.requirements = ["A", "B"]
    await make_tool(tracker)(ARTICLE_URL)
    assert seen.query.endswith(REQUIREMENTS_BLOCK)


def install_fake_pdf_extract(monkeypatch) -> SimpleNamespace:
    """Serve a cached PDF artifact and record the query passed to ``extract_pdf_for_query``."""
    seen = SimpleNamespace(query=None)
    markdown = "========== page 1 start ==========\nPRODES 2026: 3,335 km2.\n========== page 1 end ==========\n"
    layout = PdfDocumentLayout(document_title="PRODES", pages=(PdfPageSpan(page=1, start_line=1, end_line=3),))

    async def _fake_fetch(*, url, **_kwargs):
        return CachedSourceArtifact(
            url=url, title="PRODES", artifact_kind="text", text_content=markdown, layout=layout
        ), None

    async def _fake_extract(*, query, **_kwargs):
        seen.query = query
        return "PRODES", "PRODES 2026: 3,335 km2 [p. 1].", (1,)

    monkeypatch.setattr(_tools_scraper, "get_or_fetch_session_source_artifact", _fake_fetch)
    monkeypatch.setattr(_tools_scraper, "extract_pdf_for_query", _fake_extract)
    return seen


async def test_pdf_extractor_receives_requirements(monkeypatch):
    seen = install_fake_pdf_extract(monkeypatch)
    tracker = ResearchTracker()
    tracker.requirements = ["A", "B"]
    await make_tool(tracker)(PDF_URL)
    assert seen.query.endswith(REQUIREMENTS_BLOCK)
