# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
