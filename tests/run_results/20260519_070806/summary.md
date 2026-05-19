# web-scout-ai checks: quick

- Timestamp: 20260519_070806
- Repository: `/Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai`

| Step | Status | Time (s) | Artifacts |
| --- | --- | ---: | --- |
| `lint` | `passed` | 0.03 | `tests/run_results/20260519_070806/lint.log` |
| `unit-fast` | `passed` | 2.74 | `tests/run_results/20260519_070806/unit-fast.log`, `tests/run_results/20260519_070806/unit-fast.xml` |

## Commands

- `lint`: `/Users/riccardo/.local/share/mamba/envs/web-agent/bin/python -m ruff check src tests`
- `unit-fast`: `/Users/riccardo/.local/share/mamba/envs/web-agent/bin/python -m pytest tests/test_pipeline.py tests/test_scrape_tool_dedupe.py tests/test_url_utils.py -q --junitxml /Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai/tests/run_results/20260519_070806/unit-fast.xml`
