# Quality Benchmark: web-scout-ai vs OpenAI web search

Date: 2026-04-16 13:39
web-scout-ai backend: serper
OpenAI model: gpt-5.4-mini
Judge model: gpt-5.4-mini

## Summary

| Query | Tool | Scraped | Failed | Bot | Avg Depth | Time (s) | URL Rel | Compreh | Synthesis | Coverage | Overall |
|-------|------|---------|--------|-----|-----------|----------|---------|---------|-----------|----------|---------|
| Kenya interannual variability and long-term trends in p… | web-scout-ai | 6/9 | 2 | 0 | 2,756 | 105.7 | 4/5 | 4/5 | 4/5 | 4/5 | 4.0/5 |
| Kenya interannual variability and long-term trends in p… | openai-websearch (gpt-5.4-mini) | 9/9 | 0 | 0 | 508 | 15.1 | 4/5 | 3/5 | 3/5 | 4/5 | 3.5/5 |
| Global food insecurity trends 2022–2024 — FAO State of … | web-scout-ai | 8/9 | 1 | 0 | 2,988 | 381.7 | 4/5 | 4/5 | 3/5 | 4/5 | 3.8/5 |
| Global food insecurity trends 2022–2024 — FAO State of … | openai-websearch (gpt-5.4-mini) | 4/4 | 0 | 0 | 900 | 17.4 | 4/5 | 4/5 | 4/5 | 3/5 | 3.8/5 |
| Ethiopia crop production statistics 2023 — cereals area… | web-scout-ai | 3/10 | 4 | 0 | 1,055 | 164.4 | 2/5 | 2/5 | 1/5 | 2/5 | 1.8/5 |
| Ethiopia crop production statistics 2023 — cereals area… | openai-websearch (gpt-5.4-mini) | 3/3 | 0 | 0 | 362 | 15.0 | 3/5 | 1/5 | 2/5 | 2/5 | 2.0/5 |

---

## Detailed Results

### Query 1: Kenya interannual variability and long-term trends in precipitation — current status and recent trend

#### web-scout-ai

**Scrape breakdown:** 6/9 scraped (0 failed / 0 bot-blocked / 2 http-error / 1 policy-blocked / 0 irrelevant) — avg content depth: **2,756 chars/source**

**Search queries issued:**
- interannual variability of Kenyan seasonal rainfall MAM OND recent trends
- Kenya long-term precipitation trends and interannual variability analysis 1981-2024
- Kenya recent rainfall patterns and climate change impact report 2024

**Failed URLs:**

| Category | URL | Error |
|----------|-----|-------|
| source_http_error | https://www.researchgate.net/publication/334999967_Long-term_trends_in_rainfall_and_temperature_using_high-resolution_climate_datasets_in_East_Africa | [source http error: [Scrape failed: Skipped: HTTP 403 on GET]] |
| source_http_error | https://www.researchgate.net/publication/387262739_Climate_change_impacts_in_Kenya_2024_REPORT_WHAT_CLIMATE_CHANGE_MEANS_FOR_A_COUNTRY_AND_ITS_PEOPLE | [source http error: [Scrape failed: Skipped: HTTP 403 on GET]] |
| blocked_by_policy | https://rmets.onlinelibrary.wiley.com/doi/full/10.1002%2Fjoc.8528 | [blocked by policy: [Scrape failed: Skipped: blocked domain]] |

**Source content previews:**

- **[Observations of enhanced rainfall variability in Kenya, East Africa](https://pmc.ncbi.nlm.nih.gov/articles/PMC11153539/)**  
  Research on rainfall in Kenya from 1981 to 2021 reveals significant increases in interannual and intraseasonal variability, alongside diverging trends between the two primary rainy seasons. ### Long-Term Precipitation Trends (1981–2021) * **Long Rain…
- **[Long-term trends in rainfall and temperature using high-resolution climate datasets in East Africa | Scientific Reports](https://www.nature.com/articles/s41598-019-47933-8)**  
  Based on high-resolution climate datasets (CHIRPS for rainfall, 1981–2016), the following trends and statuses for Kenya's precipitation and temperature were identified: ### Current Rainfall Status and Spatial Distribution * **Country-wide average:** …
- **[Spatial and Temporal Trends of Extreme Precipitation in Eastern Africa during January 1981-2023](https://www.scirp.org/journal/paperinformation?paperid=141853)**  
  According to the study (Masunga et al., 2025) analyzing Eastern Africa from January 1981 to 2023, the region experiences significant precipitation variability and long-term trends, with specific observations for Kenya: ### Interannual Variability - *…
- **[State of the Climate Kenya 2024](https://meteo.go.ke/documents/1353/State_of_the_Climate_Kenya_2024_v1.pdf)**  
  The 'State of the Climate Kenya 2024' report provides a detailed overview of precipitation variability and trends, using the 1991–2020 climatology as a baseline. ### Current Precipitation Status (2024) 2024 was characterized by extreme spatial and te…
- **[Kenya: Impacts of Climatic Shocks on Communities - April 2024](https://reliefweb.int/report/kenya/kenya-impacts-climatic-shocks-communities-april-2024)**  
  Kenya has recently experienced extreme interannual precipitation variability, characterized by a rapid shift from a historic multi-year drought to severe flooding. ### Current Precipitation Trends (2020–2024) - **2020–2022 Drought:** Kenya faced five…
- **[State of the climate report Kenya 2024](https://www.sei.org/publications/state-climate-report-kenya-2024/)**  
  The "State of the Climate Kenya 2024" report, published by the Kenya Meteorological Department in collaboration with SEI researchers, provides a technical assessment of Kenya's climate during the year 2024. Key findings and structural details related…

**Scores:** URL Relevance 4/5 | Tailored Comprehensiveness 4/5 | Synthesis 4/5 | Extraction Coverage 4/5 | **Overall 4.0/5**

- **URL Relevance:** Most URLs are strong primary or near-primary sources for Kenya precipitation trends, including a Kenya Meteorological Department climate report, a peer-reviewed Kenya rainfall study, and a high-resolution East Africa climate paper. One source is more regional (Eastern Africa January extremes) and one is a ReliefWeb humanitarian brief, but overall the set is well targeted to the query.
- **Tailored Comprehensiveness:** The extracts contain concrete query-relevant details on interannual variability, seasonal trends, recent drought-to-flood shifts, and long-term MAM/OND changes, including specific periods and some numeric trend values. However, not every source provides equally direct evidence for the exact requested framing of 'current status and recent trend,' and some extracts are broader than the query or only partially Kenya-specific.
- **Synthesis Quality:** The synthesis is largely grounded in the extracted content and accurately reflects the main pattern: high variability, declining long rains, increasing short rains, and a recent swing from drought to floods. A few claims are somewhat stitched together across sources and the January trend section is more specific than the query requires, but the specific numbers and statements cited are present in the extracts.
- **Extraction Coverage:** There are 6 sources, which is strong breadth, and several extracts are fairly substantial and specific rather than just metadata. The coverage is not maximal because some sources are shorter or more summary-like than deep document extractions, but overall the gathered evidence is solid.

**Synthesis:**

Kenya's precipitation is characterized by high interannual variability and a significant recent increase in extreme weather events, shifting rapidly between record-breaking droughts and severe flooding [Observations of enhanced rainfall variability in Kenya, East Africa](https://pmc.ncbi.nlm.nih.gov/articles/PMC11153539/) [Kenya: Impacts of Climatic Shocks on Communities - April 2024](https://reliefweb.int/report/kenya/kenya-impacts-climatic-shocks-communities-april-2024). Current trends indicate a divergence between the two primary rainy seasons, with the long rains (March-April-May, MAM) generally decreasing and the short rains (October-November-December, OND) generally increasing [Observations of enhanced rainfall variability in Kenya, East Africa](https://pmc.ncbi.nlm.nih.gov/articles/PMC11153539/).

### Long-Term Precipitation Trends
*   **March-April-May (MAM) 'Long Rains':** Research from 1981 to 2021 shows MAM rainfall is decreasing overall, contributing to widespread drought [Observations of enhanced rainfall variability in Kenya, East Africa](https://pmc.ncbi.nlm.nih.gov/articles/PMC11153539/). Central Kenya, specifically around Kora National Reserve and Marsabit, has seen significant decreases of up to -50 mm [Long-term trends in rainfall and temperature using high-resolution climate datasets in East Africa | Scientific Reports](https://www.nature.com/articles/s41598-019-47933-8). However, 2024 was an anomaly, with MAM being exceptionally wet (111–200% of the long-term mean) across central highlands and the Rift Valley [State of the Climate Kenya 2024](https://meteo.go.ke/documents/1353/State_of_the_Climate_Kenya_2024_v1.pdf).
*   **October-November-December (OND) 'Short Rains':** This season shows a general long-term trend toward wetter conditions [Observations of enhanced rainfall variability in Kenya, East Africa](https://pmc.ncbi.nlm.nih.gov/articles/PMC11153539/). Western Kenya specifically has seen increases of up to +50 mm [Long-term trends in rainfall and temperature using high-resolution climate datasets in East Africa | Scientific Reports](https://www.nature.com/articles/s41598-019-47933-8). OND 2023 was notably wet, but OND 2024 reversed this recent trend, recording below-average rainfall with a delayed onset [State of the Climate Kenya 2024](https://meteo.go.ke/documents/1353/State_of_the_Climate_Kenya_2024_v1.pdf).
*   **Extreme January Trends:** Analysis from 1981 to 2023 identified January as the month with the highest mean precipitation and greatest variability in the region. There is a statistically significant increasing trend in January precipitation (0.844 mm/year), with a notable increase in rainfall intensity (SDII) and the frequency of heavy rainy days [Spatial and Temporal Trends of Extreme Precipitation in Eastern Africa during January 1981-2023](https://www.scirp.org/journal/paperinformation?paperid=141853).

### Recent Status and Interannual Variability
*   **Post-2013 Shift:** Seasonal variability increased significantly after 2013, marked by more unpredictable timing (delayed onset and early cessation) and more frequent extreme events [Observations of enhanced rainfall variability in Kenya, East Africa](https://pmc.ncbi.nlm.nih.gov/articles/PMC11153539/).
*   **2020–2024 Extremes:** Kenya experienced five consecutive failed rainy seasons between late 2020 and late 2022, the most protracted drought in recent history [Kenya: Impacts of Climatic Shocks on Communities - April 2024](https://reliefweb.int/report/kenya/kenya-impacts-climatic-shocks-communities-april-2024). This was followed by a rapid transition to extreme floods in 2023 and 2024 [State of the Climate Kenya 2024](https://meteo.go.ke/documents/1353/State_of_the_Climate_Kenya_2024_v1.pdf).
*   **Spatial Distribution:** High rainfall (~2,000 mm annually) is concentrated in Western Kenya, while Northern and Eastern regions typically record less than 500 mm [Long-term trends in rainfall and temperature using high-resolution climate datasets in East Africa | Scientific Reports](https://www.nature.com/articles/s41598-019-47933-8). Topography, such as Mount Kenya, further influences local precipitation through orographic effects [Spatial and Temporal Trends of Extreme Precipitation in Eastern Africa during January 1981-2023](https://www.scirp.org/journal/paperinformation?paperid=141853).

### Climate Drivers
Precipitation remains heavily modulated by large-scale forcings including the El Ni&ntilde;o Southern Oscillation (ENSO) and the Indian Ocean Dipole (IOD). Typically, El Ni&ntilde;o and positive IOD phases correlate with increased rainfall, while La Ni&ntilde;a and negative IOD phases correlate with drought [Observations of enhanced rainfall variability in Kenya, East Africa](https://pmc.ncbi.nlm.nih.gov/articles/PMC11153539/) [State of the Climate Kenya 2024](https://meteo.go.ke/documents/1353/State_of_the_Climate_Kenya_2024_v1.pdf). However, since 2013, the historical coherence between ENSO and Kenyan rainfall has diminished, contributing to higher unpredictability [Observations of enhanced rainfall variability in Kenya, East Africa](https://pmc.ncbi.nlm.nih.gov/articles/PMC11153539/). Climate change is reported to be intensifying these cycles and increasing sea surface temperatures, which further amplifies the magnitude of both wet and dry events [Kenya: Impacts of Climatic Shocks on Communities - April 2024](https://reliefweb.int/report/kenya/kenya-impacts-climatic-shocks-communities-april-2024).

#### openai-websearch (gpt-5.4-mini)

**Scores:** URL Relevance 4/5 | Tailored Comprehensiveness 3/5 | Synthesis 3/5 | Extraction Coverage 4/5 | **Overall 3.5/5**

- **URL Relevance:** Most URLs are primary or highly authoritative sources for Kenya precipitation trends, especially the Kenya Meteorological Department reports and the peer-reviewed studies. A few are secondary/news sources (AP, WMO) or regionally broader than Kenya, but overall the set is well targeted to the query.
- **Tailored Comprehensiveness:** Several extracts contain directly relevant specifics such as 2024 precipitation anomalies, 2023 rainfall variability, and a 40-year rainfall variability study. However, most extracts do not provide the actual long-term trend metrics or a clear national-level quantitative assessment of interannual variability beyond general statements, so the evidence is only partially complete for the query.
- **Synthesis Quality:** The synthesis is mostly grounded in the extracts and avoids obvious fabrication, but it adds interpretive framing like "increasing exposure to extremes" and "the literature does not show a single uniform national trend" that is only indirectly supported. It also leans on Nairobi and IGAD-region findings to generalize about Kenya nationally, which is plausible but not fully established by the extracted content.
- **Extraction Coverage:** There are 9 sources, which is strong breadth, and several extracts are substantive rather than just metadata. That said, many are still snippet-level summaries rather than deep page extractions, so this falls short of the deepest coverage tier.

**Synthesis:**

Current status: Kenya’s rainfall remains highly variable year to year and season to season, with two main rainy seasons: the March–May long rains and the October–December short rains. Recent official climate summaries say 2024 featured severe flooding during the long rains, but the short rains were below average, worsening drought conditions in some areas. The Kenya Meteorological Department’s 2024 State of the Climate report also says some parts of the country received 111–200% of the long-term mean precipitation in 2024, underscoring strong spatial contrasts. [State of the Climate Report (2024)](https://meteo.go.ke/publications/state-of-the-climate-report-2024/) [STATE OF THE CLIMATE](https://meteo.go.ke/documents/1353/State_of_the_Climate_Kenya_2024_v1.pdf)

Long-term trend: the literature does not show a single uniform national trend in rainfall across Kenya; instead, it shows strong spatial heterogeneity and large interannual swings. A 2024 Scientific Reports study describes Kenya rainfall as varying at interannual, interseasonal, and intraseasonal time scales and explicitly analyzes wetting/drying periods over a 40-year record, while a 2018 Kenya-wide analysis found prominent spatiotemporal variability across 1979–2017. In Nairobi, one recent urban hydrology study notes that total rainfall for the short and long rains does not show a significant linear trend, even though year-to-year variability is large. [Observations of enhanced rainfall variability in Kenya, East Africa](https://www.nature.com/articles/s41598-024-63786-2) [Evaluation of spatiotemporal variability of rainfall over Kenya from 1979 to 2017 - ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1364682618306941) [Frontiers | Urbanization and hydrological dynamics: a 22-year assessment of impervious surface changes and runoff in an urban watershed](https://www.frontiersin.org/journals/water/articles/10.3389/frwa.2024.1455763/full)

Recent trend: the most clearly documented recent signal is not a simple nationwide wetting or drying trend, but more frequent and intense extremes. The 2023 State of the Climate report says 2023 had above-normal rainfall in many coastal, central highland, and Lake Victoria Basin areas, but also prolonged droughts and severe flooding linked to El Niño and a positive Indian Ocean Dipole. The 2024 report says the country’s climate risks are growing and highlights worsening impacts from extreme rainfall and below-normal rainfall in some seasons/regions. Independent reporting in 2024 also noted that some counties received nearly three times their long-term average during the long rains. [State of the Climate Kenya Report (2023)](https://meteo.go.ke/our-products/state-of-the-climate-kenya-report/state-of-the-climate-kenya-report-2023/) [Kenya State of the Climate Report highlights growing climate risks (KMD)](https://public.wmo.int/media/news-from-members/kenya-state-of-climate-report-highlights-growing-climate-risks-kmd) [What's causing the catastrophic rainfall in Kenya?](https://apnews.com/article/aea93e8ee30ca16c40451cb23c397b9e)

Bottom line: Kenya’s precipitation is best characterized by high interannual variability, strong dependence on ENSO/IOD and seasonal timing, and increasing exposure to extremes. If you want a precise answer for a specific region of Kenya—such as the coast, central highlands, ASAL northeast, or Lake Victoria basin—the trend can differ substantially by area, and the national average can hide those local patterns. [Observations of enhanced rainfall variability in Kenya, East Africa](https://www.nature.com/articles/s41598-024-63786-2) [Changes and variability in rainfall onset, cessation, and length of rainy season in the IGAD region of Eastern Africa](https://link.springer.com/article/10.1007/s00704-023-04433-0)

---

### Query 2: Global food insecurity trends 2022–2024 — FAO State of Food Security and Nutrition report key findings

#### web-scout-ai

**Scrape breakdown:** 8/9 scraped (1 failed / 0 bot-blocked / 0 http-error / 0 policy-blocked / 0 irrelevant) — avg content depth: **2,988 chars/source**

**Search queries issued:**
- global food insecurity trends 2022-2024 SOFI report statistical summary
- FAO State of Food Security and Nutrition in the World 2024 report key findings
- FAO SOFI report analysis 2022 to 2024 hunger and malnutrition drivers

**Failed URLs:**

| Category | URL | Error |
|----------|-----|-------|
| scrape_failed | https://data.unicef.org/resources/sofi-2024/ | [scrape failed: [Scrape failed: Unexpected error in _crawl_web at line 718 in _crawl_web (../../../../../../../../../.local/share/mamba/envs/gee_llm/lib/python3.12/site-packages/crawl4ai/async_crawler |

**Source content previews:**

- **[The State of Food Security and Nutrition in the World (SOFI) Report](https://www.wfp.org/publications/state-food-security-and-nutrition-world-sofi-report)**  
  The 2025 edition of the State of Food Security and Nutrition in the World (SOFI) report provides critical data on global hunger and nutrition trends between 2022 and 2024. ### Key Findings on Global Hunger (2024) * **Prevalence of Hunger:** In 2024, …
- **[The State of Food Security and Nutrition in the World (SOFI) 2024: Key Findings](https://www.fao.org/interactive/state-of-food-security-nutrition/en/)**  
  The State of Food Security and Nutrition in the World (SOFI) 2024 report, released by FAO, IFAD, UNICEF, WFP, and WHO, reveals that global food insecurity and hunger have remained stubbornly high and virtually unchanged for three consecutive years (2…
- **[The State of Food Security and Nutrition in the World 2025 (SOFI) Key Findings](https://knowledge4policy.ec.europa.eu/publication/state-food-security-nutrition-world-2025-sofi_en)**  
  Key findings regarding global food insecurity trends (2022–2024) from the State of Food Security and Nutrition (SOFI) reports and related updates: ### Hunger and Undernourishment Trends (2022–2024) * **Global Prevalence:** The percentage of the globa…
- **[The State of Food Security and Nutrition in the World 2024 - Key Findings](https://www.fao.org/publications/fao-flagship-publications/the-state-of-food-security-and-nutrition-in-the-world/en)**  
  The 2024 FAO State of Food Security and Nutrition in the World (SOFI) report, themed "Financing to end hunger, food insecurity and malnutrition in all its forms," provides the latest data on global food insecurity trends for the 2022–2024 period (pri…
- **[The State of Food Security and Nutrition in the World (SOFI) 2024 - Key Findings](https://www.who.int/publications/m/item/the-state-of-food-security-and-nutrition-in-the-world-2024)**  
  According to the 2024 SOFI report, global hunger and food insecurity levels have remained stubbornly high for three consecutive years (2021–2023), following a sharp rise during the COVID-19 pandemic. ### Global Hunger Trends (2021–2023) - **Prevalenc…
- **[The State of Food Security and Nutrition in the World 2024 (SOFI 2024)](https://data.unicef.org/wp-content/uploads/2024/07/SOFI2024_Report_EN_web.pdf)**  
  The State of Food Security and Nutrition in the World 2024 report (SOFI 2024), titled 'Financing to end hunger, food insecurity and malnutrition in all its forms,' provides the following key findings regarding global food insecurity trends from 2022 …
- **[Latin America and the Caribbean Regional Overview of Food Security and Nutrition 2024](https://fscluster.org/sites/default/files/2025-01/cd3877en.pdf)**  
  The FAO Regional Overview for Latin America and the Caribbean (LAC) 2024 provides key findings on food security and nutrition trends from 2022 to 2024, with a focus on building resilience to climate variability. ### Key Food Security Trends (2022–202…
- **[The State of Food Security and Nutrition in the World 2025: Key Findings and Trends](https://repository.gheli.harvard.edu/repository/11687/)**  
  According to the summary of the report published by the Global Health Education and Learning Incubator at Harvard University, "The State of Food Security and Nutrition in the World" (SOFI) identifies the following trends and key findings regarding gl…

**Scores:** URL Relevance 4/5 | Tailored Comprehensiveness 4/5 | Synthesis 3/5 | Extraction Coverage 4/5 | **Overall 3.8/5**

- **URL Relevance:** Most URLs are primary or near-primary SOFI sources from FAO, WFP, WHO, UNICEF, and related official/partner pages, which are directly relevant to the report’s key findings. A few sources are secondary summaries (e.g., Harvard repository, EC knowledge4policy), but they still point to the exact SOFI report content rather than tangential material.
- **Tailored Comprehensiveness:** The extracts contain many query-specific figures and trends: 2022–2024 hunger prevalence, 2023 hunger counts, food insecurity rates, healthy diet affordability, and regional patterns. However, the query asks for “key findings” on global food insecurity trends, and the extracts are uneven across years and sometimes mix 2024 estimates with 2023 report data, leaving some ambiguity and not every source adds unique specifics.
- **Synthesis Quality:** The synthesis is mostly grounded in the extracts, but it includes a notable unsupported claim that there is a “slight contradiction” between reports; that interpretive framing is not explicitly stated in the source extracts. It also blends 2024 estimates from later sources with 2023 SOFI 2024 figures in a way that is somewhat confusing, though the underlying numbers themselves are traceable.
- **Extraction Coverage:** There are 8 sources, which is strong breadth, and most extracts are fairly substantial with multiple bullet points of specific data. The depth is good overall, though not all sources are equally rich and several are overlapping summaries rather than distinct primary extracts.

**Synthesis:**

Global food insecurity and hunger trends between 2022 and 2024 are characterized by a period of stagnation at high levels followed by a marginal, gradual decline that remains insufficient to meet international targets. [The State of Food Security and Nutrition in the World (SOFI) 2025 (SOFI) Key Findings](https://knowledge4policy.ec.europa.eu/publication/state-food-security-nutrition-world-2025-sofi_en) Although the global prevalence of hunger (Prevalence of Undernourishment) was reported at 9.1% for three consecutive years (2021–2023), estimates for 2024 suggest a decrease to approximately 8.2%, representing between 638 and 720 million people. [The State of Food Security and Nutrition in the World (SOFI) Report](https://www.wfp.org/publications/state-food-security-and-nutrition-world-sofi-report) Despite this recent progress, the absolute number of hungry people in 2023 (approximately 733 million) was 152 million higher than in 2019, indicating that the world has not recovered to pre-pandemic levels. [The State of Food Security and Nutrition in the World 2024 - Key Findings](https://www.fao.org/publications/fao-flagship-publications/the-state-of-food-security-and-nutrition-in-the-world/en) There is a slight contradiction in the data: while earlier 2024 reports noted a flat stagnation of 9.1% since 2021, later updates indicate a gradual downward trend from 8.7% in 2022 to 8.5% in 2023. [The State of Food Security and Nutrition in the World 2025 (SOFI) Key Findings](https://knowledge4policy.ec.europa.eu/publication/state-food-security-nutrition-world-2025-sofi_en) Moderate or severe food insecurity (FIES) affected 2.33 billion people (28.9%) in 2023, though this is estimated to have slightly improved to 28.0% by 2024. [The State of Food Security and Nutrition in the World 2025: Key Findings and Trends](https://repository.gheli.harvard.edu/repository/11687/) Regional trends show significant divergence: Africa remains the most affected region, with one in five people (20.4%) facing hunger, and it is the only region where hunger levels are steadily rising. [The State of Food Security and Nutrition in the World (SOFI) 2024: Key Findings](https://www.fao.org/interactive/state-of-food-security-nutrition/en/) Conversely, Latin America and the Caribbean showed notable progress, with hunger prevalence falling from 6.9% in 2021 to 6.2% in 2023, largely driven by improvements in South America. [The State of Food Security and Nutrition in the World (SOFI) 2024 - Key Findings](https://www.who.int/publications/m/item/the-state-of-food-security-and-nutrition-in-the-world-2024) Economic access to healthy diets remains a critical barrier; while the number of people unable to afford a healthy diet improved globally to 2.8 billion in 2022 (compared to 2021), the average cost rose by 11% to 3.96 PPP dollars per day. [The State of Food Security and Nutrition in the World 2024 (SOFI 2024)](https://data.unicef.org/wp-content/uploads/2024/07/SOFI2024_Report_EN_web.pdf) The primary drivers of these trends are conflict, climate variability and extremes, and economic slowdowns, compounded by persistent food price inflation that has eroded purchasing power. [The State of Food Security and Nutrition in the World 2024 - Key Findings](https://www.fao.org/publications/fao-flagship-publications/the-state-of-food-security-and-nutrition-in-the-world/en) Projections indicate that the world is far off track for Sustainable Development Goal Target 2.1, with 582 million people expected to be chronically undernourished by 2030. [The State of Food Security and Nutrition in the World (SOFI) 2024: Key Findings](https://www.fao.org/interactive/state-of-food-security-nutrition/en/)

#### openai-websearch (gpt-5.4-mini)

**Scores:** URL Relevance 4/5 | Tailored Comprehensiveness 4/5 | Synthesis 4/5 | Extraction Coverage 3/5 | **Overall 3.8/5**

- **URL Relevance:** Most URLs are primary or near-primary sources for SOFI key findings: two WHO-hosted UN releases and two official publication pages tied to the FAO/partner SOFI reports. They are directly relevant to global food insecurity trends for 2022–2024, though one source is a policy portal page rather than the report itself.
- **Tailored Comprehensiveness:** The extracts contain many of the exact figures the query would need: 2022 and 2023 hunger ranges, food insecurity percentages, affordability of healthy diets, and regional trends. However, they do not provide the full report text or a complete set of all key findings, so coverage is strong but not exhaustive.
- **Synthesis Quality:** The synthesis is well grounded in the extracted content and most specific claims are traceable, including the 2022 and 2023 hunger/food insecurity numbers, regional patterns, and healthy-diet affordability. A minor issue is some interpretive phrasing like "limited progress" and "continued deterioration or stagnation" that is broader than the extracts, but there are no major unsupported factual claims.
- **Extraction Coverage:** There are 4 sources, which is a decent but not large set. The extracts are moderately detailed and include multiple specific statistics, but they are still summary-level publication pages and news releases rather than deep report excerpts, so this is solid but not high-depth coverage.

**Synthesis:**

Here are the main **global food insecurity trends from the FAO/partner SOFI reports for 2022–2024**:

- **Hunger remained extremely high and did not show meaningful improvement.** In 2022, global hunger affected **between 691 million and 783 million people**, which was **122 million more than in 2019**. In 2023, around **733 million people** faced hunger, and the UN agencies said global hunger had **plateaued for three consecutive years**. The 2024 SOFI highlights say the world is still **far off track** to meet SDG 2, Zero Hunger. [The State of Food Security and Nutrition in the World 2023 (SOFI)](https://knowledge4policy.ec.europa.eu/publication/state-food-security-nutrition-world-2023-sofi_en); [Hunger numbers stubbornly high for three consecutive years as global crises deepen: UN report](https://www.who.int/news/item/24-07-2024-hunger-numbers-stubbornly-high-for-three-consecutive-years-as-global-crises-deepen--un-report); [The State of Food Security and Nutrition in the World 2024 (SOFI)](https://knowledge4policy.ec.europa.eu/publication/state-food-security-nutrition-world-2024-sofi_en)

- **Moderate or severe food insecurity stayed stubbornly high.** In 2022, about **29.6% of the global population** — **2.4 billion people** — were moderately or severely food insecure, including about **900 million** who were severely food insecure. In 2023, the figure was **2.33 billion people** (about **28.9%** of the global population), and the report says this had **not changed significantly since 2020**. [The State of Food Security and Nutrition in the World 2023 (SOFI)](https://knowledge4policy.ec.europa.eu/publication/state-food-security-nutrition-world-2023-sofi_en); [Hunger numbers stubbornly high for three consecutive years as global crises deepen: UN report](https://www.who.int/news/item/24-07-2024-hunger-numbers-stubbornly-high-for-three-consecutive-years-as-global-crises-deepen--un-report); [The State of Food Security and Nutrition in the World 2024 (SOFI)](https://knowledge4policy.ec.europa.eu/publication/state-food-security-nutrition-world-2024-sofi_en)

- **Severe food insecurity remained massive.** The 2024 highlights say **10.7%** of the global population was severely food insecure in 2023, and the WHO/UN release says that was **over 864 million people** experiencing severe food insecurity. [The State of Food Security and Nutrition in the World 2024 (SOFI)](https://knowledge4policy.ec.europa.eu/publication/state-food-security-nutrition-world-2024-sofi_en); [Hunger numbers stubbornly high for three consecutive years as global crises deepen: UN report](https://www.who.int/news/item/24-07-2024-hunger-numbers-stubbornly-high-for-three-consecutive-years-as-global-crises-deepen--un-report)

- **Regional trends diverged.** Hunger continued to rise in **Africa**, where about **1 in 5 people** faced hunger and the share was reported at **20.4%** in 2023. Hunger was relatively stable in **Asia** at **8.1%**, while **Latin America and the Caribbean** showed progress, with the WHO release citing **6.2%** for Latin America. The 2023 SOFI also said hunger was still rising in **Western Asia, the Caribbean, and all subregions of Africa**. [Hunger numbers stubbornly high for three consecutive years as global crises deepen: UN report](https://www.who.int/news/item/24-07-2024-hunger-numbers-stubbornly-high-for-three-consecutive-years-as-global-crises-deepen--un-report); [The State of Food Security and Nutrition in the World 2023 (SOFI)](https://knowledge4policy.ec.europa.eu/publication/state-food-security-nutrition-world-2023-sofi_en)

- **Access to healthy diets remained a major gap.** The 2023 and 2024 materials say more than **2.8 billion people** were unable to afford a healthy diet in **2022**, and the 2024 summary says the cost of a healthy diet rose to **$3.96 per person per day**. The 2023 report had said more than **3.1 billion people** could not afford a healthy diet in **2021**, showing persistent affordability problems. [The State of Food Security and Nutrition in the World 2023 (SOFI)](https://knowledge4policy.ec.europa.eu/publication/state-food-security-nutrition-world-2023-sofi_en); [Hunger numbers stubbornly high for three consecutive years as global crises deepen: UN report](https://www.who.int/news/item/24-07-2024-hunger-numbers-stubbornly-high-for-three-consecutive-years-as-global-crises-deepen--un-report); [The State of Food Security and Nutrition in the World 2024 (SOFI)](https://knowledge4policy.ec.europa.eu/publication/state-food-security-nutrition-world-2024-sofi_en)

- **Nutrition outcomes improved only unevenly.** The 2024 report says the world is **not on track to meet any of the seven global nutrition targets by 2030**, even though child stunting and wasting decreased. The 2023 report gives the child nutrition figures for 2022: **148.1 million** stunted children under five, **45 million** wasted, and **37 million** overweight. [The State of Food Security and Nutrition in the World 2024 (SOFI)](https://knowledge4policy.ec.europa.eu/publication/state-food-security-nutrition-world-2024-sofi_en); [The State of Food Security and Nutrition in the World 2023 (SOFI)](https://knowledge4policy.ec.europa.eu/publication/state-food-security-nutrition-world-2023-sofi_en)

- **Bottom line:** across 2022–2024, the SOFI series shows a world where hunger and food insecurity stayed **high, persistent, and regionally unequal**, with only limited progress in some places and continued deterioration or stagnation in others. [Hunger numbers stubbornly high for three consecutive years as global crises deepen: UN report](https://www.who.int/news/item/24-07-2024-hunger-numbers-stubbornly-high-for-three-consecutive-years-as-global-crises-deepen--un-report); [The State of Food Security and Nutrition in the World 2024 (SOFI)](https://knowledge4policy.ec.europa.eu/publication/state-food-security-nutrition-world-2024-sofi_en)

If you want, I can also turn this into a **1-paragraph executive summary**, a **slide-ready bullet list**, or a **2022 vs 2023 vs 2024 comparison table**.

---

### Query 3: Ethiopia crop production statistics 2023 — cereals area harvested and yield data

#### web-scout-ai

**Scrape breakdown:** 3/10 scraped (2 failed / 0 bot-blocked / 2 http-error / 0 policy-blocked / 3 irrelevant) — avg content depth: **1,055 chars/source**

**Search queries issued:**
- Ethiopia Central Statistical Service 2023 agricultural sample survey cereal area harvested yield
- USDA FAS Ethiopia Grain and Feed Annual 2023 report cereal production data
- FAOSTAT Ethiopia 2023 cereal production statistics area harvested yield per hectare

**Failed URLs:**

| Category | URL | Error |
|----------|-----|-------|
| scrape_failed | https://statbase.org/data/eth-cereal-yield/ | [scrape failed: [Scrape failed: Unexpected error in _crawl_web at line 718 in _crawl_web (../../../../../../../../../.local/share/mamba/envs/gee_llm/lib/python3.12/site-packages/crawl4ai/async_crawler |
| scrape_failed | https://statbase.org/data/eth-land-under-cereal-production/ | [scrape failed: [Scrape failed: Unexpected error in _crawl_web at line 718 in _crawl_web (../../../../../../../../../.local/share/mamba/envs/gee_llm/lib/python3.12/site-packages/crawl4ai/async_crawler |
| source_http_error | https://www.fas.usda.gov/regions/ethiopia | [source http error: [Scrape failed: Skipped: GET failed: ReadError]] |
| source_http_error | https://www.fas.usda.gov/data/ethiopia-grain-and-feed-annual-7 | [source http error: [Scrape failed: Skipped: GET failed: ReadError]] |
| scraped_irrelevant | https://ess.gov.et/agriculture/ | [scraped but irrelevant: [No relevant content found for the specific 2023 cereal production data on this landing page. The page primarily serves as a directory for agricultural surveys and socioeconom |
| scraped_irrelevant | https://catalog.ihsn.org/catalog/12861/pdf-documentation | [scraped but irrelevant: [No relevant content found for this query]  The provided URL (IHSN Catalog ID 12861) contains metadata and documentation for the **Annual Agricultural Sample Survey (AASS) 202 |
| scraped_irrelevant | https://microdata.fao.org/index.php/catalog/2689 | [scraped but irrelevant: [No relevant content found for this query]] |

**Source content previews:**

- **[FAOSTAT - Food and Agriculture Data](https://www.fao.org/faostat/)**  
  FAOSTAT is the primary database of the Food and Agriculture Organization of the United Nations (FAO), providing free access to food and agriculture data for over 245 countries and territories from 1961 to the most recent year available. For Ethiopia'…
- **[Agricultural production statistics 2010–2023 - FAO New Data Release (2024)](https://www.fao.org/statistics/highlights-archive/highlights-detail/agricultural-production-statistics-2010-2023/en)**  
  The provided FAO highlights page summarizes the 2024 release of agricultural production statistics covering the period from 2010 to 2023. While it does not list Ethiopia-specific cereal data in the summary text, it provides the following global cerea…
- **[Ethiopia - Cereal Yield (kg Per Hectare) - 2023 Data](https://tradingeconomics.com/ethiopia/cereal-yield-kg-per-hectare-wb-data.html)**  
  In 2023, the cereal yield in Ethiopia was reported at 2,864 kg per hectare. This data is part of the World Bank's collection of development indicators. Key details regarding cereal yield in Ethiopia: - Measurement: Cereal yield is measured as kilogra…

**Scores:** URL Relevance 2/5 | Tailored Comprehensiveness 2/5 | Synthesis 1/5 | Extraction Coverage 2/5 | **Overall 1.8/5**

- **URL Relevance:** FAOSTAT is the right primary source for Ethiopia crop production statistics, but the other URLs are only partially relevant: one is a FAO highlights page that does not provide Ethiopia-specific cereal figures, and one is a TradingEconomics page rather than the underlying official dataset. The USDA FAS link mentioned in the synthesis is not even present in the provided source list, which further weakens source relevance for the exact query.
- **Tailored Comprehensiveness:** Only the TradingEconomics extract gives a concrete 2023 cereal yield value, but none of the provided extracts contain the requested Ethiopia 2023 cereals area harvested figure. The FAOSTAT extract is generic guidance about where such data can be found, not the actual statistics, so the extracts do not really satisfy the query's specific data needs.
- **Synthesis Quality:** The synthesis includes an unsourced USDA FAS claim about area harvested for corn, wheat, sorghum, and barley, but that source is not among the provided extracts. It also cites TheGlobalEconomy and USDA links that are absent from the source set, and it admits there is no aggregate area harvested total, so it does not directly answer the query from the supplied evidence.
- **Extraction Coverage:** There are only 3 sources, which is modest coverage. The extracts are mostly shallow and generic, with only one source providing a specific numeric data point; the others are explanatory summaries rather than deep, query-relevant evidence.

**Synthesis:**

In 2023, the cereal yield in Ethiopia was 2,864 kg per hectare, representing an increase from the 2,823 kg per hectare recorded in 2022 [Ethiopia - Cereal Yield (kg Per Hectare) - 2023 Data](https://tradingeconomics.com/ethiopia/cereal-yield-kg-per-hectare-wb-data.html) [Ethiopia Cereal crop yield by hectar - data, chart](https://www.theglobaleconomy.com/ethiopia/cereal_yield/). This yield data includes crops harvested for dry grain such as wheat, rice, maize, barley, oats, rye, millet, sorghum, buckwheat, and mixed grains, while excluding crops harvested for hay, silage, or grazing [Ethiopia - Cereal Yield (kg Per Hectare) - 2023 Data](https://tradingeconomics.com/ethiopia/cereal-yield-kg-per-hectare-wb-data.html).

Regarding the area harvested, the available sources do not provide a single aggregate total for all cereals in 2023. However, USDA Foreign Agricultural Service estimates for annual area harvested (spanning 2016–2025) list the following specific cereal dimensions in Ethiopia: 
- **Corn:** 2.6 million hectares
- **Wheat:** 2.2 million hectares
- **Sorghum:** 1.7 million hectares
- **Barley:** 980,000 hectares [Ethiopia | USDA Foreign Agricultural Service](https://www.fas.usda.gov/data/production/et)

Comprehensive 2023 datasets for Ethiopia—covering the total aggregate area harvested and final yield through official FAOSTAT records—are typically released in December of the following year (December 2024) [FAOSTAT - Food and Agriculture Data](https://www.fao.org/faostat/).

#### openai-websearch (gpt-5.4-mini)

**Scores:** URL Relevance 3/5 | Tailored Comprehensiveness 1/5 | Synthesis 2/5 | Extraction Coverage 2/5 | **Overall 2.0/5**

- **URL Relevance:** The World Bank cereal-yield indicator page is a plausible primary source for Ethiopia yield data, and the FAO FAOSTAT highlights page is relevant as a gateway to the underlying production tables. However, the Crop Prospects PDF is only tangentially related to the requested 2023 cereals area harvested and yield statistics, so the set is mixed rather than fully primary and targeted.
- **Tailored Comprehensiveness:** The extracts do not provide the requested Ethiopia 2023 cereals area harvested or the actual yield value. They only confirm that relevant series exist and that FAOSTAT likely contains the data, which is not enough to answer the query's specific numeric request.
- **Synthesis Quality:** The synthesis is careful not to invent the missing Ethiopia 2023 numbers, but it still overstates what was confirmed by implying the World Bank page is the most accessible current source and that FAOSTAT should contain the figures. Those are reasonable inferences, yet the answer remains incomplete because it does not provide the requested statistics.
- **Extraction Coverage:** Only three sources were gathered, and none of the extracts contain the actual Ethiopia 2023 cereals area harvested or yield figures. The content is moderately informative metadata and contextual notes, but the per-source depth is too shallow for the specific statistics requested.

**Synthesis:**

For Ethiopia’s 2023 cereal crop statistics, the most accessible current source in the search results is the World Bank’s indicator page for **cereal yield (kg per hectare)**, which is sourced from FAO/FAOSTAT and shows coverage through **2023** for Ethiopia. However, the search results I found did **not** surface a clean official table with Ethiopia’s **2023 cereals area harvested** and **2023 cereal yield** together in one place. The World Bank indicator page confirms the metric and time coverage but does not expose the Ethiopia-specific 2023 value in the snippet returned here. [World Bank: Cereal yield (kg per hectare) - Ethiopia](https://data.worldbank.org/indicator/AG.YLD.CREL.KG?locations=ET) 

FAO’s agricultural production highlights confirm that FAOSTAT’s production domain includes crop production volumes and harvested areas up to **2024**, and related derived data up to **2023**. That means FAOSTAT should contain the 2023 Ethiopia cereals area/yield figures, but the specific Ethiopia numbers were not retrievable from the search snippets I obtained. [FAO: Agricultural production statistics 2010–2024](https://www.fao.org/statistics/highlights-archive/highlights-detail/agricultural-production-statistics-2010-2024/) 

A separate FAO crop-prospects document states that official Ethiopian Statistics Service production estimates from **2020 onward do not include Tigray Region**, which is important if you are comparing 2023 national cereal figures with earlier series. [FAO: Crop Prospects No. 3, November 2023](https://openknowledge.fao.org/3/cc8566en/cc8566en.pdf) 

What I could confirm from the search results:
- Ethiopia cereal-yield series exists through **2023** in the World Bank/FAO indicator page. [World Bank: Cereal yield (kg per hectare) - Ethiopia](https://data.worldbank.org/indicator/AG.YLD.CREL.KG?locations=ET)
- FAOSTAT production data include harvested area and crop quantities up to **2024**. [FAO: Agricultural production statistics 2010–2024](https://www.fao.org/statistics/highlights-archive/highlights-detail/agricultural-production-statistics-2010-2024/)
- Ethiopian official production estimates from **2020 onward exclude Tigray**. [FAO: Crop Prospects No. 3, November 2023](https://openknowledge.fao.org/3/cc8566en/cc8566en.pdf)

If you want, I can do a second pass and try to pull the exact 2023 Ethiopia cereals **area harvested (hectares)** and **yield (kg/ha)** directly from FAOSTAT or another official database.

---
