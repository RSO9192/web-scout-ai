# web-scout-ai checks: unit

- Timestamp: 20260512_101413
- Repository: `/Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai`

| Step | Status | Time (s) | Artifacts |
| --- | --- | ---: | --- |
| `lint` | `passed` | 0.02 | `tests/run_results/20260512_101413/lint.log` |
| `unit-all` | `passed` | 24.93 | `tests/run_results/20260512_101413/unit-all.log`, `tests/run_results/20260512_101413/unit-all.xml` |

## Commands

- `lint`: `ruff check src tests`
- `unit-all`: `pytest -W error::RuntimeWarning --junitxml /Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai/tests/run_results/20260512_101413/unit-all.xml`
