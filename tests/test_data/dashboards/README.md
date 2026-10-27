# Dashboard detection fixtures

Fast-HTTP HTML captured on 2026-10-26 by `capture_fixtures.py` (the package's `http_get`,
`stealthy_headers=True, follow_redirects=True`), stored gzipped as `<name>.html.gz`.
Used by `tests/test_dashboard_detection.py`. All 12 downloads returned HTTP 200.

| name | URL | expected kind |
|---|---|---|
| terrabrasilis | https://terrabrasilis.dpi.inpe.br/app/dashboard/deforestation/biomes/legal_amazon/increments | dashboard |
| owid | https://ourworldindata.org/grapher/annual-deforestation | dashboard |
| worldbank | https://data.worldbank.org/indicator/AG.LND.FRST.ZS?locations=BR | dashboard |
| sidra | https://sidra.ibge.gov.br/tabela/1612 | dashboard |
| eurostat | https://ec.europa.eu/eurostat/databrowser/view/tps00001/default/table?lang=en | dashboard |
| fpma | https://fpma.fao.org/giews/fpmat4/#/dashboard/tool/domestic | dashboard |
| gfw | https://www.globalforestwatch.org/dashboards/country/BRA/ | dashboard |
| powerbi | https://app.powerbi.com/view?r=eyJrIjoiM2FhMGJiZDUtZjM1NS00NmFhLWEzMDUtNmM4OWVkMzgyM2YyIiwidCI6ImE2ZmEzYjAzLTBhM2MtNDI1OC04NDMzLWExMjBkZmZjZDM0OCIsImMiOjh9&pageName=ReportSection0fd9b7e5048e21eacb2c | bi |
| tableau | https://public.tableau.com/views/FoodPriceDashboard_17762642711180/Dashboard1?:language=en-US&:embed=true | bi |
| govbr_news | https://www.gov.br/inpe/pt-br/assuntos/ultimas-noticias/brasil-alcanca-menores-taxas-de-desmatamento-da-serie-historica-na-amazonia-e-no-cerrado | "" |
| infoamazonia | https://infoamazonia.org/en/2021/11/19/deforestation-amazon-grows-22-in-2021/ | "" |
| oeco | https://oeco.org.br/externo/amazonia-tem-a-menor-taxa-de-desmatamento-ja-registrada-pelo-inpe/ | "" |

Redirects at capture time: `tableau` landed on
`https://public.tableau.com/app/profile/anubhuti.pal4290/viz/FoodPriceDashboard_17762642711180/Dashboard1`
and `gfw` on `https://globalnaturewatch.org/dashboards/country/BRA/`. The tests use the URLs above.
