# web-scout-ai checks: quick

- Timestamp: 20260422_124943
- Repository: `/Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai`

| Step | Status | Time (s) | Artifacts |
| --- | --- | ---: | --- |
| `lint` | `passed` | 0.01 | `tests/run_results/20260422_124943/lint.log` |
| `unit-fast` | `passed` | 3.00 | `tests/run_results/20260422_124943/unit-fast.log`, `tests/run_results/20260422_124943/unit-fast.xml` |

## Commands

- `lint`: `ruff check src tests`
- `unit-fast`: `pytest tests/test_pipeline.py tests/test_scrape_tool_dedupe.py tests/test_url_utils.py -q --junitxml /Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai/tests/run_results/20260422_124943/unit-fast.xml`
