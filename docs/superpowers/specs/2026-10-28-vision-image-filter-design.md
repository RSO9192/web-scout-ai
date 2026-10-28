# Skip worthless PDF pictures before vision summaries, and lower thinking effort

Date: 2026-10-28
Repos: `pdf-extractor-ai` (main change), `web-scout-ai` (adoption + thinking effort)

## Why

Gemini figure summaries dominate Web Scout's LLM spend. Measured on 2026-10-28
(4 deep eval queries, cold caches):

| Run | PDFs | Gemini calls | Gemini $ | Share of LLM $ |
|---|---|---|---|---|
| Guatemala | 4 | 30 | 0.21 | 86% |
| Kenya | 3 | 70 | 0.35 | 94% |
| Côte d'Ivoire | 4 | 59 | 0.27 | 86% |
| Senegal | 9 | 108 | 0.63 | 89% |

Two causes:

1. `pdf_extractor_ai.summarize_images` sends **every** picture Docling finds
   (≤4 per call, plus the section text). Most are not information: of 128
   hand-labelled Kenya visuals only 33 carried data. The rest were icons,
   stock photos, logos, chapter numerals, decorative banners. Senegal: 111 of
   175 pictures were photographs.
2. About half of Gemini's output tokens are thinking tokens (23.8k of 44.7k in
   the Guatemala run), billed at the output rate ($3.75/M vs $0.75/M input).

Note: Google serves `gemini-3.8-flash` for requests to `gemini-3.7-flash`
(the response `modelVersion` says 3.8; no pinned 3.7 is reachable). Web
Scout's default `vision_fallback` name is therefore stale but behaviour is 3.8.

## Goals

- Stop sending pictures with no information content to the vision model.
  Target: roughly two-thirds fewer vision calls on typical report PDFs.
- Lower vision thinking effort to `low` **if** a test shows description
  quality holds.
- Answer quality unchanged (eval bar: net no regression; see
  `feedback-eval-bar`).

## Non-goals

- Changing which PDFs get parsed or how many pages.
- Describing only pages the extractor later uses (separate, larger idea).
- Changing the screenshot / Power BI vision fallbacks in Web Scout.
- Renaming the stale `gemini-3.7-flash` default (can ride along as a one-line
  change if the builder wishes; not required).

## Anti-goals

- Noticeable added latency or memory per PDF.
- Running the classifier when no vision model is in use (plain extraction
  must cost nothing extra).
- Silently losing charts, maps, tables, diagrams at a meaningful rate.

## Decisions (made with the user)

- **The filter lives in `pdf-extractor-ai`**, at the root cause (it is a
  local project we can change and release), not as a workaround in Web Scout.
- **On by default** in `pdf-extractor-ai`. Callers can override or disable.
- **Thinking effort is part of this work.** Target `low`, but only after a
  test; if quality drops, keep the current default and record why.
- **Latency of the added classification must be measured** and reported.

## Design

### `pdf-extractor-ai`

Classify inside `summarize_images`, not during conversion. Docling's
`do_picture_classification` would run on every conversion, including callers
that never summarise images (anti-goal). `summarize_images` already crops
each picture (`picture.get_image(document)`), so classification adds only a
small batched model call on images already in memory.

Classifier: Docling's `DocumentPictureClassifier` with the default preset
(`docling-project/DocumentFigureClassifier-v2.5`, EfficientNet, transformers
engine), via `docling.datamodel.pipeline_options._default_picture_classification_options`
or the public equivalent if one exists. Torch and transformers are already
docling dependencies, so no new packages. Weights download from Hugging Face
on first use, like Docling's layout models. Load lazily once per process and
reuse. *Designer's call:* `summarize_images` runs up to 3 workers in threads
and the transformers engine touches global torch state, so classifier calls
are serialized behind a lock (or done in one batch per document before the
workers start). The model is small (128 images in ~6 s on CPU), so
serializing costs little. Do not assume the classifier is re-entrant.

Skip rule (defaults from the 2026-10-28 test):

- skip if the cropped image is smaller than **10,000 px** (w×h at the
  extractor's 2.0 image scale), or
- skip if the top class is in
  `{icon, photograph, logo, bar_code, qr_code, signature, stamp}` with
  confidence **≥ 0.6**.

Measured effect of this rule: 67% of 372 pictures skipped (Kenya 63%,
Senegal 71%, Côte d'Ivoire 64%). On the hand-labelled Kenya set it lost 5 of
33 useful visuals: four small stat callouts and one pie chart drawn in a
photographic style (k48, 83.8% informal employment). That loss is accepted.

Skipped pictures are left untouched in the document, exactly like a picture
whose summary failed today. A section whose pictures are all skipped makes no
model call.

API: `summarize_images` gains a keyword argument for the filter. Shape is the
builder's call; it must allow (a) the default rule, (b) a custom skip-class
set / thresholds, (c) disabling the filter entirely. Expose the classification
result on `SectionVisual` only if cheap. It is not required.

Logging: one debug/info line per document with pictures seen, skipped by
size, skipped by class, sent, and classification time.

Release as **0.3.0** (default behaviour change). Update README/CHANGELOG if
the package has them. The package is published on PyPI (0.1.0–0.2.3 there;
web-scout's `poetry.lock` hashes match the PyPI files). Publishing to PyPI is
outward-facing and done by the user. The builder prepares the version bump
and build, and stops before upload.

Development happens in a git worktree of `pdf-extractor-ai`
(`~/orca/workspaces/pdf-extractor-ai/vision-filter`, branch
`RSO9192/vision-filter`). Before release, Web Scout is tested against it by
putting the worktree's `src` first on `PYTHONPATH`. Nothing is installed.

### `web-scout-ai`

- After the user publishes 0.3.0, bump `pdf-extractor-ai` to `>=0.3.0`. The
  user runs the install/lock update
  (`conda run -p /Users/riccardo/.local/share/mamba/envs/web-scout poetry add "pdf-extractor-ai>=0.3.0"`);
  never run it.
- `_document._convert_pdf_bytes` uses the package default filter; no new
  Web Scout option unless the builder finds a concrete need.
- PDF parse cache: cached results from before the filter stay valid (they
  just contain more summaries). Bump `pdf_converter_version()` only if the
  builder judges stale entries harmful. Default: do not bump.
- Thinking effort: pass `reasoning_effort` on the vision calls in
  `scraping/_vision.py` (`describe_section_visuals`, and `_call_vision_model`
  if the test covers it), at the level the test supports. Vision model is
  user-configurable (any LiteLLM provider), so the parameter must not break
  models that don't support it. Use LiteLLM's capability check or
  `drop_params`; the builder chooses.

## Tests and measurements (must pass before claiming done)

1. **Unit tests in `pdf-extractor-ai`** with a stub classifier: skipped
   classes are not sent; below-threshold confidence is sent; tiny images are
   skipped; all-skipped section makes no call; filter disabled sends
   everything; skipped pictures stay in the document.
2. **Classifier accuracy check** (offline, one-off measurement; *designer's
   call*): feed the captured Kenya PNGs
   (`~/orca/workspaces/pdf-extractor-ai/_vision_cmp/runs/imgs_kenya/`, labels in `feats_kenya_on.json`
   field `useful`) through the package's own skip decision (size check +
   classifier, no vision API) and confirm ≈63% skipped / ≈5 of 33 useful
   lost. Report the numbers; it is not a permanent suite test (the images are
   third-party content and too large for the repo). Item 5 covers the live
   pipeline.
3. **Latency**: report for the Crop Prospects PDF
   (`web-scout-ai/tests/test_data/Crop Prospects…pdf`) and one large PDF:
   classifier cold load time, per-document classification time, and total
   `summarize_images` wall time before vs after. Before = 167 s / 25 calls for
   Crop Prospects.
4. **Thinking-effort test**: on chart/map/table pictures from the captured
   sets, describe each at the provider default and at `low` (record which
   level the default actually is for `gemini-3.8-flash`). Compare cost and
   output tokens, and check that the low descriptions keep the axis labels,
   units, series and readable values the default ones have (number overlap
   plus a manual read of a sample). Adopt `low` only if quality holds.
5. **End-to-end**: rerun at least two of the four eval queries (Kenya and
   Senegal had the most pictures) with the new code. Report Gemini calls,
   cost, and run time vs the table above. Answers must not regress.

Measurement scripts and captured data from the investigation live in `~/orca/workspaces/pdf-extractor-ai/_vision_cmp/` (copied from `/tmp/vision_cmp/`)
(`meter.py` wraps `litellm.acompletion` to count calls/tokens/cost by caller;
`value_ab.py` runs a query and captures visuals). They may be reused but are
not part of either repo.

## Left to the builder

- Exact API shape of the filter argument and whether it is a dataclass,
  callable, or plain kwargs.
- Where the classifier singleton lives and how it is batched.
- Whether to expose class labels on `SectionVisual`.
- Mechanism for provider-safe `reasoning_effort`.
