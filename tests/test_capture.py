"""Browser data capture (NetworkCapture) and dashboard routing in ScraplingFetcher."""

import gzip
import json
from pathlib import Path

import pytest

from web_scout.scraping import ScraplingFetcher, URLContext, _capture
from web_scout.scraping._capture import NetworkCapture

FIXTURE_DIR = Path(__file__).parent / "test_data" / "dashboards"
OWID_URL = "https://ourworldindata.org/grapher/annual-deforestation"
GOVBR_URL = (
    "https://www.gov.br/inpe/pt-br/assuntos/ultimas-noticias/"
    "brasil-alcanca-menores-taxas-de-desmatamento-da-serie-historica-na-amazonia-e-no-cerrado"
)
DATA_JSON = json.dumps({"v": list(range(100))})


def _fixture(name: str) -> str:
    return gzip.decompress((FIXTURE_DIR / f"{name}.html.gz").read_bytes()).decode("utf-8", "replace")


class FakeRequest:
    def __init__(self, resource_type):
        self.resource_type = resource_type


class FakeResponse:
    def __init__(self, url, content_type, body, resource_type="fetch"):
        self.url = url
        self.headers = {"content-type": content_type}
        self.request = FakeRequest(resource_type)
        self._body = body.encode()

    async def body(self):
        return self._body


class FakePage:
    def __init__(self, responses=(), body_text="", url="", html="", late=()):
        self.url = url
        self._responses = list(responses)
        self._late = list(late)
        self._body_text = body_text
        self._html = html
        self.handlers = []

    def on(self, event, handler):
        assert event == "response"
        self.handlers.append(handler)

    def remove_listener(self, event, handler):
        self.handlers.remove(handler)

    def emit_all(self):
        for response in self._responses:
            for handler in list(self.handlers):
                handler(response)

    async def content(self):
        return self._html

    async def inner_text(self, selector):
        assert selector == "body"
        # Responses arriving while the capture settles must still be kept.
        for response in self._late:
            for handler in list(self.handlers):
                handler(response)
        self._late = []
        return self._body_text


@pytest.fixture(autouse=True)
def _fast_settle(monkeypatch):
    monkeypatch.setattr(_capture, "_SETTLE_QUIET_S", 0.05)
    monkeypatch.setattr(_capture, "_SETTLE_MAX_S", 0.5)


async def test_capture_keeps_same_site_json_and_settles():
    page = FakePage(
        responses=[
            FakeResponse("https://t.example.org/files/data.json", "application/json", DATA_JSON, "fetch"),
            FakeResponse("https://www.google-analytics.com/collect", "application/json", "{}", "fetch"),
        ],
        body_text="Painel",
        url="https://t.example.org/app/dashboard",
    )
    cap = NetworkCapture("https://t.example.org/app/dashboard")
    await cap.page_setup(page)
    page.emit_all()
    await cap.page_action(page)
    assert [s.url for s in cap.sources] == ["https://t.example.org/files/data.json"]
    assert cap.sources[0].kind == "response_json" and cap.sources[0].body == DATA_JSON
    assert cap.rendered_text == "Painel"
    assert page.handlers == []  # listener removed so the pooled page does not keep capturing


async def test_capture_skips_oversized_body():
    page = FakePage(
        responses=[FakeResponse("https://t.example.org/big.json", "application/json", "1" * 6_000_000, "xhr")],
        url="https://t.example.org/app",
    )
    cap = NetworkCapture("https://t.example.org/app")
    await cap.page_setup(page)
    page.emit_all()
    await cap.page_action(page)
    assert cap.sources == []


async def test_capture_rejects_config_json_and_document_responses():
    page = FakePage(
        responses=[
            FakeResponse("https://t.example.org/config.json", "application/json", '{"theme": "dark"}', "fetch"),
            FakeResponse("https://t.example.org/data.json", "application/json", DATA_JSON, "document"),
        ],
        url="https://t.example.org/app/dashboard",
    )
    cap = NetworkCapture("https://t.example.org/app/dashboard")
    await cap.page_setup(page)
    page.emit_all()
    await cap.page_action(page)
    assert cap.sources == []


async def test_capture_keeps_preloaded_data():
    # OWID preloads its indicator data with <link rel=preload>, which Playwright reports as "other".
    page = FakePage(
        responses=[FakeResponse("https://api.example.org/v1/1.data.json", "application/json", DATA_JSON, "other")],
        url="https://example.org/grapher/x",
    )
    cap = NetworkCapture("https://example.org/grapher/x")
    await cap.page_setup(page)
    page.emit_all()
    await cap.page_action(page)
    assert [s.url for s in cap.sources] == ["https://api.example.org/v1/1.data.json"]


async def test_capture_uses_final_url_after_redirect():
    # GFW redirects to another registrable site; data from the final site must be kept.
    page = FakePage(
        responses=[FakeResponse("https://api.globalnaturewatch.org/v1/data.json", "application/json", DATA_JSON)],
        url="https://globalnaturewatch.org/dashboards/country/BRA/",
    )
    cap = NetworkCapture("https://www.globalforestwatch.org/dashboards/country/BRA/")
    await cap.page_setup(page)
    page.emit_all()
    await cap.page_action(page)
    assert [s.url for s in cap.sources] == ["https://api.globalnaturewatch.org/v1/data.json"]
    assert cap.final_url == "https://globalnaturewatch.org/dashboards/country/BRA/"


async def test_capture_keeps_late_response_and_skips_settle_off_dashboards():
    late = [FakeResponse("https://t.example.org/late.csv", "text/csv", "year,value\n2020,5\n", "fetch")]
    page = FakePage(url="https://t.example.org/app/dashboard", body_text="x", late=late)
    cap = NetworkCapture("https://t.example.org/app/dashboard")
    await cap.page_setup(page)
    await cap.page_action(page)
    assert [(s.url, s.kind) for s in cap.sources] == [("https://t.example.org/late.csv", "response_csv")]

    article = FakePage(url="https://t.example.org/news/story", body_text="story")
    cap = NetworkCapture("https://t.example.org/news/story")
    await cap.page_setup(article)
    await cap.page_action(article)
    assert cap.rendered_text == "" and not cap.is_dashboard


class _HttpResponse:
    def __init__(self, html, *, content_type="text/html; charset=utf-8", status=200):
        self.status = status
        self.headers = {"content-type": content_type}
        self.html_content = html
        self.body = html.encode()


@pytest.fixture
def fake_network(monkeypatch):
    """Fake http_get (by URL) and stealthy_fetch (records kwargs, runs the capture hooks on a FakePage)."""
    from web_scout.scraping import _fetcher, _scrapling

    _fetcher._BROWSER_HOSTS.clear()
    state = {"http": {}, "downloads": {}, "stealthy_calls": [], "page": None, "rendered": ""}

    async def http_get(url, **kwargs):
        return state["http"][url]

    async def get_capped(url):
        return state["downloads"].get(url)

    async def stealthy_fetch(url, **kwargs):
        state["stealthy_calls"].append(kwargs)
        page = state["page"] or FakePage(url=url, html=state["rendered"])
        if kwargs.get("page_setup"):
            await kwargs["page_setup"](page)
            page.emit_all()
        if kwargs.get("page_action"):
            await kwargs["page_action"](page)
        response = _HttpResponse(state["rendered"])
        response.url = page.url
        return response

    monkeypatch.setattr("web_scout.scraping._resources.http_get", http_get)
    monkeypatch.setattr(_scrapling, "stealthy_fetch", stealthy_fetch)
    monkeypatch.setattr(_capture, "_get_capped", get_capped)
    yield state
    _fetcher._BROWSER_HOSTS.clear()


async def test_dashboard_with_full_fast_html_forces_browser(fake_network):
    owid = _fixture("owid")
    fake_network["http"][OWID_URL] = _HttpResponse(owid)
    fake_network["rendered"] = owid
    result = await ScraplingFetcher().fetch(OWID_URL, URLContext(url=OWID_URL, depth=0))
    assert result.used_browser and result.dashboard_kind == "dashboard"
    assert "page_setup" in fake_network["stealthy_calls"][0]


async def test_article_with_full_fast_html_stays_on_fast_path(fake_network):
    fake_network["http"][GOVBR_URL] = _HttpResponse(_fixture("govbr_news"))
    result = await ScraplingFetcher().fetch(GOVBR_URL, URLContext(url=GOVBR_URL, depth=0))
    assert not result.used_browser and result.dashboard_kind == "" and result.data_sources == ()
    assert fake_network["stealthy_calls"] == []


async def test_dashboard_fetch_collects_captured_inline_and_download_sources(fake_network):
    url = "https://t.example.org/app/dashboard"
    inline = "<script>window.__data = " + DATA_JSON + ";</script>"
    links = (
        '<a href="/files/series.csv">CSV</a><a href="https://t.example.org/files/table.xlsx">XLSX</a>'
        '<a href="https://other.example.com/x.csv">offsite</a><a href="/files/empty.csv">empty</a>'
    )
    rendered = f"<html><body>{inline}<p>Painel</p>{links}</body></html>"
    fake_network["http"][url] = _HttpResponse("<html><script src=app.js></script></html>")
    fake_network["downloads"]["https://t.example.org/files/series.csv"] = b"year,area\n2020,10851\n"
    fake_network["downloads"]["https://t.example.org/files/empty.csv"] = b"year,area\n"
    fake_network["rendered"] = rendered
    fake_network["page"] = FakePage(
        responses=[FakeResponse("https://t.example.org/files/data.json", "application/json", DATA_JSON)],
        body_text="Painel",
        url=url,
        html=rendered,
    )

    result = await ScraplingFetcher().fetch(url, URLContext(url=url, depth=0))

    assert result.used_browser and result.dashboard_kind == "dashboard"
    assert [(s.source_id, s.kind, s.url) for s in result.data_sources] == [
        ("D1", "response_json", "https://t.example.org/files/data.json"),
        ("D2", "inline_json", url),
        ("D3", "download_csv", "https://t.example.org/files/series.csv"),
        ("D4", "download_xlsx", "https://t.example.org/files/table.xlsx"),
    ]
    assert result.data_sources[3].body == "" and result.rendered_text == "Painel"


async def test_thin_non_dashboard_browser_fetch_keeps_no_sources(fake_network):
    url = "https://t.example.org/app/page"
    fake_network["http"][url] = _HttpResponse("<html><script src=app.js></script></html>")
    fake_network["rendered"] = "<html><body><p>" + "A sentence. " * 20 + "</p></body></html>"
    fake_network["page"] = FakePage(
        responses=[FakeResponse("https://t.example.org/files/data.json", "application/json", DATA_JSON)],
        body_text="text",
        url=url,
        html=fake_network["rendered"],
    )
    result = await ScraplingFetcher().fetch(url, URLContext(url=url, depth=0))
    assert result.used_browser and result.dashboard_kind == ""
    assert result.data_sources == () and result.rendered_text == ""


async def test_dashboard_browser_failure_falls_back_to_fast_html(fake_network, monkeypatch):
    from web_scout.scraping import _scrapling

    owid = _fixture("owid")
    fake_network["http"][OWID_URL] = _HttpResponse(owid)

    async def broken(url, **kwargs):
        raise RuntimeError("browser crashed")

    monkeypatch.setattr(_scrapling, "stealthy_fetch", broken)
    result = await ScraplingFetcher().fetch(OWID_URL, URLContext(url=OWID_URL, depth=0))
    assert result.error is None and not result.used_browser
    assert result.html_content == owid and result.dashboard_kind == "dashboard"


async def test_stealthy_fetch_passes_page_setup_to_session_fetch(monkeypatch):
    from web_scout.scraping import _scrapling

    seen = {}

    async def fake_fetch_via_session(url, **kwargs):
        seen.update(kwargs)
        return _HttpResponse("<html></html>")

    async def setup(page):
        return None

    monkeypatch.setattr(_scrapling, "fetch_via_session", fake_fetch_via_session)
    await _scrapling.stealthy_fetch("https://t.example.org/", page_setup=setup)
    assert seen["page_setup"] is setup


def _thin_shell():
    return _HttpResponse("<html><script src=app.js></script></html>")


async def test_dashboard_forced_render_does_not_cache_browser_host(fake_network):
    from web_scout.scraping import _fetcher

    fake_network["http"][OWID_URL] = _HttpResponse(_fixture("owid"))
    fake_network["rendered"] = _fixture("owid")
    result = await ScraplingFetcher().fetch(OWID_URL, URLContext(url=OWID_URL, depth=0))
    assert result.used_browser and "ourworldindata.org" not in _fetcher._BROWSER_HOSTS


async def test_thin_shell_and_bot_wall_renders_still_cache_browser_host(fake_network):
    from web_scout.scraping import _fetcher

    thin = "https://thin.example.org/app/page"
    wall = "https://wall.example.org/page"
    fake_network["http"][thin] = _thin_shell()
    fake_network["http"][wall] = _HttpResponse("<html>Just a moment... cloudflare</html>", status=403)
    fake_network["rendered"] = "<html><body><p>" + "A sentence. " * 20 + "</p></body></html>"
    for url in (thin, wall):
        result = await ScraplingFetcher().fetch(url, URLContext(url=url, depth=0))
        assert result.used_browser
    assert {"thin.example.org", "wall.example.org"} <= set(_fetcher._BROWSER_HOSTS)


async def test_dashboard_forced_render_over_budget_returns_fast_response(fake_network, monkeypatch):
    import asyncio
    import time

    from web_scout.scraping import _scrapling

    owid = _fixture("owid")
    fake_network["http"][OWID_URL] = _HttpResponse(owid)
    pages = []

    async def slow(url, **kwargs):
        page = FakePage(url=url, html=owid)
        pages.append(page)
        await kwargs["page_setup"](page)
        await asyncio.sleep(5)

    monkeypatch.setattr(_capture, "DASHBOARD_RENDER_BUDGET_S", 0.2)
    monkeypatch.setattr(_scrapling, "stealthy_fetch", slow)
    started = time.monotonic()
    result = await ScraplingFetcher().fetch(OWID_URL, URLContext(url=OWID_URL, depth=0))
    assert time.monotonic() - started < 2
    assert result.error is None and not result.used_browser
    assert result.html_content == owid and result.dashboard_kind == "dashboard"
    assert pages[0].handlers == []  # capture detached when the budget expired


async def test_thin_shell_render_is_not_bounded_by_dashboard_budget(fake_network, monkeypatch):
    import asyncio

    from web_scout.scraping import _scrapling

    url = "https://t.example.org/app/page"
    fake_network["http"][url] = _thin_shell()
    rendered = "<html><body><p>" + "A sentence. " * 20 + "</p></body></html>"

    async def slow(url, **kwargs):
        await asyncio.sleep(0.4)
        response = _HttpResponse(rendered)
        response.url = url
        return response

    monkeypatch.setattr(_capture, "DASHBOARD_RENDER_BUDGET_S", 0.1)
    monkeypatch.setattr(_scrapling, "stealthy_fetch", slow)
    result = await ScraplingFetcher().fetch(url, URLContext(url=url, depth=0))
    assert result.error is None and result.used_browser and result.html_content == rendered


async def test_capture_detached_when_stealthy_fetch_raises(fake_network, monkeypatch):
    from web_scout.scraping import _scrapling

    url = "https://t.example.org/app/page"
    fake_network["http"][url] = _thin_shell()
    page = FakePage(url=url)

    async def broken(url, **kwargs):
        await kwargs["page_setup"](page)
        raise RuntimeError("Page.goto: net::ERR_FAILED")

    monkeypatch.setattr(_scrapling, "stealthy_fetch", broken)
    result = await ScraplingFetcher().fetch(url, URLContext(url=url, depth=0))
    assert result.error and "browser fetch failed" in result.error
    assert page.handlers == []


async def test_detach_cancels_pending_body_reads():
    import asyncio

    class Hanging(FakeResponse):
        async def body(self):
            await asyncio.sleep(10)

    page = FakePage(
        responses=[Hanging("https://t.example.org/d.json", "application/json", "")], url="https://t.example.org/"
    )
    cap = NetworkCapture("https://t.example.org/")
    await cap.page_setup(page)
    page.emit_all()
    (task,) = cap._pending
    cap.detach()
    await asyncio.sleep(0)
    assert task.cancelled() and page.handlers == []


async def test_capture_body_errors_do_not_escape_read_task():
    class Broken(FakeResponse):
        async def body(self):
            return None  # len(None) raises inside _read

    page = FakePage(
        responses=[Broken("https://t.example.org/d.json", "application/json", "")],
        url="https://t.example.org/dashboard",
    )
    cap = NetworkCapture("https://t.example.org/dashboard")
    await cap.page_setup(page)
    page.emit_all()
    (task,) = cap._pending
    await cap.page_action(page)
    assert task.done() and not task.cancelled() and task.exception() is None and cap.sources == []


class _StreamResponse:
    def __init__(self, chunks, headers=None, status_code=200):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks
        self.read = False

    async def aiter_content(self):
        self.read = True
        for chunk in self._chunks:
            yield chunk


def _stream_session(monkeypatch, response):
    from contextlib import asynccontextmanager

    class Session:
        @asynccontextmanager
        async def stream(self, method, url, **kwargs):
            assert method == "GET" and kwargs["impersonate"] == "chrome"
            yield response

    monkeypatch.setattr("web_scout.scraping._resources.binary_session", lambda: Session())


async def test_csv_download_checks_content_length_before_reading(monkeypatch):
    response = _StreamResponse([b"x"], headers={"content-length": "6000000"})
    _stream_session(monkeypatch, response)
    assert await _capture._get_capped("https://t.example.org/big.csv") is None
    assert not response.read


async def test_csv_download_caps_streamed_body_and_returns_small_body(monkeypatch):
    _stream_session(monkeypatch, _StreamResponse([b"1" * 3_000_000, b"1" * 3_000_000]))
    assert await _capture._get_capped("https://t.example.org/big.csv") is None
    _stream_session(monkeypatch, _StreamResponse([b"year,v\n", b"2020,1\n"]))
    assert await _capture._get_capped("https://t.example.org/s.csv") == b"year,v\n2020,1\n"
