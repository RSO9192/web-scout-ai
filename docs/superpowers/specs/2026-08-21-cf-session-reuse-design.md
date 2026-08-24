# Cloudflare Session Reuse — Design Spec

Date: 2026-08-21

## Problem

`stealthy_fetch` ([src/web_scout/scraping/_scrapling.py](../../../src/web_scout/scraping/_scrapling.py)) is a bare one-shot call to `StealthyFetcher.async_fetch`. In scrapling 0.4.14 every invocation launches a fresh **patchright Chromium** (not Camoufox) with a throwaway temp profile, passes the Cloudflare wall from zero, and tears everything down. There is no browser reuse and no cap on concurrent browser launches.

Measured failure mode (2026-08-21 experiments, scripts in `/private/tmp/`):

- Solo: 6/6 Wiley URLs fetched sequentially at ~15–20 s each.
- 6 concurrent direct fetches: still 6/6, but 4 of 6 inflate to ~135 s (~8× — CPU contention between Chromium processes, not a Cloudflare block).
- Real pipeline load (4 concurrent `run_web_research` ≈ up to 24 browsers): 0/9–10 scraped, every fetch dies on `Page.goto: Timeout 60000ms exceeded` (`browser_page_timeout_ms`, [src/web_scout/config.py:26](../../../src/web_scout/config.py)).

## Key experimental findings that constrain the design

1. **HTTP cookie replay is dead for Wiley.** The browser earns **no wiley-scoped `cf_clearance` at all** (only one scoped to `.scienceconnect.io`, Wiley's OIDC IdP). Cloudflare passes the browser on TLS/browser fingerprint alone; curl_cffi replay 403s even on the exact URL the browser just fetched at 200. Therefore clearance-cookie capture → HTTP replay **cannot** be the mechanism. It is dropped from this work (possible future optimization for domains that do issue domain-scoped clearances).
2. **tandfonline.com and data.unicef.org passed curl_cffi (`impersonate="chrome"`) with no cookies** — Cloudflare enforcement is dynamic; the existing fast HTTP path already works for many CF domains most of the time.
3. The contention ratio (8×) is network-independent; absolute solve times measured on 2026-08-21 were inflated by a degraded connection. **Timeout values are explicitly NOT tuned in this work** — re-measure on a stable connection first.

## Design

**Per-host shared browser sessions with single-flight creation, plus a global cap on concurrent browser work.**

- One long-lived `AsyncStealthySession` per host (`urlparse(url).netloc`), page-pooled (`max_pages=3`). First URL for a host launches the browser and passes the wall; subsequent URLs reuse pooled pages in the same browser — where the fingerprint that satisfied Cloudflare lives. Works for fingerprint-gated (Wiley) and cookie-gated domains alike (cookies persist in the browser context).
- **Single-flight:** an `asyncio.Lock` per host guards session *creation* only. Concurrent same-host fetches don't each launch a browser; fetches themselves run concurrently on the session's page pool.
- **Global browser semaphore (4):** caps concurrent browser *fetches + launches* across all sessions. The fast HTTP path is untouched — cross-domain parallelism is unlimited. The semaphore is acquired *before* `session.fetch()` is called, so queue wait never consumes the page timeout.
- **LRU cap (3 live sessions per event loop):** each browser holds ~300–500 MB. Only *idle* sessions are evicted (in-flight counter); if all are busy the cap overshoots temporarily rather than killing active fetches.
- **Per-event-loop registry** (`WeakKeyDictionary` keyed by running loop): sessions are never reused across loops (a Playwright object is unusable from another loop). `close_stealthy_sessions()` closes all sessions for the current loop; `run_web_research` calls it in a `finally`.
- **Error eviction:** if `session.fetch()` raises (after scrapling's internal retries), the session is closed and dropped; the next call for that host creates a fresh one. Callers keep their existing error handling — `stealthy_fetch`'s public signature and semantics (`solve_cloudflare=True` default, exceptions propagate) are unchanged.

### kwarg split (scrapling 0.4.14 `engines/_browsers/_types.py`)

Call sites pass: `headless`, `network_idle`, `solve_cloudflare`, `timeout`, `wait`, `wait_selector`, `page_action`, `disable_resources`, `retries`. Of these, **session-constructor-only** keys are `headless`, `max_pages`, `retries`, `retry_delay`; all others are valid `StealthFetchParams` and are passed per-fetch (fetch params override session defaults via scrapling's `validate_fetch`).

## Out of scope (deliberate)

- `cf_clearance` HTTP replay (refuted by experiment).
- Changing `browser_page_timeout_ms` (needs re-measurement on stable network).
- Removing wiley/tandfonline/etc. from `BLOCKED_DOMAINS` — separate follow-up after the acceptance test passes.
- Config plumbing for the new constants (module-level constants; make configurable only if a need appears).

## Acceptance

1. All new unit tests pass; the full existing suite passes.
2. On a **stable connection**: `/private/tmp/webscout_domain_test.py` (4 concurrent `run_web_research`, one per domain) — wiley.com matches its solo result (was 0 vs 8/8). `[fetcher] falling back to StealthyFetcher` log lines for wiley collapse to ~1–2 per run.
3. Validation domain matrix (from real-run failures + live wall probes): CF-403: onlinelibrary.wiley.com, agupubs.onlinelibrary.wiley.com, tandfonline.com, journals.sagepub.com, academic.oup.com, data.unicef.org, wfpusa.org, gijn.org. Non-CF walls (graceful degradation): mdpi.com, fas.usda.gov. Regression: cambridge.org, sciencedirect.com, statbase.org. Timeout path: kalroerepository.kalro.org.
