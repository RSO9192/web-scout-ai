# Eval redesign: Web Scout vs snippet-based web search

## Intent

The README needs a comparison that shows where Web Scout earns its cost: questions whose answer
lives inside real pages and documents (PDF tables, non-English official texts, regulations), not
in a search snippet. The current eval (`eval/compare_search.py`, `eval/judge.py`, `eval/metrics.py`)
points that way but a careful reader can dismantle it:

- "Source content" for Web Scout scores Web Scout's own LLM extracts as if they were page text;
  for OpenAI it scores cited spans of OpenAI's own answer, and the rubric caps that side at 3.
- The judge has no date, so it penalised OpenAI's correct PRODES 2026 figures as "future".
- "Text kept" and "Sources" compare unlike quantities.
- No reference answers or claim checks, 4 questions, one run each, no control questions.

The redesign measures one thing both systems can be held to equally: **is what the answer says
actually on the page it cites, and does the answer contain the facts that matter?**

## Goals

- Every cited page, from both systems, is fetched by one neutral fetcher and claims are checked
  against that text.
- No hand-written reference answers (user decision). Completeness is judged blind, with the
  date given, from the question and the answers.
- Deep-reading questions where Web Scout should win, plus control questions where a snippet is
  enough and a near-tie is expected. Controls are what make the claim credible.
- A judge that is told today's date and does not know which system produced which answer.
- Time, cost, and Web Scout's failure lists are out of scope (user decision).
- A README table a skeptical reader accepts.

## Non-goals

- No new baseline in this round (user decision): OpenAI single-call `web_search` stays the only
  one. The README must say "vs snippet-based web search", not "vs similar systems". The code
  should make adding a baseline later a matter of adding one runner function (see Runs), without
  building a plugin system now.
- No ground-truth answers (user decision).
- No changes to `src/web_scout`.
- No leaderboard-style statistics; two runs per question and the spread is enough.

## Anti-goals

- A metric that favours Web Scout by construction. If a metric can only be won by one side
  because of how it is computed, it is out.
- Hiding losses. A question Web Scout loses (Brazil in the current run) stays in the table.
- New dependencies. Use what `pyproject.toml` already has.

## Design

### Questions — `eval/questions.json`

About 12 questions, moved out of `compare_search.py` into a data file. Each entry:

```json
{"id": "senegal", "title": "Senegal small pelagics", "kind": "deep", "query": "..."}
```

`kind` is `deep` or `control`.

- ~8–9 `deep` questions in the FAO-adjacent domains already used (fisheries, forestry,
  agriculture, biodiversity, environmental regulation): answers in PDF tables, official
  non-English documents, or deep pages of national authorities. Keep the current four.
- ~3–4 `control` questions answerable from a snippet (e.g. a widely reported headline figure
  or a basic fact about an institution).

Claude drafts the question list; the user approves it before any scored run.

### Runs — `eval/compare_search.py`

- Systems are a small mapping of name → runner function returning the common record shape
  (final answer text, cited URLs, system-specific extras). Web Scout and OpenAI
  are the two entries; a future baseline is one more function and one more entry. Report code
  iterates over the systems present instead of hard-coding two sides, but the README table
  stays `Web Scout / OpenAI` for now.
- Each question runs on every system `--repeats` times (default 2). Results stored per run.
- Web Scout record keeps the answer, cited URLs, and the search queries (for the worked example).
- The existing `--only`, `--skip-*`, and `--judge-dir` (re-score saved runs) behaviours stay.

### Neutral fetcher — new `eval/fetch.py`

- Fetches every URL cited in either system's final answer (Web Scout: URLs cited in the
  synthesis; OpenAI: `url_citation` URLs). Tracking parameters like `utm_source` stripped before
  fetching and deduplication.
- HTML: `httpx` GET with a normal browser user agent, converted to text with `markdownify`.
  PDF: `docling`. Does **not** import `web_scout`, so neither system's scraper is trusted.
- Fetched text cached on disk under the run's results folder so re-scoring does not refetch.
  When `--judge-dir` points at a folder with no cache (e.g. older runs), missing pages are
  fetched and cached then; a per-URL fetch timestamp is stored so the report can note it.
  *(Builder's call.)*
- A URL that cannot be fetched (status, bot wall, empty text) is recorded as `unfetchable` and
  its claims are reported separately, never counted as supported or unsupported. Unfetchable
  rate per system is shown in the report so neither side benefits from it silently.

### Scoring — `eval/judge.py`

All judge calls use `gemini/gemini-3.1-pro-preview` via litellm with Pydantic outputs, and every
prompt starts with today's date.

1. **Claim extraction.** Split each final answer into atomic factual claims, each tied to the
   URL(s) it cites. Uncited claims are kept and counted as uncited.
2. **Claim support.** For each claim, give the judge the claim and the neutral-fetched text of
   its cited page (truncated with a documented limit; for long pages, pass the passages most
   lexically similar to the claim). Verdict: `supported`, `not_supported`, `contradicted`.
3. **Completeness (blind, per answer).** The judge sees the question and one answer, without
   the system name, and scores 1–5 with a reason: does it answer every part of the question,
   with the specific names, figures, years, or rules asked for, from the latest period available
   as of today's date. Hedged or "not found" answers score low on completeness but are not
   penalised as wrong.
4. **Blind pairwise preference.** The judge sees both answers labelled A/B, run twice with the
   order swapped; only agreeing verdicts count as a win, otherwise tie. With more than two
   systems this becomes Web Scout vs each baseline.
   With repeats, compare run *i* of Web Scout against run *i* of the baseline (2 run pairs × 2
   orders = 4 calls per question at the default); the summary counts wins/ties/losses over run
   pairs. *(Builder's call.)*

The old `source_relevance` / `source_content` scores and the 3-cap rule are removed.

### Report — `eval/metrics.py` → `summary.md`

Main table, one row per question, cells `Web Scout / OpenAI`, averaged over repeats:

| Question | Kind | Completeness | Claims supported by cited page | Unsupported or contradicted | Uncited claims | Pages cited |

- Totals row split into `deep` and `control`.
- Pairwise preference summary (wins / ties / losses).
- Per-question notes: unfetchable URLs, judge reasons for any
  contradicted claim.
- One worked example (Guatemala or a stronger deep question), trimmed: the queries, the list of
  pages read, both answers, and two or three claims with their supporting passage.

### README

Replace the current "Evaluation" section with the new method in plain words and the headline
table from the latest run.

### Tests — `tests/test_eval_metrics.py`

Extend for: URL normalisation (utm stripping), cited-URL extraction for both systems, report
aggregation (averaging over repeats, deep/control totals),
unfetchable claims excluded from rates. No network in tests.

## Left to the builder

- Exact passage-selection method for long pages and the truncation limits.
- Batching of claim-support calls (one call per answer with all claims is fine if output stays
  reliable).
- File layout of per-run JSON inside the results folder.
- Wording of judge prompts.
