# web-scout-ai checks: quick

- Timestamp: 20260422_083625
- Repository: `/Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai`

| Step | Status | Time (s) | Artifacts |
| --- | --- | ---: | --- |
| `lint` | `passed` | 0.06 | `tests/run_results/20260422_083625/lint.log` |
| `unit-fast` | `passed` | 2.51 | `tests/run_results/20260422_083625/unit-fast.log`, `tests/run_results/20260422_083625/unit-fast.xml` |

## Commands

- `lint`: `ruff check src tests`
- `unit-fast`: `pytest tests/test_pipeline.py tests/test_scrape_tool_dedupe.py tests/test_url_utils.py -q --junitxml /Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai/tests/run_results/20260422_083625/unit-fast.xml`
