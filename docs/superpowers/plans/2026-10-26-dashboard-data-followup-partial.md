# Dashboard Data, Link Following and Partial Answers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Web Scout quote figures that dashboard-style sources display or store, follow extractor links in open search, and keep pages that answer part of a question, then prove the gain on real old-versus-new runs.

**Architecture:** A deterministic detector marks dashboard-like pages. Those pages are rendered in the shared Scrapling browser while a network listener captures same-site JSON and CSV responses. Inline script JSON and CSV/XLSX download links are collected too. The captured sources travel in the cached fetch artifact to the extractor, which sees a short catalog and a lookup tool that can filter and rank stored records, and reports which data file each quoted figure came from. Open search gains one capped follow-up wave per round. The query planner always produces requirements, which the page extractor uses to keep partial answers.

**Tech Stack:** Python 3.13, Scrapling 0.5 / Playwright, openai-agents, litellm, Prefect cache, pytest with pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-26-dashboard-data-sources-design.md`

## Global Constraints

- Quote rule, revised by the user: report numbers as the source displays or stores them, citing the data file. Ranking records from a source's own data file is allowed; summing and averaging are not offered. Looking up the label behind an ID is allowed. No deterministic verbatim check (dropped by the user).
- Latency for dashboard handling: at most about 5 to 15 extra seconds per search round, only when the round contains dashboard-like pages. Runs without them must not change.
- No new dependencies. Never run install commands.
- Do not modify anything under `eval/`. In `src/web_scout/tools/pdf_extractor.py`, change only the no-evidence prompt rule (Task 7).
- Merge compatibility: the branch starts from `dev` at `37edddf`. Uncommitted work in the main checkout changes `stealthy_fetch` (new `wait_ready` keyword, `page_action` chaining) and the deadlines in `ScraplingFetcher.fetch` and `download_pdf`. Edits to those functions must be additive and must not change deadlines. If that work lands on `dev` before Task 3, rebase onto it first.
- Existing models only: `content_extractor` for extraction, `vision_fallback` for screenshots.
- Run tests from this worktree so `pyproject.toml`'s `pythonpath = ["src"]` puts this branch's code first: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest <path> -v`. The environment's editable install points at the main checkout.
- Live scripts load `.env` with `dotenv.load_dotenv`, never a hand-rolled parser. Path: `/Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai/.env`.
- Git: commit steps run only if the user has authorized commits for this execution. Otherwise leave changes uncommitted and continue.
- Lint: `ruff`, line length 120, rules E, F, I, W.
- Caps, my calls from the spec's "left to the builder": per captured response 5 MB; at most 8 sources per page; at most 12 MB per page; a JSON response counts as data with at least 50 numeric leaves; catalog at most 2,500 characters; lookup returns at most 30 records of at most 600 characters each; data-settle wait ends after 1.5 s without a new data response, capped at 6 s.

## Review Focus

1. A cached artifact written before this change has no data-source fields. Loading it must not fail, and dashboard pages must not reuse it. Test in Task 4.
2. Numeric values stored as locale strings, such as `"4,210"` in a `;` CSV. Ranking must order them by numeric value. Test in Task 5.
3. Lookup terms with accents or different case, such as `labrea` for `Lábrea`. Test in Task 5.
4. A Power BI or Tableau page when no vision model is configured. It must fall back to its page text without raising. Test in Task 4.
5. Follow-up candidates that point back to the parent page, to pages already tried, or to excluded domains. They must be dropped, and a follow-up wave must not start another. Test in Task 8.

---

### Task 1: Dashboard detection with real-page fixtures

**Files:**
- Create: `src/web_scout/scraping/_data_sources.py`
- Create: `tests/test_data/dashboards/capture_fixtures.py`
- Create: `tests/test_data/dashboards/*.html.gz` and `tests/test_data/dashboards/README.md`
- Test: `tests/test_dashboard_detection.py`

**Interfaces:**
- Produces:
  - `DashboardKind = Literal["", "dashboard", "bi"]`
  - `@dataclass(frozen=True) class DashboardAssessment: kind: DashboardKind; reasons: tuple[str, ...]` with property `is_dashboard -> bool` true for both non-empty kinds.
  - `assess_dashboard(url: str, html: str) -> DashboardAssessment`, pure, no network.

- [ ] **Step 1: Capture fixtures**

`capture_fixtures.py` downloads the fast-HTTP HTML for each URL below with the package's own `http_get` (stealthy headers), and saves it gzipped as `<name>.html.gz`. The README records each URL, the capture date and the expected kind. Run it once with network:

| name | URL | expected |
|---|---|---|
| terrabrasilis | `https://terrabrasilis.dpi.inpe.br/app/dashboard/deforestation/biomes/legal_amazon/increments` | dashboard |
| owid | `https://ourworldindata.org/grapher/annual-deforestation` | dashboard |
| worldbank | `https://data.worldbank.org/indicator/AG.LND.FRST.ZS?locations=BR` | dashboard |
| sidra | `https://sidra.ibge.gov.br/tabela/1612` | dashboard |
| eurostat | `https://ec.europa.eu/eurostat/databrowser/view/tps00001/default/table?lang=en` | dashboard |
| fpma | `https://fpma.fao.org/giews/fpmat4/#/dashboard/tool/domestic` | dashboard |
| gfw | `https://www.globalforestwatch.org/dashboards/country/BRA/` | dashboard |
| powerbi | the Library Statistics report URL in `/tmp/brazil_repro/survey_urls.txt` | bi |
| tableau | the Food Price Dashboard URL in `/tmp/brazil_repro/survey_urls.txt` | bi |
| govbr_news | `https://www.gov.br/inpe/pt-br/assuntos/ultimas-noticias/brasil-alcanca-menores-taxas-de-desmatamento-da-serie-historica-na-amazonia-e-no-cerrado` | "" |
| infoamazonia | `https://infoamazonia.org/en/2021/11/19/deforestation-amazon-grows-22-in-2021/` | "" |
| oeco | `https://oeco.org.br/externo/amazonia-tem-a-menor-taxa-de-desmatamento-ja-registrada-pelo-inpe/` | "" |

Expected: 12 `.html.gz` files exist.

- [ ] **Step 2: Write the failing test**

```python
@pytest.mark.parametrize("name,url,expected", FIXTURES)  # FIXTURES mirrors the table above
def test_assess_dashboard_on_real_pages(name, url, expected):
    html = gzip.decompress((FIXTURE_DIR / f"{name}.html.gz").read_bytes()).decode("utf-8", "replace")
    result = assess_dashboard(url, html)
    assert result.kind == expected, result.reasons

def test_bi_hosts_without_html():
    assert assess_dashboard("https://app.powerbi.com/view?r=abc", "").kind == "bi"
    assert assess_dashboard("https://public.tableau.com/views/x/y", "").kind == "bi"

def test_plain_article_without_signals():
    html = "<html><body>" + "<p>This is a sentence about forests. It has words.</p>" * 200 + "</body></html>"
    assert assess_dashboard("https://example.org/news/1", html).kind == ""
```

- [ ] **Step 3: Run test to verify it fails**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_dashboard_detection.py -v`
Expected: FAIL with `ImportError` for `assess_dashboard`.

- [ ] **Step 4: Implement `assess_dashboard` in `src/web_scout/scraping/_data_sources.py`**

Signals from the spec, each adding a reason string: BI hosts `app.powerbi.com` and `public.tableau.com` give kind `bi`; dashboard hosts and paths such as `arcgis.com/apps/dashboards`, `/dashboard`, `/dashboards/`, `/grapher/`, `/databrowser/`; chart or map library markers in script sources or inline code, such as highcharts, echarts, plotly, chart.js, d3, vega, leaflet, mapbox-gl, amcharts, apexcharts, gridstack; a script holding JSON of at least 50 KB with at least 500 numbers; and low prose density, meaning few sentences per 1,000 visible characters. Tune the combination until the fixture table passes. The gov.br news page has 64 sentences in about 29,000 characters and must stay `""`.

- [ ] **Step 5: Run test to verify it passes**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_dashboard_detection.py -v`
Expected: PASS, 14 tests.

- [ ] **Step 6: Commit**

```bash
git add src/web_scout/scraping/_data_sources.py tests/test_dashboard_detection.py tests/test_data/dashboards
git commit -m "feat: detect dashboard-like pages from fast HTML"
```

### Task 2: Data source model, response filter and inline JSON

**Files:**
- Modify: `src/web_scout/scraping/_data_sources.py`
- Test: `tests/test_data_sources.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `DataSourceKind = Literal["response_json", "response_csv", "inline_json", "download_csv", "download_xlsx"]`
  - `@dataclass(frozen=True) class DataSource: source_id: str; url: str; kind: DataSourceKind; content_type: str; body: str; size: int` where `source_id` is `"D1"`, `"D2"` and so on in page order, and `body` is empty for `download_xlsx`.
  - `is_data_response(url: str, content_type: str, page_url: str) -> bool`: same registrable site as the page, JSON or CSV type, and not noise.
  - `count_numeric_leaves(value: Any) -> int`
  - `inline_json_sources(html: str, page_url: str) -> list[DataSource]`: `<script type="application/json">`, `__NEXT_DATA__`, and `window.<name> = {...}` or `[...]` assignments, kept only if data-like.
  - `cap_sources(sources: list[DataSource]) -> tuple[DataSource, ...]`: applies the caps in Global Constraints, renumbers IDs.

- [ ] **Step 1: Write the failing tests**

```python
def test_data_response_filter():
    page = "https://terrabrasilis.dpi.inpe.br/app/dashboard/x"
    assert is_data_response("https://terrabrasilis.dpi.inpe.br/app/prodes/files/data/a.json", "application/json", page)
    assert not is_data_response("https://region1.google-analytics.com/g/collect", "application/json", page)
    assert not is_data_response("https://terrabrasilis.dpi.inpe.br/assets/i18n/pt-br.json", "application/json", page)
    assert not is_data_response("https://other.example/data.json", "application/json", page)
    assert is_data_response("https://terrabrasilis.dpi.inpe.br/export.csv", "text/csv", page)

def test_inline_json_window_assignment_and_next_data():
    blob = json.dumps({"rows": [{"year": 2000 + i, "value": i * 1.5} for i in range(60)]})
    html = f"<script>window.__data={blob};</script><script id='__NEXT_DATA__' type='application/json'>{blob}</script>"
    sources = inline_json_sources(html, "https://data.example.org/indicator/X")
    assert [s.kind for s in sources] == ["inline_json", "inline_json"]
    assert json.loads(sources[0].body)["rows"][59]["year"] == 2059

def test_inline_json_ignores_small_config():
    html = "<script>window.cfg={\"theme\": \"dark\", \"n\": 3};</script>"
    assert inline_json_sources(html, "https://x.org/") == []

def test_cap_sources_limits_and_renumbers():
    big = [DataSource(f"D{i}", f"https://x.org/{i}.json", "response_json", "application/json", "x" * 3_000_000, 3_000_000) for i in range(10)]
    capped = cap_sources(big)
    assert len(capped) == 4  # 12 MB total cap
    assert [s.source_id for s in capped] == ["D1", "D2", "D3", "D4"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_data_sources.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the produced interfaces in `src/web_scout/scraping/_data_sources.py`**

Noise covers analytics, tag managers, consent, translation (`i18n`, `locales`), auth and telemetry URLs. Compare registrable sites by the last two host labels.

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_data_sources.py tests/test_dashboard_detection.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/web_scout/scraping/_data_sources.py tests/test_data_sources.py
git commit -m "feat: model captured data sources and extract inline JSON"
```

### Task 3: Browser capture and fetcher routing

**Files:**
- Create: `src/web_scout/scraping/_capture.py`
- Modify: `src/web_scout/scraping/types.py` (`FetchResult`)
- Modify: `src/web_scout/scraping/_fetcher.py` (`ScraplingFetcher._fetch`)
- Modify: `src/web_scout/scraping/_scrapling.py` (`stealthy_fetch` passes `page_setup` through, as a small additive change that keeps the pending `wait_ready` keyword working when it lands)
- Test: `tests/test_capture.py`, plus a live test in `tests/test_live_scraping.py`

**Interfaces:**
- Consumes: `assess_dashboard`, `is_data_response`, `DataSource`, `inline_json_sources`, `cap_sources` from Tasks 1 and 2.
- Produces:
  - `class NetworkCapture` with `__init__(self, page_url: str)`, `async def page_setup(self, page) -> None` that registers a `response` listener before navigation, `async def page_action(self, page) -> None` that waits for data to settle, awaits pending body reads and stores `page.inner_text("body")`, plus attributes `sources: list[DataSource]` and `rendered_text: str`.
  - `FetchResult` gains `dashboard_kind: DashboardKind = ""`, `data_sources: tuple[DataSource, ...] = ()`, `rendered_text: str = ""`.

- [ ] **Step 1: Write the failing unit tests with a fake Playwright page**

```python
async def test_capture_keeps_same_site_json_and_settles():
    page = FakePage(responses=[
        FakeResponse("https://t.example.org/files/data.json", "application/json", json.dumps({"v": list(range(100))}), "fetch"),
        FakeResponse("https://www.google-analytics.com/collect", "application/json", "{}", "fetch"),
    ], body_text="Painel")
    cap = NetworkCapture("https://t.example.org/app/dashboard")
    await cap.page_setup(page)
    page.emit_all()
    await cap.page_action(page)
    assert [s.url for s in cap.sources] == ["https://t.example.org/files/data.json"]
    assert cap.rendered_text == "Painel"

async def test_capture_skips_oversized_body():
    page = FakePage(responses=[FakeResponse("https://t.example.org/big.json", "application/json", "1" * 6_000_000, "xhr")])
    cap = NetworkCapture("https://t.example.org/app")
    await cap.page_setup(page); page.emit_all(); await cap.page_action(page)
    assert cap.sources == []

async def test_dashboard_with_full_fast_html_forces_browser(monkeypatch):
    # http_get returns OWID fixture HTML (not thin); stealthy_fetch is faked and records kwargs.
    result = await ScraplingFetcher().fetch(OWID_URL, URLContext(url=OWID_URL, depth=0))
    assert result.used_browser and result.dashboard_kind == "dashboard"
    assert "page_setup" in FAKE_STEALTHY_CALLS[0]

async def test_article_with_full_fast_html_stays_on_fast_path(monkeypatch):
    result = await ScraplingFetcher().fetch(GOVBR_URL, URLContext(url=GOVBR_URL, depth=0))
    assert not result.used_browser and result.dashboard_kind == "" and result.data_sources == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_capture.py -v`
Expected: FAIL with `ImportError` for `NetworkCapture`.

- [ ] **Step 3: Implement `NetworkCapture` and fetcher routing**

In `_fetch`: run `assess_dashboard(url, fast_html)` after a successful fast response. A dashboard verdict sets `needs_browser_retry`. Every browser fetch gets a `NetworkCapture`, passed as `page_setup` and chained into `page_action`. Re-assess on the rendered HTML. Keep captured, inline JSON and download sources only when either assessment is a dashboard. Same-site `.csv` links found on the rendered page become `download_csv` sources, fetched with `http_get` within the caps. `.xlsx` links become `download_xlsx` with an empty body. Set `rendered_text` only for dashboards. Confirm that `AsyncStealthySession.fetch` accepts `page_setup`. If it does not, register the listener at the start of `page_action` and also read `performance.getEntriesByType("resource")` to re-request missed same-site GET data URLs through `page.request`.

- [ ] **Step 4: Add the live check to `tests/test_live_scraping.py`, following that file's skip style**

```python
async def test_live_terrabrasilis_captures_dashboard_data():
    result = await fetch("https://terrabrasilis.dpi.inpe.br/app/dashboard/deforestation/biomes/legal_amazon/increments")
    urls = [s.url for s in result.data_sources]
    assert any("files/data/prodes_legal_amazon.json" in u for u in urls)
    assert any("loinames/prodes_legal_amazon.json" in u for u in urls)
```

- [ ] **Step 5: Run the tests**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_capture.py tests/test_live_scraping.py -k "capture or terrabrasilis or dashboard" -v`
Expected: PASS. Also run `tests/test_fetch.py tests/test_scraping_routing.py` and expect no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/web_scout/scraping/_capture.py src/web_scout/scraping/types.py src/web_scout/scraping/_fetcher.py src/web_scout/scraping/_scrapling.py tests/test_capture.py tests/test_live_scraping.py
git commit -m "feat: capture dashboard data sources during browser fetch"
```

### Task 4: Parser, cache plumbing and BI vision

**Files:**
- Modify: `src/web_scout/scraping/types.py` (`ParseResult`)
- Modify: `src/web_scout/scraping/_parser.py` (`DefaultParser.parse_html`)
- Modify: `src/web_scout/tools/types.py` (`CachedSourceArtifact`)
- Modify: `src/web_scout/tools/session_cache.py` (`cacheable_from_parse_result`, `_url_fetch_task`, `get_or_fetch_session_source_artifact`)
- Test: `tests/test_dashboard_parse_cache.py`

**Interfaces:**
- Consumes: `FetchResult.dashboard_kind`, `.data_sources`, `.rendered_text` from Task 3.
- Produces:
  - `ParseResult` and `CachedSourceArtifact` both gain `dashboard_kind: DashboardKind = ""` and `data_sources: tuple[DataSource, ...] = ()`.
  - `_url_fetch_task` gains the cache-key parameter `data_capture_version: int`, passed as `1`, so pages cached before this change are not reused.

- [ ] **Step 1: Write the failing tests**

```python
async def test_rendered_grid_text_is_appended_when_richer():
    fr = fetch_result(html="<div>Eurostat</div>", dashboard_kind="dashboard",
                      rendered_text="Country\t2023\t2024\nBelgium\t11.7\t11.8\n" * 40)
    pr = await DefaultParser().parse_html(fr, ctx(fr.url))
    assert "Belgium\t11.7\t11.8" in pr.text_content

async def test_bi_page_uses_vision(monkeypatch):
    monkeypatch.setattr(_vision, "scrape_url_via_vision", fake_vision_returning("Loans 2025: 41,203"))
    fr = fetch_result(url="https://app.powerbi.com/view?r=x", html="<div>Microsoft Power BI</div>", dashboard_kind="bi")
    pr = await DefaultParser(vision_model="gemini/x").parse_html(fr, ctx(fr.url))
    assert "41,203" in pr.text_content

async def test_bi_page_without_vision_model_keeps_shell_text():
    fr = fetch_result(url="https://app.powerbi.com/view?r=x", html="<div>Microsoft Power BI</div>", dashboard_kind="bi")
    pr = await DefaultParser(vision_model=None).parse_html(fr, ctx(fr.url))
    assert "Microsoft Power BI" in pr.text_content

def test_cache_round_trip_keeps_sources():
    src = DataSource("D1", "https://t.org/a.json", "response_json", "application/json", '{"a": 1}', 8)
    art = cacheable_from_parse_result("https://t.org/app", parse_result(data_sources=(src,), dashboard_kind="dashboard"))
    assert art.data_sources == (src,) and art.dashboard_kind == "dashboard"

def test_old_cached_artifact_without_new_fields_loads():
    art = CachedSourceArtifact(url="https://t.org", title="t", artifact_kind="text", text_content="x")
    assert art.data_sources == () and art.dashboard_kind == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_dashboard_parse_cache.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the plumbing**

Append `rendered_text` under a `## Rendered page text` heading when it holds at least twice the numeric tokens of the markdown content. A `bi` page with a vision model replaces the shell text with the vision text. Without a vision model it keeps the shell text. Copy `dashboard_kind` and `data_sources` through `ParseResult`, `cacheable_from_parse_result` and the cache key.

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_dashboard_parse_cache.py tests/test_result_cache.py tests/test_html_to_markdown.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/web_scout/scraping src/web_scout/tools/types.py src/web_scout/tools/session_cache.py tests/test_dashboard_parse_cache.py
git commit -m "feat: carry dashboard data sources through parse and cache"
```

### Task 5: Records, lookup with ranking, and catalog

**Files:**
- Create: `src/web_scout/tools/data_lookup.py`
- Test: `tests/test_data_lookup.py`

**Interfaces:**
- Consumes: `DataSource` from Task 2.
- Produces:
  - `records_from_source(source: DataSource) -> list[dict]`
  - `@dataclass(frozen=True) class LookupResult: source_id: str; url: str; total_matches: int; records: list[dict]`
  - `lookup_records(source: DataSource, term: str, limit: int = 30) -> LookupResult`
  - `render_catalog(sources: Sequence[DataSource]) -> str`
  - Revised by the user mid-task: `lookup_records(source, term="", limit=30, rank_by=None, descending=True)` can order matching records by one numeric field (top-level or in `_context`); `LookupResult` gains `ranked_by` and `descending`. `figure_in_source` was removed with the verbatim check.

- [ ] **Step 1: Write the failing tests**

```python
def test_nested_records_keep_ancestor_context():
    body = json.dumps({"periods": [{"startDate": {"year": 2025}, "endDate": {"year": 2026},
                                    "features": [{"loi": 2, "loiname": 19124, "areas": [{"type": 1, "area": 155.79}]}]}]})
    rec = records_from_source(src(body))[0]
    assert rec["loiname"] == 19124 and rec["_context"]["endDate"] == {"year": 2026}

def test_columnar_json_is_zipped():
    body = json.dumps({"values": [1.5, 2.5], "years": [2019, 2020], "entities": [76, 76]})
    assert records_from_source(src(body)) == [{"values": 1.5, "years": 2019, "entities": 76},
                                               {"values": 2.5, "years": 2020, "entities": 76}]

def test_json_stat_values_keep_labels():
    body = json.dumps({"class": "dataset", "id": ["geo", "time"], "size": [1, 2], "value": [11.7, 11.8],
                       "dimension": {"geo": {"category": {"index": {"BE": 0}, "label": {"BE": "Belgium"}}},
                                     "time": {"category": {"index": {"2023": 0, "2024": 1}}}}})
    assert {"geo": "Belgium", "time": "2024", "value": 11.8} in records_from_source(src(body))

def test_bare_scalar_list_yields_no_records():
    assert records_from_source(src(json.dumps([1, 2, 3, 4]))) == []

def test_csv_rows():
    assert records_from_source(src("year,value\n2025,3335\n", kind="response_csv")) == [{"year": "2025", "value": "3335"}]

def test_lookup_is_accent_and_case_insensitive_and_unsorted():
    body = json.dumps({"lois": [{"gid": 1, "loiname": "Lábrea"}, {"gid": 2, "loiname": "Altamira"}]})
    res = lookup_records(src(body), "labrea")
    assert res.total_matches == 1 and res.records[0]["gid"] == 1

def test_catalog_is_short():
    text = render_catalog([src(json.dumps({"rows": [{"a": i} for i in range(5000)]}))] * 8)
    assert len(text) <= 2500 and "D1" in text

@pytest.mark.parametrize("figure,source,ok", [
    ("155.8", '{"area": 155.79873}', True),
    ("156", '{"area": 155.79873}', False),
    ("3.335", '{"total": 3335}', True),
    ("3,335", '{"total": 3335}', True),
    ("1.309,6", "1309.6", True),
    ("4,210", '{"total": 3335}', False),
])
def test_figure_in_source(figure, source, ok):
    assert figure_in_source(figure, source) is ok
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_data_lookup.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `src/web_scout/tools/data_lookup.py`**

Records: a list of objects gives one record per object, with scalar or small-dict fields of enclosing objects under `_context`. A dict of two or more equal-length lists is zipped. A JSON-stat dataset expands every cell to its dimension labels plus `value`. CSV gives one dict per row. `download_xlsx` sources are opened through `get_or_fetch_session_source_artifact` and their markdown table rows become records. Lookup matches the term against the record's serialized text after accent folding and lowercasing, keeps source order, and never sorts. Verbatim check, my call to flag to the user: a figure passes if it equals a source number after trying both separator readings, or if rounding a longer source decimal to the figure's decimal places gives the figure, provided the figure keeps at least one decimal place or the source number is an integer.

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_data_lookup.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/web_scout/tools/data_lookup.py tests/test_data_lookup.py
git commit -m "feat: look up and rank records in captured data sources"
```

### Task 6: Extractor integration, quote verification and citations

**Files:**
- Modify: `src/web_scout/tools/types.py` (`ExtractorOutput`, new `DataQuote`)

Revised by the user: there is no verbatim figure check. `DataQuote` records which data file a fact came from so it can be cited; a quote whose `source_id` is not in the page's sources is dropped. The `figures` field and the invented-figure test below are superseded.
- Modify: `src/web_scout/tools/extractor.py` (`build_extractor_agent`, instructions)
- Modify: `src/web_scout/tools/scraper.py` (`scrape_and_extract`)
- Test: `tests/test_dashboard_extraction.py`

**Interfaces:**
- Consumes: `CachedSourceArtifact.data_sources` and `.dashboard_kind` from Task 4; `lookup_records` (with `rank_by`), `render_catalog` from Task 5.
- Produces:
  - `class DataQuote(BaseModel): source_id: str; quote: str`
  - `ExtractorOutput.data_quotes: list[DataQuote] = []`
  - `build_extractor_agent(..., data_sources: Sequence[DataSource] = (), is_dashboard: bool = False)`. When sources exist it adds a function tool `open_data_source(source_id: str, lookup: str) -> str` that returns `LookupResult` as compact JSON lines. `is_dashboard` allows the click tools regardless of page shape.

- [ ] **Step 1: Write the failing tests, faking `run_with_retry` as `tests/test_sentinel_link_preservation.py` does**

`install_fake_extractor`, `cache_artifact` and `make_tool` are helpers local to the test module. They fake `run_with_retry`, `get_or_fetch_session_source_artifact` and build the scrape tool with a `ResearchTracker`. Task 7 reuses the same helpers by importing them from this module.

```python
async def test_catalog_and_tool_reach_the_extractor(monkeypatch):
    seen = install_fake_extractor(monkeypatch, output=ExtractorOutput(title="t", has_evidence=False,
                                  relevant_content="[No relevant content found for this query]"))
    cache_artifact(monkeypatch, dashboard_kind="dashboard", data_sources=(NAMES_SRC, DATA_SRC))
    await make_tool()(DASH_URL)
    assert "D1" in seen.input_text and "open_data_source" in seen.tool_names
    assert "click_element" in seen.tool_names

async def test_verified_data_quote_is_recorded_as_its_own_source(monkeypatch):
    install_fake_extractor(monkeypatch, output=ExtractorOutput(title="t", has_evidence=False,
        relevant_content="[No relevant content found for this query]",
        data_quotes=[DataQuote(source_id="D2", quote="Altamira (gid 19124), 2025-2026: area 155.8 km2")]))
    cache_artifact(monkeypatch, dashboard_kind="dashboard", data_sources=(NAMES_SRC, DATA_SRC))
    tracker = ResearchTracker()
    await make_tool(tracker)(DASH_URL)
    assert tracker.action_for(DATA_SRC.url) == "scraped"
    assert tracker.action_for(DASH_URL) == "scraped_irrelevant"

async def test_quote_with_unknown_source_is_dropped(monkeypatch):
    install_fake_extractor(monkeypatch, output=ExtractorOutput(title="t", has_evidence=False,
        relevant_content="[No relevant content found for this query]",
        data_quotes=[DataQuote(source_id="D9", quote="Altamira: 155.8 km2")]))
    cache_artifact(monkeypatch, dashboard_kind="dashboard", data_sources=(NAMES_SRC, DATA_SRC))
    tracker = ResearchTracker()
    await make_tool(tracker)(DASH_URL)
    assert tracker.action_for(DATA_SRC.url) is None

async def test_non_dashboard_page_gets_no_catalog(monkeypatch):
    seen = install_fake_extractor(monkeypatch, output=ExtractorOutput(title="t", relevant_content="Fact 1. " * 80))
    cache_artifact(monkeypatch, dashboard_kind="", data_sources=())
    await make_tool()(ARTICLE_URL)
    assert "open_data_source" not in seen.tool_names and "Data sources" not in seen.input_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_dashboard_extraction.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

The scrape tool appends `render_catalog` under a `## Data sources` heading to the page text it sends, only when sources exist. Extractor instructions gain a data-source section: use `open_data_source` to find or rank exact records, put data-derived facts in `data_quotes` with values as stored, rankings from a cited data file are allowed, never sum or average, and label lookups across sources are allowed. `open_data_source` takes `source_id`, `lookup` and optional `rank_by`. After extraction, drop quotes whose `source_id` is unknown, logging each drop at INFO. Record each surviving quote with `tracker.record_scrape(source.url, title, quote_text, reference=title)`, where the title names the page title and the data file. The outcome is a success when the page has evidence or any quote is recorded. The page itself is recorded as scraped only when it has evidence. Log `[extract] data_source_opened <source_id> <url>`, `[extract] data_quote_recorded <source_id> <url>` and `[extract] data_quote_dropped <source_id> unknown source` lines so the comparison can count them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_dashboard_extraction.py tests/test_sentinel_link_preservation.py tests/test_scrape_tool_dedupe.py tests/test_interactive_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/web_scout/tools tests/test_dashboard_extraction.py
git commit -m "feat: let the extractor quote and rank dashboard data"
```

### Task 7: Requirements for partial answers (Part C)

**Files:**
- Modify: `src/web_scout/_pipeline_flow.py` (`_build_query_agents`, `_generate_search_queries`, `_search_and_scrape_iteration`)
- Modify: `src/web_scout/tools/tracker.py` (`ResearchTracker.__init__`)
- Modify: `src/web_scout/tools/extractor.py` (instructions, new helper)
- Modify: `src/web_scout/tools/types.py` (`ExtractorOutput.has_evidence` description)
- Modify: `src/web_scout/tools/scraper.py` (extractor input text and the `query` passed to `extract_pdf_for_query`)
- Modify: `src/web_scout/tools/pdf_extractor.py` (`_NO_EVIDENCE_RULE` only)
- Test: `tests/test_requirements_extraction.py`

**Interfaces:**
- Produces:
  - `ResearchTracker.requirements: list[str]`, default empty.
  - `format_research_query(query: str, requirements: Sequence[str]) -> str` in `tools/extractor.py`. Returns `query` unchanged when `requirements` is empty or equals `[query]`. Otherwise appends a numbered `Research requirements:` block.
- Consumes: nothing from Parts A or B.

- [ ] **Step 1: Write the failing tests**

```python
def test_query_agent_always_lists_requirements(monkeypatch):
    monkeypatch.setenv("DISABLE_JEV", "true")
    qa, _ = _build_query_agents(query_gen_model=object(), evaluator_model=object(), domain_expertise=None)
    assert qa.output_type is SearchQueryChecklist and "atomic requirement" in qa.instructions

async def test_requirements_reach_tracker(monkeypatch):
    # Fake Runner.run returns SearchQueryChecklist(queries=[...], requirements=["A", "B"]).
    tracker = ResearchTracker()
    await _search_and_scrape_iteration(..., tracker=tracker, iteration=0)
    assert tracker.requirements == ["A", "B"]

def test_format_research_query():
    assert format_research_query("Q", []) == "Q"
    assert format_research_query("Q", ["Q"]) == "Q"
    assert format_research_query("Q", ["A", "B"]) == "Q\nResearch requirements:\n1. A\n2. B"

async def test_extractor_input_carries_requirements(monkeypatch):
    seen = install_fake_extractor(monkeypatch, output=ExtractorOutput(title="t", relevant_content="Fact. " * 80))
    tracker = ResearchTracker(); tracker.requirements = ["A", "B"]
    await make_tool(tracker)(ARTICLE_URL)
    assert "Research requirements:\n1. A\n2. B" in seen.input_text

def test_instructions_contain_requirement_rule():
    assert "at least one requirement" in _BASE_EXTRACTOR_INSTRUCTIONS
    assert "at least one requirement" in pdf_extractor._NO_EVIDENCE_RULE

async def test_pdf_extractor_receives_requirements(monkeypatch):
    seen = install_fake_pdf_extract(monkeypatch)  # records the query passed to extract_pdf_for_query
    tracker = ResearchTracker(); tracker.requirements = ["A", "B"]
    await make_tool(tracker)(PDF_URL)
    assert seen.query.endswith("Research requirements:\n1. A\n2. B")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_requirements_extraction.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Remove the Jev-only gating on the checklist output type and the requirement instruction. Set `_research_requirements` on the first round for every backend, then switch the output type back as today. `_search_and_scrape_iteration` copies them to `tracker.requirements`. The extractor rule, worded like the spike: the page is evidence when it satisfies at least one requirement; extract facts only for satisfied requirements; statements about what the page lacks are not evidence. The scrape tool passes `format_research_query(query, tracker.requirements)` as the `query` of `extract_pdf_for_query`, and `_NO_EVIDENCE_RULE` gains the same rule: when the query lists requirements, the document is evidence if it satisfies at least one of them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_requirements_extraction.py tests/test_pipeline.py tests/test_agent_helpers.py tests/test_backend_selection.py -v`
Expected: PASS.

- [ ] **Step 5: Live spot check on the spike pages**

Run `/tmp/brazil_repro/spike_req.py` adapted to use the branch code without monkeypatching, adding the INPE press presentation PDF `.../ApresentacaoColetivaPRODES2026_INPE.pdf`. Expected: gov.br and the presentation accepted, TerraBrasilis home and the unlabelled dashboard rejected, in both trials.

- [ ] **Step 6: Commit**

```bash
git add src/web_scout tests/test_requirements_extraction.py
git commit -m "feat: keep pages that satisfy part of the research requirements"
```

### Task 8: Follow extractor links in open search (Part B)

**Files:**
- Modify: `src/web_scout/_pipeline_flow.py` (new helper, `_run_search_mode_impl`)
- Test: `tests/test_open_followup.py`

**Interfaces:**
- Consumes: existing `_outcome_followup_candidates`, `_rerank_followup_urls`, `_scrape_urls`, `is_blocked_domain`, `ResearchTracker.has_attempted_url`.
- Produces: `async def _follow_extractor_links(*, query: str, iteration_result: SearchIterationResult, depth: dict[str, int], followup_model: Any, followup_backend: str, tracker: ResearchTracker, scrape_tool: Any, exclude_domains: Optional[frozenset[str]]) -> SearchIterationResult`

- [ ] **Step 1: Write the failing tests**

```python
async def test_links_from_rejected_pages_are_followed_with_cap(monkeypatch):
    it = iteration_with_outcomes({
        GOV: failure_outcome(GOV, links=[PDF1, PDF2, GOV, ATTEMPTED, BLOCKED, L3, L4, L5]),
    })
    tracker = tracker_with_attempted(ATTEMPTED)
    scraped = []
    res = await _follow_extractor_links(query="q", iteration_result=it, depth={"urls_followup": 4},
                                        followup_model=None, followup_backend="gpt", tracker=tracker,
                                        scrape_tool=recording_tool(scraped), exclude_domains=frozenset({"blocked.org"}))
    assert set(scraped) <= {PDF1, PDF2, L3, L4, L5} and len(scraped) == 4
    assert GOV not in scraped and ATTEMPTED not in scraped and BLOCKED not in scraped

async def test_open_mode_runs_one_wave_per_round(monkeypatch):
    calls = run_search_mode_with_fakes(include_domains=None, rounds=2)
    assert calls.followup_waves == 2

async def test_domain_mode_does_not_run_open_followup(monkeypatch):
    calls = run_search_mode_with_fakes(include_domains=["inpe.br"], rounds=1)
    assert calls.followup_waves == 0

async def test_followed_pages_do_not_start_another_wave(monkeypatch):
    calls = run_search_mode_with_fakes(include_domains=None, rounds=1, followed_pages_have_links=True)
    assert calls.followup_waves == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_open_followup.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement**

Pool candidates from every outcome with `_outcome_followup_candidates(outcome, parent_url=url)`. Drop attempted, blocked and parent URLs. Rank with `_rerank_followup_urls`, using the first contributing outcome as parent and `cap=depth["urls_followup"]`. Scrape with `_scrape_urls`. In `_run_search_mode_impl`, call it after both the search branch and the backlog branch when `include_domains` is empty, before the coverage check, including in the last round. Log `[pipeline] open-mode follow-up on %d links: %s`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_open_followup.py tests/test_pipeline.py tests/test_followup_reranker.py tests/test_pipeline_speed.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout python tests/run_checks.py unit`
Expected: ruff clean and all tests pass. Report any failure with its output.

- [ ] **Step 6: Commit**

```bash
git add src/web_scout/_pipeline_flow.py tests/test_open_followup.py
git commit -m "feat: follow extractor links in open search"
```

### Task 9: Old-versus-new comparison and the Brazil gate

**Files:**
- Create: `scripts/compare_versions.py`
- Test: `tests/test_compare_versions.py`

**Interfaces:**
- Consumes: `run_web_research`, `WebResearchResult`; read-only `eval/judge.py`, `eval/fetch.py` and `eval/questions.json` from this branch.
- Produces a CLI:
  - `export-old --commit 37edddf --dest /tmp/ws_compare/old` runs `git archive 37edddf src | tar -x -C <dest>`.
  - `run --src <path-to-src> --label old|new --question-id brazil --out <dir>` runs one research call in a subprocess with `<src>` first on `sys.path` and default settings. The child loads the shared `.env` the way the report project's `fao_ess_lib.config.setup_environment_variables` does, with `dotenv.load_dotenv(path)` before importing `web_scout`, so `BEDROCK_MANTLE_API_KEY=${AWS_BEARER_TOKEN_BEDROCK}` is expanded. It fails fast if `BEDROCK_MANTLE_API_KEY` is unset or still starts with `${`.
  - `report --dir <dir>` writes `report.md` and `report.json`.

- [ ] **Step 1: Write the failing tests for the pure parts**

```python
def test_log_counters():
    log = "[pipeline] open-mode follow-up on 2 links: [...]\n[extract] data_source_opened D1 https://t.org/a.json\n[extract] data_quote_dropped D1 999\n"
    assert parse_log_counters(log) == {"followup_waves": 1, "followup_links": 2, "data_sources_opened": 1,
                                       "data_quotes_kept": 0, "data_quotes_dropped": 1}

def test_data_file_citation_check_uses_raw_body():
    assert check_figures_in_body(["155.8", "999.9"], '{"area": 155.79873}') == {"155.8": True, "999.9": False}

def test_bucket_summary():
    summary = bucket_summary({"scraped": [{"url": "a"}], "scraped_irrelevant": [{"url": "b"}, {"url": "c"}]})
    assert summary == {"scraped": 1, "scraped_irrelevant": 2}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_compare_versions.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `scripts/compare_versions.py`**

Each run record holds: the full `model_dump`, the URL bucket summary, wall time, a model-call count from a `litellm` success callback, the captured log, `parse_log_counters`, and a SHA-256 of the imported eval files. `report` runs `eval.judge.judge_record` with `eval.fetch.fetch_page` on each answer. The eval fetcher rejects JSON and CSV, so claims citing those files are checked with `check_figures_in_body` against the raw body fetched with `httpx`. The report shows old and new side by side: answer, citations, buckets, followed links, data sources opened, quotes kept and dropped, claim verdicts, wall time and model calls.

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_compare_versions.py -v`
Expected: PASS.

- [ ] **Step 5: Run the Brazil gate in the background, one run per version**

```bash
cd /Users/riccardo/orca/workspaces/web-scout-ai/makara
P=/Users/riccardo/.local/share/mamba/envs/web-scout
conda run -p $P python scripts/compare_versions.py export-old --commit 37edddf --dest /tmp/ws_compare/old
conda run -p $P python scripts/compare_versions.py run --src /tmp/ws_compare/old/src --label old --question-id brazil --out /tmp/ws_compare/brazil
conda run -p $P python scripts/compare_versions.py run --src $PWD/src --label new --question-id brazil --out /tmp/ws_compare/brazil
conda run -p $P python scripts/compare_versions.py report --dir /tmp/ws_compare/brazil
```

Expected: `report.md` exists with both runs.

- [ ] **Step 6: Judge the gate against the spec and stop for the user**

The gate holds when the new answer quotes the official PRODES 2026 Legal Amazon figure with an INPE citation, every municipal figure appears verbatim in its cited source, no quoted figure fails a check, and the added dashboard time stays within budget. Report the old and new results to the user with the follow-up cost, and wait for their decision before Task 10.

- [ ] **Step 7: Commit**

```bash
git add scripts/compare_versions.py tests/test_compare_versions.py
git commit -m "feat: add old-versus-new comparison runner"
```

### Task 10: Expansion set, only after the user approves the gate

**Files:**
- Create: `scripts/compare_questions.json`

**Interfaces:**
- Consumes: the Task 9 CLI, extended with `--questions scripts/compare_questions.json` as an alternative to `--question-id`.

- [ ] **Step 1: Write the question set with hand-checked answers**

Include Guatemala and Senegal from `eval/questions.json`, one question per dashboard among Our World in Data, World Bank, TerraBrasilis, Eurostat, IBGE SIDRA and FAO FPMA, and three ordinary article or PDF questions. For each dashboard question, record the expected figure and the URL where it was checked by hand, before any run.

- [ ] **Step 2: Run every question once per version and write the report**

Same commands as Task 9 Step 5, looping over the question set, in the background.

- [ ] **Step 3: Report to the user**

Success means dashboard questions improve, ordinary questions are no worse on figures and citations, and latency stays within budget. Report the per-question table and any regressions with their evidence.

### Task 11: Resolve API keys for direct LiteLLM calls (run right after Task 1)

Added at the user's request on 2026-10-26, as its own commit. Root cause: `tools/pdf_extractor._llm_json` and `scraping/_vision` call `litellm.acompletion` without `api_key`, so LiteLLM reads `BEDROCK_MANTLE_API_KEY` raw. When `.env` is loaded without expanding `${AWS_BEARER_TOKEN_BEDROCK}`, Bedrock rejects the literal placeholder as an invalid bearer token. The main pipeline avoids this because `get_model` resolves the key with `_find_api_key` and passes it explicitly.

**Files:**
- Modify: `src/web_scout/utils.py` (new helper)
- Modify: `src/web_scout/tools/pdf_extractor.py` (`_llm_json` passes `api_key`)
- Modify: `src/web_scout/scraping/_vision.py` (both `litellm.acompletion` calls pass `api_key`)
- Test: `tests/test_direct_llm_api_keys.py`

**Interfaces:**
- Produces: `resolve_api_key(model: Any) -> str | None` in `src/web_scout/utils.py`. Returns the model object's own `api_key` when it is set and is not a `${...}` placeholder; otherwise `_find_api_key(_detect_provider(name))` for the model's name; otherwise `None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_resolve_api_key_skips_unexpanded_reference(monkeypatch):
    monkeypatch.setenv("BEDROCK_MANTLE_API_KEY", "${AWS_BEARER_TOKEN_BEDROCK}")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "tok")
    assert resolve_api_key("bedrock_mantle/openai.gpt-6-luna") == "tok"

def test_resolve_api_key_prefers_model_object_key():
    model = LitellmModel(model="bedrock_mantle/openai.gpt-6-luna", api_key="explicit")
    assert resolve_api_key(model) == "explicit"

async def test_pdf_llm_json_passes_resolved_key(monkeypatch):
    monkeypatch.setenv("BEDROCK_MANTLE_API_KEY", "${AWS_BEARER_TOKEN_BEDROCK}")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "tok")
    seen = capture_acompletion(monkeypatch, pdf_extractor.litellm, content='{"evidence": []}')
    await pdf_extractor._llm_json("bedrock_mantle/openai.gpt-6-luna", "p", pdf_extractor._ChunkEvidence)
    assert seen[0]["api_key"] == "tok"

async def test_vision_calls_pass_resolved_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    seen = capture_acompletion(monkeypatch, _vision.litellm, content="text")
    await _vision._call_vision_model(image_bytes=b"x", mime_type="image/png", query="q",
                                     vision_model="gemini/gemini-3.7-flash", prompt_prefix="Extract{query_clause}.")
    assert seen[0]["api_key"] == "g"
```

`capture_acompletion` is a local helper that monkeypatches `acompletion` on the given module object and records kwargs, following `tests/test_pdf_structured_output.py`. Use the schema class that `_llm_json` really receives for chunk evidence; check its name in `pdf_extractor.py`. Cover the second `_vision` call site the same way if it is a separate function.

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_direct_llm_api_keys.py -v`
Expected: FAIL with `ImportError` for `resolve_api_key`.

- [ ] **Step 3: Implement `resolve_api_key` and pass `api_key=resolve_api_key(model)` at the three call sites**

`get_model` keeps its current behaviour; it may use the helper if that removes duplication without changing results.

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_direct_llm_api_keys.py tests/test_pdf_structured_output.py tests/test_pdf_has_evidence.py tests/test_model_routing.py tests/test_document_visuals.py -v`
Expected: PASS.
