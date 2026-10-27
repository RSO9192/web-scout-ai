# Eval Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Web Scout vs OpenAI eval with one that checks each answer's claims against the cited pages (fetched neutrally), scores completeness blind, and runs a blind order-swapped pairwise preference, over deep-reading and control questions.

**Architecture:** `compare_search.py` runs every system in a name → runner mapping over `questions.json`, `--repeats` times. `metrics.py` normalises each system's output into one record shape. `fetch.py` downloads every cited URL with httpx (HTML → markdownify, PDF → docling) into an on-disk cache. `judge.py` extracts claims, checks them against fetched text, scores completeness and pairwise preference via Gemini. `report.py` aggregates into `summary.md`.

**Tech Stack:** Python 3.13, httpx, markdownify, docling, litellm (Gemini judge), openai SDK, pydantic v2, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-26-eval-redesign-design.md`

## Global Constraints

- No new dependencies; no changes under `src/web_scout`. `eval/fetch.py` must not import `web_scout`.
- Judge model: `gemini/gemini-3.1-pro-preview` via litellm, Pydantic `response_format`. Every judge prompt starts with `Today's date: YYYY-MM-DD.`
- Baseline unchanged: `gpt-6-astra`, `reasoning.effort="high"`, `web_search` with `search_context_size="high"`, `return_token_budget="unlimited"`.
- Time and cost are not measured.
- Systems iterate from one mapping; the README wording is "vs snippet-based web search".
- Tests: no network. Run with `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_eval_metrics.py -q`.
- The user's rules: no git commits unless the user asks (no commit steps below); never install packages.
- Ruff line length 120.

## Review Focus

1. A run that errors (exception, empty answer) on one side for one repeat — report shows `error` for that run and skips that run pair in pairwise; nothing crashes. Test in Task 5.
2. URLs with parentheses or trailing punctuation inside markdown links (e.g. `https://en.wikipedia.org/wiki/Petén_(department)`), and `utm_*` mixed with meaningful query params — extracted whole, only `utm_*` stripped. Test in Task 1.
3. A PDF served as `application/octet-stream` or from a URL without `.pdf` — detected by `%PDF` magic bytes. Test in Task 2.
4. Judge returns fewer/more verdicts than claims sent — no misalignment; unmatched claims get verdict `judge_error`, excluded from rates. Test in Task 3.
5. Judge returns a URL for a claim that is not among the answer's cited URLs — dropped; a claim left with no URL is `uncited`. Test in Task 3.

## Shared data shapes

Record (one system, one run), produced by `metrics.py`:

```python
{
  "answer": str,               # final answer markdown
  "cited_urls": list[str],     # normalised, order of first appearance
  "extras": dict,              # system-specific (Web Scout: queries; OpenAI: consulted_urls, open_page_urls)
  "error": str | None,
  "judgment": {                # added by judge
    "completeness": {"score": int, "reason": str},
    "claims": [{"claim": str, "urls": list[str], "verdict": str, "reason": str}],
  },
}
```

Claim verdicts: `supported`, `not_supported`, `contradicted` (from judge); `uncited`, `unfetchable`, `judge_error` (assigned in code).

Row file `<out>/<question_id>.json`:

```python
{"id", "title", "kind", "query",
 "runs": {"web_scout": [record, ...], "openai": [record, ...]},
 "pairwise": [{"run": int, "baseline": str, "winner": str, "reasons": [str, str]}]}  # winner: system name or "tie"
```

Page cache `<out>/pages/<sha1(url)>.json`: `{"url", "fetched_at" (ISO), "ok": bool, "text": str, "error": str}`.

---

### Task 1: Records and URL handling (`metrics.py`)

**Files:**
- Modify: `eval/metrics.py` (replace record builders; move report code out in Task 4)
- Test: `tests/test_eval_metrics.py` (replace the old record tests)

**Interfaces:**
- Produces: `normalize_url(url: str | None) -> str`; `markdown_link_urls(text: str) -> list[str]`; `record_web_scout(result: dict) -> dict` (input is `WebResearchResult.model_dump()`); `record_openai_response(response: dict) -> dict`; `record_error(message: str) -> dict`.

- [ ] **Step 1: Write failing tests**

```python
def test_normalize_url_strips_only_tracking_params_and_trailing_slash():
    assert normalize_url("https://a.org/x/?utm_source=openai") == "https://a.org/x"
    assert normalize_url("https://a.org/p?id=3&utm_medium=x") == "https://a.org/p?id=3"
    assert normalize_url("  ") == ""

def test_markdown_link_urls_keeps_parentheses_and_dedupes():
    text = ("See [Petén](https://en.wikipedia.org/wiki/Pet%C3%A9n_(department)). "
            "Also [gov.br](https://www.gov.br/inpe/x/?utm_source=openai) and [again](https://www.gov.br/inpe/x).")
    assert markdown_link_urls(text) == [
        "https://en.wikipedia.org/wiki/Pet%C3%A9n_(department)", "https://www.gov.br/inpe/x"]

def test_record_web_scout_keeps_cited_urls_and_queries_only():
    result = {"synthesis": "Maya [CONAP](https://conap.gob.gt/sigap).",
              "scraped": [{"url": "https://conap.gob.gt/sigap", "title": "SIGAP", "content": "x"}],
              "bot_detected": [{"url": "https://b.org", "content": "Cloudflare"}],
              "queries": [{"query": "q", "language_code": "es"}]}
    record = record_web_scout(result)
    assert record["cited_urls"] == ["https://conap.gob.gt/sigap"]
    assert set(record["extras"]) == {"queries"}
    assert record["extras"]["queries"][0]["language_code"] == "es"
    assert record["error"] is None

def test_record_openai_uses_answer_links_and_keeps_consulted():
    # reuse the existing Responses payload fixture shape: one search call, one open_page, one message
    record = record_openai_response(payload)  # message text contains "[conap](https://conap.gob.gt/sigap/?utm_source=openai)"
    assert record["cited_urls"] == ["https://conap.gob.gt/sigap"]
    assert "https://www.iucn.org/hotspots" in record["extras"]["consulted_urls"]

def test_record_empty_answer_is_error():
    assert record_web_scout({"synthesis": ""})["error"]
```

- [ ] **Step 2: Run tests, expect FAIL (names undefined)**

- [ ] **Step 3: Implement in `eval/metrics.py`**

Cited URLs for both systems come from markdown links in the answer text (both answers use inline `[label](url)`), so both sides are treated identically. OpenAI `url_citation` URLs not present as links are appended. `markdown_link_urls` must balance parentheses inside the URL (scan from `](` counting `(`/`)`), not use a naive `\(([^)]+)\)` regex. `normalize_url` drops query params whose key starts with `utm_`, then one trailing `/`. Drop `text_kept_chars`, `_cited_chars`, `_span_text`, `per_url` and the old `record_web_scout(scraped=..., ...)` signature.

- [ ] **Step 4: Run tests, expect PASS**

---

### Task 2: Neutral fetcher (`eval/fetch.py`)

**Files:**
- Create: `eval/fetch.py`
- Test: `tests/test_eval_metrics.py`

**Interfaces:**
- Consumes: `normalize_url` (Task 1).
- Produces: `fetch_page(url: str, cache_dir: Path, *, client: httpx.Client | None = None) -> dict` returning the page-cache dict; `html_to_text(html: str) -> str`; `is_pdf(content: bytes, content_type: str) -> bool`.

- [ ] **Step 1: Write failing tests** (use `httpx.MockTransport`)

```python
def test_is_pdf_by_magic_bytes():
    assert is_pdf(b"%PDF-1.7 ...", "application/octet-stream")
    assert not is_pdf(b"<html>", "text/html")

def test_fetch_page_caches_and_does_not_refetch(tmp_path):
    calls = []
    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<html><body><h1>Zona núcleo</h1><p>No-take zone.</p></body></html>")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    first = fetch_page("https://kkprajaampat.id/x", tmp_path, client=client)
    second = fetch_page("https://kkprajaampat.id/x", tmp_path, client=client)
    assert first["ok"] and "No-take zone." in first["text"]
    assert second == first and len(calls) == 1

def test_fetch_page_marks_403_and_empty_as_unfetchable(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, text="blocked")))
    page = fetch_page("https://x.org", tmp_path, client=client)
    assert page["ok"] is False and "403" in page["error"]
```

- [ ] **Step 2: Run tests, expect FAIL**

- [ ] **Step 3: Implement**

httpx GET, `follow_redirects=True`, 30 s timeout, desktop Chrome user agent. Non-2xx, network error, or extracted text under 200 characters → `ok=False` with the reason. HTML: strip `script`/`style`/`nav`/`footer` before `markdownify`. PDF: `DocumentConverter().convert(DocumentStream(name="page.pdf", stream=BytesIO(content)), max_num_pages=60).document.export_to_markdown()`; docling import is local to the PDF branch. Cache file name `sha1(normalize_url(url))`; cache writes include `fetched_at` UTC ISO.

- [ ] **Step 4: Run tests, expect PASS**

---

### Task 3: Judge (`eval/judge.py`)

**Files:**
- Modify: `eval/judge.py` (replace contents)
- Test: `tests/test_eval_metrics.py`

**Interfaces:**
- Consumes: `normalize_url`, `markdown_link_urls` (Task 1); page dicts from `fetch_page` (Task 2).
- Produces:
  - `select_passages(text: str, claims: list[str], limit: int = 24_000) -> str`
  - `judge_record(*, query: str, record: dict, today: str, get_page: Callable[[str], dict]) -> dict` → the `judgment` dict
  - `judge_pair(*, query: str, answer: str, baseline_answer: str, today: str) -> tuple[str, list[str]]` → (`"system"`, `"baseline"` or `"tie"`, two reasons)
  - module-level `_complete(system: str, user: str, schema: type[BaseModel]) -> BaseModel` (the only litellm call; tests monkeypatch it)

- [ ] **Step 1: Write failing tests** (monkeypatch `eval.judge._complete` with a fake that returns schema instances keyed on schema type)

```python
def test_judge_record_assigns_code_verdicts(monkeypatch):
    # fake claim extraction returns:
    #  c1 -> ["https://a.org"] (fetched ok), c2 -> ["https://b.org"] (unfetchable),
    #  c3 -> [] (uncited), c4 -> ["https://not-cited.org"] (dropped -> uncited)
    # fake claim check for a.org returns supported for c1
    judgment = judge_record(query="q", record=record_with_links_to_a_and_b, today="2026-10-26", get_page=fake_pages)
    verdicts = {c["claim"]: c["verdict"] for c in judgment["claims"]}
    assert verdicts == {"c1": "supported", "c2": "unfetchable", "c3": "uncited", "c4": "uncited"}

def test_judge_record_marks_missing_verdicts_as_judge_error(monkeypatch):
    # two claims cite a.org; fake check returns one verdict only
    assert [c["verdict"] for c in judgment["claims"]] == ["supported", "judge_error"]

def test_every_prompt_carries_today(monkeypatch):
    # capture `user` args passed to _complete across judge_record and judge_pair
    assert all(u.startswith("Today's date: 2026-10-26.") for u in captured)

def test_judge_pair_needs_agreement_across_swapped_order(monkeypatch):
    # fake returns "A" both times -> first call prefers system, second prefers baseline -> tie
    assert judge_pair(query="q", answer="x", baseline_answer="y", today="2026-10-26")[0] == "tie"

def test_select_passages_keeps_short_text_and_trims_long_text():
    assert select_passages("short", ["anything"]) == "short"
    long_text = ("filler " * 20_000) + "Apuí 1,230 km² deforestation" + (" filler" * 20_000)
    picked = select_passages(long_text, ["Apuí deforestation 1,230 km²"], limit=3_000)
    assert "Apuí 1,230 km²" in picked and len(picked) <= 3_000
```

- [ ] **Step 2: Run tests, expect FAIL**

- [ ] **Step 3: Implement**

Pydantic schemas: `ExtractedClaims(claims: list[ExtractedClaim(claim: str, urls: list[str])])`; `ClaimChecks(checks: list[ClaimCheck(index: int, verdict: Literal["supported","not_supported","contradicted"], reason: str)])` — verdicts are matched to claims by `index`, never by position; `ScoreReason(score: int 1–5, reason: str)`; `PairVerdict(winner: Literal["A","B","tie"], reason: str)`.

Flow in `judge_record`: one extraction call on the answer (prompt: atomic factual claims, copy each claim's cited URLs exactly as linked, `urls=[]` if none); normalise and filter URLs to `record["cited_urls"]`; group claims by first cited URL; for each URL `get_page(url)` → unfetchable if not ok, else one check call with numbered claims and `select_passages(text, claims)`; a claim whose URL group is unfetchable but has a second fetchable URL uses that one. One completeness call (question + answer only, no system name; rubric from the spec: every part answered, specific names/figures/years/rules, latest period as of today; hedged or not-found answers score low but are not "wrong"). `select_passages`: return text unchanged if ≤ limit; else split into ~1,500-char windows, rank by count of shared lowercase word tokens (≥3 chars) with any claim, keep top windows in document order up to `limit`. `judge_pair`: call with (A=system, B=baseline) then (A=baseline, B=system); same underlying winner both times → that winner, else `"tie"`.

`_complete` uses `litellm.completion(model=JUDGE_MODEL, messages=[system, user], response_format=schema)` and `schema.model_validate_json`, raising `RuntimeError` on empty content (as today).

- [ ] **Step 4: Run tests, expect PASS**

---

### Task 4: Report (`eval/report.py`)

**Files:**
- Create: `eval/report.py` (move and rewrite `format_report` from `metrics.py`)
- Test: `tests/test_eval_metrics.py` (replace the old report test)

**Interfaces:**
- Consumes: row files (shape above).
- Produces: `format_report(rows: list[dict], *, run_date: str, systems: list[str], labels: dict[str, str]) -> str`; `summarize_runs(records: list[dict]) -> dict` with keys `completeness`, `supported_rate`, `unsupported`, `uncited`, `pages`, `unfetchable`, `errors` (floats averaged over non-error runs; `None` when no run succeeded).

- [ ] **Step 1: Write failing tests**

```python
def test_summarize_runs_excludes_unfetchable_uncited_and_judge_errors_from_rate():
    run = {"error": None, "cited_urls": ["u1", "u2"], "judgment": {
        "completeness": {"score": 4, "reason": ""},
        "claims": [{"verdict": v} for v in
                   ["supported", "supported", "not_supported", "contradicted", "unfetchable", "uncited", "judge_error"]]}}
    s = summarize_runs([run, {"error": "boom"}])
    assert s["supported_rate"] == 0.5          # 2 / (2 + 1 + 1)
    assert s["unsupported"] == 2 and s["uncited"] == 1 and s["unfetchable"] == 1
    assert s["completeness"] == 4 and s["pages"] == 2 and s["errors"] == 1

def test_report_has_kind_totals_pairwise_and_error_cells():
    md = format_report(rows, run_date="2026-10-26", systems=["web_scout", "openai"],
                       labels={"web_scout": "Web Scout", "openai": "OpenAI web search"})
    assert "| Question | Kind | Completeness | Claims supported by cited page | Unsupported or contradicted | Uncited claims | Pages cited |" in md
    assert "| **Deep questions** |" in md and "| **Control questions** |" in md
    assert "Web Scout vs OpenAI web search: 1 win, 1 tie, 0 losses" in md
    assert "error" in md  # row where every openai run errored
```

- [ ] **Step 2: Run tests, expect FAIL**

- [ ] **Step 3: Implement**

Cells `Web Scout / OpenAI`, rate as a percentage, other numbers one decimal. Sections in order: method paragraph (plain words, says fetched by an independent fetcher, judge given date and blind, controls explained), main table with deep/control totals rows, pairwise summary over run pairs, per-question notes (unfetchable URLs per system, judge reasons for every `contradicted` claim, completeness reasons), and a trimmed worked example for `EXAMPLE_QUESTION_ID = "guatemala"` (queries, cited pages, both answers, up to three supported claims with the URL). Remove the old report code and the old report test.

- [ ] **Step 4: Run tests, expect PASS**

---

### Task 5: Runner, questions, repeats (`compare_search.py`, `questions.json`)

**Files:**
- Create: `eval/questions.json`
- Modify: `eval/compare_search.py`
- Test: `tests/test_eval_metrics.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `SYSTEMS: dict[str, Callable[[str], Awaitable[dict]]]` = `{"web_scout": run_web_scout, "openai": run_openai}`; `LABELS`; `BASELINE_SYSTEM_PAIRS = [("web_scout", "openai")]`; `load_questions(path: Path) -> list[dict]`; `score_row(row: dict, *, today: str, pages_dir: Path) -> dict`.

- [ ] **Step 1: Write failing tests**

```python
def test_load_questions_validates_kind_and_unique_ids(tmp_path):
    path = tmp_path / "q.json"
    path.write_text('[{"id": "a", "title": "A", "kind": "deep", "query": "q"},'
                    ' {"id": "a", "title": "B", "kind": "deep", "query": "q"}]')
    with pytest.raises(ValueError, match="duplicate"):
        load_questions(path)

def test_bundled_questions_have_deep_and_control():
    qs = load_questions(REPO_ROOT / "eval" / "questions.json")
    assert {q["kind"] for q in qs} == {"deep", "control"}
    assert {"guatemala", "brazil", "senegal", "indonesia"} <= {q["id"] for q in qs}

def test_score_row_skips_error_runs_in_pairwise(monkeypatch):
    # runs: web_scout [ok, ok], openai [ok, error]; judge_record and judge_pair monkeypatched
    row = score_row(row, today="2026-10-26", pages_dir=tmp_path)
    assert [p["run"] for p in row["pairwise"]] == [0]
    assert "judgment" not in row["runs"]["openai"][1]
```

- [ ] **Step 2: Run tests, expect FAIL**

- [ ] **Step 3: Write `eval/questions.json`** with this draft set (user approves before the scored run):

| id | kind | query |
| --- | --- | --- |
| guatemala | deep | What are the main biodiversity hotspots in Guatemala? |
| brazil | deep | (current text) |
| senegal | deep | (current text) |
| indonesia | deep | (current text) |
| peru | deep | What anchoveta catch quota did Peru's Ministry of Production set for the most recent first fishing season in the north-central zone, and under which ministerial resolution? |
| eudr | deep | Under the EU Deforestation Regulation, which countries are classified as low risk and high risk in the latest country benchmarking implementing regulation? |
| kenya | deep | What maize production and yield figures did the Kenya National Bureau of Statistics publish for the latest year in its Economic Survey? |
| cotedivoire | deep | What forest cover and annual forest loss figures does Côte d'Ivoire report in its latest national forest inventory? |
| fao_dg | control | Who is the Director-General of FAO and when did they take office? |
| ifad_hq | control | In which city is the headquarters of the International Fund for Agricultural Development? |
| ffpi | control | What was the FAO Food Price Index value in the latest monthly release? |

- [ ] **Step 4: Implement `compare_search.py`**

`load_questions` checks required keys, `kind in {"deep","control"}`, unique ids (`ValueError` naming the duplicate). Runners return records via Task 1 (`run_web_scout` calls `run_web_research(query)` then `record_web_scout(result.model_dump())`; exceptions → `record_error`). CLI: keep `--only`, `--output`, `--judge-dir`, `--skip-judge`; replace `--skip-web-scout/--skip-openai` with `--systems` (repeatable, default all); add `--repeats` (default 2). Per question: run each system `repeats` times sequentially, write the row after each run (so a crash keeps partial results), then `score_row`. `score_row` attaches `judgment` to non-error records (judge exceptions stored as `{"error": ...}` like today) using `get_page = lambda url: fetch_page(url, pages_dir)`, and for each `(system, baseline)` pair and run index where both records are non-error, calls `judge_pair` and maps `"system"/"baseline"` to system names. `--judge-dir` re-scores saved rows; missing pages are fetched then. `pages_dir = output_dir / "pages"`. `today = date.today().isoformat()`.

- [ ] **Step 5: Run full test file, expect PASS**

Run: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout pytest tests/test_eval_metrics.py -q`

- [ ] **Step 6: Live smoke test on one question, one repeat**

Run in background: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout python eval/compare_search.py --only indonesia --repeats 1 --output eval/results/smoke > /tmp/eval_smoke.log 2>&1`
Expected: `eval/results/smoke/indonesia.json` has both runs with `judgment.claims` non-empty, `pages/` populated, `summary.md` renders the table. Read the claim verdicts for a few claims against the cached page text to confirm they are sensible.

---

### Task 6: Full run and README

**Files:**
- Modify: `README.md` (the `## Evaluation` section added in the working tree)

- [ ] **Step 1: Get the user's approval of `eval/questions.json`**, then run the full eval in the background with `--repeats 2`; log to `/tmp/eval_full.log`.
- [ ] **Step 2: Review `summary.md`**: spot-check five `not_supported`/`contradicted` verdicts per system against the cached page text; check unfetchable rates are not lopsided enough to distort rates (if one side exceeds ~30 %, say so in the README).
- [ ] **Step 3: Rewrite `## Evaluation` in README**: method in plain words (systems and settings, independent fetcher, claim check, blind completeness with date, pairwise with order swap, deep vs control), the headline table with deep/control totals and pairwise summary from the run, losses left in, "vs snippet-based web search" wording, and the command (`python eval/compare_search.py`, `--only`, `--repeats`, `--judge-dir`).
