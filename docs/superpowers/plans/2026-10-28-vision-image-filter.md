# Vision Image Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop sending worthless PDF pictures (icons, photos, logos, …) to the vision model, and lower vision thinking effort if a test shows quality holds.

**Architecture:** A new `picture_filter` module in `pdf-extractor-ai` decides, per cropped picture, whether it is worth describing: a size check, then Docling's `DocumentPictureClassifier` (lazy singleton, calls serialized by a lock). `summarize_images` applies it by default inside its existing `encode` step. Web Scout passes a provider-safe `reasoning_effort` on its section-visual call, sets the level from a measurement, and adopts the new package version.

**Tech Stack:** Python 3.13, Docling (`DocumentPictureClassifier`, transformers engine), Pillow, LiteLLM, pytest + pytest-asyncio.

**Spec:** `/Users/riccardo/orca/workspaces/web-scout-ai/skua/docs/superpowers/specs/2026-10-28-vision-image-filter-design.md`

## Global Constraints

- Two worktrees. `PE` = `/Users/riccardo/orca/workspaces/pdf-extractor-ai/vision-filter` (branch `RSO9192/vision-filter`). `WS` = `/Users/riccardo/orca/workspaces/web-scout-ai/skua` (branch `RSO9192/skua`).
- Python env for everything: `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout …`. PE tests run with `PYTHONPATH=src`. WS runs against the unreleased PE code with `PYTHONPATH=<PE>/src:src`.
- Never install, lock, build-and-publish, or upload anything. The user runs `poetry add`, builds and uploads to PyPI.
- No new dependencies. Torch and transformers come with docling.
- Default skip rule: picture area `< 10_000` px (w×h of the crop) → skip. Otherwise top class in `{"icon", "photograph", "logo", "bar_code", "qr_code", "signature", "stamp"}` with confidence `>= 0.6` → skip.
- Filter is **on by default** in `summarize_images`; `picture_filter=None` disables it.
- Classifier only runs inside `summarize_images` (never during `extract`).
- Skipped pictures stay in the document unchanged (rendered `<!-- image -->`), the same as a failed summary.
- PE release version `0.3.0`. WS dependency `pdf-extractor-ai>=0.3.0` after the user publishes.
- Measurement data and scripts live in `~/orca/workspaces/pdf-extractor-ai/_vision_cmp/` (call it `VC`), outside both repos. Measurement scripts written by this plan go there, not in a repo.
- Commits: on the worktree branches only, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No pushes.
- Lint PE with `conda run -p … ruff check src tests` (the binary is in the env). mypy is not installed; keep full type annotations anyway (PE uses `mypy --strict` and ruff `ANN`).

## Review Focus

1. **Classifier cannot load or crashes** (offline, HF down, bad weights). Expected: fail open. Send the pictures as before and log one warning; never fail the document. Test in Task 1 (`test_classifier_error_keeps_picture`) and Task 2 (`test_summarize_fails_open_when_classifier_raises`).
2. **Section with some pictures kept and some skipped.** Expected: visual ids stay contiguous (`s{n}-v0`, `-v1`, …) over the *sent* pictures, so model replies map to the right picture. Test in Task 2 (`test_summarize_mixed_section_ids_contiguous`).
3. **Concurrent workers** (default concurrency 3). Expected: the classifier is never entered by two threads at once. Test in Task 1 (`test_default_classifier_calls_are_serialized`).
4. **Non-RGB crops** (RGBA, L, P, CMYK). Expected: classified without error. Test in Task 1 (`test_docling_classifier_handles_non_rgb`, `slow` mark, loads weights).
5. **Every picture in a section skipped.** Expected: no model call for that section, pictures remain. Test in Task 2 (`test_summarize_all_skipped_section_makes_no_call`).

---

### Task 1 (PE): `picture_filter` module

**Files:**
- Create: `PE/src/pdf_extractor_ai/picture_filter.py`
- Test: `PE/tests/test_picture_filter.py`

**Interfaces:**
- Produces:
  - `DEFAULT_SKIP_CLASSES: frozenset[str]`, the 7 classes from Global Constraints.
  - `class PictureClassifier(Protocol)`: `def classify(self, images: list[PIL.Image.Image]) -> list[tuple[str, float]]`, returning (top class, confidence) per image, same order.
  - `class DoclingPictureClassifier`: implements the protocol. Lazily builds `docling.models.stages.picture_classifier.document_picture_classifier.DocumentPictureClassifier(enabled=True, artifacts_path=None, options=<default preset options>, accelerator_options=AcceleratorOptions())` on first `classify`, converts every image to RGB, calls `engine.predict_batch([ImageClassificationEngineInput(image=…)])`, and maps `label_ids[0]`/`scores[0]` through `_classes`. One `threading.Lock` guards both construction and prediction. Default preset options: `docling.datamodel.pipeline_options._default_picture_classification_options`, or `DocumentPictureClassifierOptions.from_preset("document_figure_classifier_v2")` if that works without the private name. Prefer the public one.
  - `@dataclass(frozen=True, slots=True) class PictureFilter`: fields `min_pixels: int = 10_000`, `skip_classes: frozenset[str] = DEFAULT_SKIP_CLASSES`, `min_confidence: float = 0.6`, `classifier: PictureClassifier | None = field(default=None, compare=False)` (None → process-wide `DoclingPictureClassifier` singleton).
  - `PictureFilter.select(self, images: list[PIL.Image.Image]) -> tuple[list[bool], FilterStats]`: keep flags in order. Size check first, and only images passing it are classified (one `classify` call per `select`, skipped when nothing needs classifying). If `classify` raises: log a warning (once per process is fine), return all size-passing images as kept, and count them in `stats.classifier_errors`.
  - `@dataclass(slots=True) class FilterStats`: `seen: int = 0`, `skipped_size: int = 0`, `skipped_class: int = 0`, `sent: int = 0`, `classifier_errors: int = 0`, `classify_seconds: float = 0.0`, plus `def add(self, other: FilterStats) -> None`.
  - `DEFAULT_PICTURE_FILTER = PictureFilter()`.

- [ ] **Step 1: Write the failing tests** in `PE/tests/test_picture_filter.py`, using a stub classifier that returns preset `(class, conf)` per call and records calls:

```python
def test_small_image_skipped_without_classifying():  # 99x100 image
    f = PictureFilter(classifier=stub([]))
    keep, stats = f.select([Image.new("RGB", (99, 100))])
    assert keep == [False] and stats.skipped_size == 1 and stub.calls == 0

def test_skip_class_above_confidence_dropped():  # ("photograph", 0.6) -> False
def test_skip_class_below_confidence_kept():     # ("logo", 0.59) -> True
def test_chart_class_kept():                     # ("bar_chart", 0.99) -> True
def test_custom_skip_classes_respected():        # skip_classes=frozenset({"bar_chart"})
def test_classifier_error_keeps_picture():       # stub raises -> [True], classifier_errors == 1
def test_stats_counts():                         # 1 tiny + 1 photo + 1 chart -> seen 3, skipped_size 1, skipped_class 1, sent 1

def test_default_classifier_calls_are_serialized(monkeypatch):
    # Patch DoclingPictureClassifier's underlying predict with a function that sleeps 50 ms
    # and tracks max concurrent entries; call classify from 3 threads; assert max_active == 1.

@pytest.mark.slow
def test_docling_classifier_handles_non_rgb():
    out = DoclingPictureClassifier().classify(
        [Image.new(m, (200, 200)) for m in ("RGBA", "L", "P", "CMYK")])
    assert len(out) == 4 and all(isinstance(c, str) and 0 <= s <= 1 for c, s in out)
```

- [ ] **Step 2: Run, expect failure (ImportError)**

Run: `cd PE && PYTHONPATH=src conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout python -m pytest tests/test_picture_filter.py -v`

- [ ] **Step 3: Implement `picture_filter.py`** per the Interfaces block. Short module docstring stating the contract: decides which cropped pictures are worth a vision call, and fails open.

- [ ] **Step 4: Run tests including slow**

Run: `cd PE && PYTHONPATH=src conda run -p … python -m pytest tests/test_picture_filter.py -v -m "slow or not slow"`
Expected: all PASS (first slow run downloads the classifier weights).

- [ ] **Step 5: Lint and commit**

```bash
cd PE && conda run -p … ruff check src tests && git add src/pdf_extractor_ai/picture_filter.py tests/test_picture_filter.py && git commit -m "Add picture filter for vision summaries"
```

### Task 2 (PE): apply the filter in `summarize_images`

**Files:**
- Modify: `PE/src/pdf_extractor_ai/image_summarizer.py` (`summarize_images` signature, `encode`, end-of-run log)
- Modify: `PE/src/pdf_extractor_ai/__init__.py` (export `PictureFilter`, `DEFAULT_PICTURE_FILTER`)
- Test: `PE/tests/test_document_visuals.py` (extend)

**Interfaces:**
- Consumes: Task 1 `PictureFilter`, `DEFAULT_PICTURE_FILTER`, `FilterStats`.
- Produces: `summarize_images(document, model, *, model_name: str, concurrency: int = 3, picture_filter: PictureFilter | None = DEFAULT_PICTURE_FILTER) -> DoclingDocument`.

- [ ] **Step 1: Write failing tests** in `tests/test_document_visuals.py`. Use `add_picture(..., size=…)` from `tests/support.py` and a stub classifier that maps image size to a class, so pictures in one document can be told apart:

```python
async def test_summarize_skips_filtered_picture():
    # one picture classified ("logo", 0.95) -> model.calls == 0, "<!-- image -->" in markdown
async def test_summarize_all_skipped_section_makes_no_call():
    # section with 2 pictures, both "icon" 0.9 -> calls == 0, result.pictures has 2
async def test_summarize_mixed_section_ids_contiguous():
    # pictures [photo, chart, chart] in one section -> model receives visual_ids ["s0-v0", "s0-v1"],
    # both summaries land on the two chart pictures, the photo stays a placeholder
async def test_summarize_filter_disabled_sends_all():
    # picture_filter=None with a "logo" picture -> model.calls == 1
async def test_summarize_fails_open_when_classifier_raises():
    # stub raises -> picture is summarized, model.calls == 1
```

Existing tests use a 160×140 picture (22,400 px), so they pass the size check. Give them a stub filter (`PictureFilter(classifier=<always "bar_chart", 0.99>)`) so the suite never loads weights by default.

- [ ] **Step 2: Run, expect the new tests to fail**

Run: `cd PE && PYTHONPATH=src conda run -p … python -m pytest tests/test_document_visuals.py -v`

- [ ] **Step 3: Implement.** In `encode(job)` (already off the event loop): crop the images as now, call `picture_filter.select(images)` when the filter is not None, then assign `visual_id = f"s{job.index}-v{len(visuals)}"` only to kept pictures. Accumulate `FilterStats` across jobs (guard with a lock or collect per job and sum after `gather`). After `gather`, log one `logger.info` line: `"[picture-filter] seen=%d skipped_size=%d skipped_class=%d sent=%d errors=%d classify_s=%.2f"`.

- [ ] **Step 4: Run the whole PE suite**

Run: `cd PE && PYTHONPATH=src conda run -p … python -m pytest -q`
Expected: all PASS (slow tests deselected by default addopts).

- [ ] **Step 5: Update README section "summarize_images"** (around README line 115): one short paragraph on the default filter, the skip rule values, how to pass a custom `PictureFilter`, `picture_filter=None` to disable, and the first-use weight download. Lint, then commit `"Filter worthless pictures before vision summaries by default"`.

### Task 3 (measure): classifier accuracy and latency

**Files:**
- Create: `VC/filter_check.py` (outside repos)

**Interfaces:**
- Consumes: PE `PictureFilter`, `summarize_images`. Existing data: `VC/runs/imgs_kenya/*.png`, `VC/runs/feats_kenya_on.json` (`i`, `useful`), `VC/runs/visuals_kenya_on.json` (`img` filename by index). Existing helper `VC/meter.py` (wraps `litellm.acompletion`, `meter.CALLS`, `meter.report(path)`). Note: `meter.py` loads `.env` from `/Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai/.env`.

- [ ] **Step 1: Accuracy.** Run every Kenya PNG through `DEFAULT_PICTURE_FILTER.select` (in batches of 4 to mimic jobs). Print skipped %, useful lost (expect ≈63% skipped, ≈5 of 33 useful lost), cold classifier load seconds, and warm per-image ms.
- [ ] **Step 2: Latency.** With WS's `_SectionVisionModel("gemini/gemini-3.8-flash")` and `meter`, run `extract_async` + `summarize_images` on `WS/tests/test_data/Crop Prospects and Food Situation - Triannual Global Report, No. 2, July 2026.pdf` (50 pages) twice: `picture_filter=None` (baseline; investigation saw 25 calls / 167 s) and the default filter. Then do the same for one large PDF from the Senegal run (re-download a URL from `VC/runs/ab_senegal_on_docs.json`). Report calls, Gemini $, `summarize_images` wall seconds, and the filter's `classify_s` from the log line.

Run: `cd WS && PYTHONPATH=<PE>/src:src conda run --no-capture-output -p … python VC/filter_check.py > VC/runs/filter_check.log 2>&1` (background; tail the log).
Expected: numbers recorded in the log. Classification adds a few seconds per document at most. If it adds more than ~10% of `summarize_images` wall time, stop and report before continuing.

### Task 4 (WS): provider-safe `reasoning_effort` on section visuals

**Files:**
- Modify: `WS/src/web_scout/scraping/_vision.py` (`describe_section_visuals`, new module constant)
- Test: `WS/tests/test_direct_llm_api_keys.py` (extend; it already captures `acompletion` kwargs via `capture_acompletion`)

**Interfaces:**
- Produces: module constant `_SECTION_VISUAL_REASONING_EFFORT: str | None = None` (Task 5 sets the value) and helper `_reasoning_kwargs(model: str, effort: str | None) -> dict[str, str]`. It returns `{"reasoning_effort": effort}` only when `effort` is set and `litellm.supports_reasoning(model=model)` is true (catch exceptions → `{}`). `describe_section_visuals` gains keyword `reasoning_effort: str | None = _SECTION_VISUAL_REASONING_EFFORT` and spreads `_reasoning_kwargs(...)` into its `acompletion` call. `_call_vision_model` is unchanged.

- [ ] **Step 1: Failing tests**

```python
async def test_section_visuals_pass_reasoning_effort_when_supported(monkeypatch):
    seen = capture_acompletion(monkeypatch, litellm, content='{"request_id":"","visuals":[]}')
    await _vision.describe_section_visuals(section_markdown="x", visuals=[one_visual],
        vision_model="gemini/gemini-3.8-flash", reasoning_effort="low")
    assert seen[0]["reasoning_effort"] == "low"

async def test_section_visuals_omit_reasoning_effort_when_unsupported(...):
    # vision_model="openai/gpt-4o-mini" (supports_reasoning False) -> "reasoning_effort" not in seen[0]

async def test_section_visuals_omit_reasoning_effort_when_none(...):
    # reasoning_effort=None -> key absent
```

- [ ] **Step 2: Run, expect failure.** `cd WS && PYTHONPATH=src conda run -p … python -m pytest tests/test_direct_llm_api_keys.py -v`
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** the same file plus `tests/test_document_visuals.py`. Expected PASS.
- [ ] **Step 5: Commit** `"Allow reasoning effort on section visual descriptions"`.

### Task 5 (measure + WS): pick the thinking level

**Files:**
- Create: `VC/thinking_check.py`
- Modify: `WS/src/web_scout/scraping/_vision.py` (value of `_SECTION_VISUAL_REASONING_EFFORT`), `WS/CHANGELOG.md`

- [ ] **Step 1: Measure.** Pick 30 pictures classified as chart, map or table (`cls` in `{bar_chart, line_chart, pie_chart, scatter_plot, box_plot, table, geographical_map, flow_chart}`) from `VC/runs/feats_{kenya,senegal,cotedivoire}_on.json`. Spread them across the three sets. Load each PNG and use its `caption` from the matching `visuals_*_on.json`. Call `_vision.describe_section_visuals` once per picture with `reasoning_effort=None` (provider default) and with `"low"`. Use `vision_model="gemini/gemini-3.8-flash"` and wrap with `meter` so `reasoning` tokens are captured. Record per arm: total cost, completion tokens, reasoning tokens, mean seconds. Per picture: number overlap = share of numbers (≥2 digits, normalized like `value_ab.nums`) in the default description that also appear in the low one. Save both texts side by side to `VC/runs/thinking_pairs.md`.
- [ ] **Step 2: Report** the arm totals, the median overlap, and the 5 lowest-overlap pairs, and read those 5 manually. State which level the provider default turned out to be (from reasoning-token volume vs `low`).
- [ ] **Step 3: Decide.** Set `_SECTION_VISUAL_REASONING_EFFORT = "low"` only if median overlap ≥ 0.9 and the manual read finds no lost axis labels, units, series or readable values. Otherwise leave `None` and record why in the changelog entry. Add a one-line comment above the constant giving the measured basis.
- [ ] **Step 4: Run the WS unit tests touching vision** (`tests/test_direct_llm_api_keys.py tests/test_document_visuals.py`), then commit `"Use low reasoning effort for section visuals"` (or skip the commit if unchanged).

### Task 6 (PE): release prep for 0.3.0

**Files:**
- Modify: `PE/pyproject.toml` (`version = "0.3.0"`), `PE/CHANGELOG.md`

- [ ] **Step 1:** Add a `## [0.3.0] - <date>` entry in the existing Keep-a-Changelog style. Under `### Changed`: the default filter, the skip rule values, measured effect (from Task 3), and how to disable. Under `### Added`: `PictureFilter`, `DEFAULT_PICTURE_FILTER`.
- [ ] **Step 2:** Run the full PE suite including slow: `PYTHONPATH=src conda run -p … python -m pytest -q -m "slow or not slow"`. Expected PASS.
- [ ] **Step 3:** Commit `"Release pdf-extractor-ai 0.3.0"`. Stop: tell the user the branch is ready to merge, build and upload. Do not build or upload.

### Task 7 (WS): end-to-end check and adoption

**Files:**
- Modify (after the user publishes 0.3.0 and runs `conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout poetry add "pdf-extractor-ai>=0.3.0"`): `WS/CHANGELOG.md` (Unreleased: lower vision cost; numbers from Task 3/5/this task)

- [ ] **Step 1: End-to-end, before release.** Using `VC/value_ab.py` (mode `on`, cold caches already forced via `refresh_pdf_cache=True`), run Kenya and Senegal with `PYTHONPATH=<PE>/src:src`, in the background with logs in `VC/runs/`:

```
Kenya:   "What maize production and yield figures did the Kenya National Bureau of Statistics publish for the latest year in its Economic Survey?"
Senegal: "What are the main small-pelagic species landed in Senegal in the latest reported year, and what volumes did the national fisheries authorities publish?"
```

Use tag names `kenya_filt` / `senegal_filt` so the earlier files are not overwritten.

- [ ] **Step 2: Compare** with the baseline (Kenya on: 70 calls, $0.352, 530 s; Senegal on: 108 calls, $0.625, 1283 s). Report calls, Gemini $, run seconds, and the key answer facts: Kenya 45.8 M bags; Senegal 2024 species tonnages (S. maderensis 134,686 t etc.). Search and PDF selection vary run to run, so report PDFs parsed alongside. Expected: about two-thirds fewer Gemini calls per PDF and the same key facts.
- [ ] **Step 3: Adoption (after publish).** Confirm `pyproject.toml` shows `pdf-extractor-ai = ">=0.3.0"` (the user's `poetry add` writes it). Run `PYTHONPATH=src conda run -p … python -m pytest -q` in WS. Add the changelog entry and commit `"Adopt pdf-extractor-ai 0.3.0 picture filter"`.
