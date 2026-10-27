"""Dashboard detection on real fast-HTTP captures (see tests/test_data/dashboards/README.md)."""

import gzip
import time
from pathlib import Path

import pytest

from web_scout.scraping._data_sources import assess_dashboard

FIXTURE_DIR = Path(__file__).parent / "test_data" / "dashboards"

FIXTURES = [
    (
        "terrabrasilis",
        "https://terrabrasilis.dpi.inpe.br/app/dashboard/deforestation/biomes/legal_amazon/increments",
        "dashboard",
    ),
    ("owid", "https://ourworldindata.org/grapher/annual-deforestation", "dashboard"),
    ("worldbank", "https://data.worldbank.org/indicator/AG.LND.FRST.ZS?locations=BR", "dashboard"),
    ("sidra", "https://sidra.ibge.gov.br/tabela/1612", "dashboard"),
    ("eurostat", "https://ec.europa.eu/eurostat/databrowser/view/tps00001/default/table?lang=en", "dashboard"),
    ("fpma", "https://fpma.fao.org/giews/fpmat4/#/dashboard/tool/domestic", "dashboard"),
    ("gfw", "https://www.globalforestwatch.org/dashboards/country/BRA/", "dashboard"),
    (
        "powerbi",
        "https://app.powerbi.com/view?r=eyJrIjoiM2FhMGJiZDUtZjM1NS00NmFhLWEzMDUtNmM4OWVkMzgyM2YyIiwidCI6ImE2ZmEzYjAzLTBh"
        "M2MtNDI1OC04NDMzLWExMjBkZmZjZDM0OCIsImMiOjh9&pageName=ReportSection0fd9b7e5048e21eacb2c",
        "bi",
    ),
    (
        "tableau",
        "https://public.tableau.com/views/FoodPriceDashboard_17762642711180/Dashboard1?:language=en-US&:embed=true",
        "bi",
    ),
    (
        "govbr_news",
        "https://www.gov.br/inpe/pt-br/assuntos/ultimas-noticias/"
        "brasil-alcanca-menores-taxas-de-desmatamento-da-serie-historica-na-amazonia-e-no-cerrado",
        "",
    ),
    ("infoamazonia", "https://infoamazonia.org/en/2021/11/19/deforestation-amazon-grows-22-in-2021/", ""),
    ("oeco", "https://oeco.org.br/externo/amazonia-tem-a-menor-taxa-de-desmatamento-ja-registrada-pelo-inpe/", ""),
]


@pytest.mark.parametrize("name,url,expected", FIXTURES)
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


def test_large_unpunctuated_table_is_fast():
    html = "<html><body><table><tr>" + "<td>1234 5678</td>" * 100_000 + "</tr></table></body></html>"
    start = time.perf_counter()
    assess_dashboard("https://example.org/report/1", html)
    assert time.perf_counter() - start < 1.0


def test_article_shell_with_chart_library_is_not_dashboard():
    html = '<html><body><div id="root"></div><script src="https://cdn.example.org/leaflet.js"></script></body></html>'
    result = assess_dashboard("https://example.org/news/2024/05/forest-loss", html)
    assert result.kind == "", result.reasons


def test_article_shell_with_large_state_blob_is_not_dashboard():
    state = "{" + ",".join(f'"item{i}":{{"id":{i},"ts":{1700000000 + i}}}' for i in range(3000)) + "}"
    assert len(state) >= 50_000
    html = f'<html><body><div id="root"></div><script>window.__INITIAL_STATE__ = {state};</script></body></html>'
    result = assess_dashboard("https://example.org/news/2024/05/forest-loss", html)
    assert result.kind == "", result.reasons


def test_dashboard_word_in_article_slug_is_not_dashboard():
    html = (
        "<html><body>" + "<p>Good dashboards start with a clear question for the reader.</p>" * 300 + "</body></html>"
    )
    result = assess_dashboard("https://example.org/blog/dashboard-design-tips-for-2024", html)
    assert result.kind == "", result.reasons
