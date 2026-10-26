"""Data source model, background-response filter, inline JSON extraction and per-page caps."""

import json

from web_scout.scraping._data_sources import (
    DataSource,
    cap_sources,
    count_numeric_leaves,
    inline_json_sources,
    is_data_body,
    is_data_response,
)


def test_data_response_filter():
    page = "https://terrabrasilis.dpi.inpe.br/app/dashboard/x"
    assert is_data_response("https://terrabrasilis.dpi.inpe.br/app/prodes/files/data/a.json", "application/json", page)
    assert not is_data_response("https://region1.google-analytics.com/g/collect", "application/json", page)
    assert not is_data_response("https://terrabrasilis.dpi.inpe.br/assets/i18n/pt-br.json", "application/json", page)
    assert not is_data_response("https://other.example/data.json", "application/json", page)
    assert is_data_response("https://terrabrasilis.dpi.inpe.br/export.csv", "text/csv", page)


def test_data_response_filter_noise_categories_and_types():
    page = "https://data.example.org/dashboard"
    noise = [
        "https://analytics.example.org/g/collect?v=2",  # analytics host
        "https://data.example.org/gtm.js?id=1",  # tag manager
        "https://data.example.org/cookieconsentpub/v1/geo/location",  # consent
        "https://cdn.example.org/assets/t9n/messages.en.json",  # translation
        "https://data.example.org/locales/en.json",  # translation
        "https://data.example.org/sharing/rest/oauth2/platformSelf",  # auth
        "https://api.example.org/api/v1/auth/guest",  # auth
        "https://data.example.org/c1/telemetry",  # telemetry
        "https://ingest.example.org/api/1/envelope/?sentry_version=7",  # telemetry
    ]
    for url in noise:
        assert not is_data_response(url, "application/json", page), url
    # Words that merely contain a noise token stay data.
    assert is_data_response("https://data.example.org/api/authors/stats.json", "application/json", page)
    assert is_data_response("https://data.example.org/catalog/blog-series.json", "application/json", page)
    # Generic words in a same-site path are real data APIs (DHIS2 analytics, record tracks, collections).
    for url in (
        "https://data.example.org/api/analytics.json?dimension=dx:abc",
        "https://data.example.org/api/track/records",
        "https://data.example.org/api/v1/collect/indicators",
        "https://data.example.org/lang/en/series.json",
    ):
        assert is_data_response(url, "application/json", page), url
    assert is_data_response("https://api.example.org/data/x", "application/dbrjson", page)
    assert is_data_response("https://data.example.org/x", "application/json; charset=utf-8", page)
    assert not is_data_response("https://data.example.org/app.js", "application/javascript", page)
    assert not is_data_response("https://data.example.org/page", "text/html", page)


def test_count_numeric_leaves():
    assert count_numeric_leaves({"a": [1, 2.5, {"b": -3}], "c": "4", "d": True, "e": None}) == 3
    assert count_numeric_leaves([]) == 0


def test_inline_json_window_assignment_and_next_data():
    blob = json.dumps({"rows": [{"year": 2000 + i, "value": i * 1.5} for i in range(60)]})
    html = f"<script>window.__data={blob};</script><script id='__NEXT_DATA__' type='application/json'>{blob}</script>"
    sources = inline_json_sources(html, "https://data.example.org/indicator/X")
    assert [s.kind for s in sources] == ["inline_json", "inline_json"]
    assert json.loads(sources[0].body)["rows"][59]["year"] == 2059


def test_inline_json_ignores_small_config():
    html = '<script>window.cfg={"theme": "dark", "n": 3};</script>'
    assert inline_json_sources(html, "https://x.org/") == []


def test_inline_json_source_fields_and_trailing_code():
    rows = json.dumps([[2000 + i, i] for i in range(30)])
    html = (
        '<script>var x = 1; window.notdata = {"a": 1};</script>'
        f"<script>window.series = {rows}; render(window.series);</script>"
        "<script type='application/json'>{not json</script>"
    )
    sources = inline_json_sources(html, "https://data.example.org/p")
    assert len(sources) == 1
    source = sources[0]
    assert source.source_id == "D1"
    assert source.url == "https://data.example.org/p"
    assert source.content_type == "application/json"
    assert json.loads(source.body)[29] == [2029, 29]
    assert source.size == len(source.body.encode("utf-8"))


def test_inline_json_survives_deep_nesting():
    html = "<script type='application/json'>" + "[" * 100_000 + "]" * 100_000 + "</script>"
    assert inline_json_sources(html, "https://x.org/") == []


def test_is_data_body():
    assert not is_data_body('{"theme": "dark", "zoom": 3}', "application/json")
    manifest = json.dumps({"name": "App", "icons": [{"src": "i.png", "sizes": "192x192"}], "start_url": "/"})
    assert not is_data_body(manifest, "application/json")
    assert is_data_body(json.dumps({"values": list(range(60))}), "application/json; charset=utf-8")
    assert is_data_body("year,value\n2020,1.5\n", "text/csv")
    assert not is_data_body("year,value\n", "text/csv")
    assert not is_data_body("year,value\nn/a,none\n", "text/csv")
    assert not is_data_body('{"values": [1, 2', "application/json")
    assert not is_data_body("[" * 100_000 + "]" * 100_000, "application/json")
    assert not is_data_body(json.dumps(list(range(60))), "text/html")


def test_cap_sources_limits_and_renumbers():
    big = [
        DataSource(f"D{i}", f"https://x.org/{i}.json", "response_json", "application/json", "x" * 3_000_000, 3_000_000)
        for i in range(10)
    ]
    capped = cap_sources(big)
    assert len(capped) == 4  # 12 MB total cap
    assert [s.source_id for s in capped] == ["D1", "D2", "D3", "D4"]


def test_cap_sources_count_and_single_response_limits():
    small = [DataSource(f"X{i}", f"https://x.org/{i}.csv", "response_csv", "text/csv", "a,b", 3) for i in range(10)]
    capped = cap_sources(small)
    assert [s.url for s in capped] == [f"https://x.org/{i}.csv" for i in range(8)]
    assert [s.source_id for s in capped] == [f"D{i}" for i in range(1, 9)]

    huge = DataSource("D1", "https://x.org/huge.json", "response_json", "application/json", "", 6_000_000)
    xlsx = DataSource("D2", "https://x.org/t.xlsx", "download_xlsx", "application/vnd.ms-excel", "", 20_000)
    assert cap_sources([huge, xlsx]) == (
        DataSource("D1", "https://x.org/t.xlsx", "download_xlsx", "application/vnd.ms-excel", "", 20_000),
    )
