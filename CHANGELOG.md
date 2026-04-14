<!-- markdownlint-disable MD024 -->
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
