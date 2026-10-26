# Dashboard data, link following and partial answers: design

Date: 2026-10-26
Status: draft for approval

## Intent

Web Scout fails on dashboard-style sources. The trigger case is the Brazil
eval question about Legal Amazon municipal deforestation. INPE publishes those
figures mainly through the TerraBrasilis PRODES dashboard. Web Scout found the
right pages and fetched them without errors, but never reached the numbers.

The goal is a general improvement to the package, not a TerraBrasilis fix.
The Brazil trace showed three separate failures, and the user decided to fix
them together:

- **A. Dashboard data.** Web Scout should be able to quote figures that a
  dashboard-style source displays or stores, across the common ways
  dashboards publish data.
- **B. Link following in open search.** Links the extractor flags on a page
  should be followed, not discarded.
- **C. Partial answers.** A page that answers one part of a multi-part
  question should be kept for that part.

The user also decided that the work is judged on real runs, comparing the
old and new versions, not on unit tests alone.

## Evidence

### Brazil root cause

Repro scripts and captures live in `/tmp/brazil_repro`.

1. Search found the TerraBrasilis dashboards and the gov.br PRODES 2026 news
   page. All fetches succeeded.
2. The TerraBrasilis dashboard rendered only its default "states" view. The
   text the extractor received was navigation plus unlabelled chart numbers.
3. The page-shape check scored that text as a rich article
   (`content_page`, content score 8, only 6 sentences in 12,000 characters).
   `prefetched_allows_interaction` therefore withheld the click tools, so the
   extractor could not open the "Municípios" view. A welcome modal also covers
   the page.
4. The municipal data is in static JSON loaded in the background:
   `files/data/prodes_legal_amazon.json` holds values keyed by municipality ID,
   and `files/config/loinames/prodes_legal_amazon.json` maps IDs to names.
   The fetcher never captures background requests.
5. Even a direct scrape of that JSON would fail. `trim_json_value` keeps 20
   items per list, and the names are in a second file.
6. The extractor rejected the gov.br news page as having no relevant content
   in 5 of 5 trials, although it holds the official 3,335 km² Legal Amazon
   figure and a table by state. Asked only for the national figure, the same
   page is accepted. Adding "partial answers count" to the prompt did not
   help in 3 of 3 trials.
7. The extractor returned useful links from rejected pages, such as INPE's
   technical note and press presentation PDFs. In open search those links are
   dropped: link following runs only when the caller restricts domains, in
   `_run_search_mode_impl`. The coverage check is skipped when no page has
   been scraped successfully, and standard depth has only two rounds.

### Partial-answer spike

The real query planner, given the schema already used by the Jev backend,
split the Brazil question into three requirements: the top municipalities,
their figures, and the latest official year with its units and basis. The
extractor then received those requirements and a rule to keep a page that
satisfies at least one of them. Two trials per page:

| Page | Before | With requirements |
|---|---|---|
| gov.br PRODES 2026 news | rejected 5 of 5 | accepted 2 of 2, quoting 3,335 km² |
| TerraBrasilis home | rejected | rejected 2 of 2 |
| Dashboard with unlabelled state numbers | rejected | rejected 2 of 2 |
| InfoAmazonia 2021 article | rejected | accepted 2 of 2, for older figures |

The last row is a risk to watch. Once, the accepted extract also carried a
"does not identify the municipalities" note, which is not evidence.

### Survey of 13 dashboards

Results live in `/tmp/brazil_repro/survey`. Each site was rendered in a real
browser with network capture and compared with what Web Scout extracts today.

| Where the numbers live | Sites |
|---|---|
| Background JSON or CSV requests | TerraBrasilis, Our World in Data, Eurostat, FAO FPMA price tool, amazonialegalemdados |
| Inline JSON in the page's own scripts | World Bank (`window.__data`, about 1 MB), Global Forest Watch |
| Tables or grids that exist only after rendering | IBGE SIDRA, Eurostat |
| Proprietary BI rendering | Power BI public report, Tableau Public |
| Query builder needing parameter choices | FAOSTAT |

Cross-cutting findings:

- The fetcher opens a browser only when the fast HTTP response has under 200
  characters of visible text. Our World in Data, World Bank and SIDRA return
  enough page chrome to skip the browser, so their data is never seen.
- Eurostat was rendered in the browser, yet Web Scout extracted 81 numbers
  where the rendered page shows over 1,000. Its table is a div-based grid
  that the HTML-to-markdown step loses.
- The vision fallback only runs when a page renders completely empty.

### Latency measurements

| Page | Today | Render with data capture |
|---|---|---|
| Our World in Data | 0.2 s, no browser | 1.7 s |
| World Bank | 0.6 s, no browser | 4.6 s |
| IBGE SIDRA | 0.8 s, no browser | 2.9 s |
| TerraBrasilis | 12.1 s, browser | 8.8 s |
| Eurostat | 13.3 s, browser | 12.2 s |
| FAO FPMA | 8.4 s, browser | 9.5 s |

## Goals

1. Detect dashboard-like pages cheaply, from the fast HTTP response and the
   URL.
2. Render detected pages in the browser even when the fast response looks
   full.
3. Collect candidate data sources from the rendered page through three
   channels: background JSON or CSV responses, inline script JSON, and
   rendered tables or grids. Explicit data download links on the page, such
   as CSV or XLSX, are a fourth channel.
4. Show the extractor a compact catalog of those sources, and let it open one
   source and look up matching records in full.
5. Allow the click tools on detected dashboards.
6. Fall back to a screenshot read by the vision model for Power BI and
   Tableau pages.
7. Apply the quote rule below to every figure taken from these channels.
8. In open search, follow the links the extractor flags, within a cap.
9. Split the question into requirements and keep any page that satisfies at
   least one of them, for the requirements it satisfies.
10. Show on real runs that the new version beats the old one.

## The quote rule

Revised by the user on 2026-10-26, replacing the first, stricter version:

- Web Scout reports numbers as the source displays or stores them. A number
  stored in the source's own data file counts as displayed by the source,
  provided the answer quotes it as stored and cites that file's URL.
- Ranking records from a source's own data file is allowed. Example: the ten
  municipalities with the largest deforested area in the latest period,
  ordered from the stored per-municipality values. The values in the ranking
  stay exactly as stored, and the answer cites the data file.
- Summing, averaging or otherwise deriving new figures is still not offered.
- Looking up the label behind an ID, such as a municipality name for a
  municipality ID, counts as quoting, not computing.

Enforcement is by the extractor's prompt and the lookup tool's design: the
tool returns stored values and can order records, but offers no arithmetic.
The user dropped the earlier deterministic verbatim check after review. The
old-versus-new comparison measures unsupported figures with the eval's
independent checker.

## Non-goals

- Query builders such as FAOSTAT, where the user must pick filters before
  any data exists.
- Site-specific adapters for TerraBrasilis, Our World in Data, Eurostat or
  any other single site.
- Decoding Power BI or Tableau data protocols.
- Any change under `eval/`. Another session owns it.

## Anti-goals

These would make the work a failure even if it technically runs:

- Any reported figure that does not appear in the source. This includes
  totals produced by summing and rounding that changes a value. Rankings of
  stored values from a cited data file are allowed.
- Slowing down runs that contain no dashboard-like pages.
- Accepted extracts padded with statements about what a page lacks. Those
  are not evidence.
- Link following that wanders into navigation pages or loops back to pages
  already tried.
- Turning ordinary articles into dashboards. Opening a browser or showing a
  data catalog on a normal news page wastes time and confuses the extractor.
- Citations pointing at a page when the figure came from a data file, or the
  reverse.

## Constraints

- Latency budget for part A, agreed with the user: about 5 to 15 extra
  seconds per search round, and only when the round contains dashboard-like
  pages. Runs without them must not change.
- Part B adds a follow-up scrape wave to any round whose pages flag links.
  No budget was agreed for it. The comparison reports its cost so the user
  can decide.
- No new dependencies. Playwright and Scrapling 0.5 are already installed.
  Scrapling exposes `page_action` and records network traffic in
  `engines/_browsers/_network.py`. The user installs any dependency manually.
- Existing models only: the content extractor for data sources and the
  configured `vision_fallback` model for BI screenshots.
- Fetch results are cached across processes through Prefect via
  `CachedSourceArtifact`. Captured data sources must survive that cache, or a
  cached page will lose them.
- Fetches must stay bounded: per-fetch deadline, concurrency limits and
  browser session sharing already exist and must keep working.
- Merge compatibility, decided with the user: this work starts from `dev`.
  Uncommitted work in the main checkout changes `stealthy_fetch`, adding a
  `wait_ready` keyword and chaining `page_action`, and changes the fetch
  deadlines in `ScraplingFetcher.fetch` and `download_pdf`. Changes to those
  functions here must be additive and must not alter the deadlines. If that
  work lands on `dev` first, rebase onto it before continuing.

## Part A: dashboard data

### 1. Dashboard detection

A cheap, deterministic check runs on the fast HTTP response and the URL. It
decides whether the page gets the dashboard treatment.

Signals seen in the survey:

- Known dashboard hosts and paths, such as `app.powerbi.com`,
  `public.tableau.com`, ArcGIS dashboard apps, and `/dashboard` paths.
- Many numbers or scripts relative to very few sentences.
- Chart or map library markers.
- A large inline JSON blob in a script tag.

Tuning is left to the builder against the survey set and against ordinary
article pages from the existing tests and benchmarks. The gov.br news page,
with 64 sentences, must not be detected.

### 2. Rendering and capture

Detected pages go through the existing browser path even when the fast
response is not thin. During rendering:

- Keep background responses whose type is JSON or CSV, that come from the
  page's own site, and that are not analytics, translation, consent or
  configuration noise. Cap each response's size and the number kept per page.
- Wait a bounded time for data requests to settle. Today's browser fetch
  returns on content readiness with network idle disabled, so background data
  may still be loading.
- Extract inline script JSON from the HTML and keep blobs that look like
  data.
- Keep rendered tables and grids. The Eurostat case shows the
  HTML-to-markdown step loses div-based grids, so the rendered text must
  preserve their numbers.
- Record explicit CSV or XLSX download links found on the page.

### 3. Data source catalog

The extractor's input gains a short catalog after the page text. Each entry
shows a stable source ID, its URL, its size, its key or column names, and a
few sample records. The catalog is short enough that it does not push out the
page text.

### 4. Opening a data source

A new extractor tool opens one source by ID with a lookup term, such as
"Brazil" or a municipality name. It returns the matching records verbatim,
with a row cap and a total count, plus any label lookup the source provides.
It can order the matching records by one numeric field, so the extractor can
answer ranking questions from stored values. It offers no summing or other
aggregation.

Records must keep the meaning of each value. For list-of-object JSON a record
is one object. For dimension-indexed formats such as Eurostat's JSON-stat,
values must stay paired with their dimension labels. A bare value with no
labels must not be returned.

### 5. Interaction on dashboards

Click tools are allowed on detected dashboards even when the page-shape check
calls the text rich. The existing click helper triggers clicks from page
JavaScript, which may bypass overlays such as the TerraBrasilis welcome modal;
the builder must confirm this.

### 6. Vision fallback for BI pages

For Power BI and Tableau pages, take a screenshot and read it with the
existing vision model. Numbers read from the screenshot count as displayed.
The current vision path in `_vision.scrape_url_via_vision` is the starting
point.

My call: the trigger is proactive. A page on a known BI host goes straight to
the screenshot path after rendering, because its shell text is never useful
and is never empty, so today's empty-content trigger never fires. Other
detected dashboards use vision only if rendering and data capture yield
nothing usable.

### 7. Citations

A figure taken from a data source cites that source's URL, and a figure from
the page cites the page. The synthesis and the result's URL buckets must show
data file URLs as sources.

My call: each data source the extractor actually quotes from becomes its own
citable entry in the tracker, with its own URL and the quoted records as
content. One scrape can therefore record several entries: the page, if it
gave evidence, plus each data file used. This needs a small extension of the
tracker and outcome code, not only a prompt change, because the existing
single `source_url` pattern from PDF following cannot carry several sources.

### 8. Plumbing

My call: captured sources travel inside the cached fetch result.
`CachedSourceArtifact` gains a field holding the captured sources, so they
survive the cross-process cache. That field is passed from the scrape tool
into `build_extractor_agent` as a new parameter, so the lookup tool's closure
can query the raw sources. The sources are not embedded in the page text
string; only the catalog is.

## Part B: link following in open search

My call on behaviour:

- After a round's scrapes, collect the links the extractor returned from
  both accepted pages and rejected pages. Rejected pages keep their links
  today, so no extractor change is needed.
- Drop links already tried, blocked domains and the parent page itself.
- Rank the rest with the existing follow-up ranker, `_rerank_followup_urls`,
  and scrape the top few in the same round, before the coverage check. The
  cap follows the depth preset's `urls_followup`, which is 4 at standard
  depth.
- This also runs in the last round, because it happens inside the round.
- Domain-restricted mode keeps its existing deepening. The two must not both
  run on the same round.
- A follow-up wave does not trigger another follow-up wave in the same
  round.
- The wave runs after both kinds of round: search rounds and rounds that
  scrape coverage-selected backlog URLs. Backlog pages can flag links to the
  data too. The same cap applies, and the comparison reports the cost.

## Part C: partial answers

My call on behaviour, based on the spike:

- The query planner always produces requirements on the first round, using
  the schema the Jev backend already uses, `SearchQueryChecklist`. Today the
  default backend skips this.
- The requirements are stored on the run's `ResearchTracker`, which the
  scrape tool already holds. The scrape tool reads them at call time. This
  avoids changing the tool's signature or recreating it, which would lose its
  in-flight and outcome caches. Query generation finishes before the first
  scrape, so the requirements exist in time.
- Direct-URL mode has no query generation. It keeps today's behaviour, with
  the whole query as its single requirement.
- The requirements are passed to the page extractor and to the PDF
  extractor. Both keep a source that satisfies at least one requirement and
  extract facts only for the requirements it satisfies. Statements about
  what the source lacks stay out of the extract. The INPE press presentation
  PDF was rejected for the same reason as the gov.br news page.
- The PDF change is limited to the extractor's prompt and the query string it
  receives, decided with the user. Separate, uncommitted work in the main
  checkout improves PDF downloading and does not touch
  `tools/pdf_extractor.py`, so the two merge cleanly.
- The coverage check and synthesis are unchanged, apart from receiving the
  pages that are now kept.

## Comparison on real runs

Decided by the user: compare old and new behaviour on real runs, one run per
question per version. Brazil runs first as a gate. Only if the new version
holds up there does the comparison expand to more questions.

### Setup

- **Old version:** `dev` at `37edddf`, whose package code equals release
  1.12.1. My call: it is exported with `git archive` into a temporary
  folder, so no branch or worktree is created.
- **New version:** the feature branch.
- **Same conditions:** both use the same question, the default settings the
  eval uses, the same models and the same `.env`, loaded with
  `load_dotenv`.
- **Cache:** both share the URL fetch cache, so they see the same page
  content. The new version must use a new cache key for pages with captured
  data sources, or it will reuse old cached pages without them.
- **Location:** a comparison script outside `eval/`, my call
  `scripts/compare_versions.py`.
- **Independent checker:** the eval's page fetcher and judge are committed
  on `dev` and present in this branch. The script imports them read-only
  and never modifies `eval/`. Each run record stores a hash of the imported
  eval files. The eval fetcher rejects JSON and CSV, so claims citing data
  files are checked against the raw file body by the comparison script.

### What each run records

- The full result, including every URL bucket: scraped, rejected, failed,
  blocked and snippet-only.
- Which links were followed and which data sources were opened.
- The final answer and its citations.
- Wall time and the number of model calls.
- Whether each quoted figure appears in the cited source, using the eval's
  independent fetcher.

### Brazil gate

My proposal for "holds up", to confirm with the user when the plan is
reviewed:

- The new answer quotes the official PRODES 2026 Legal Amazon figure with
  an INPE citation. The old answer did not.
- Any municipal figure appears as stored in its cited source. A ranking may
  come from a cited data file.
- No quoted figure fails the independent check.
- Added time stays within the agreed budget for part A, and part B's cost
  is reported.

### Expansion set

Only after the gate passes. One run per question per version:

- The other two eval questions, Guatemala and Senegal.
- About six questions aimed at the surveyed dashboards, each with an answer
  checked by hand at the source before running. These cover Our World in
  Data, World Bank, TerraBrasilis, Eurostat, IBGE SIDRA and FAO FPMA.
- A few ordinary questions answered by articles and PDFs, to catch
  regressions.

Success: dashboard questions improve, ordinary questions are no worse on
figures and citations, and latency stays within budget.

### Unit-level checks

Still required alongside the runs:

- Dashboard detection fires on the survey dashboards and not on the gov.br
  news page or other article pages from the existing tests.
- Existing tests keep passing.

## Left to the builder

- Detection thresholds and the exact signal mix.
- Caps for response size, number of sources, catalog length and rows
  returned.
- Noise filtering rules for background responses.
- How records are flattened for each data shape, within the label-pairing
  rule.
- The exact shape of the captured-source field and its serialisation.
- Tool names and the exact catalog layout.
- Test structure, following the existing `tests/` style.
- The exact requirement and partial-answer wording in the extractor prompts,
  within the behaviour above.
