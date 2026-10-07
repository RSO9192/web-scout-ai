"""Environment selection and the organization-wide Jev opt-out."""

from inspect import signature
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from web_scout import _classification as classification
from web_scout import _pipeline_flow as flow
from web_scout import agent
from web_scout.scraping import _crawler

FEATURES = ("crawler", "coverage", "followup", "pdf_claims")


@pytest.mark.parametrize("value", [None, "false", " FALSE "])
def test_jev_remains_default(monkeypatch, value):
    monkeypatch.delenv("WEB_SCOUT_CLASSIFICATION_BACKEND")
    if value is not None:
        monkeypatch.setenv("DISABLE_JEV", value)
    assert all(classification.backend(feature) == "jev" for feature in FEATURES)


@pytest.mark.parametrize("feature", FEATURES)
@pytest.mark.parametrize("selected", ["jev", "gpt"])
def test_feature_setting_is_independent(monkeypatch, feature, selected):
    monkeypatch.delenv("WEB_SCOUT_CLASSIFICATION_BACKEND")
    monkeypatch.setenv(f"WEB_SCOUT_{feature.upper()}_BACKEND", selected)
    assert classification.backend(feature) == selected
    assert all(classification.backend(other) == "jev" for other in FEATURES if other != feature)


@pytest.mark.parametrize("value", ["true", "TRUE", " true "])
def test_disable_jev_overrides_all_settings_without_credentials(monkeypatch, value):
    monkeypatch.setenv("DISABLE_JEV", value)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("WEB_SCOUT_CLASSIFICATION_BACKEND", "jev")
    for feature in FEATURES:
        monkeypatch.setenv(f"WEB_SCOUT_{feature.upper()}_BACKEND", "jev")
    assert all(classification.backend(feature) == "gpt" for feature in FEATURES)


@pytest.mark.parametrize("feature", FEATURES)
def test_invalid_feature_setting_fails(monkeypatch, feature):
    variable = f"WEB_SCOUT_{feature.upper()}_BACKEND"
    monkeypatch.setenv(variable, "unknown")
    with pytest.raises(classification.ClassificationError, match=variable):
        classification.backend(feature)


def test_followup_selection_is_no_longer_a_public_parameter():
    assert "followup_backend" not in signature(agent.run_web_research).parameters


@pytest.mark.asyncio
@pytest.mark.parametrize("direct_url", [None, "https://example.org/report"])
async def test_disable_jev_routes_public_pipeline_to_gpt_without_jev_token(monkeypatch, direct_url):
    monkeypatch.setenv("DISABLE_JEV", "true")
    monkeypatch.setenv("WEB_SCOUT_CLASSIFICATION_BACKEND", "jev")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    require = Mock(side_effect=AssertionError("Jev credential check must not run"))
    monkeypatch.setattr(classification, "require_credentials", require)
    monkeypatch.setattr("web_scout.utils.get_model", lambda model: model)
    monkeypatch.setattr(agent, "create_scrape_and_extract_tool", lambda **kwargs: AsyncMock())
    run_mode = AsyncMock()
    monkeypatch.setattr(agent, "_run_direct_url_mode", run_mode)
    monkeypatch.setattr(agent, "_run_search_mode", run_mode)
    monkeypatch.setattr(agent, "_synthesise_result", AsyncMock(return_value="result"))
    assert await agent.run_web_research("query", direct_url=direct_url, verify_pdf_claims=True) == "result"
    assert run_mode.await_args.kwargs["followup_backend"] == "gpt"
    assert run_mode.await_args.kwargs["followup_model"] is not None
    require.assert_not_called()


@pytest.mark.asyncio
async def test_disable_jev_uses_gpt_followup_ranker(monkeypatch):
    monkeypatch.setenv("DISABLE_JEV", "true")
    jev = AsyncMock(side_effect=AssertionError("Jev must not run"))
    monkeypatch.setattr(flow, "select_links_with_jev", jev)
    urls = [f"https://example.org/reports/report-{i}.pdf" for i in range(3)]
    result = SimpleNamespace(final_output_as=lambda _: agent.FollowupSelection(selected_urls=[urls[1]]))
    gpt = AsyncMock(return_value=result)
    monkeypatch.setattr(flow.Runner, "run", gpt)
    selected = await flow._rerank_followup_urls(
        query="report", parent_url="https://example.org/root", parent_content="reports",
        candidates=urls, cap=1, model="dummy", selector=classification.backend("followup"),
    )
    assert selected == [urls[1]]
    gpt.assert_awaited_once()
    jev.assert_not_awaited()


@pytest.mark.asyncio
async def test_disable_jev_uses_gpt_crawler(monkeypatch):
    monkeypatch.setenv("DISABLE_JEV", "true")
    crawler = _crawler.Crawl4AICrawler()
    gpt = AsyncMock(return_value=["https://example.org/report.pdf"])
    monkeypatch.setattr(crawler, "_llm_select", gpt)
    selected = await crawler._select_links(SimpleNamespace(title="report", url="https://example.org"), None)
    assert selected == ["https://example.org/report.pdf"]
    gpt.assert_awaited_once()


@pytest.mark.asyncio
async def test_disable_jev_uses_gpt_coverage(monkeypatch):
    from web_scout.tools.tracker import ResearchTracker

    monkeypatch.setenv("DISABLE_JEV", "true")
    monkeypatch.setenv("WEB_SCOUT_COVERAGE_BACKEND", "jev")
    jev = AsyncMock(side_effect=AssertionError("Jev must not run"))
    monkeypatch.setattr(flow, "jev_coverage", jev)
    evaluation = agent.CoverageEvaluation(
        fully_answered=True, gaps="", promising_unscraped_urls=[], needs_new_searches=False,
    )
    gpt = AsyncMock(return_value=SimpleNamespace(final_output_as=lambda _: evaluation))
    monkeypatch.setattr(flow.Runner, "run", gpt)
    tracker = ResearchTracker()
    tracker.record_scrape("https://example.org/report", "Report", "Relevant facts")
    assert await flow._evaluate_search_coverage(
        query="facts", include_domains=None, depth={}, evaluator_agent="dummy",
        tracker=tracker, exclude_domains=None, state=agent.SearchLoopState(),
    )
    gpt.assert_awaited_once()
    jev.assert_not_awaited()
