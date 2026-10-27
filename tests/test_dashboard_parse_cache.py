"""Dashboard data sources carried through the parser and the URL cache."""

from web_scout.result_cache import call_cached_task, pdf_converter_version
from web_scout.scraping import _vision
from web_scout.scraping._data_sources import DataSource
from web_scout.scraping._parser import DefaultParser
from web_scout.scraping.context import URLContext
from web_scout.scraping.types import FetchResult, ParseResult, SourceArtifact
from web_scout.tools.session_cache import (
    _url_fetch_task,
    cacheable_from_parse_result,
    get_or_fetch_session_source_artifact,
)
from web_scout.tools.types import CachedSourceArtifact


def fetch_result(*, html: str, url: str = "https://t.org/app", **kwargs) -> FetchResult:
    return FetchResult(
        url=url,
        status=200,
        content_type="text/html",
        content_disposition="",
        html_content=html,
        body=None,
        headers={},
        used_browser=True,
        **kwargs,
    )


def ctx(url: str) -> URLContext:
    return URLContext(url=url, depth=0)


def parse_result(**kwargs) -> ParseResult:
    artifact = SourceArtifact(kind="text", title="App", text_content="Eurostat")
    return ParseResult(
        url="https://t.org/app", title="App", text_content="Eurostat", links=[], artifact=artifact, **kwargs
    )


def fake_vision_returning(text: str):
    async def _fake(url, *, query, vision_model):
        return SourceArtifact(kind="text", title="Vision", text_content=text), None

    return _fake


async def test_rendered_grid_text_is_appended_when_richer():
    fr = fetch_result(
        html="<div>Eurostat</div>",
        dashboard_kind="dashboard",
        rendered_text="Country\t2023\t2024\nBelgium\t11.7\t11.8\n" * 40,
    )
    pr = await DefaultParser().parse_html(fr, ctx(fr.url))
    assert "Belgium\t11.7\t11.8" in pr.text_content
    assert "## Rendered page text" in pr.text_content


async def test_rendered_text_not_appended_when_not_richer():
    fr = fetch_result(
        html="<table><tr><td>Belgium</td><td>11.7</td><td>11.8</td></tr></table>",
        dashboard_kind="dashboard",
        rendered_text="Belgium 11.7 11.8",
    )
    pr = await DefaultParser().parse_html(fr, ctx(fr.url))
    assert "## Rendered page text" not in pr.text_content


async def test_rendered_text_ignored_for_non_dashboard_page():
    fr = fetch_result(html="<div>Eurostat</div>", rendered_text="Belgium\t11.7\t11.8\n" * 40)
    pr = await DefaultParser().parse_html(fr, ctx(fr.url))
    assert "Belgium" not in pr.text_content


async def test_parse_result_carries_dashboard_fields():
    src = DataSource("D1", "https://t.org/a.json", "response_json", "application/json", '{"a": 1}', 8)
    fr = fetch_result(html="<div>Eurostat</div>", dashboard_kind="dashboard", data_sources=(src,))
    pr = await DefaultParser().parse_html(fr, ctx(fr.url))
    assert pr.dashboard_kind == "dashboard" and pr.data_sources == (src,)


async def test_bi_page_uses_vision(monkeypatch):
    monkeypatch.setattr(_vision, "scrape_url_via_vision", fake_vision_returning("Loans 2025: 41,203"))
    fr = fetch_result(url="https://app.powerbi.com/view?r=x", html="<div>Microsoft Power BI</div>", dashboard_kind="bi")
    pr = await DefaultParser(vision_model="gemini/x").parse_html(fr, ctx(fr.url))
    assert "41,203" in pr.text_content
    assert pr.artifact.text_content == pr.text_content


async def test_bi_page_without_vision_model_keeps_shell_text():
    fr = fetch_result(url="https://app.powerbi.com/view?r=x", html="<div>Microsoft Power BI</div>", dashboard_kind="bi")
    pr = await DefaultParser(vision_model=None).parse_html(fr, ctx(fr.url))
    assert "Microsoft Power BI" in pr.text_content


async def test_bi_page_keeps_shell_text_when_vision_fails(monkeypatch):
    async def _failing(url, *, query, vision_model):
        return SourceArtifact(kind="text", title=""), "vision timeout"

    monkeypatch.setattr(_vision, "scrape_url_via_vision", _failing)
    fr = fetch_result(url="https://app.powerbi.com/view?r=x", html="<div>Microsoft Power BI</div>", dashboard_kind="bi")
    pr = await DefaultParser(vision_model="gemini/x").parse_html(fr, ctx(fr.url))
    assert "Microsoft Power BI" in pr.text_content
    assert pr.artifact.text_content == pr.text_content


def test_cache_round_trip_keeps_sources():
    src = DataSource("D1", "https://t.org/a.json", "response_json", "application/json", '{"a": 1}', 8)
    art = cacheable_from_parse_result(
        "https://t.org/app", parse_result(data_sources=(src,), dashboard_kind="dashboard")
    )
    assert art.data_sources == (src,) and art.dashboard_kind == "dashboard"


def test_old_cached_artifact_without_new_fields_loads():
    art = CachedSourceArtifact(url="https://t.org", title="t", artifact_kind="text", text_content="x")
    assert art.data_sources == () and art.dashboard_kind == ""


async def test_pages_cached_before_data_capture_are_refetched(monkeypatch):
    calls = {"n": 0}
    src = DataSource("D1", "https://t.org/a.json", "response_json", "application/json", '{"a": 1}', 8)

    async def _fake_fetch(url, **kwargs):
        calls["n"] += 1
        return fetch_result(html="<div>Eurostat</div>", url=url), parse_result(
            data_sources=(src,), dashboard_kind="dashboard"
        )

    monkeypatch.setattr("web_scout.scraping.fetch_and_parse_url", _fake_fetch)
    url = "https://t.org/app"
    # A pre-change cache entry: same inputs, but no data capture version.
    await call_cached_task(
        _url_fetch_task,
        kind="url",
        refresh=False,
        exclude=("fetch_max_pdf_pages",),
        url=url,
        wait_for_selector="",
        max_pdf_pages=0,
        vision_model="",
        exclude_domains=(),
        fetch_max_pdf_pages=50,
        data_capture_version=0,
        converter_version=pdf_converter_version(),
    )
    assert calls["n"] == 1

    kwargs = dict(url=url, wait_for=None, vision_model=None, exclude_domains=None, max_pdf_pages=50)
    cached, error = await get_or_fetch_session_source_artifact(**kwargs)
    assert error is None and calls["n"] == 2
    assert cached.data_sources == (src,) and cached.dashboard_kind == "dashboard"

    again, _ = await get_or_fetch_session_source_artifact(**kwargs)
    assert calls["n"] == 2 and again == cached


def empty_parse_result(url: str, **kwargs) -> ParseResult:
    artifact = SourceArtifact(kind="text", title="App", text_content="")
    return ParseResult(url=url, title="App", text_content="", links=[], artifact=artifact, **kwargs)


async def test_empty_text_dashboard_with_data_sources_is_cached(monkeypatch):
    src = DataSource("D1", "https://t.org/a.json", "response_json", "application/json", '{"a": 1}', 8)

    async def _fake_fetch(url, **kwargs):
        return fetch_result(html="<div></div>", url=url), empty_parse_result(
            url, data_sources=(src,), dashboard_kind="dashboard"
        )

    monkeypatch.setattr("web_scout.scraping.fetch_and_parse_url", _fake_fetch)
    cached, error = await get_or_fetch_session_source_artifact(
        url="https://t.org/empty-dashboard", wait_for=None, vision_model=None, exclude_domains=None, max_pdf_pages=50
    )
    assert error is None and cached.data_sources == (src,) and cached.text_content == ""


async def test_empty_text_page_without_data_sources_still_fails(monkeypatch):
    async def _fake_fetch(url, **kwargs):
        return fetch_result(html="<div></div>", url=url), empty_parse_result(url)

    monkeypatch.setattr("web_scout.scraping.fetch_and_parse_url", _fake_fetch)
    cached, error = await get_or_fetch_session_source_artifact(
        url="https://t.org/empty-page", wait_for=None, vision_model=None, exclude_domains=None, max_pdf_pages=50
    )
    assert cached is None and "Extraction returned empty content" in error
