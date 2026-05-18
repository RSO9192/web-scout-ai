# Pipeline Flow

This document is the readable map of the package's main behavior.

Compatibility note: this map reflects a structural refactor of the pipeline files.
The runtime behavior and public API are intentionally unchanged.

- Entry point: [`run_web_research`](../src/web_scout/agent.py)
- Scrape router: [`scrape_url`](../src/web_scout/scraping.py)
- Extractor tool: [`create_scrape_and_extract_tool`](../src/web_scout/tools.py)

## Top-Level Flow

```mermaid
flowchart TD
    A[run_web_research(query, ...)] --> B{direct_url provided?}

    B -->|Yes| C[Direct URL mode]
    B -->|No| D[Search mode]

    C --> C1[Scrape requested URL]
    C1 --> C2{Page marked as list/hub?}
    C2 -->|Yes| C3[Collect item links]
    C3 --> C4[Optional one-hop next-page scrape]
    C4 --> C5[LLM reranks follow-up URLs]
    C5 --> C6[Scrape chosen follow-up URLs]
    C2 -->|No| C7{Direct URL is a document?}
    C7 -->|Yes| C8[Do not fan out into site chrome]
    C7 -->|No| C9[Extract same-domain follow-up links]
    C9 --> C10[LLM reranks up to 3]
    C10 --> C6

    D --> D1[Build search backend]
    D1 --> D2[LLM generates search queries]
    D2 --> D3[Execute searches in parallel]
    D3 --> D4[Interleave results across queries]
    D4 --> D5[Select unscripted unsampled URLs]
    D5 --> D6[Scrape URLs in parallel]
    D6 --> D7{Domain-restricted mode?}
    D7 -->|Yes| D8[Optional hub deepening or same-domain deepening]
    D7 -->|No| D9[Skip deepening step]
    D8 --> D10{More iterations left?}
    D9 --> D10
    D10 -->|Yes| D11[Coverage evaluator checks scraped evidence]
    D11 --> D12{Fully answered?}
    D12 -->|Yes| E[Synthesis phase]
    D12 -->|No, backlog URLs| D13[Scrape evaluator-selected backlog URLs]
    D12 -->|No, new searches| D14[Generate follow-up queries]
    D13 --> D10
    D14 --> D3
    D10 -->|No| E

    C6 --> E
    C8 --> E

    E --> E1[Build synthesis prompt from scraped + snippet-only context]
    E1 --> E2[LLM writes grounded synthesis]
    E2 --> E3[Deterministic citation judge]
    E3 --> E4{Citation issues found?}
    E4 -->|Yes| E5[Retry synthesis with judge feedback]
    E4 -->|No| F[Return WebResearchResult]
    E5 --> F
```

## Scrape Routing

```mermaid
flowchart TD
    A[scrape_url(url, ...)] --> B[Build ScrapePlan]
    B --> C{Blocked by policy?}
    C -->|Yes| Z[Return Skipped]
    C -->|No| D{Looks like document?}
    D -->|Yes| E[DOCUMENT -> docling]
    D -->|No| F[HEAD request]
    F --> G{JSON or image or binary by headers?}
    G -->|JSON| H[JSON handler]
    G -->|Image| I[Vision image handler]
    G -->|Binary| Z
    G -->|HTML-ish| J[Fast GET]
    J --> K{Document / JSON / image after GET?}
    K -->|Document| E
    K -->|JSON| H
    K -->|Image| I
    K -->|No| L[Inspect HTML size, text density, scripts]
    L --> M{Likely static HTML?}
    M -->|Yes| N[HTML_FAST -> crawl4ai HTTP strategy]
    M -->|No| O[HTML_BROWSER -> crawl4ai browser strategy]

    E --> P{PDF with too little text?}
    P -->|Yes and vision configured| Q[Vision fallback]
    P -->|No| R[Return extracted document content]

    N --> S{Content too thin?}
    S -->|Yes| O
    S -->|No| T[Return HTML content]

    O --> U{Browser download starts?}
    U -->|Yes| E
    U -->|No| V{Browser content empty / failed?}
    V -->|Yes and vision configured| Q
    V -->|No| T

    H --> W[Return structured JSON markdown]
    I --> X[Return image-derived text]
    Q --> Y[Return screenshot-derived text]
```

## Rule Summary

### Mode selection

| Condition | Path |
| --- | --- |
| `direct_url` provided | Skip search and start from the requested URL |
| `include_domains` provided without `direct_url` | Search mode with domain filtering and same-domain deepening |
| Neither provided | Open-web search mode |

### Search loop rules

| Rule | Behavior |
| --- | --- |
| `research_depth="standard"` | 2 iterations, 3 initial queries, 6 initial URLs |
| `research_depth="deep"` | 3 iterations, 5 initial queries, 12 initial URLs |
| Non-final search iteration | Run coverage evaluation to decide whether current evidence is sufficient |
| Evaluator says backlog is enough | Skip fresh search and scrape promising snippet-only URLs |
| Evaluator says coverage is weak | Generate follow-up searches targeting missing information |
| Domain mode + hub page | Follow ranked item links and allow one pagination hop |

### Scrape routing rules

| Signal | Handler |
| --- | --- |
| URL or headers look like PDF/DOCX/PPTX/XLSX | `docling` document path |
| URL or headers look like legacy DOC/XLS/PPT | Skip as unsupported legacy Office binary |
| JSON content-type or JSON body | Structured JSON extraction |
| Image content-type | Vision image extraction |
| Static HTML | crawl4ai HTTP strategy |
| SPA shell / JS-heavy / timed-out GET | crawl4ai browser strategy |
| Browser-triggered download | Treat as document and use document path |
| Empty or image-only PDF and vision is configured | Vision fallback |

### Extractor rules

| Rule | Behavior |
| --- | --- |
| Thin content or SPA/form signal | `list_interactive_elements()` may be used |
| Interactive data controls found | `click_element()` may be used up to 5 times |
| Page is a metadata record linking to a real primary document | `scrape_linked_document()` may fetch the document once |
| Page is marked `list` | Prioritize returning ranked follow-up links over prose |

### Synthesis rules

| Rule | Behavior |
| --- | --- |
| Only scraped sources count as citation targets | Snippet-only URLs are context, not valid citations |
| Citation judge finds bare or hallucinated URLs | Retry synthesis with deterministic feedback |
| No evidence found | Return a synthesis that explicitly says so |

## Reading Order In Code

If you want to follow the runtime path in source:

1. [`run_web_research`](../src/web_scout/agent.py) — public facade and entry point
2. [`_pipeline_flow.py`](../src/web_scout/_pipeline_flow.py) — orchestration helpers
3. [`_pipeline_rules.py`](../src/web_scout/_pipeline_rules.py) — heuristics and prompt builders
4. [`create_scrape_and_extract_tool`](../src/web_scout/tools.py) — extractor contract and rendering
5. [`scrape_url`](../src/web_scout/scraping.py) — URL-level routing
