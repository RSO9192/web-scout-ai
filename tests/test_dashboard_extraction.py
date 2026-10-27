"""Extractor integration for dashboard data sources: catalog, ``open_data_source`` and data-file citations."""

import json
import logging
from types import SimpleNamespace

from agents.tool import ToolContext

import web_scout.tools.extractor as _extractor
import web_scout.tools.scraper as _tools_scraper
from web_scout.scraping._data_sources import DataSource
from web_scout.tools import ResearchTracker, create_scrape_and_extract_tool, resolve_scrape_outcome
from web_scout.tools.extractor import build_extractor_agent
from web_scout.tools.types import CachedSourceArtifact, DataQuote, ExtractorOutput

QUERY = "Deforestation area in Altamira 2025-2026"
DASH_URL = "https://terrabrasilis.example.org/app/dashboard/deforestation"
ARTICLE_URL = "https://news.example.org/2026/amazon-deforestation"
SENTINEL = "[No relevant content found for this query]"


def _json_source(source_id: str, url: str, payload) -> DataSource:
    body = json.dumps(payload)
    return DataSource(source_id, url, "response_json", "application/json", body, len(body))


NAMES_SRC = _json_source(
    "D1",
    "https://terrabrasilis.example.org/api/municipalities.json",
    [{"gid": 19124, "name": "Altamira"}, {"gid": 19125, "name": "Lábrea"}, {"gid": 19126, "name": "Apuí"}],
)
DATA_SRC = _json_source(
    "D2",
    "https://terrabrasilis.example.org/api/increments.json",
    {
        "period": "2025-2026",
        "features": [
            {"gid": 19124, "area": 155.8},
            {"gid": 19125, "area": 98.1},
            {"gid": 19126, "area": 120.4},
        ],
    },
)
XLSX_SRC = DataSource(
    "D3",
    "https://terrabrasilis.example.org/downloads/increments.xlsx",
    "download_xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "",
    4096,
)
XLSX_MARKDOWN = "## Sheet1\n\n| municipality | area |\n| --- | --- |\n| Altamira | 155.8 |\n| Apuí | 120.4 |\n"
DASHBOARD_TEXT = "Deforestation dashboard. Select a municipality and a period to see yearly increments. " * 40
ARTICLE_TEXT = "Deforestation in the Amazon fell sharply this year, according to officials in Brasília. " * 40


class _FakeRunResult:
    def __init__(self, output):
        self._output = output

    def final_output_as(self, _output_type):
        return self._output


def install_fake_extractor(monkeypatch, output: ExtractorOutput) -> SimpleNamespace:
    """Fake ``run_with_retry`` in the scrape tool; returns what the real extractor agent was given."""
    seen = SimpleNamespace(agent=None, input_text="", tool_names=set())

    async def _fake_run(agent, input_text, max_turns=30):
        seen.agent = agent
        seen.input_text = input_text
        seen.tool_names = {tool.name for tool in agent.tools}
        return _FakeRunResult(output)

    monkeypatch.setattr(_tools_scraper, "run_with_retry", _fake_run)
    return seen


def cache_artifact(monkeypatch, *, dashboard_kind="", data_sources=(), text=None) -> None:
    """Serve one cached text artifact for any URL the scrape tool fetches."""
    if text is None:
        text = DASHBOARD_TEXT if dashboard_kind else ARTICLE_TEXT

    async def _fake_fetch(*, url, **_kwargs):
        return (
            CachedSourceArtifact(
                url=url,
                title="Page",
                artifact_kind="text",
                text_content=text,
                dashboard_kind=dashboard_kind,
                data_sources=tuple(data_sources),
            ),
            None,
        )

    monkeypatch.setattr(_tools_scraper, "get_or_fetch_session_source_artifact", _fake_fetch)


def make_tool(tracker: ResearchTracker | None = None):
    """Build the scrape tool with a dummy extractor model."""
    return create_scrape_and_extract_tool(extractor_model="dummy", tracker=tracker, query=QUERY)


def _ctx() -> ToolContext:
    return ToolContext(context=None, tool_name="open_data_source", tool_call_id="test-id", tool_arguments="{}")


def _data_tool(data_sources, **kwargs):
    agent, _cleanup = build_extractor_agent(
        "dummy",
        QUERY,
        DASH_URL,
        None,
        pre_fetched_content=DASHBOARD_TEXT,
        data_sources=data_sources,
        is_dashboard=True,
        **kwargs,
    )
    return next(tool for tool in agent.tools if tool.name == "open_data_source")


async def test_catalog_and_tool_reach_the_extractor(monkeypatch):
    seen = install_fake_extractor(
        monkeypatch, output=ExtractorOutput(title="t", has_evidence=False, relevant_content=SENTINEL)
    )
    cache_artifact(monkeypatch, dashboard_kind="dashboard", data_sources=(NAMES_SRC, DATA_SRC))
    await make_tool()(DASH_URL)
    assert "D1" in seen.input_text and "open_data_source" in seen.tool_names
    assert "## Data sources" in seen.input_text
    assert "click_element" in seen.tool_names
    assert "open_data_source" in seen.agent.instructions


async def test_verified_data_quote_is_recorded_as_its_own_source(monkeypatch, caplog):
    install_fake_extractor(
        monkeypatch,
        output=ExtractorOutput(
            title="t",
            has_evidence=False,
            relevant_content=SENTINEL,
            data_quotes=[DataQuote(source_id="D2", quote="Altamira (gid 19124), 2025-2026: area 155.8 km2")],
        ),
    )
    cache_artifact(monkeypatch, dashboard_kind="dashboard", data_sources=(NAMES_SRC, DATA_SRC))
    tracker = ResearchTracker()
    tool = make_tool(tracker)
    with caplog.at_level(logging.INFO):
        rendered = await tool(DASH_URL)
    assert tracker.action_for(DATA_SRC.url) == "scraped"
    assert tracker.action_for(DASH_URL) == "scraped_irrelevant"
    assert "155.8" in tracker.entry_for(DATA_SRC.url).content
    assert f"[extract] data_quote_recorded D2 {DATA_SRC.url}" in caplog.messages
    outcome = resolve_scrape_outcome(tool, DASH_URL, rendered)
    assert outcome.status == "success"
    assert "155.8" in rendered and DATA_SRC.url in rendered


async def test_quote_with_unknown_source_is_dropped(monkeypatch, caplog):
    install_fake_extractor(
        monkeypatch,
        output=ExtractorOutput(
            title="t",
            has_evidence=False,
            relevant_content=SENTINEL,
            data_quotes=[DataQuote(source_id="D9", quote="Altamira: 155.8 km2")],
        ),
    )
    cache_artifact(monkeypatch, dashboard_kind="dashboard", data_sources=(NAMES_SRC, DATA_SRC))
    tracker = ResearchTracker()
    tool = make_tool(tracker)
    with caplog.at_level(logging.INFO):
        rendered = await tool(DASH_URL)
    assert tracker.action_for(DATA_SRC.url) is None
    assert "[extract] data_quote_dropped D9 unknown source" in caplog.messages
    assert resolve_scrape_outcome(tool, DASH_URL, rendered).status == "failure"


async def test_page_evidence_and_quote_are_both_recorded(monkeypatch):
    install_fake_extractor(
        monkeypatch,
        output=ExtractorOutput(
            title="Dashboard",
            relevant_content="The dashboard reports yearly deforestation increments per municipality. " * 5,
            data_quotes=[DataQuote(source_id="D2", quote="Altamira, 2025-2026: area 155.8")],
        ),
    )
    cache_artifact(monkeypatch, dashboard_kind="dashboard", data_sources=(NAMES_SRC, DATA_SRC))
    tracker = ResearchTracker()
    await make_tool(tracker)(DASH_URL)
    assert tracker.action_for(DASH_URL) == "scraped"
    assert tracker.action_for(DATA_SRC.url) == "scraped"
    assert "Dashboard" in tracker.entry_for(DATA_SRC.url).title


async def test_non_dashboard_page_gets_no_catalog(monkeypatch):
    seen = install_fake_extractor(monkeypatch, output=ExtractorOutput(title="t", relevant_content="Fact 1. " * 80))
    cache_artifact(monkeypatch, dashboard_kind="", data_sources=())
    await make_tool()(ARTICLE_URL)
    assert "open_data_source" not in seen.tool_names and "Data sources" not in seen.input_text
    assert "click_element" not in seen.tool_names
    assert "open_data_source" not in seen.agent.instructions
    assert "dashboard" not in seen.agent.instructions.lower()


def test_open_data_source_schema_is_strict():
    tool = _data_tool((NAMES_SRC, DATA_SRC))
    schema = tool.params_json_schema
    assert tool.strict_json_schema
    assert set(schema["properties"]) == {"source_id", "lookup", "rank_by", "descending", "where"}
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert {"type": "null"} in schema["properties"]["where"]["anyOf"]
    item = schema["$defs"]["FieldFilter"]
    assert item["properties"] == {
        "field": {"title": "Field", "type": "string"},
        "value": {"anyOf": [{"type": "string"}, {"type": "null"}], "title": "Value"},
        "any_of": {"anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}], "title": "Any Of"},
    }
    assert item["required"] == ["field", "value", "any_of"] and item["additionalProperties"] is False


async def test_open_data_source_filters_and_ranks(caplog):
    tool = _data_tool((NAMES_SRC, DATA_SRC))
    args = {"source_id": "D2", "lookup": "", "rank_by": "area", "descending": True, "where": None}
    with caplog.at_level(logging.INFO):
        ranked = await tool.on_invoke_tool(_ctx(), json.dumps(args))
    lines = [json.loads(line) for line in ranked.splitlines()]
    assert lines[0]["source_id"] == "D2" and lines[0]["total_matches"] == 3 and lines[0]["ranked_by"] == "area"
    assert [record["area"] for record in lines[1:]] == [155.8, 120.4, 98.1]
    assert f"[extract] data_source_opened D2 {DATA_SRC.url}" in caplog.messages

    args = {**args, "rank_by": None, "where": [{"field": "gid", "value": "19125"}]}
    filtered = [json.loads(line) for line in (await tool.on_invoke_tool(_ctx(), json.dumps(args))).splitlines()]
    assert filtered[0]["total_matches"] == 1 and filtered[0]["where"] == {"gid": "19125"}
    assert filtered[1]["area"] == 98.1


async def test_open_data_source_unknown_id_lists_known_ids():
    tool = _data_tool((NAMES_SRC, DATA_SRC))
    args = {"source_id": "D7", "lookup": "Altamira", "rank_by": None, "descending": True, "where": None}
    result = await tool.on_invoke_tool(_ctx(), json.dumps(args))
    assert "D7" in result and "D1" in result and "D2" in result


async def test_open_data_source_parses_each_source_once(monkeypatch):
    calls = []
    real = _extractor.records_from_source

    def _counting(source):
        calls.append(source.source_id)
        return real(source)

    monkeypatch.setattr(_extractor, "records_from_source", _counting)
    tool = _data_tool((NAMES_SRC, DATA_SRC))
    for term in ("Altamira", "Apuí"):
        args = {"source_id": "D1", "lookup": term, "rank_by": None, "descending": True, "where": None}
        result = await tool.on_invoke_tool(_ctx(), json.dumps(args))
        assert term in result
    assert calls == ["D1"]


async def test_open_data_source_fetches_xlsx_body_once(monkeypatch):
    fetched = []

    async def _fake_fetch(*, url, **_kwargs):
        fetched.append(url)
        return CachedSourceArtifact(url=url, title="x", artifact_kind="text", text_content=XLSX_MARKDOWN), None

    monkeypatch.setattr(_extractor, "get_or_fetch_session_source_artifact", _fake_fetch)
    tool = _data_tool((XLSX_SRC,))
    for _ in range(2):
        args = {"source_id": "D3", "lookup": "", "rank_by": "area", "descending": False, "where": None}
        lines = [json.loads(line) for line in (await tool.on_invoke_tool(_ctx(), json.dumps(args))).splitlines()]
        assert [record["municipality"] for record in lines[1:]] == ["Apuí", "Altamira"]
    assert fetched == [XLSX_SRC.url]


def _inline_source(source_id: str, payload) -> DataSource:
    body = json.dumps(payload)
    return DataSource(source_id, DASH_URL, "inline_json", "application/json", body, len(body))


INLINE_SRC = _inline_source("D1", {"period": "2025-2026", "rows": [{"municipality": "Altamira", "area": 155.8}]})
INLINE_SRC_2 = _inline_source("D2", {"period": "2024-2025", "rows": [{"municipality": "Altamira", "area": 160.2}]})


async def test_inline_quote_without_page_evidence_makes_the_page_citable(monkeypatch):
    install_fake_extractor(
        monkeypatch,
        output=ExtractorOutput(
            title="t",
            has_evidence=False,
            relevant_content=SENTINEL,
            data_quotes=[DataQuote(source_id="D1", quote="Altamira, 2025-2026: area 155.8")],
        ),
    )
    cache_artifact(monkeypatch, dashboard_kind="dashboard", data_sources=(INLINE_SRC,))
    tracker = ResearchTracker()
    await make_tool(tracker)(DASH_URL)
    entry = tracker.entry_for(DASH_URL)
    assert tracker.action_for(DASH_URL) == "scraped"
    assert "155.8" in entry.content and "scraped but irrelevant" not in entry.content
    assert entry.title == "t"


async def test_inline_quote_is_kept_with_page_evidence(monkeypatch):
    install_fake_extractor(
        monkeypatch,
        output=ExtractorOutput(
            title="t",
            relevant_content="The dashboard shows yearly deforestation increments. " * 5,
            data_quotes=[DataQuote(source_id="D1", quote="Altamira, 2025-2026: area 155.8")],
        ),
    )
    cache_artifact(monkeypatch, dashboard_kind="dashboard", data_sources=(INLINE_SRC,))
    tracker = ResearchTracker()
    await make_tool(tracker)(DASH_URL)
    content = tracker.entry_for(DASH_URL).content
    assert tracker.action_for(DASH_URL) == "scraped"
    assert "yearly deforestation increments" in content and "155.8" in content


async def test_quotes_from_two_inline_sources_are_both_kept(monkeypatch):
    install_fake_extractor(
        monkeypatch,
        output=ExtractorOutput(
            title="t",
            has_evidence=False,
            relevant_content=SENTINEL,
            data_quotes=[
                DataQuote(source_id="D1", quote="Altamira, 2025-2026: area 155.8"),
                DataQuote(source_id="D2", quote="Altamira, 2024-2025: area 160.2"),
            ],
        ),
    )
    cache_artifact(monkeypatch, dashboard_kind="dashboard", data_sources=(INLINE_SRC, INLINE_SRC_2))
    tracker = ResearchTracker()
    await make_tool(tracker)(DASH_URL)
    content = tracker.entry_for(DASH_URL).content
    assert "155.8" in content and "160.2" in content


async def test_data_file_shared_by_two_pages_keeps_quotes_from_both(monkeypatch):
    cache_artifact(monkeypatch, dashboard_kind="dashboard", data_sources=(NAMES_SRC, DATA_SRC))
    tracker = ResearchTracker()
    tool = make_tool(tracker)
    first = DataQuote(source_id="D2", quote="Altamira, 2025-2026: area 155.8")
    second = DataQuote(source_id="D2", quote="Lábrea, 2025-2026: area 98.1")
    for page, quotes in ((DASH_URL, [first]), (DASH_URL + "/municipalities", [first, second])):
        install_fake_extractor(
            monkeypatch,
            output=ExtractorOutput(title=page, has_evidence=False, relevant_content=SENTINEL, data_quotes=quotes),
        )
        await tool(page)
    entry = tracker.entry_for(DATA_SRC.url)
    assert entry.content.split("\n") == [first.quote, second.quote]
    assert entry.title == f"{DASH_URL} (data file: increments.json)"


async def test_dashboard_with_empty_text_still_offers_its_data_sources(monkeypatch):
    seen = install_fake_extractor(
        monkeypatch, output=ExtractorOutput(title="t", has_evidence=False, relevant_content=SENTINEL)
    )
    cache_artifact(monkeypatch, dashboard_kind="dashboard", data_sources=(NAMES_SRC, DATA_SRC), text="")
    await make_tool()(DASH_URL)
    assert "## Data sources" in seen.input_text and "open_data_source" in seen.tool_names


async def test_page_with_empty_text_and_no_sources_still_fails_early(monkeypatch):
    seen = install_fake_extractor(monkeypatch, output=ExtractorOutput(title="t", relevant_content="x"))
    cache_artifact(monkeypatch, dashboard_kind="", data_sources=(), text="")
    rendered = await make_tool()(ARTICLE_URL)
    assert seen.agent is None and "[Page returned empty content]" in rendered


async def test_open_data_source_rejects_conflicting_filters_on_one_field():
    tool = _data_tool((NAMES_SRC, DATA_SRC))
    where = [{"field": "gid", "value": "19124"}, {"field": "gid", "value": "19125"}]
    args = {"source_id": "D2", "lookup": "", "rank_by": None, "descending": True, "where": where}
    result = await tool.on_invoke_tool(_ctx(), json.dumps(args))
    assert "conflicting values for field 'gid'" in result


async def test_open_data_source_any_of_batches_name_lookup(caplog):
    tool = _data_tool((NAMES_SRC, DATA_SRC))
    where = [{"field": "gid", "value": None, "any_of": ["19124", "19125", "19126"]}]
    args = {"source_id": "D1", "lookup": "", "rank_by": None, "descending": True, "where": where}
    with caplog.at_level(logging.INFO):
        result = await tool.on_invoke_tool(_ctx(), json.dumps(args))
    lines = [json.loads(line) for line in result.splitlines()]
    assert lines[0]["total_matches"] == 3 and lines[0]["where"] == {"gid": ["19124", "19125", "19126"]}
    assert [record["name"] for record in lines[1:]] == ["Altamira", "Lábrea", "Apuí"]
    opened = [m for m in caplog.messages if m.startswith("[extract] data_source_opened")]
    assert opened == [f"[extract] data_source_opened D1 {NAMES_SRC.url}"]


async def test_field_filter_requires_exactly_one_of_value_or_any_of(caplog):
    tool = _data_tool((NAMES_SRC, DATA_SRC))
    cases = {
        "neither": [{"field": "gid", "value": None, "any_of": None}],
        "both": [{"field": "gid", "value": "19124", "any_of": ["19125"]}],
        "empty any_of": [{"field": "gid", "value": None, "any_of": []}],
        "value and any_of across filters": [
            {"field": "gid", "value": "19124", "any_of": None},
            {"field": "gid", "value": None, "any_of": ["19125"]},
        ],
        "two any_of": [
            {"field": "gid", "value": None, "any_of": ["19124"]},
            {"field": "gid", "value": None, "any_of": ["19125"]},
        ],
    }
    with caplog.at_level(logging.INFO):
        for name, where in cases.items():
            args = {"source_id": "D1", "lookup": "", "rank_by": None, "descending": True, "where": where}
            result = await tool.on_invoke_tool(_ctx(), json.dumps(args))
            assert result.startswith("[open_data_source error:") and "'gid'" in result, name
    assert not any(m.startswith("[extract] data_source_opened") for m in caplog.messages)
    # One value repeated, and one any_of next to a filter on another field, are fine.
    where = [
        {"field": "gid", "value": None, "any_of": ["19124", "19125"]},
        {"field": "name", "value": "Lábrea", "any_of": None},
        {"field": "name", "value": "Lábrea", "any_of": None},
    ]
    args = {"source_id": "D1", "lookup": "", "rank_by": None, "descending": True, "where": where}
    lines = (await tool.on_invoke_tool(_ctx(), json.dumps(args))).splitlines()
    assert json.loads(lines[0])["total_matches"] == 1 and json.loads(lines[1])["gid"] == 19125
