# Tests

This folder contains three kinds of checks:

- `pytest` unit and integration tests for local deterministic behavior
- live probe scripts for inspecting real package behavior against search/scraping/LLM backends
- benchmarks for comparing quality or runtime over fixed query sets

## Quick Start

Fast local confidence:

```bash
mamba run -n web-agent python tests/run_checks.py quick
```

Full local suite:

```bash
mamba run -n web-agent python tests/run_checks.py unit
```

Live behavior probes with saved artifacts:

```bash
mamba run -n web-agent python tests/run_checks.py behavior --env-file /path/to/.env
```

Everything:

```bash
mamba run -n web-agent python tests/run_checks.py all --env-file /path/to/.env
```

## What `run_checks.py` Does

`tests/run_checks.py` is the main entry point when you want repeatable runs with logs.

It writes a timestamped folder under `tests/run_results/<timestamp>/` containing:

- one `.log` file per step
- `summary.md` with a human-readable summary
- `manifest.json` with machine-readable status, commands, timings, and artifact paths
- JUnit XML for pytest steps

Presets:

- `quick`: `ruff` + targeted unit slice
- `unit`: `ruff` + full pytest suite
- `behavior`: live probes only
- `all`: local checks + live probes

The live presets skip cleanly when required API keys are missing.

## Test Map

### Core local tests

- `test_pipeline.py`: top-level pipeline orchestration and parameter validation
- `test_agent_helpers.py`: helper logic inside the research agent flow
- `test_scraping_routing.py`: scrape routing decisions such as HTML vs browser vs document handling
- `test_scrape_tool_dedupe.py`: tracker behavior, dedupe, and scrape-tool reuse/caching
- `test_url_utils.py`: URL normalization and canonicalization behavior
- `test_search_backends.py`: search backend wrapper behavior and result normalization
- `test_followup_reranker.py`: reranking for follow-up URLs
- `test_hub_detection.py`: hub/list page detection logic
- `test_synthesis_grounding.py`: grounding and citation-related synthesis checks

### Interaction and scraping edge cases

- `test_interactive_tools.py`: interactive browser fallback helpers
- `test_spa_form_detection.py`: SPA/form detection heuristics
- `test_live_scraping.py`: live scraping checks against real pages
- `test_pipeline_speed.py`: pipeline speed-oriented checks

### Probe scripts

- `query_probe.py`: search + scrape smoke probe without LLM synthesis
- `validation_matrix_probe.py`: maintained live matrix for open-web, domain-restricted, and direct-URL cases; supports `--preset standard` and `--preset diverse`
- `gmo_probe.py`: targeted regression probe for the extractor/max-turn failure class
- `quality_probe.py`: prints a scored report for a few end-to-end research tasks

### Benchmarks

- `benchmark.py`: benchmark runner writing JSON and Markdown reports
- `quality_benchmark.py`: quality comparison benchmark with saved reports
- `test_quality_benchmark.py`: unit tests for pure helper/report logic in `quality_benchmark.py`

## Which One To Run

Use `quick` when:

- you changed routing, tracker, agent flow, or utility logic
- you want a fast regression check before committing

Use `unit` when:

- you changed package behavior broadly
- you want full local confidence without depending on external services

Use `behavior` when:

- you want to inspect what the package actually does on real queries and URLs
- you are debugging search quality, scraping failures, bot blocking, or synthesis quality

Use the individual probe scripts when:

- you want to tune one specific probe family directly
- you want control over limits like `--max-results`, `--max-scrapes`, or `--preset`

## Environment Notes

Local `pytest` coverage does not require live API access for most files.

Live probes typically require some combination of:

- `SERPER_API_KEY`
- `OPENAI_API_KEY`
- `GEMINI_API_KEY`

If you prefer not to export them in your shell, pass `--env-file /path/to/.env` to `tests/run_checks.py`.

## Saved Outputs

Generated artifacts in this folder:

- `run_results/`: timestamped runs from `run_checks.py`
- `probe_results/`: saved outputs from ad hoc live probes
- `benchmark_results/`: benchmark JSON/Markdown reports

These are useful when you want to compare runs over time or inspect a failure after the fact.
