# Fetching and PDF performance review — 5 October 2026

Implemented CPU-only PDF conversion, lazy initialized Docling pipelines, reused transports and browser sessions, content readiness, bounded admission and source caches, and Jev classification. Existing exported call signatures and return types remain compatible. No packages were published and nothing was deployed. The existing notebook was preserved.

## Measurements

These are local measurements on macOS 15.7.3 / ARM, 16 logical CPUs, one native conversion worker, Docling CPU acceleration with its default four threads. No Cloud Run CPU quota was applied. Dependencies: Docling 2.130.0, docling-core 2.99.0, PyTorch 2.14.0, Pillow 12.3.0, pypdfium2 5.13.0. The web integration environment separately used Docling 2.132.0, PyTorch 2.13.0, TypeSafe SDK 0.7.2, Scrapling 0.4.15, Playwright/Patchright 1.63.0 and httpx 0.28.1. The baseline was captured before changing production code. Full JSON results are in [performance-results.json](performance-results.json).

PDF fixture: the first eight physical pages of `pdf-extractor-ai/tests/test_data/cd9804en.pdf`. Each chunk-size trial ran in a separate process; “cold” includes the first pipeline initialization, “warm” reuses that converter. Peak RSS is the process high-water mark, including previously retained native allocations, rather than an incremental per-document allocation. Trials are single measurements and have local machine noise.

| PDF path | Cold seconds | Warm seconds | Cold peak GiB | Warm peak GiB |
|---|---:|---:|---:|---:|
| Unchanged baseline | 15.33 | 6.64 | 1.82 | 1.86 |
| Optimized, one page, image-bearing API | 7.36 | 4.24 | 1.78 | 1.86 |
| Optimized, four pages | 7.70 | 3.84 | 2.75 | 2.89 |
| Optimized, eight pages | 6.44 | 3.45 | 3.13 | 3.25 |
| Optimized, one page, text-only internal path | 6.67 | 2.84 | 1.65 | 1.82 |

The one-page image-bearing path is 52% faster cold and 36% faster warm. Text-only warm throughput improves 57% relative to the original image-bearing path. Pipeline construction plus `initialize_pipeline(InputFormat.PDF)` took 3.87 seconds in the text-only trial, and was called once across both PDFs. Its child-process peak RSS was 5.6 MiB; the native conversion worker is a thread. Child-process memory was not separately instrumented in the original baseline.

**The default remains one page.** Four and eight pages increased peak memory substantially, violating the agreed acceptance rule for keeping four as the default. They also produced a one-character Markdown difference on this fixture. One-page full and text-only outputs both preserve the baseline SHA-256 `65e369d8247ed4b8a9f48ef256e8028ef1e659c8658ca3e1304cc531468f987e`, physical pages 1–8, tables, headings and captions. The full API retains 11 figures; the internal text-only path retains their significant placeholders and layout without rasters.

The benefit is bounded intermediate pages, queued inputs, and admitted visual payloads. Returned documents, Markdown, layout and source evidence still grow with returned content. Peak full-document RSS changed only slightly on this eight-page fixture; no claim of constant memory or a large reduction in all workloads is justified.

Browser fixture: content appears after 400 ms while analytics requests continue every 100 ms. One cold browser launch took 0.31 seconds. Network-idle timed out after 2.05 seconds; readiness succeeded after 0.65 seconds on two successive isolated contexts and read the completed content both times. End-to-end context/navigation/read times were 2.83 seconds versus 0.76 and 0.74 seconds. Child-process RSS was approximately 782–807 MiB. This compares waiting policies in the same Chromium process, rather than pretending to be a production browser-pool before/after benchmark. The production Patchright revision was unavailable locally; the controlled probe used installed Chromium 1228 through Playwright.

Jev 1.13.0: 20/20 independently labeled held-out synthetic cases passed, with no service errors, in 4.79 seconds total. All unsupported evidence cases and incomplete-coverage cases were rejected. Cases cover paraphrases, fabricated quotes, negation, uncertainty, wrong geography/year, units and misleading snippets. They are separate from the mocked regression cases and were not used to tune the prompts. This small evaluation is a smoke check, not sufficient evidence of domain-wide parity. The unchanged configured GPT route rejected all 20 comparison requests; GPT accuracy and comparative latency are unavailable. The benchmark detects the legacy verifier's fail-open behavior on service errors and excludes those results from quality claims.

## Execution and compatibility

- Browser reads require DOM readiness, challenge resolution, the requested visible selector when supplied, otherwise useful content/controls, and two stable observations. Timeout produces an error. Screenshots additionally await fonts and visible images. Text fetches block unnecessary resources; screenshots retain them.
- Browser processes are retained on the same event loop. Stealth sessions have a bounded host pool, an active-fetch cap, safe failure draining, and 120-second idle eviction. Known included hosts are prewarmed alongside search. Interactive calls share a process and use isolated contexts, with idle eviction and active-session shutdown protection.
- HTTP, binary and search clients are reused. URL fetching and PDF downloads have 75-second overall deadlines; search retries share 45 seconds and respect numeric or HTTP-date Retry-After headers. Browser escalation is restricted to plausible JavaScript shells or challenges, with short-lived host routing knowledge.
- PDF admission is two at a time, acquired before known-PDF download and held through conversion in direct fetch/parse and orchestration. The native worker queue is capped at four jobs and 64 MiB of admitted inputs (one oversized input can proceed alone). Downloads are capped at 128 MiB each. No RAM-backed temporary-file strategy was introduced.
- Default Docling initialization is lazy and non-OCR. Explicit OCR remains supported and initializes separately only when requested. Stage queues are bounded. Figures on a page share one render and native/PIL resources are closed. Visual jobs contain at most four figures, share section context, and have bounded consumers; errors preserve images and the input document remains unchanged.
- Source caching retains at most 128 items, 32 MiB of text/binary/layout estimates, for five minutes. Internal keys include page limits, visual model, selector and domain exclusions. Tools release consumed transport/raw HTML references; public raw fetch/parse results are preserved for callers and crawlers.
- `WEB_SCOUT_CLASSIFICATION_BACKEND=jev` is the default. Set `gpt` to retain the existing GPT prompts/models and verification behavior. Existing follow-up backend configuration is retained. Jev handles coverage, gaps, candidates, crawler link selection and optional PDF evidence support against actual cited pages. Requirements are generated once with the query, and coverage uses scraped source content instead of generated summaries where available. Generative extraction, queries, visuals and synthesis retain their configured models.
- Jev support/completion requires probability at least 0.8; uncertain results stay unverified or unfilled. Missing credentials, authentication failures, invalid responses and exhausted service retries raise errors. No GPT fallback is used. Clients are reused, with bounded transient/unavailable-model retries. The default crawler no longer executes crawl4ai; the exported crawler, explicit heuristics and custom configuration retain their optional compatibility path.

Call `await web_scout.scraping.close_resources()` during application shutdown **after requests finish**. It closes the current loop's transports and browser pools. Explicit `PdfExtractor` owners still call `shutdown()` or use its context manager. Set `PDF_EXTRACTOR_PAGE_CHUNK_SIZE=1|4|8` to compare bounded chunks; one is the accepted default. `WEB_SCOUT_MAX_PDF_BYTES` controls the download cap.

Monotonic debug timings are emitted for fetch, readiness, download, search, classification, pipeline initialization and PDF conversion. Pytest duration reporting is enabled in both projects.

## Verification and reproduction

Unchanged web baseline: 401 passed, 1 skipped, 3 failed in 124.40 seconds. Failures were an opt-in blocked-domain policy expectation, live JSON incorrectly escalating to unavailable browser binaries, and a model-routing test relying on a changed external LiteLLM price template. Tests now isolate the intended domain policy and routing template; narrowed JSON escalation fixes the production behavior. The PDF baseline had 30 passed and 1 skipped.

Final validation: the web full suite passed **421 tests, with one opt-in integration disabled**, in 95.21 seconds, using both updated package sources. PDF passed **all 40 tests with Gemini credentials** in 145.50 seconds; without credentials, 39 pass and its live integration skips (2.94 seconds). Strict PDF mypy and Ruff checks pass, and changed web code and benchmark scripts pass Ruff. Exported signatures were compared against the original revision.

The optional page-span citation integration was also enabled and exercised separately. With the unchanged default GPT route it failed because the model is unsupported and the provider rejects `reasoning_effort`. With the authorized configured Gemini model and LiteLLM's `drop_params=True` compatibility setting, it passed in 13.47 seconds, including the physical page-17 citation. These overrides were confined to the test process; production defaults were preserved. Live HTML, JSON, PDF downloads, Gemini visual enrichment, Jev judgments and this citation path were exercised. Cloud Run performance, the production Patchright binary, and a working live GPT quality comparison remain unverified.

Run web tests in the project's `scout` conda environment through Poetry. To verify both working-tree packages together, set `PYTHONPATH=src:/absolute/path/to/pdf-extractor-ai/src` before `poetry run pytest`. Run PDF tests through `uv run pytest`; load authorized credentials with `uv run --env-file /path/to/.env pytest` for its Gemini integration.

- Browser probe: `poetry run python scripts/benchmark_browser.py`. Optionally set `BROWSER_EXECUTABLE_PATH` to an installed Chromium binary. The script uses a local HTTP fixture and reports child-process RSS.
- Jev/GPT labeled probe: `poetry run python tests/classification_benchmark.py --output /path/to/results.json`.
- PDF probe, from the PDF project: `uv run python scripts/benchmark_pdf.py tests/test_data/cd9804en.pdf --pages 8`; add `--text-only` for the internal path. Run each chunk setting in a fresh process. The baseline cannot be recreated from optimized code; use the saved measurements or the original revision.
