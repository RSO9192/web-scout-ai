# web-scout-ai checks: behavior

- Timestamp: 20260512_101737
- Repository: `/Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai`

| Step | Status | Time (s) | Artifacts |
| --- | --- | ---: | --- |
| `query-probe` | `passed` | 208.06 | `tests/run_results/20260512_101737/query-probe.log` |
| `matrix-probe` | `passed` | 282.64 | `tests/run_results/20260512_101737/matrix-probe.log` |
| `full-query-probe` | `passed` | 200.66 | `tests/run_results/20260512_101737/full-query-probe.log` |

## Commands

- `query-probe`: `/Users/riccardo/.local/share/mamba/envs/web-agent/bin/python tests/query_probe.py --max-results 6 --max-scrapes 4 --output-dir /Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai/tests/run_results/20260512_101737`
- `matrix-probe`: `/Users/riccardo/.local/share/mamba/envs/web-agent/bin/python tests/matrix_probe.py --limit 2 --search-backend serper --research-depth standard --output-dir /Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai/tests/run_results/20260512_101737 --env-file /Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/.env`
- `full-query-probe`: `/Users/riccardo/.local/share/mamba/envs/web-agent/bin/python tests/full_query_probe.py --limit 1 --search-backend serper --research-depth standard --output-dir /Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai/tests/run_results/20260512_101737 --env-file /Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/.env`
