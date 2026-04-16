# Quality Benchmark: web-scout-ai vs OpenAI web search

Date: 2026-04-16 13:54
web-scout-ai backend: serper
OpenAI model: gpt-5.4-mini
Judge model: gpt-5.4-mini

## Summary

| Query | Tool | Scraped | Failed | Bot | Avg Depth | Time (s) | URL Rel | Compreh | Synthesis | Coverage | Overall |
|-------|------|---------|--------|-----|-----------|----------|---------|---------|-----------|----------|---------|
| Kenya interannual variability and long-term trends in p… | web-scout-ai | 4/6 | 1 | 0 | 2,701 | 89.2 | 4/5 | 4/5 | 3/5 | 3/5 | 3.5/5 |
| Kenya interannual variability and long-term trends in p… | openai-websearch (gpt-5.4-mini) | 2/2 | 0 | 0 | 718 | 11.7 | 4/5 | 4/5 | 4/5 | 3/5 | 3.8/5 |
| Global food insecurity trends 2022–2024 — FAO State of … | web-scout-ai | 5/6 | 1 | 0 | 2,454 | 146.6 | 4/5 | 4/5 | 2/5 | 4/5 | 3.5/5 |
| Global food insecurity trends 2022–2024 — FAO State of … | openai-websearch (gpt-5.4-mini) | 4/4 | 0 | 0 | 693 | 13.4 | 4/5 | 3/5 | 2/5 | 3/5 | 3.0/5 |
| Ethiopia crop production statistics 2023 — cereals area… | web-scout-ai | 6/10 | 1 | 1 | 1,839 | 148.7 | 4/5 | 3/5 | 3/5 | 4/5 | 3.5/5 |
| Ethiopia crop production statistics 2023 — cereals area… | openai-websearch (gpt-5.4-mini) | 6/6 | 0 | 0 | 273 | 17.8 | 4/5 | 3/5 | 3/5 | 4/5 | 3.5/5 |

---

## Detailed Results

### Query 1: Kenya interannual variability and long-term trends in precipitation — current status and recent trend

#### web-scout-ai

**Scrape breakdown:** 4/6 scraped (0 failed / 0 bot-blocked / 1 http-error / 1 policy-blocked / 0 irrelevant) — avg content depth: **2,701 chars/source**

**Search queries issued:**
- Kenya long-term precipitation trends and interannual variability analysis 1901-2023
- drivers of interannual precipitation variability in Kenya and long-term observational trends
- recent trends in Kenya seasonal rainfall patterns and current climate status 2024

**Failed URLs:**

| Category | URL | Error |
|----------|-----|-------|
| source_http_error | https://www.researchgate.net/publication/303828025_Inter_Annual_Variability_of_Onset_and_Cessation_of_the_Long_Rains_in_Kenya | [source http error: [Scrape failed: Skipped: HTTP 403 on GET]] |
| blocked_by_policy | https://www.sciencedirect.com/science/article/pii/S2214581825005567 | [blocked by policy: [Scrape failed: Skipped: blocked domain]] |

**Source content previews:**

- **[Observations of enhanced rainfall variability in Kenya, East Africa (1981–2021)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11153539/)**  
  This study analyzes rainfall variability and trends in Kenya from 1981 to 2021 using the CHIRPS v2.0 dataset. ### Key Findings on Precipitation Trends and Variability: - **Increased Variability:** Evidence shows a substantial increase in the frequenc…
- **[Drivers and impacts of Eastern African rainfall variability](https://www.icpac.net/documents/829/s43017-023-00397-x_1.pdf)**  
  ### Precipitation Variability and Trends in Kenya **Interannual Variability and Drivers:** * **Bimodal Rainfall Pattern:** Kenya experiences two primary wet seasons: the 'long rains' (March–May, MAM), which are typically more intense, and the 'short …
- **[State of the Climate Kenya 2024](https://meteo.go.ke/documents/1353/State_of_the_Climate_Kenya_2024_v1.pdf)**  
  ### Current Status of Precipitation in Kenya (2024) In 2024, Kenya's rainfall exhibited significant spatial and temporal variability. While the western and central highlands experienced above-normal rainfall, most other regions saw drier-than-average…
- **[State of the Climate Report Kenya 2024 | SEI](https://www.sei.org/publications/state-climate-report-kenya-2024/)**  
  The "State of the Climate Kenya 2024" report is a collaborative publication by researchers from the Stockholm Environment Institute (SEI) and the Kenya Meteorological Department (KMD). It provides a comprehensive analysis of Kenya's climate status fo…

**Scores:** URL Relevance 4/5 | Tailored Comprehensiveness 4/5 | Synthesis 3/5 | Extraction Coverage 3/5 | **Overall 3.5/5**

- **URL Relevance:** Three of the four URLs are strong primary sources for Kenya precipitation trends: a peer-reviewed Kenya rainfall study, an ICPAC review on Eastern African rainfall variability, and the Kenya Meteorological Department's 2024 climate report. The SEI page is more of a secondary landing page than a primary data source, but it still points to the same official report, so the set is mostly authoritative and directly relevant.
- **Tailored Comprehensiveness:** The extracts contain concrete, query-specific information on interannual variability, long-term trends, seasonal behavior, and current status in 2024, including trend directions and several numeric values. However, not every source adds unique depth, and Source 4 is mostly metadata rather than substantive precipitation evidence, so the coverage is strong but not complete enough for a 5.
- **Synthesis Quality:** Most of the synthesis is supported by the extracts, but it includes some borderline or unsupported specificity, such as naming 2024 alongside 2018 and 2020 as part of the MAM recovery/reversal trend without the extracts explicitly establishing a trend calculation through 2024. The statement that the short rains may deliver more total rainfall than the long rains by 2030–2040 is supported, but the synthesis also adds interpretive framing that goes beyond the extracted evidence in places.
- **Extraction Coverage:** There are four sources, which is a decent breadth, but only three contain substantial query-relevant content and one is mostly bibliographic/summary metadata. The extracts are moderately deep and specific, but they are not extensive enough across enough sources to qualify for the higher coverage tiers.

**Synthesis:**

Precipitation in Kenya is characterized by extreme interannual variability and distinct seasonal trends that have shifted significantly in recent decades. The country's climate is dominated by a bimodal rainfall pattern consisting of the 'long rains' (March–May, MAM) and 'short rains' (October–December, OND) [Drivers and impacts of Eastern African rainfall variability](https://www.icpac.net/documents/829/s43017-023-00397-x_1.pdf).

### Long-Term Precipitation Trends
Historically, the two main rainy seasons have exhibited opposing trends:
- **Long Rains (MAM):** Between 1985 and 2010, a consistent drying trend was observed, attributed to a shortening of the season through delayed onset and earlier cessation [Drivers and impacts of Eastern African rainfall variability](https://www.icpac.net/documents/829/s43017-023-00397-x_1.pdf). However, this trend has shown signs of recovery or reversal since 2018 due to extremely wet seasons in 2018, 2020, and 2024 [State of the Climate Kenya 2024](https://meteo.go.ke/documents/1353/State_of_the_Climate_Kenya_2024_v1.pdf).
- **Short Rains (OND):** A consistent wetting trend has been recorded from 1983 to 2021, with seasonal rainfall increasing by approximately 1.44 to 2.36 mm per year [Drivers and impacts of Eastern African rainfall variability](https://www.icpac.net/documents/829/s43017-023-00397-x_1.pdf). Projections suggest the short rains may deliver more total rainfall than the long rains by 2030–2040.

### Interannual Variability and Drivers
Rainfall variability has increased substantially since 2013, marked by more frequent and intense extremes [Observations of enhanced rainfall variability in Kenya, East Africa (1981–2021)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11153539/). Primary drivers include:
- **IOD and ENSO:** The Indian Ocean Dipole (IOD) is the dominant driver for the short rains; positive IOD phases can lead to rainfall totals 2–3 times the long-term mean [Drivers and impacts of Eastern African rainfall variability](https://www.icpac.net/documents/829/s43017-023-00397-x_1.pdf). While El Niño traditionally correlates with wetter conditions, recent observations indicate that the coherence between ENSO and Kenyan rainfall has diminished since 2013, suggesting other regional factors are becoming more influential [Observations of enhanced rainfall variability in Kenya, East Africa (1981–2021)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11153539/).
- **Intraseasonal Factors:** The Madden–Julian Oscillation (MJO) explains approximately 20% of interannual variation and is a primary driver of intraseasonal variability [Drivers and impacts of Eastern African rainfall variability](https://www.icpac.net/documents/829/s43017-023-00397-x_1.pdf).

### Current Status (2024)
The year 2024 exemplified the current state of extreme variability:
- **MAM 2024:** Recorded as one of the wettest seasons on record for several stations, including Nairobi and Central Kenya. Ndakaini station recorded a seasonal high of 1,355.5 mm. Many areas received 111% to over 200% of their long-term mean, resulting in widespread flooding and crop destruction [State of the Climate Kenya 2024](https://meteo.go.ke/documents/1353/State_of_the_Climate_Kenya_2024_v1.pdf).
- **OND 2024:** In sharp contrast, the short rains were generally below average, receiving only 26–75% of normal rainfall in the Northeast and Turkana regions. This poor performance led to a deterioration in food security, with 2.15 million people facing food insecurity by early 2025 [State of the Climate Kenya 2024](https://meteo.go.ke/documents/1353/State_of_the_Climate_Kenya_2024_v1.pdf).

### Data Gaps and Contradictions
While there is a consensus on increasing variability, different datasets (such as CHIRPS vs. TAMSAT) show slight variations in the magnitude of recent wetting trends in the MAM season [Drivers and impacts of Eastern African rainfall variability](https://www.icpac.net/documents/829/s43017-023-00397-x_1.pdf). The available sources did not contain specific long-term projections for rainfall variability shifts beyond the 2040 timeframe.

#### openai-websearch (gpt-5.4-mini)

**Scores:** URL Relevance 4/5 | Tailored Comprehensiveness 4/5 | Synthesis 4/5 | Extraction Coverage 3/5 | **Overall 3.8/5**

- **URL Relevance:** Both URLs are highly relevant primary scientific sources for Kenya precipitation variability and trends. They are not Kenya-only datasets or official climate reports, but they are authoritative review and observational papers that plausibly contain exactly the requested information.
- **Tailored Comprehensiveness:** The extracts contain concrete, query-specific trend information: bimodal seasonality, ENSO/IOD controls, long-rains drying, short-rains wetting, and Kenya-specific anomaly changes from 1981–2021. The main gap is that the query asks for Kenya's current status and recent trend, and the extracts provide strong evidence but not a single unified national metric or a fully explicit 'current status' summary.
- **Synthesis Quality:** The synthesis is well grounded in the extracts and accurately summarizes the main findings without obvious fabrication. The only issue is minor formatting corruption in the trend units (season/year), but the underlying numeric ranges and claims are traceable to Source 1, and the Kenya-specific variability statements are traceable to Source 2.
- **Extraction Coverage:** There are only 2 sources, which limits breadth, but both extracts are moderately detailed and highly relevant. The coverage is solid for a focused answer, though it falls short of the deeper multi-source evidence base needed for a higher score.

**Synthesis:**

Kenya’s precipitation shows a strongly bimodal regime, with the long rains in March–May and the short rains in October–December. A recent Nature Reviews Earth & Environment review says interannual variability is dominated by remote teleconnections, especially ENSO and the Indian Ocean Dipole, and that the long rains generally trended drier from the mid-1980s to about 2010, while the short rains became wetter from the mid-1980s onward. The review gives indicative trend ranges of about −0.65 to −2.95 mm season year for long rains and +1.44 to +2.36 mm season year for short rains, with some recovery in long rains after 2010. [Drivers and impacts of Eastern African rainfall variability](https://www.nature.com/articles/s43017-023-00397-x)

A 2024 Scientific Reports study focused on Kenya found strong spatial heterogeneity and increasing rainfall variability. It reported that, between 1981 and 2021, both dry and wet anomalies during the MAM season became more frequent, while in OND the dry pattern decreased and the wet pattern increased. The paper also noted that variability became more pronounced after 2013 and linked that shift to changing teleconnections and climate-change-related warming patterns. [Observations of enhanced rainfall variability in Kenya, East Africa](https://www.nature.com/articles/s41598-024-63786-2)

So, the current status is not a simple nationwide monotonic trend. The best recent synthesis is: Kenya has substantial year-to-year rainfall swings; the long rains have generally weakened over the long term but may have partially recovered after 2010; and the short rains have generally strengthened. At the same time, rainfall change is highly uneven across space and season, which means local trends can differ from the national average. [Drivers and impacts of Eastern African rainfall variability](https://www.nature.com/articles/s43017-023-00397-x) [Observations of enhanced rainfall variability in Kenya, East Africa](https://www.nature.com/articles/s41598-024-63786-2)

Sources:
- URL: https://www.nature.com/articles/s43017-023-00397-x
  Title: Drivers and impacts of Eastern African rainfall variability | Nature Reviews Earth & Environment
  Relevant content: Eastern Africa has bimodal rainfall with long rains (March–May) and short rains (October–December). Remote teleconnections, especially ENSO and the Indian Ocean Dipole, dominate interannual variability. From the mid-1980s to 2010, long rains tended toward a drier state (−0.65 to −2.95 mm season year), with some recovery thereafter. Short rains have become wetter since the mid-1980s (1.44 to 2.36 mm season year). These trends overlay large year-to-year variation and affect flooding, droughts, food and energy systems, disease risk, and ecosystem stability. Projections suggest short rains may exceed long rains by 2030–2040.

- URL: https://www.nature.com/articles/s41598-024-63786-2
  Title: Observations of enhanced rainfall variability in Kenya, East Africa | Scientific Reports
  Relevant content: Kenya rainfall anomalies are spatially heterogeneous. Between 1981 and 2021, MAM dry and wet anomalies both increased in frequency, implying more months with rainfall above or below average and fewer average months. In OND, the dry pattern decreased and the wet pattern increased, indicating increasingly wetter short rains. More extreme MAM months imply poorer rainfall distribution and challenges for rainfed agriculture and pastoral systems. Interseasonal variability became more prominent after 1995 and especially after 2013. The authors relate the increased variability after 2013 to an abrupt climate shift linked to warming of the western Pacific SST and changing ENSO/IOD relationships, including declining MAM rainfall and stronger links between OND La Niña and subsequent dry MAM seasons.

---

### Query 2: Global food insecurity trends 2022–2024 — FAO State of Food Security and Nutrition report key findings

#### web-scout-ai

**Scrape breakdown:** 5/6 scraped (1 failed / 0 bot-blocked / 0 http-error / 0 policy-blocked / 0 irrelevant) — avg content depth: **2,454 chars/source**

**Search queries issued:**
- global food insecurity trends 2022-2024 FAO report data
- FAO SOFI 2022 2023 2024 summary of prevalence of undernourishment
- FAO State of Food Security and Nutrition in the World SOFI 2024 key findings

**Failed URLs:**

| Category | URL | Error |
|----------|-----|-------|
| scrape_failed | https://wfpusa.org/news/global-hunger-declines-but-rises-africa-western-asia/ | [scrape failed: [Scrape failed: Unexpected error in _crawl_web at line 718 in _crawl_web (../../../../../../../../../.local/share/mamba/envs/gee_llm/lib/python3.12/site-packages/crawl4ai/async_crawler |

**Source content previews:**

- **[The State of Food Security and Nutrition in the World (SOFI) Report Findings](https://www.fao.org/publications/fao-flagship-publications/the-state-of-food-security-and-nutrition-in-the-world/en)**  
  The FAO State of Food Security and Nutrition in the World (SOFI) reports from 2022 to 2024 provide critical data on global food insecurity trends. The 2024 report, titled 'Financing to end hunger, food insecurity and malnutrition in all its forms,' r…
- **[The State of Food Security and Nutrition in the World 2025: Key Findings on Global Food Insecurity Trends (2022–2024)](https://www.fao.org/3/cd6008en/online/state-food-security-and-nutrition-2025/ending-hunger-food-security.html)**  
  According to the FAO State of Food Security and Nutrition in the World 2025 report, global hunger and food insecurity showed signs of a gradual decrease between 2022 and 2024, though levels remain significantly higher than pre-pandemic and 2015 bench…
- **[The State of Food Security and Nutrition in the World 2024 (SOFI 2024)](https://data.unicef.org/wp-content/uploads/2024/07/SOFI2024_Report_EN_web.pdf)**  
  The 2024 State of Food Security and Nutrition in the World (SOFI) report, published by FAO, IFAD, UNICEF, WFP, and WHO, provides the following key findings on global food insecurity trends from 2022 to 2024: ### 1. Global Hunger and Undernourishment …
- **[SDG 2.1: Prevalence of Undernourishment in Latin America and the Caribbean (2024 Statistics)](https://openknowledge.fao.org/server/api/core/bitstreams/0556ea9c-65bb-46e9-aa6b-39fdeb8afbe7/content/sofi-statistics-rlc-2024/sdg-2-prevalence-undernourishment.html)**  
  Key findings from the 2024 FAO Regional Overview of Food Security and Nutrition for Latin America and the Caribbean (part of the SOFI series) regarding food insecurity and undernourishment trends from 2022–2024: ### Regional Undernourishment Trends (…
- **[The State of Food Security and Nutrition in the World 2024](https://www.who.int/publications/m/item/the-state-of-food-security-and-nutrition-in-the-world-2024)**  
  The 2024 edition of 'The State of Food Security and Nutrition in the World' (SOFI) report provides the latest global monitoring data for Sustainable Development Goal (SDG) 2 targets 2.1 (ending hunger) and 2.2 (ending all forms of malnutrition). Key …

**Scores:** URL Relevance 4/5 | Tailored Comprehensiveness 4/5 | Synthesis 2/5 | Extraction Coverage 4/5 | **Overall 3.5/5**

- **URL Relevance:** Most URLs are authoritative FAO or closely related UN/WHO-hosted SOFI pages, and they are directly about the exact report series the query asks for. The only slight issue is that one source is a 2025 report page used to answer a 2022–2024 trends query, but it is still a primary FAO source for the requested trend data.
- **Tailored Comprehensiveness:** The extracts contain many query-specific figures and findings: 2023 hunger and food insecurity levels, 2022 healthy-diet affordability, regional breakdowns, and the 2024/2025 trend updates. However, the query asks for 2022–2024 key findings, and the extracts are uneven on 2022 specifics and rely heavily on 2023 plus a 2025 update rather than fully covering each year.
- **Synthesis Quality:** The synthesis includes several supported claims, but it also introduces unsupported or conflicting framing, especially the statement that the landscape 'transitioned from post-pandemic stagnation to a marginal improvement in 2024' and the claim that this was 'primarily due to updated data from key regions like Southern Asia,' which is only partially supported and not a key finding of the 2022–2024 SOFI reports themselves. It also mixes 2024 report findings with 2025 preliminary data in a way that goes beyond the extracted content for the original query.
- **Extraction Coverage:** There are 5 sources, which is solid breadth, and several extracts are fairly substantial with multiple bullet points and concrete statistics. That said, the depth is uneven and not all sources provide equally rich, query-targeted material, so this falls short of the strongest coverage tier.

**Synthesis:**

The FAO State of Food Security and Nutrition in the World (SOFI) reports for 2022–2024 (and preliminary data for 2025) reveal a global food security landscape that transitioned from post-pandemic stagnation to a marginal improvement in 2024, primarily due to updated data from key regions like Southern Asia. 

### Global Hunger Trends (Undernourishment)
- **Plateau and Decline:** Between 2021 and 2023, global hunger was described as "stubbornly high" and stagnated at approximately 9.1% of the population, affecting roughly 733 million people in 2023 [The State of Food Security and Nutrition in the World (SOFI) Report Findings](https://www.fao.org/publications/fao-flagship-publications/the-state-of-food-security-and-nutrition-in-the-world/en) [The State of Food Security and Nutrition in the World 2024 (SOFI 2024)](https://data.unicef.org/wp-content/uploads/2024/07/SOFI2024_Report_EN_web.pdf). However, updated projections for 2024 show a gradual decrease to 8.2%, with approximately 673 million people facing hunger—a reduction of 22 million from 2022 [The State of Food Security and Nutrition in the World 2025: Key Findings on Global Food Insecurity Trends (2022–2024)](https://www.fao.org/3/cd6008en/online/state-food-security-and-nutrition-2025/ending-hunger-food-security.html).
- **Historical Perspective:** Despite recent slight improvements, the 2024 hunger level remains significantly higher than pre-pandemic figures; 96 million more people were chronically hungry in 2024 compared to 2015 [The State of Food Security and Nutrition in the World 2025: Key Findings on Global Food Insecurity Trends (2022–2024)](https://www.fao.org/3/cd6008en/online/state-food-security-and-nutrition-2025/ending-hunger-food-security.html).

### Food Insecurity (Moderate and Severe)
- **Prevalence:** In 2023, 28.9% of the world (2.33 billion people) experienced moderate or severe food insecurity [The State of Food Security and Nutrition in the World 2024 (SOFI 2024)](https://data.unicef.org/wp-content/uploads/2024/07/SOFI2024_Report_EN_web.pdf). By 2024, this figure declined marginally to 28.0% (2.3 billion people) [The State of Food Security and Nutrition in the World 2025: Key Findings on Global Food Insecurity Trends (2022–2024)](https://www.fao.org/3/cd6008en/online/state-food-security-and-nutrition-2025/ending-hunger-food-security.html).
- **Severity:** In 2023, approximately 864 million people suffered from severe food insecurity, meaning they had gone a day or more without eating [The State of Food Security and Nutrition in the World (SOFI) Report Findings](https://www.fao.org/publications/fao-flagship-publications/the-state-of-food-security-and-nutrition-in-the-world/en). By 2024, acute food insecurity reached critical levels in specific crises, with 100% of the population in the Gaza Strip facing high levels of acute food insecurity [The State of Food Security and Nutrition in the World 2025: Key Findings on Global Food Insecurity Trends (2022–2024)](https://www.fao.org/3/cd6008en/online/state-food-security-and-nutrition-2025/ending-hunger-food-security.html).

### Regional Disparities
- **Africa:** Hunger continues to rise, affecting 20.4% of the population in 2023 (roughly 1 in 5 people) [The State of Food Security and Nutrition in the World 2024 (SOFI 2024)](https://data.unicef.org/wp-content/uploads/2024/07/SOFI2024_Report_EN_web.pdf). It is the only region where food insecurity worsened in both rural and urban areas from 2022 to 2024 [The State of Food Security and Nutrition in the World 2025: Key Findings on Global Food Insecurity Trends (2022–2024)](https://www.fao.org/3/cd6008en/online/state-food-security-and-nutrition-2025/ending-hunger-food-security.html).
- **Latin America and the Caribbean:** This region has shown steady progress, with hunger prevalence dropping from 6.9% in 2021 to 6.2% in 2023 [SDG 2.1: Prevalence of Undernourishment in Latin America and the Caribbean (2024 Statistics)](https://openknowledge.fao.org/server/api/core/bitstreams/0556ea9c-65bb-46e9-aa6b-39fdeb8afbe7/content/sofi-statistics-rlc-2024/sdg-2-prevalence-undernourishment.html).
- **Asia:** Hunger levels remained relatively stable at 8.1% in 2023, though the region still accounts for over half of the world's undernourished population [The State of Food Security and Nutrition in the World 2024 (SOFI 2024)](https://data.unicef.org/wp-content/uploads/2024/07/SOFI2024_Report_EN_web.pdf).

### Critical Drivers and Financing
- **Primary Drivers:** Conflict, climate extremes, and economic downturns—often occurring simultaneously—remain the core causes of food insecurity [The State of Food Security and Nutrition in the World 2024](https://www.who.int/publications/m/item/the-state-of-food-security-and-nutrition-in-the-world-2024). 
- **Financing Gap:** The 2024 report emphasizes a massive financing shortfall for low- and middle-income countries attempting to reach SDG 2 (Zero Hunger) targets by 2030 [The State of Food Security and Nutrition in the World (SOFI) Report Findings](https://www.fao.org/publications/fao-flagship-publications/the-state-of-food-security-and-nutrition-in-the-world/en).
- **Dietary Affordability:** In 2022, over 2.8 billion people could not afford a healthy diet, a challenge most severe in low-income countries where 71.5% of the population lacked access [The State of Food Security and Nutrition in the World (SOFI) Report Findings](https://www.fao.org/publications/fao-flagship-publications/the-state-of-food-security-and-nutrition-in-the-world/en).

### Data Conflicts
A notable revision in data for India (household consumption surveys for 2022–2024) significantly altered global estimates. While the 2024 SOFI report (using 2023 data) cited a 9.1% global undernourishment rate, the 2025 SOFI report (covering 2024 data) recalculated 2023 levels to 8.5% based on these new insights from Southern Asia [The State of Food Security and Nutrition in the World 2025: Key Findings on Global Food Insecurity Trends (2022–2024)](https://www.fao.org/3/cd6008en/online/state-food-security-and-nutrition-2025/ending-hunger-food-security.html).

#### openai-websearch (gpt-5.4-mini)

**Scores:** URL Relevance 4/5 | Tailored Comprehensiveness 3/5 | Synthesis 2/5 | Extraction Coverage 3/5 | **Overall 3.0/5**

- **URL Relevance:** Most URLs are primary or near-primary sources for SOFI findings: two UNICEF Data pages summarizing the 2022 and 2024 reports, plus an official FAO news page on SOFI 2024. The 2023 FAO publication page is authoritative, though the extracted content is thin and not the actual findings page, so this is not a perfect set of primary sources for every year.
- **Tailored Comprehensiveness:** The extracts contain several query-relevant figures and trends for 2022 and 2024, including hunger counts, food insecurity prevalence, and healthy-diet affordability. However, the 2023 source lacks detailed findings in the extract, and the query asks for 2022–2024 trends, so the evidence is incomplete for the middle year.
- **Synthesis Quality:** The synthesis includes many claims that are supported by the extracts, but it also introduces a likely unsupported year framing by calling the 2024 SOFI report the '2023 data' report in a way that blurs the source-year distinction. More importantly, it presents a coherent 2022–2024 trend narrative that is only partially grounded because the provided extracts do not actually supply detailed 2023 report findings beyond the 2024 summary.
- **Extraction Coverage:** There are 4 sources, which is a moderate breadth. Two extracts are fairly substantive, one is a short news summary, and one is only a publication-page snippet, so the overall depth is moderate rather than deep.

**Synthesis:**

Here are the key FAO SOFI findings for **global food insecurity trends from 2022–2024**. The 2022 report said the world was still feeling the combined effects of conflict, climate extremes, economic shocks, and rising inequality; it estimated that **828 million people** were hungry in **2021**, **11.7%** of the global population faced severe food insecurity, and the number unable to afford a healthy diet rose to **almost 3.1 billion** people. [The State of Food Security and Nutrition in the World 2022](https://data.unicef.org/resources/sofi-2022/)

The 2023 SOFI report showed that the situation had not improved: hunger remained stubbornly high, with **about 733 million people** facing hunger in **2023**, and **2.33 billion** people experiencing moderate or severe food insecurity. It also found that **over 864 million** people experienced severe food insecurity, and **more than 2.8 billion** people could not afford a healthy diet in **2022**. [The State of Food Security and Nutrition in the World 2024](https://data.unicef.org/resources/sofi-2024/)

The 2024 SOFI report emphasized stagnation and backsliding in some measures: the world was described as having been set back **15 years**, with undernourishment levels comparable to **2008–2009**, and global hunger had **plateaued for three consecutive years**. It also said the report’s 2023 data showed **2.33 billion** people in moderate or severe food insecurity and **733 million** people hungry, while **71.5%** of people in low-income countries could not afford a healthy diet compared with **6.3%** in high-income countries. [The State of Food Security and Nutrition in the World 2024](https://data.unicef.org/resources/sofi-2024/)

Overall, the trend across 2022–2024 was **not a sustained recovery**: food insecurity remained extremely high after the pandemic-era surge, hunger stayed near historic highs, and affordability of healthy diets remained a major structural barrier. The reports did note some progress in nutrition-related indicators such as **exclusive breastfeeding** and a decline in **stunting** to **22.3%** in 2022, but they stressed that these gains were not enough to put the world on track for **SDG 2 (Zero Hunger)** by 2030. [The State of Food Security and Nutrition in the World 2022](https://data.unicef.org/resources/sofi-2022/) [The State of Food Security and Nutrition in the World 2024](https://data.unicef.org/resources/sofi-2024/)

If you want, I can turn this into a **compact table for 2022, 2023, and 2024** or a **2–3 sentence executive summary**.

---

### Query 3: Ethiopia crop production statistics 2023 — cereals area harvested and yield data

#### web-scout-ai

**Scrape breakdown:** 6/10 scraped (1 failed / 1 bot-blocked / 0 http-error / 1 policy-blocked / 1 irrelevant) — avg content depth: **1,839 chars/source**

**Search queries issued:**
- FAOSTAT Ethiopia cereal yield and harvested area 2023 data
- Ethiopia 2022/2023 Meher season crop production statistics cereals
- Ethiopia Central Statistical Agency 2023 cereal production report area harvested yield

**Failed URLs:**

| Category | URL | Error |
|----------|-----|-------|
| scrape_failed | https://statbase.org/data/eth-cereal-production/ | [scrape failed: Unexpected error in _crawl_web at line 718 in _crawl_web (../../../../../../../../../.local/share/mamba/envs/gee_llm/lib/python3.12/site-packages/crawl4ai/async_crawler_strategy.py): E |
| bot_detected | https://www.fas.usda.gov/data/production/et | [bot detection: [Scrape failed: bot_detected: Vision extraction returned too little content (227 chars — page likely blocked)]] |
| blocked_by_policy | https://www.sciencedirect.com/science/article/pii/S1658077X23000693 | [blocked by policy: [Scrape failed: Skipped: blocked domain]] |
| scraped_irrelevant | https://www.fao.org/statistics/highlights-archive/highlights-detail/agricultural-production-statistics-2010-2023/en | [scraped but irrelevant: [No relevant content specific to Ethiopia was found on this summary page. The page provides global highlights for the 2024 FAOSTAT agricultural production update covering the  |

**Source content previews:**

- **[Ethiopia SDG Indicator 2.4.1A3 - Production of Major Food Crops](https://sdg.mopd.gov.et/api/ethiopia/export/2.4.1A3?framework=sdg)**  
  According to the Ethiopia SDG monitoring data (Indicator 2.4.1A3), the statistics for crop production in the Meher season by smallholders are as follows: ### Production Statistics (in Million Quintals) * **Total Major Food Crops (2023):** 393 million…
- **[DRM-ATF Meher Seasonal Update - August 2023](https://fscluster.org/sites/default/files/documents/drm-atf_meher_seasonal_update_-_august_2023.pdf)**  
  This document provides a seasonal update for Ethiopia's 2023 Meher (main) growing season as of August 2023, detailing cultivation progress, input availability, and challenges. ### Crop Cultivation and Planting Progress (2023 Meher Season) As of Augus…
- **[National Bank of Ethiopia (NBE) Annual Report 2022/23](https://nbe.gov.et/wp-content/uploads/2025/02/Annual-Report-2022-2023.pdf)**  
  According to the National Bank of Ethiopia (NBE) Annual Report 2022/23, the agricultural sector grew by 6.3% during the fiscal year. The report provides detailed statistics for major crop production during the 2022/23 Meher (main) season. ### Overall…
- **[Annual Agricultural Sample Survey 2022-2023 (Tanzania)](https://catalog.ihsn.org/catalog/12861/pdf-documentation)**  
  The provided URL corresponds to the metadata and documentation for the **Annual Agricultural Sample Survey (AASS) 2022-2023 for Tanzania (TZA)**, conducted by the National Bureau of Statistics (NBS) and the Office of the Chief Government Statistician…
- **[Cereal yield (kg per hectare) - Ethiopia | World Bank Data](https://data.worldbank.org/indicator/AG.YLD.CREL.KG?locations=ET)**  
  The World Bank Data portal provides statistics for cereal yield in Ethiopia under the indicator 'AG.YLD.CREL.KG'. Cereal yield is measured in kilograms per hectare of harvested land and includes crops such as wheat, rice, maize, barley, oats, rye, mi…
- **[Ethiopia Cereal crop yield by hectar - data, chart](https://www.theglobaleconomy.com/ethiopia/cereal_yield/)**  
  In 2023, the cereal crop yield in Ethiopia was recorded at 2,864 kg per hectare. This represents an increase from the 2022 yield of 2,823 kg per hectare. For context, the maximum historical yield for Ethiopia was 2,903 kg per hectare recorded in 2021…

**Scores:** URL Relevance 4/5 | Tailored Comprehensiveness 3/5 | Synthesis 3/5 | Extraction Coverage 4/5 | **Overall 3.5/5**

- **URL Relevance:** Most of the key URLs are authoritative and directly relevant to Ethiopia cereal statistics, especially the NBE annual report and the Ethiopia SDG indicator page. Two sources are tangential or wrong-country (Tanzania) or secondary aggregators, but the core sources plausibly contain the requested area-harvested and yield data.
- **Tailored Comprehensiveness:** The NBE extract contains the exact cereal area harvested and yield figures for 2022/23, and the external yield source gives a 2023 cereal yield estimate. However, the Ethiopia SDG source explicitly lacks area harvested and yield, and the other sources are mostly contextual rather than providing the specific 2023 cereal area/yield dataset the query asks for.
- **Synthesis Quality:** The synthesis correctly uses the NBE figures and notes the data gap, but it also adds the 2023 calendar-year yield from TheGlobalEconomy and frames it as supplemental rather than clearly separating it from the Meher-season statistics. The mention of a 58% seed-supply gap is not directly stated in the extracts and appears to be a derived or unsupported summary.
- **Extraction Coverage:** There are 6 sources, which is strong breadth, and at least one source (NBE) has substantial, query-relevant detail including a crop table. But several extracts are shallow, contextual, or off-topic, so the overall gathered evidence is not deep enough across most sources to merit the highest score.

**Synthesis:**

Ethiopia's cereal production during the 2022/23 Meher (main) season saw significant growth, with the agricultural sector expanding by 6.3% overall. For the 2023 calendar year, the Ethiopia SDG monitoring data records the total production of major food crops (produced by smallholders during the Meher season) at 393 million quintals, a notable increase from 327.9 million quintals in 2022 [Ethiopia SDG Indicator 2.4.1A3 - Production of Major Food Crops](https://sdg.mopd.gov.et/api/ethiopia/export/2.4.1A3?framework=sdg).

### 2022/23 Meher Season Cereal Statistics
According to the [National Bank of Ethiopia (NBE) Annual Report 2022/23](https://nbe.gov.et/wp-content/uploads/2025/02/Annual-Report-2022-2023.pdf), cereal crops dominated the agricultural landscape, accounting for 79.5% of the total area under major crops and 88.4% of total production. 

*   **Total Cereal Area Harvested:** 10,698,800 hectares (a 2.8% increase from the previous year).
*   **Total Cereal Production:** 345,671,000 quintals (a 14.4% increase from 302,154,600 quintals in 2021/22).
*   **Average Cereal Yield:** 32.3 quintals per hectare (up from 29.0 quintals/ha in 2021/22).

#### Breakdown by Major Cereal Type (Meher 2022/23)
| Cereal Crop | Area Harvested ('000 Hectares) | Production ('000 Quintals) | Yield (Quintals/Hectare) |
| :--- | :--- | :--- | :--- |
| **Maize** | 2,560.1 | 114,460.0 | 44.7 |
| **Wheat** | 2,246.9 | 74,453.6 | 33.1 |
| **Teff** | 2,968.1 | 60,676.0 | 20.4 |
| **Sorghum** | 1,675.2 | 46,268.4 | 27.6 |
| **Barley** | 816.0 | 22,236.4 | 27.2 |
| **Finger Millet** | 375.4 | 10,248.6 | 27.3 |

### Supplemental Production and External Yield Data
In addition to Meher production, the government’s summer irrigation initiative contributed 46.2 million quintals of wheat in 2022/23 [National Bank of Ethiopia (NBE) Annual Report 2022/23](https://nbe.gov.et/wp-content/uploads/2025/02/Annual-Report-2022-2023.pdf). Production from the secondary Belg season was estimated at 26.5 million quintals, which represented a 10.1% decline compared to the previous year. For the 2023 calendar year, external data recorded Ethiopia's average cereal yield at 2,864 kg per hectare (approximately 28.64 quintals/ha), showing a slight increase from 2,823 kg/ha in 2022 [Ethiopia Cereal crop yield by hectar - data, chart](https://www.theglobaleconomy.com/ethiopia/cereal_yield/).

### Challenges and Cultivation Progress (2023 Meher Season)
Seasonal updates as of August 2023 indicated that while 80% of land had been cultivated, planting was inconsistent across regions, ranging from 80% in Oromia to only 30% in Tigray [DRM-ATF Meher Seasonal Update - August 2023](https://fscluster.org/sites/default/files/documents/drm-atf_meher_seasonal_update_-_august_2023.pdf). Productivity was hampered by a 58% gap in the supply of improved seeds (particularly maize) and armyworm infestations affecting approximately 135,000 hectares [DRM-ATF Meher Seasonal Update - August 2023](https://fscluster.org/sites/default/files/documents/drm-atf_meher_seasonal_update_-_august_2023.pdf).

### Data Gaps and Contradictions
The available sources do not provide a detailed crop-by-crop breakdown (area and yield) for the 2023/24 season, as most reported figures represent the 2022/23 Meher season which concluded in 2023. There is a slight discrepancy in reported yield: the NBE reports 32.3 quintals/ha for the 2022/23 Meher season, while TheGlobalEconomy reports a lower 2023 calendar year average of 28.64 quintals/ha.

#### openai-websearch (gpt-5.4-mini)

**Scores:** URL Relevance 4/5 | Tailored Comprehensiveness 3/5 | Synthesis 3/5 | Extraction Coverage 4/5 | **Overall 3.5/5**

- **URL Relevance:** Most of the URLs are authoritative and plausibly relevant to Ethiopia crop statistics: the World Bank WDI is a primary source for national agricultural indicators, and USDA/FAS country summary pages are primary sources for crop area/yield/production. The only limitation is that several USDA pages are crop-specific rather than a single national cereals table, so they are relevant but not perfectly matched to the exact aggregate query.
- **Tailored Comprehensiveness:** The extracts do contain concrete yield and area-harvested figures for several cereal crops, but they do not provide a single Ethiopia national cereals area harvested series, which is the core of the query. The World Bank extract gives cereal production and yield, but explicitly lacks direct cereals area harvested data in the visible snippet, so the requested aggregate is only partially covered.
- **Synthesis Quality:** The synthesis stays mostly within the extracted content and correctly notes the absence of a single national cereals area harvested figure. However, it introduces a potentially unsupported framing by calling the World Bank figure a '2023' statistic when the extract only says the latest year shown includes the 2023 value, and it mixes crop-level USDA figures with the national cereals query without fully resolving the mismatch.
- **Extraction Coverage:** There are 6 sources, which is strong breadth for this query, and the USDA extracts provide specific numeric area, production, and yield values for multiple crops. The World Bank extract is also fairly deep as a long time-series table, though the visible snippet is still limited in that it does not expose a direct cereals area harvested series.

**Synthesis:**

I found a reliable 2023 Ethiopia cereal statistic in the World Bank’s World Development Indicators, which reports **cereal production** for Ethiopia at **92,198,347.34 metric tons** and **cereal yield** at **5,410.40 kg per hectare** in the latest year shown in the table extract. The same source also lists Ethiopia’s **arable land** at **42,229,000 hectares** in the latest year shown, but it does **not** break out **cereals area harvested** directly in the extract I could access. ([databank.worldbank.org](https://databank.worldbank.org/World-Development-Indicators-F/id/14940f0a))

For a crop-specific 2023 cross-check, USDA/FAS country summary pages show individual cereal crops for Ethiopia in 2023/2024 such as **corn** at **2,500 thousand hectares**, **10,000 thousand metric tons**, and **4.00 t/ha**; **sorghum** at **1,550 thousand hectares**, **4,140 thousand metric tons**, and **2.67 t/ha**; **barley** at **983 thousand hectares**, **2,184 thousand metric tons**, and **2.22 t/ha**; **wheat** with a 5-year average of **1,889 thousand hectares**, **5,816 thousand metric tons**, and **3.08 t/ha**; and **millet** at **366 thousand hectares**, **942 thousand metric tons**, and **2.57 t/ha**. However, I did **not** find a single authoritative 2023 source in the accessible search results that gave **total cereals area harvested** for Ethiopia as one national figure alongside yield. ([ipad.fas.usda.gov](https://ipad.fas.usda.gov/countrysummary/Default.aspx?crop=Corn&id=ET&utm_source=openai))

So, the best-supported answer from the sources I found is: **Ethiopia’s cereal yield was about 5.41 t/ha in 2023 (World Bank/WDI), while a single national “cereals area harvested” figure was not available in the retrieved sources; instead, only crop-level harvested areas were available from USDA/FAS.** ([databank.worldbank.org](https://databank.worldbank.org/World-Development-Indicators-F/id/14940f0a))

---
