<!-- markdownlint-disable MD024 -->
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]


## [1.2.1] - 2026-05-14

### Changed

- **Legacy Office routing is now explicit**: legacy Office binaries such as `.doc`, `.xls`, and `.ppt` are no longer routed into the docling document path. They are detected during URL validation and skipped with a deterministic unsupported-format reason instead of failing later during conversion.
- **Follow-up document heuristics now match actual support**: direct follow-up candidate selection and document-link heuristics now treat `PDF`, `DOCX`, `PPTX`, and `XLSX` as supported document targets, and stop prioritizing legacy Office binaries that the extractor cannot parse.
- **Direct-URL document deepening now uses authoritative routing**: direct URL mode now consults the same `_build_scrape_plan(...)` router used by the scraper, so extensionless document downloads and header-detected documents are treated consistently when deciding whether to suppress same-domain follow-up scraping.
- **Failure-aware direct-URL follow-up selection**: direct URL deepening now distinguishes between successful extractor outcomes and failure outcomes. Successful pages keep the existing broad legacy follow-up parsing, while failed pages only contribute explicitly rendered follow-up links instead of mining arbitrary URLs out of failure boilerplate.

### Fixed

- **Legacy Office document crashes**: URLs resolving to legacy Word/Excel/PowerPoint formats no longer reach docling and trigger `InputFormat ... does not match any allowed format` errors.
- **Mixed-signal document detection**: when a server advertises a legacy MIME type but the filename or URL clearly indicates a supported format such as `.docx`, the router now prefers the supported extension and keeps the document path enabled.
- **Linked-document validation metadata scope**: `scrape_linked_document` now correctly passes validation-discovered `Content-Type` and `Content-Disposition` metadata through its uncached path, restoring consistent behavior for extensionless document downloads.
- **Self-follow-up loops after direct-URL failures**: failed direct URLs no longer re-enter same-domain deepening by rediscovering the parent URL from the legacy `No relevant content found at ...` wrapper text.
- **Malformed bare follow-up URLs**: fallback link extraction now strips trailing colons from bare URLs, preventing malformed candidates such as `https://example.org/report:`.
- **Version metadata alignment**: package version metadata is now aligned across `pyproject.toml` and `src/web_scout/__init__.py`.

## [1.2.0] - 2026-05-13

### Added

- **Process-lifetime session source cache** (`cache=True`): `run_web_research(..., cache=True)` reuses successful query-agnostic URL source artifacts for the lifetime of the current Python process. On the first visit to a URL the raw artifact (full page markdown, converted PDF/DOCX bytes, JSON payload) is fetched and stored in memory. Subsequent calls for the same URL skip the network fetch and document conversion entirely — the LLM extractor still runs per-query to produce a query-specific summary. The cache is keyed on URL, `wait_for` selector, and `max_pdf_pages`; `max_content_chars` is applied at read time so a single cached artifact serves any truncation setting. The cache is in-memory only, is not shared across processes, and is cleared automatically when Python exits.

### Changed

- **Extractor reruns on cached sources**: cached URLs still pass through the LLM extractor and synthesiser for every query. Query-specific extracted summaries and final synthesis are never cached — only the raw source artifact is reused.

## [1.1.1] - 2026-04-30

### Changed

- **Concurrent linked-document de-duplication**: parallel extractor agents now share in-flight state for `scrape_linked_document`, so the same linked PDF or primary document is fetched and converted only once per run even when multiple pages reference it at the same time.
- **Document routing metadata reuse**: document `Content-Type` and `Content-Disposition` discovered during validation now flow through to document scraping, avoiding redundant metadata probes for extensionless download URLs.
- **PDF retry client reuse**: PDF HTTP download retries now reuse a single `httpx` client instead of recreating one for each attempt, reducing connection setup overhead on slow or flaky document hosts.

### Fixed

- **Domain normalization for plain hostnames**: `_normalize_domain` now correctly handles raw domain inputs like `wocat.net` and `iccat.int`, restoring expected behavior for `include_domains` and other domain-restricted workflows.

## [1.1.0] - 2026-04-23

### Added

- **SPA/form-contamination detection**: two new detectors — `_has_fragment` (identifies SPA URLs with hash-based routing) and `_is_form_contaminated` (identifies pages that are survey/nav-only with no substantive content) — prevent the extractor from wasting a scrape slot on pages that can never yield research value.
- **Interactive browser navigation**: the content extractor can now follow SPA routes and interact with page elements during extraction, enabling access to JS-rendered content that previously returned empty or thin results.
- **Quality probe script**: `tests/quality_probe.py` provides a reusable benchmark for manual quality assessment across a diverse query matrix.
- **Comprehensive test suite**: new test modules covering search backends, agent helpers, full pipeline, live scraping, SPA/form detection, interactive tools, and coverage grounding.

### Changed

- **Pipeline concurrency raised**: `max_concurrent` default increased from 3 to 6, roughly halving wall-clock time on multi-source queries.
- **Shared document cache**: `scrape_linked_document` now uses a process-level cache keyed on URL, eliminating duplicate fetches when the same linked document appears across multiple search results.
- **Extractor instructions extended**: the extractor LLM is now instructed to react to SPA and form-contamination signals injected into the raw scrape output, improving routing decisions without requiring a second scrape pass.
- **Hardened evaluator prompt**: coverage evaluation instructions tightened to reduce false positives on thin or bot-blocked source sets.
- **Improved bot-detection rules**: additional heuristics added to detect and classify bot-blocked responses more reliably.

### Removed

- **DuckDuckGo search backend**: `DuckDuckGoBackend` and its dependency are removed. The backend was already flagged as development/fallback-only since v0.9.4. `SearchBackend` is now an open extension point for community contributions.

## [1.0.5]

### Changed

- Suppress crawl4ai logs
- **Synthesis grounding rules tightened**: `SYNTHESISER_INSTRUCTIONS` now contains three explicit, absolute directives — NO TRAINING DATA, REPORT GAPS, and THIN COVERAGE — that prevent the synthesiser model from filling missing evidence with training-data knowledge. Previously the instructions said "only use scraped sources" but the phrasing was weak enough that models routinely violated it when scraped content was thin.
- **Synthesis prompt includes failure context**: the prompt sent to the synthesiser now lists all sources that could not be accessed (bot-blocked, policy-blocked, or failed URLs) in a dedicated section, so the model knows why coverage is limited and cannot plausibly hallucinate content from those sources. A source count and explicit thin-coverage warning are prepended when fewer than three sources were successfully scraped.
- **Open-access and abstract-available scientific publishers unblocked**: `frontiersin.org`, `mdpi.com`, `journals.plos.org` (fully open-access) and `researchgate.net`, `nature.com`, `academic.oup.com` (abstract always accessible, often full text) are removed from the default blocked-domain list. These publishers are primary sources for many research queries and were incorrectly treated as thin-content domains. Consistently paywalled publishers (`sciencedirect.com`, `springer.com`, `wiley.com`, `cambridge.org`, `jstor.org`, `tandfonline.com`, `sagepub.com`) remain blocked.
- **Deterministic citation judge now restricted to scraped sources**: previously `valid_urls` (the set used to detect hallucinated citations) included both scraped URLs and snippet-only search results. This allowed the model to cite a real-looking snippet URL while providing training-data facts that never appeared in any extracted content. `valid_urls` now only includes URLs that were actually scraped and extracted, so any citation to a snippet-only or invented URL is flagged and triggers the synthesis retry.

## [1.0.3]

### Added

- configurable max characters from web scrapes
- increased max turns to 30
- Better logging and better failed url classification
- **Structured JSON extraction**: URLs that return JSON payloads are now routed to a dedicated extractor instead of being skipped outright. The scraper returns a trimmed, readable markdown representation of the payload for downstream extraction.
- **Image URL extraction**: direct image URLs can now be routed through the optional vision fallback model, allowing extraction from charts, maps, scans, and image-only sources when a vision-capable model is configured.
- **`max_pdf_pages` parameter on `run_web_research()`**: controls how many pages are extracted from PDFs (default: 50). Useful for reducing latency on large reports when only the opening sections are needed.
- **Transient LLM error retry**: the content extractor now retries automatically on transient provider errors (`ServiceUnavailableError`, `RateLimitError`, `APIConnectionError`, `BadGatewayError`) with exponential backoff (1 s → 2 s → 4 s delays, up to 4 total attempts). Transient 503/502/429 responses no longer cause a URL to be immediately marked as `scrape_failed`.
- **Routing tests for content-type handling**: added coverage for extensionless document downloads, JSON endpoints, image URLs, and short metadata pages that should not be discarded as thin content.

### Changed

- **Document detection now uses response headers**: primary documents are no longer identified only by filename suffix. The router now inspects `Content-Type` and `Content-Disposition`, which allows extensionless repository and signed download URLs to be treated as documents.
- **Thin-page validation is less aggressive for metadata records**: short repository and catalogue pages are now preserved when they look like metadata pages or document landing pages, allowing the extractor to follow linked primary documents.
- **Hub deepening cap now honoured**: the depth-preset hub caps (10 for `standard`, 15 for `deep`) were previously silently overridden to 3 at both hub call sites. The preset values are now respected. Non-hub fallback deepening (same-domain links from a direct URL, or thin-coverage domain mode) intentionally retains a cap of 3.
- **Follow-up link extraction improved**: `_extract_links_from_markdown` now uses regex scanning across the full content instead of only matching line-leading list items. Links embedded mid-sentence, in indented blocks, or as bare URLs are now captured, improving hub deepening and follow-up candidate quality.
- **Log noise reduced**: scraping logs are now limited to bot-detected URLs and scrape failures. Routing decisions, intermediate fallbacks, and successful scrapes are no longer logged at `INFO` level, keeping production logs actionable.

### Fixed

- **Playwright browser context leak in PDF download and vision fallback**: `_download_pdf_via_browser` and `_scrape_via_vision` both called `browser.close()` without first calling `context.close()`, and had no outer `try/finally` around the browser launch itself. Playwright `BrowserContext` objects are now always explicitly closed before the browser, in all exit paths including exceptions.
- **`asyncio.Future` left pending on unexpected scrape exceptions**: if an exception bypassed both inner `try/except` blocks inside the scrape tool, the `Future` stored in `in_flight` was removed from the dict but never resolved, leaving concurrent callers awaiting `asyncio.shield()` to hang indefinitely. The `finally` block now always resolves the future.
- **pypdfium2 GC ordering in docling PDF conversion**: child objects (pages) were being garbage-collected after their parent `PdfDocument` had already been finalized, triggering an `AssertionError` in pypdfium2's `_close_template`. The conversion wrapper now explicitly deletes the result and converter objects before calling `gc.collect()`, enforcing the correct cleanup order.
- **`BrowserConfig` passed to HTTP-only crawler**: `_scrape_html_fast` was passing a `BrowserConfig` to `AsyncWebCrawler` when using `AsyncHTTPCrawlerStrategy`, which does not launch a browser and does not accept a browser config. The argument is removed.
- **Browser retry path had no exception handling**: the second `AsyncWebCrawler` call in `_scrape_html_browser` (triggered when `wait_for` causes a timeout) was not wrapped in a `try/except`. Exceptions now return a clean error tuple instead of propagating uncaught.
- **Playwright fallback for bot-protected PDFs**: when a plain httpx PDF download is blocked (e.g. Akamai returns 403), the scraper now falls back to a headless Chromium download via Playwright. Previously, these downloads silently triggered a crash via the now-removed docling direct-URL fallback.
- **Crash on PDF download failure**: the docling direct-URL fallback (`source = url`) has been removed. It could not succeed in any case that httpx already failed, and its unhandled exception surfaced as a confusing `ERROR` log. Failures now return a clean error string.
- **Crash on non-PDF document conversion failure**: DOCX/PPTX/XLSX conversion via docling is now wrapped in a `try/except`, returning a clean error instead of propagating an unhandled exception to the caller.
- **Linked-document extraction rejected valid extensionless downloads**: `scrape_linked_document` now validates whether a URL actually resolves to a document instead of requiring a visible `.pdf`/`.docx` suffix.
- **Version metadata mismatch**: package version metadata is now aligned across `pyproject.toml` and `src/web_scout/__init__.py`.

## [0.9.4]

### Fixed

- **DuckDuckGo dependency pinned to stable package**: replaced the `ddgs` dependency with `duckduckgo-search = ">=6.0,<9.0"`. The `ddgs` package only exists from version 9.x (a full rename and rewrite of `duckduckgo-search`) and its "Dux Distributed Global Search" aggregation mode returns completely irrelevant results when Bing parsing fails — including programming tutorial sites for biodiversity queries. The stable `duckduckgo-search` 6–8.x series is used instead.
- **DuckDuckGo off-topic result guard**: `DuckDuckGoBackend` now checks whether returned results share at least one keyword with the query. Results that fail this check are dropped and a warning is logged, preventing the synthesis stage from receiving garbage sources.
- **DuckDuckGo production warning**: selecting `search_backend="duckduckgo"` now emits a `WARNING` log explaining it is a development/fallback option only and that Serper should be used in production.

## [0.9.2]

### Added

- **Database / list-page deepening**: the pipeline now detects when a scraped page is a database list or index ("hub page") via a new `page_type` field on the extractor output. When a hub is detected, the pipeline follows up to 10 item links (standard depth) or 15 (deep), and performs one hop of pagination when a "next page" link is present. Works in both `direct_url` and domain-restricted modes.
- `allowed_domains` parameter on `run_web_research`, `create_scrape_and_extract_tool`, and `scrape_url`: pass a list of domains (e.g. `["reddit.com"]`) to remove them from the default blocked-domain list, enabling research on social platforms when needed.
- `_normalize_domain` helper: automatically strips scheme, `www.`, paths, and ports from `include_domains` entries, so callers can pass `"https://www.wocat.net/en/"` and get correct results.
- `pytest pythonpath = ["src"]` configuration so tests run without manual `PYTHONPATH` setup.

### Changed

- `relevant_links` cap in the content extractor raised from 5 to 15. On list pages, the extractor LLM ranks and returns up to 15 item links ordered by query relevance.
- URL deduplication (`ResearchTracker._normalize_url`) now strips common tracking query parameters (`utm_*`, `fbclid`, `gclid`, `_ga`, etc.) before keying URLs, preventing the same page from being scraped multiple times due to tracking suffixes.

### Fixed

- `_find_next_page_url` now handles relative pagination links (e.g. `[Next](/page/2)`) by resolving them against the base URL, and treats `www.example.com` and `example.com` as the same domain when comparing for same-domain validation.
- Fragment-only links (`#section`) and non-navigable schemes (`mailto:`, `javascript:`) are now explicitly excluded from pagination link detection.
- Hub page candidate deduplication: both page-1 and page-2 link collections now apply the same `l not in candidates` guard, preventing duplicate URLs from consuming hub deepening slots.
- `include_domains` entries are normalised at the API boundary, fixing silent failures when callers pass scheme-prefixed or path-suffixed domain strings.
- `extracted_contents` is now explicitly reset in all four branches of the search-mode loop, preventing stale values from leaking between iterations.

## [0.9.0-beta] - 2026-03-11

### Added

- Initial release.
- Deterministic web research pipeline: query generation, search, triage, parallel scrape, coverage evaluation, synthesis.
- Three research modes: open web search, domain-restricted search, direct URL extraction.
- Pluggable search backends: Serper (Google-quality) and DuckDuckGo (zero-config).
- Smart scraping via crawl4ai: static HTML (fast HTTP), JS-rendered pages (Playwright).
- Document extraction via docling: PDF, DOCX, PPTX, XLSX.
- Vision fallback for scanned PDFs and empty JS pages.
- Any LLM provider via LiteLLM: OpenAI, Anthropic, Google Gemini, Mistral, Groq, and more.
- Structured Pydantic v2 output with full source attribution.
- URL validation: skips dead links, paywalls, binary files, blocked domains.
- Circuit breakers to prevent runaway API costs.
