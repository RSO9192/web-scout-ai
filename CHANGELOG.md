# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-03-11

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
