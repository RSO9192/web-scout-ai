"""Capture the fast-HTTP HTML of dashboard and article pages as gzipped test fixtures.

Run once with network from the repository root:

    python tests/test_data/dashboards/capture_fixtures.py

Each page is fetched with the package's own ``http_get`` (stealthy headers), the same
request the fast scraping path makes, and saved as ``<name>.html.gz`` next to this file.
"""

import asyncio
import gzip
from pathlib import Path

from web_scout.scraping._resources import close_resources, http_get

FIXTURE_DIR = Path(__file__).parent

PAGES = {
    "terrabrasilis": "https://terrabrasilis.dpi.inpe.br/app/dashboard/deforestation/biomes/legal_amazon/increments",
    "owid": "https://ourworldindata.org/grapher/annual-deforestation",
    "worldbank": "https://data.worldbank.org/indicator/AG.LND.FRST.ZS?locations=BR",
    "sidra": "https://sidra.ibge.gov.br/tabela/1612",
    "eurostat": "https://ec.europa.eu/eurostat/databrowser/view/tps00001/default/table?lang=en",
    "fpma": "https://fpma.fao.org/giews/fpmat4/#/dashboard/tool/domestic",
    "gfw": "https://www.globalforestwatch.org/dashboards/country/BRA/",
    "powerbi": (
        "https://app.powerbi.com/view?r=eyJrIjoiM2FhMGJiZDUtZjM1NS00NmFhLWEzMDUtNmM4OWVkMzgyM2YyIiwidCI6ImE2ZmEzYjAzLTBh"
        "M2MtNDI1OC04NDMzLWExMjBkZmZjZDM0OCIsImMiOjh9&pageName=ReportSection0fd9b7e5048e21eacb2c"
    ),
    "tableau": "https://public.tableau.com/views/FoodPriceDashboard_17762642711180/Dashboard1?:language=en-US&:embed=true",
    "govbr_news": (
        "https://www.gov.br/inpe/pt-br/assuntos/ultimas-noticias/"
        "brasil-alcanca-menores-taxas-de-desmatamento-da-serie-historica-na-amazonia-e-no-cerrado"
    ),
    "infoamazonia": "https://infoamazonia.org/en/2021/11/19/deforestation-amazon-grows-22-in-2021/",
    "oeco": "https://oeco.org.br/externo/amazonia-tem-a-menor-taxa-de-desmatamento-ja-registrada-pelo-inpe/",
}


async def capture(name: str, url: str) -> None:
    """Fetch one page and write its HTML to ``<name>.html.gz``; print the outcome."""
    try:
        response = await http_get(url, stealthy_headers=True, follow_redirects=True, timeout=30)
    except Exception as exc:
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        return
    html = getattr(response, "html_content", None) or ""
    (FIXTURE_DIR / f"{name}.html.gz").write_bytes(gzip.compress(html.encode("utf-8")))
    print(f"{response.status} {name}: {len(html)} chars")


async def main() -> None:
    try:
        await asyncio.gather(*(capture(name, url) for name, url in PAGES.items()))
    finally:
        await close_resources()


if __name__ == "__main__":
    asyncio.run(main())
