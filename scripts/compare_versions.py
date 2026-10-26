"""Run two Web Scout versions on the same questions and report the difference.

Subcommands:
    export-old  Extract ``src/`` of an older commit with ``git archive`` (no branch or worktree is created).
    run         Run one research call with a given ``src/`` first on ``sys.path``, in a fresh process with a
                fresh empty result cache, and save the run record as ``<out>/<label>.json``.
    report      Judge every run record in a directory with ``eval/judge.py`` and write ``report.md`` and
                ``report.json``.
    run-set     Run old then new on every question of a set, one run at a time, into ``<out>/<id>/``.
    report-set  Run ``report`` in every question directory, judge new against old head to head, optionally judge a
                saved OpenAI eval answer as a reference, and write ``summary.md`` with regression flags.

Usage:
    python scripts/compare_versions.py export-old --commit cb36b2b --dest /tmp/ws_compare/old
    python scripts/compare_versions.py run --src /tmp/ws_compare/old/src --label old --question-id brazil \
        --out /tmp/ws_compare/brazil
    python scripts/compare_versions.py run --src "$PWD/src" --label new --question-id brazil \
        --out /tmp/ws_compare/brazil
    python scripts/compare_versions.py report --dir /tmp/ws_compare/brazil
    python scripts/compare_versions.py run-set --questions scripts/compare_questions.json --include-eval \
        --old-src /tmp/ws_compare/old/src --new-src "$PWD/src" --out /tmp/ws_compare/set
    python scripts/compare_versions.py report-set --dir /tmp/ws_compare/set \
        --reference-dir eval/results/2026-10-26-full
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = REPO_ROOT / "eval" / "questions.json"
SET_QUESTIONS_PATH = REPO_ROOT / "scripts" / "compare_questions.json"
# Every eval file the report imports or reads; eval.judge imports eval.metrics.
EVAL_FILES = ("eval/judge.py", "eval/fetch.py", "eval/metrics.py", "eval/questions.json")
DEFAULT_ENV_PATH = Path("/Users/riccardo/Library/CloudStorage/Dropbox/RIKI/FAO/tools/ESSapp/utils/web-scout-ai/.env")
EXPORT_MARKER = "EXPORTED_FROM"
LABEL_ORDER = ("old", "new")
URL_BUCKETS = (
    "scraped",
    "scraped_irrelevant",
    "scrape_failed",
    "source_http_error",
    "bot_detected",
    "blocked_by_policy",
    "snippet_only",
)
PDF_FAILED_BUCKETS = ("scrape_failed", "source_http_error", "bot_detected")
# Download URLs that usually serve a PDF without a .pdf suffix (repositories, journal systems). Path text
# matches anywhere, so "/bitstream" also covers "/bitstreams/" and "/download" covers "/article/download/".
PDF_LIKE_PATH_PATTERNS = ("/bitstream", "/download")
PDF_LIKE_QUERY_PATTERNS = ("format=pdf",)
PDF_SUMMARY_KEYS = {
    "pdf": ("pdf_urls_found", "pdf_scraped", "pdf_failed", "pdf_irrelevant"),
    "pdf_like": ("pdf_like_found", "pdf_like_scraped", "pdf_like_failed", "pdf_like_irrelevant"),
}
# Old minus new supported-claim rate, in percentage points, above which a question is flagged.
SUPPORTED_RATE_TOLERANCE = 10
DATA_SUFFIXES = (".json", ".geojson", ".csv")
RAW_TIMEOUT_SECONDS = 30

_FOLLOWUP = re.compile(r"\[pipeline\] open-mode follow-up on (\d+) links: (.*)")
_SOURCE_OPENED = re.compile(r"\[extract\] data_source_opened (\S+) (\S+)")
_QUOTE_RECORDED = re.compile(r"\[extract\] data_quote_recorded (\S+) (\S+)")
_QUOTE_DROPPED = re.compile(r"\[extract\] data_quote_dropped (\S+) unknown source")
_QUOTED_URL = re.compile(r"""['"](https?://[^'"]+)['"]""")
# A figure in an answer: digit groups of three after a separator (1,234 / 1.234.567), or a plain number,
# each with an optional decimal part. Digits after a letter, dot or slash (km2, PM2.5, R12, 2025/26) are
# units, codes or crop years, not figures; a unit right after a number (155.8km², US$1.2bn) is allowed. The
# lookahead also forbids ending before "[.,]digit", so a rejected match never backtracks to a truncated number.
_FIGURE = re.compile(
    r"(?<![\w./])(?:[0-9]{1,3}(?:[.,][0-9]{3})+(?:[.,][0-9]+)?|[0-9]+(?:[.,][0-9]+)?)(?![0-9/]|[.,][0-9])"
)
# A number in a raw data file: no grouping separators, at most one decimal separator.
_BODY_NUMBER = re.compile(r"(?<![0-9])[0-9]+(?:[.,][0-9]+)?(?![0-9])")


def parse_log_counters(log: str) -> dict[str, int]:
    """Count follow-up waves and data-source events in a captured Web Scout log.

    Keys: ``followup_waves``, ``followup_links``, ``data_sources_opened``, ``data_quotes_recorded``,
    ``data_quotes_dropped``. Versions that predate these log lines get zeros.
    """
    counters = dict.fromkeys(
        ("followup_waves", "followup_links", "data_sources_opened", "data_quotes_recorded", "data_quotes_dropped"), 0
    )
    for line in log.splitlines():
        if match := _FOLLOWUP.search(line):
            counters["followup_waves"] += 1
            counters["followup_links"] += int(match.group(1))
        elif _SOURCE_OPENED.search(line):
            counters["data_sources_opened"] += 1
        elif _QUOTE_RECORDED.search(line):
            counters["data_quotes_recorded"] += 1
        elif _QUOTE_DROPPED.search(line):
            counters["data_quotes_dropped"] += 1
    return counters


def parse_log_urls(log: str) -> dict[str, list[str]]:
    """Return the follow-up links and opened data-source URLs named in a log, in order, duplicates kept."""
    urls: dict[str, list[str]] = {"followup_links": [], "data_sources_opened": []}
    for line in log.splitlines():
        if match := _FOLLOWUP.search(line):
            urls["followup_links"].extend(_QUOTED_URL.findall(match.group(2)))
        elif match := _SOURCE_OPENED.search(line):
            urls["data_sources_opened"].append(match.group(2))
    return urls


def bucket_summary(result: dict) -> dict[str, int]:
    """Count the URL entries in each non-empty bucket of a ``WebResearchResult`` dump."""
    return {bucket: len(result.get(bucket) or []) for bucket in URL_BUCKETS if result.get(bucket)}


def pdf_counters(result: dict) -> dict[str, int]:
    """Count distinct PDF and PDF-like URLs in a ``WebResearchResult`` dump.

    ``pdf_*``: URL path ends in ``.pdf``. ``pdf_like_*``: any other URL whose path contains one of
    ``PDF_LIKE_PATH_PATTERNS`` or whose query contains one of ``PDF_LIKE_QUERY_PATTERNS`` (disjoint sets).
    The dump stores no content type, so other PDF URLs are not counted. ``found`` counts every bucket,
    ``scraped`` the scraped bucket, ``failed`` scrape_failed, source_http_error and bot_detected together,
    ``irrelevant`` scraped_irrelevant.
    """

    def kind(url: str) -> str | None:
        parts = urlsplit(url.lower())
        if parts.path.endswith(".pdf"):
            return "pdf"
        if any(p in parts.path for p in PDF_LIKE_PATH_PATTERNS) or any(
            p in parts.query for p in PDF_LIKE_QUERY_PATTERNS
        ):
            return "pdf_like"
        return None

    groups = (URL_BUCKETS, ("scraped",), PDF_FAILED_BUCKETS, ("scraped_irrelevant",))
    return {
        key: len(
            {entry["url"] for bucket in buckets for entry in result.get(bucket) or [] if kind(entry["url"]) == prefix}
        )
        for prefix, keys in PDF_SUMMARY_KEYS.items()
        for key, buckets in zip(keys, groups)
    }


def data_sources_distinct(record: dict) -> int:
    """Number of distinct data-source URLs a run record's log shows opened (zero for versions without them)."""
    return len(set((record.get("log_urls") or {}).get("data_sources_opened") or []))


def expected_matches(facts: list[str], answer: str) -> tuple[int, int]:
    """Return how many distinct figures of the expected facts appear in the answer, and how many there are.

    A figure appears when one of its readings (comma or dot as the decimal separator) equals a reading of a
    figure in the answer. No rounding: an answer giving 58.5 does not match an expected 58.5468. Integers of
    one or two digits are left out, since almost any answer contains them.
    """
    figures = list(
        dict.fromkeys(
            figure for fact in facts for figure in claim_figures(fact) if not (figure.isdigit() and len(figure) <= 2)
        )
    )
    answer_values = set().union(*(_readings(token) for token in claim_figures(answer)))
    return sum(bool(_readings(figure) & answer_values) for figure in figures), len(figures)


def regression_reasons(old: dict, new: dict) -> list[str]:
    """Return why ``new`` regressed against ``old``, or an empty list.

    Each run is a summary with ``run_error`` and ``judge_error`` flags, ``completeness`` (score or None),
    ``verdicts`` (claim verdicts), ``pdf_scraped`` and ``pdf_like_scraped``. A run error on either side is
    the only reason given ("old run error", "new run error"): nothing else compares. A judge error on either
    side skips the completeness and supported-rate checks; ``summary_row`` lists it separately. Otherwise
    flags: lower completeness (a missing new score counts as lower when old has one); a supported-claim rate
    more than ``SUPPORTED_RATE_TOLERANCE`` points lower (only when both runs have claims); fewer strict PDFs
    scraped, unless strict plus PDF-like scraped did not drop (the PDF may now come from a download URL).
    """
    reasons = [f"{label} run error" for label, run in (("old", old), ("new", new)) if run["run_error"]]
    if reasons:
        return reasons
    if not (old["judge_error"] or new["judge_error"]):
        old_score, new_score = old["completeness"], new["completeness"]
        if old_score is not None and (new_score is None or new_score < old_score):
            reasons.append(f"completeness {old_score} -> {'none' if new_score is None else new_score}")
        old_rate, new_rate = _supported_rate(old["verdicts"]), _supported_rate(new["verdicts"])
        if old_rate is not None and new_rate is not None and old_rate - new_rate > SUPPORTED_RATE_TOLERANCE:
            reasons.append(f"supported rate {old_rate:.0f}% -> {new_rate:.0f}%")
    old_any, new_any = (run["pdf_scraped"] + run["pdf_like_scraped"] for run in (old, new))
    if new["pdf_scraped"] < old["pdf_scraped"] and new_any < old_any:
        reasons.append(f"pdf_scraped {old['pdf_scraped']} -> {new['pdf_scraped']}")
    return reasons


def _supported_rate(verdicts: list[str]) -> float | None:
    return 100 * verdicts.count("supported") / len(verdicts) if verdicts else None


def claim_figures(claim: str) -> list[str]:
    """Return the numeric tokens of a claim, in order, without bare years (1900 to 2100)."""
    figures = []
    for token in _FIGURE.findall(claim):
        if len(token) == 4 and token.isdigit() and 1900 <= int(token) <= 2100:
            continue
        figures.append(token)
    return figures


def check_figures_in_body(figures: list[str], body: str) -> dict[str, bool]:
    """Check each figure against the numbers stored in a raw data-file body.

    A figure passes when one of its readings (comma or dot as the decimal separator, the other as the
    thousands separator) equals a number in the body, or when rounding a longer body decimal to the
    figure's decimal places gives it. Rounding applies only to figures that keep a decimal place, so an
    integer figure never matches a decimal value. This is an evaluation measure, not a runtime check.
    """
    body_values = set()
    for token in _BODY_NUMBER.findall(body):
        body_values.update(_readings(token))
    return {figure: any(_matches(value, body_values) for value in _readings(figure)) for figure in figures}


def eval_files_sha256() -> dict[str, str]:
    """SHA-256 of each eval file the report imports or reads, so runs judged by different eval code show it."""
    return {name: hashlib.sha256((REPO_ROOT / name).read_bytes()).hexdigest() for name in EVAL_FILES}


def _readings(token: str) -> set[Decimal]:
    readings = set()
    for group, decimal_mark in ((",", "."), (".", ",")):
        whole, _, fraction = token.partition(decimal_mark)
        if group in fraction:
            continue
        parts = whole.split(group)
        if len(parts) > 1 and (not 1 <= len(parts[0]) <= 3 or any(len(part) != 3 for part in parts[1:])):
            continue
        try:
            readings.add(Decimal("".join(parts) + ("." + fraction if fraction else "")))
        except InvalidOperation:
            continue
    return readings


def _matches(figure: Decimal, body_values: set[Decimal]) -> bool:
    if figure in body_values:
        return True
    places = -figure.as_tuple().exponent
    if places < 1:
        return False
    step = Decimal(1).scaleb(-places)
    return any(
        -value.as_tuple().exponent > places and value.quantize(step, rounding=ROUND_HALF_UP) == figure
        for value in body_values
    )


def load_question(question_id: str, questions_path: Path = QUESTIONS_PATH) -> dict:
    """Return the question with this id from ``questions_path`` (default ``eval/questions.json``)."""
    for question in json.loads(questions_path.read_text(encoding="utf-8")):
        if question["id"] == question_id:
            return question
    raise SystemExit(f"Unknown question id {question_id!r} in {questions_path}.")


def set_questions(questions_path: Path, include_eval: bool) -> list[tuple[dict, Path]]:
    """Return ``(question, file it came from)`` pairs for a run set, in run order.

    With ``include_eval``, every eval question comes first, and questions of ``questions_path`` whose id is
    already an eval question are dropped.
    """
    items = [(question, QUESTIONS_PATH) for question in json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))]
    items = items if include_eval else []
    seen = {question["id"] for question, _ in items}
    for question in json.loads(questions_path.read_text(encoding="utf-8")):
        if question["id"] not in seen:
            items.append((question, questions_path))
    return items


def run_record_done(path: Path) -> bool:
    """Whether a run record exists, parses and holds no error, so a resumed run set can skip that run."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(record, dict) and "label" in record and not record.get("error")


def export_old(commit: str, dest: Path) -> int:
    """Extract ``src/`` of ``commit`` into ``dest`` and record the full commit hash in ``dest/EXPORTED_FROM``."""
    if dest.exists() and any(dest.iterdir()):
        print(f"{dest} is not empty; choose an empty or new directory.", file=sys.stderr)
        return 2
    full = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--verify", f"{commit}^{{commit}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.Popen(["git", "-C", str(REPO_ROOT), "archive", full, "src"], stdout=subprocess.PIPE)
    untar = subprocess.run(["tar", "-x", "-C", str(dest)], stdin=archive.stdout)
    archive.stdout.close()
    if archive.wait() or untar.returncode:
        print(f"git archive {full} src | tar -x -C {dest} failed.", file=sys.stderr)
        return 1
    (dest / EXPORT_MARKER).write_text(full + "\n", encoding="utf-8")
    print(dest / "src")
    return 0


def run_version(
    *,
    src: Path,
    label: str,
    question_id: str,
    out: Path,
    env_file: Path,
    timeout_s: float,
    questions_path: Path = QUESTIONS_PATH,
) -> int:
    """Run one research call in a child process and write ``<out>/<label>.json`` and ``<out>/<label>.log``.

    The question is looked up in ``questions_path``. A child still running after ``timeout_s`` is killed with
    its process group and recorded as an error.
    """
    question = load_question(question_id, questions_path)
    src = src.resolve()
    if not _is_package_src(src):
        return 2
    out.mkdir(parents=True, exist_ok=True)
    # A fresh empty cache per run, so neither version reuses pages the other fetched and timings compare.
    cache_dir = tempfile.mkdtemp(prefix=f"ws_compare_cache_{label}_")
    log_path = out / f"{label}.log"
    child_path = out / f"{label}.child.json"
    child_path.unlink(missing_ok=True)
    env = {**os.environ, "WEB_SCOUT_CACHE_DIR": cache_dir, "PYTHONUNBUFFERED": "1"}
    env.pop("PYTHONPATH", None)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "_child",
        "--src",
        str(src),
        "--query",
        question["query"],
        "--env-file",
        str(env_file),
        "--result",
        str(child_path),
    ]
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"Running {label} on {question_id}; log: {log_path}", flush=True)
    timed_out = False
    with log_path.open("w", encoding="utf-8") as log_file:
        # Own session, so a timeout kills the child's process group.
        process = subprocess.Popen(command, env=env, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            exit_code = process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            exit_code = process.wait()
    log = log_path.read_text(encoding="utf-8", errors="replace")
    if timed_out:
        shutil.rmtree(cache_dir, ignore_errors=True)
        child = {"error": f"Timed out after {timeout_s:g} s; child killed. Log tail:\n{log[-3000:]}"}
        print(child["error"], file=sys.stderr)
    elif not child_path.is_file():
        shutil.rmtree(cache_dir, ignore_errors=True)
        print(f"Child exited with {exit_code} before running research. Log tail:\n{log[-3000:]}", file=sys.stderr)
        return exit_code or 1
    else:
        child = json.loads(child_path.read_text(encoding="utf-8"))
    child_path.unlink(missing_ok=True)
    result = child.get("result")
    record = {
        "label": label,
        "question_id": question_id,
        "query": question["query"],
        "src": str(src),
        "source_revision": _source_revision(src),
        "web_scout_file": child.get("web_scout_file"),
        "settings": "run_web_research(query) with defaults",
        "cache_dir": cache_dir,
        "env_file": str(env_file),
        "started_at": started_at,
        "timeout_s": timeout_s,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "error": child.get("error"),
        "wall_time_s": child.get("wall_time_s"),
        "model_calls": child.get("model_calls"),
        "model_calls_note": child.get("model_calls_note"),
        "result": result,
        "buckets": bucket_summary(result or {}),
        "log_counters": parse_log_counters(log),
        "log_urls": parse_log_urls(log),
        "log_path": str(log_path),
        "log": log,
        "eval_sha256": eval_files_sha256(),
    }
    record_path = out / f"{label}.json"
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(record_path)
    return 0 if exit_code == 0 and not record["error"] else 1


def _is_package_src(src: Path) -> bool:
    if (src / "web_scout" / "__init__.py").is_file():
        return True
    print(f"{src} does not contain web_scout/__init__.py.", file=sys.stderr)
    return False


def run_set(
    *,
    questions_path: Path,
    include_eval: bool,
    old_src: Path,
    new_src: Path,
    out: Path,
    env_file: Path,
    timeout_s: float,
) -> int:
    """Run old then new on every question of the set, one run at a time, into ``<out>/<id>/``.

    Runs whose record already exists without error are skipped, so an interrupted set resumes. Prints one
    ``[run-set]`` progress line per run.
    """
    if not (_is_package_src(old_src.resolve()) and _is_package_src(new_src.resolve())):
        return 2
    failed = 0
    for question, source in set_questions(questions_path, include_eval):
        question_id = question["id"]
        for label, src in (("old", old_src), ("new", new_src)):
            directory = out / question_id
            if run_record_done(directory / f"{label}.json"):
                print(f"[run-set] {question_id} {label} skipped: record exists", flush=True)
                continue
            started = time.perf_counter()
            status = run_version(
                src=src,
                label=label,
                question_id=question_id,
                out=directory,
                env_file=env_file,
                timeout_s=timeout_s,
                questions_path=source,
            )
            failed += status != 0
            print(f"[run-set] {question_id} {label} exit={status} {time.perf_counter() - started:.0f}s", flush=True)
    print(f"[run-set] finished; {failed} failed runs", flush=True)
    return 1 if failed else 0


def _source_revision(src: Path) -> str:
    marker = src.parent / EXPORT_MARKER
    if marker.is_file():
        return f"export of {marker.read_text(encoding='utf-8').strip()}"
    head = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True)
    if head.returncode:
        return "unknown"
    dirty = subprocess.run(
        ["git", "-C", str(src), "status", "--porcelain", "--", "."], capture_output=True, text=True
    ).stdout.strip()
    return head.stdout.strip() + (" with uncommitted changes" if dirty else "")


def child_main(*, src: Path, query: str, env_file: Path, result_path: Path) -> int:
    """Child process: import ``web_scout`` from ``src``, run the query with defaults, write the outcome JSON."""
    sys.path.insert(0, str(src))
    from dotenv import load_dotenv

    # load_dotenv expands BEDROCK_MANTLE_API_KEY=${AWS_BEARER_TOKEN_BEDROCK}; a literal placeholder reaches
    # Bedrock and fails as an invalid bearer token deep inside the run.
    load_dotenv(env_file)
    key = os.environ.get("BEDROCK_MANTLE_API_KEY", "")
    if not key or key.startswith("${"):
        print(f"BEDROCK_MANTLE_API_KEY is unset or unexpanded after loading {env_file}.", file=sys.stderr)
        return 2

    import logging

    import web_scout

    if not Path(web_scout.__file__).resolve().is_relative_to(src):
        print(f"Imported web_scout from {web_scout.__file__}, not from {src}.", file=sys.stderr)
        return 2
    # run_web_research calls configure_logging() at INFO, which would hide the DEBUG [extract] traces.
    configure_logging = web_scout.configure_logging
    web_scout.configure_logging = lambda level=logging.DEBUG: configure_logging(logging.DEBUG)

    # litellm is imported after web_scout so the package's import-time litellm settings apply first.
    import litellm
    from litellm.integrations.custom_logger import CustomLogger

    call_ids: set[str] = set()

    class _CallCounter(CustomLogger):
        def log_success_event(self, kwargs, response_obj, start_time, end_time):
            call_ids.add(kwargs.get("litellm_call_id") or str(id(response_obj)))

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            call_ids.add(kwargs.get("litellm_call_id") or str(id(response_obj)))

    litellm.callbacks = [*litellm.callbacks, _CallCounter()]

    outcome: dict = {"web_scout_file": web_scout.__file__, "result": None, "error": None}
    started = time.perf_counter()
    try:
        import asyncio

        result = asyncio.run(web_scout.run_web_research(query))
        outcome["result"] = result.model_dump(mode="json")
    except Exception as exc:
        outcome["error"] = f"{exc}\n{traceback.format_exc()}"
        traceback.print_exc()
    outcome["wall_time_s"] = round(time.perf_counter() - started, 1)
    # Every run makes model calls, so zero means the callback did not see them, not that none happened.
    if call_ids:
        outcome["model_calls"] = len(call_ids)
        outcome["model_calls_note"] = "successful litellm calls, counted by a success callback"
    else:
        outcome["model_calls"] = "unavailable"
        outcome["model_calls_note"] = "the litellm success callback saw no calls on this code path"
    result_path.write_text(json.dumps(outcome, ensure_ascii=False), encoding="utf-8")
    return 0 if outcome["error"] is None else 1


def write_report(
    directory: Path,
    env_file: Path,
    judge: bool = True,
    pairwise: bool = False,
    reference: dict | None = None,
) -> int:
    """Judge every run record in ``directory`` and write ``report.json`` and ``report.md`` there.

    With ``pairwise``, ``report.json`` gets ``preference``: new against old by ``judge_pair`` when both have an
    answer (``{"winner": "new" | "old" | "tie", "reasons": [...]}``, ``{"error": ...}`` on a judge exception),
    else None. ``reference`` is a ``load_reference`` result; it is stored as ``reference`` with a ``judgment``
    from the same ``judge_record`` call and page cache as the runs. With ``judge`` false, nothing is judged or
    data-checked and no model or network call is made.
    """
    from dotenv import load_dotenv

    load_dotenv(env_file)
    if judge and not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set. The eval judge uses Gemini.", file=sys.stderr)
        return 2
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    import httpx

    from eval.fetch import USER_AGENT, fetch_page
    from eval.judge import judge_pair, judge_record
    from eval.metrics import markdown_link_urls

    records = _load_records(directory)
    if not records:
        print(f"No run records in {directory}.", file=sys.stderr)
        return 2
    today = date.today().isoformat()
    pages_dir = directory / "pages"
    raw_bodies: dict[str, tuple[str, str]] = {}

    def get_page(url: str) -> dict:
        return fetch_page(url, pages_dir)

    def raw_body(url: str) -> tuple[str, str]:
        if url not in raw_bodies:
            try:
                response = httpx.get(
                    url, follow_redirects=True, timeout=RAW_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}
                )
                response.raise_for_status()
                raw_bodies[url] = (response.text, response.headers.get("content-type", ""))
            except Exception as exc:
                raw_bodies[url] = ("", f"error: {type(exc).__name__}: {exc}")
        return raw_bodies[url]

    runs = {}
    for label, record in records.items():
        answer = (record.get("result") or {}).get("synthesis") or ""
        cited = markdown_link_urls(answer)
        run = {
            "source_revision": record.get("source_revision"),
            "error": record.get("error"),
            "wall_time_s": record.get("wall_time_s"),
            "model_calls": record.get("model_calls"),
            "model_calls_note": record.get("model_calls_note"),
            "buckets": record.get("buckets") or {},
            "log_counters": record.get("log_counters") or {},
            "log_urls": record.get("log_urls") or {},
            "answer": answer,
            "cited_urls": cited,
            "judgment": None,
        }
        if judge and answer.strip():
            print(f"Judging {label}...", flush=True)
            try:
                run["judgment"] = judge_record(
                    query=record["query"],
                    record={"answer": answer, "cited_urls": cited},
                    today=today,
                    get_page=get_page,
                )
            except Exception as exc:
                run["judgment"] = {"error": f"{exc}\n{traceback.format_exc()}"}
            for claim in run["judgment"].get("claims", []):
                try:
                    claim["data_check"] = _data_check(claim, get_page, raw_body)
                except Exception as exc:
                    # Kept per claim so a data-check failure never discards the finished judgment.
                    claim["data_check"] = {"urls": [], "figures": {}, "errors": [f"{type(exc).__name__}: {exc}"]}
        runs[label] = run

    preference = None
    old_answer, new_answer = ((runs.get(label) or {}).get("answer", "").strip() for label in LABEL_ORDER)
    if pairwise and judge and old_answer and new_answer:
        print("Judging new against old...", flush=True)
        try:
            winner, reasons = judge_pair(
                query=records["new"]["query"], answer=new_answer, baseline_answer=old_answer, today=today
            )
            preference = {"winner": {"system": "new", "baseline": "old"}.get(winner, "tie"), "reasons": reasons}
        except Exception as exc:
            preference = {"error": f"{exc}\n{traceback.format_exc()}"}
    if reference is not None:
        reference = {**reference, "judgment": None}
        if judge and reference["answer"].strip():
            print(f"Judging reference from {reference['run']}...", flush=True)
            try:
                # The run records' query, so all three answers are judged against the same question text.
                reference["judgment"] = judge_record(
                    query=next(iter(records.values()))["query"],
                    record={"answer": reference["answer"], "cited_urls": reference["cited_urls"]},
                    today=today,
                    get_page=get_page,
                )
            except Exception as exc:
                reference["judgment"] = {"error": f"{exc}\n{traceback.format_exc()}"}

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question_id": next(iter(records.values())).get("question_id"),
        "query": next(iter(records.values())).get("query"),
        "eval_sha256": eval_files_sha256(),
        "run_eval_sha256": {label: record.get("eval_sha256") for label, record in records.items()},
        "runs": runs,
        "preference": preference,
        "reference": reference,
    }
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "report.md").write_text(format_report(report), encoding="utf-8")
    print(directory / "report.md")
    return 0


def load_reference(reference_dir: Path, question_id: str) -> dict | None:
    """Return the first non-error OpenAI record of ``<reference_dir>/<question_id>.json``, or None.

    The file is a saved eval result (top-level ``query``, ``runs.openai`` records with ``answer``,
    ``cited_urls`` and ``error``); it is only read. A missing file or no non-error record gives None.
    """
    path = reference_dir / f"{question_id}.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    for record in (data.get("runs") or {}).get("openai") or []:
        if not record.get("error"):
            return {
                "run": reference_dir.name,
                "query": data.get("query"),
                "answer": record.get("answer") or "",
                "cited_urls": record.get("cited_urls") or [],
            }
    return None


def _load_records(directory: Path) -> dict[str, dict]:
    records = {}
    for path in sorted(directory.glob("*.json")):
        if path.name == "report.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "label" in data and "result" in data:
            records[data["label"]] = data
    order = {label: index for index, label in enumerate(LABEL_ORDER)}
    return dict(sorted(records.items(), key=lambda item: (order.get(item[0], len(order)), item[0])))


def _data_check(claim: dict, get_page, raw_body) -> dict | None:
    """Check a claim's figures against the raw bodies of its cited JSON or CSV files, or None if it cites none.

    The eval fetcher rejects JSON, so the judge cannot read these files; a figure passes if any cited data
    file stores it.
    """
    figures = claim_figures(claim["claim"])
    checked_urls, errors = [], []
    passed = dict.fromkeys(figures, False)
    for url in claim["urls"]:
        page = get_page(url)
        page_error = (page.get("error") or "").lower()
        is_data = url.lower().split("?")[0].endswith(DATA_SUFFIXES) or "json" in page_error or "csv" in page_error
        if not is_data:
            continue
        body, content_type = raw_body(url)
        if content_type.startswith("error:"):
            errors.append(f"{url}: {content_type}")
            continue
        checked_urls.append(url)
        for figure, ok in check_figures_in_body(figures, body).items():
            passed[figure] = passed[figure] or ok
    if not checked_urls and not errors:
        return None
    return {"urls": checked_urls, "figures": passed if checked_urls else {}, "errors": errors}


def format_report(report: dict) -> str:
    """Render ``report.json`` content as markdown: a side-by-side metrics table, then one section per run."""
    runs = report["runs"]
    labels = list(runs)
    lines = [
        f"# Web Scout old versus new: {report['question_id']}",
        "",
        f"Question: {report['query']}",
        "",
        f"Generated {report['generated_at']}. Eval files SHA-256: "
        + ", ".join(f"`{name}` {digest[:12]}" for name, digest in report["eval_sha256"].items()),
        "",
        "| Measure | " + " | ".join(labels) + " |",
        "|---|" + "---|" * len(labels),
    ]
    for name, values in _metric_rows(runs):
        lines.append(f"| {name} | " + " | ".join(str(value) for value in values) + " |")
    for label, run in runs.items():
        lines += ["", f"## {label}", "", f"Source: {run['source_revision']}", ""]
        if run.get("model_calls_note"):
            lines += [f"Model calls: {run['model_calls']} ({run['model_calls_note']})", ""]
        if run.get("error"):
            lines += ["Error:", "", "```", run["error"].strip(), "```", ""]
        lines += ["### Answer", "", run["answer"].strip() or "(none)", "", "### Citations", ""]
        lines += [f"- {url}" for url in run["cited_urls"]] or ["(none)"]
        lines += ["", "### Followed links", ""]
        lines += [f"- {url}" for url in run["log_urls"].get("followup_links", [])] or ["(none)"]
        lines += ["", "### Data sources opened", ""]
        lines += [f"- {url}" for url in run["log_urls"].get("data_sources_opened", [])] or ["(none)"]
        lines += ["", "### Claims", ""]
        judgment = run.get("judgment") or {}
        if judgment.get("error"):
            lines += ["Judge error:", "", "```", judgment["error"].strip(), "```"]
        elif judgment:
            completeness = judgment["completeness"]
            lines += [f"Completeness {completeness['score']}/5: {completeness['reason']}", ""]
            lines += [
                "A data-file figure marked found appears somewhere in the cited JSON or CSV file; "
                "it was not checked against the field the claim describes.",
                "",
            ]
            lines += ["| # | Verdict | Data figures | Claim | Checked URL | Reason |", "|---|---|---|---|---|---|"]
            for number, claim in enumerate(judgment["claims"], start=1):
                lines.append(
                    f"| {number} | {claim['verdict']} | {_data_cell(claim.get('data_check'))} | "
                    f"{_cell(claim['claim'])} | {claim['checked_url']} | {_cell(claim['reason'])} |"
                )
        else:
            lines.append("(not judged)" if run["answer"].strip() else "(not judged: no answer)")
    return "\n".join(lines) + "\n"


def _metric_rows(runs: dict) -> list[tuple[str, list]]:
    buckets = [bucket for bucket in URL_BUCKETS if any(bucket in run["buckets"] for run in runs.values())]
    counters = (
        "followup_waves",
        "followup_links",
        "data_sources_opened",
        "data_quotes_recorded",
        "data_quotes_dropped",
    )
    rows = [
        ("Wall time (s)", [run["wall_time_s"] for run in runs.values()]),
        ("Model calls", [run["model_calls"] for run in runs.values()]),
        ("Cited URLs", [len(run["cited_urls"]) for run in runs.values()]),
    ]
    rows += [(f"URLs {bucket}", [run["buckets"].get(bucket, 0) for run in runs.values()]) for bucket in buckets]
    rows += [(name.replace("_", " "), [run["log_counters"].get(name, 0) for run in runs.values()]) for name in counters]
    verdicts = ("supported", "not_supported", "contradicted", "unfetchable", "uncited", "judge_error")
    claims = [((run.get("judgment") or {}).get("claims") or []) for run in runs.values()]
    rows.append(("Claims", [len(items) for items in claims]))
    for verdict in verdicts:
        rows.append((f"Claims {verdict}", [sum(claim["verdict"] == verdict for claim in items) for items in claims]))
    rows.append(("Data-file figures found / checked", [_data_totals(items) for items in claims]))
    rows.append(
        (
            "Completeness",
            [((run.get("judgment") or {}).get("completeness") or {}).get("score", "-") for run in runs.values()],
        )
    )
    return rows


def report_set(
    directory: Path, env_file: Path, questions_path: Path, judge: bool, reference_dir: Path | None = None
) -> int:
    """Write ``report`` in every question directory under ``directory``, then ``summary.md`` there.

    Rows follow run order (eval questions, then ``questions_path``). ``kind`` comes from eval for eval ids,
    ``expected`` from ``questions_path``. A question whose report fails is listed under "Report errors".
    Each report includes the new-versus-old preference; with ``reference_dir`` (a saved eval result
    directory, only read), also the judged OpenAI reference answer for each question found there.
    """
    from_file = {question["id"]: question for question in json.loads(questions_path.read_text(encoding="utf-8"))}
    questions = {
        question["id"]: {**from_file.get(question["id"], {}), **question}
        for question, _ in set_questions(questions_path, include_eval=True)
    }
    order = {question_id: index for index, question_id in enumerate(questions)}
    directories = sorted(
        (path for path in directory.iterdir() if path.is_dir() and _load_records(path)),
        key=lambda path: (order.get(path.name, len(order)), path.name),
    )
    if not directories:
        print(f"No question directories with run records in {directory}.", file=sys.stderr)
        return 2
    rows = []
    for question_dir in directories:
        print(f"[report-set] {question_dir.name}", flush=True)
        question = questions.get(question_dir.name, {"id": question_dir.name})
        try:
            reference = load_reference(reference_dir, question_dir.name) if reference_dir else None
            status = write_report(question_dir, env_file, judge=judge, pairwise=True, reference=reference)
            error = f"write_report exited with status {status}; see its output above" if status else None
        except Exception as exc:
            traceback.print_exc()
            error = f"{type(exc).__name__}: {exc}"
        if error:
            rows.append(
                {
                    "id": question["id"],
                    "kind": question.get("kind", "-"),
                    "runs": dict.fromkeys(LABEL_ORDER),
                    "regression": [],
                    "judge_errors": [],
                    "preference": None,
                    "reference": None,
                    "report_error": error,
                }
            )
            continue
        report = json.loads((question_dir / "report.json").read_text(encoding="utf-8"))
        rows.append(summary_row(question, report, _load_records(question_dir)))
    summary = format_summary(rows, reference_run=reference_dir.name if reference_dir else None)
    (directory / "summary.md").write_text(summary, encoding="utf-8")
    print(directory / "summary.md")
    return 0


def summary_row(question: dict, report: dict, records: dict[str, dict]) -> dict:
    """Summarise one question's old and new runs for ``summary.md``; a missing run is None."""
    facts = (question.get("expected") or {}).get("facts")
    runs: dict[str, dict | None] = {}
    for label in LABEL_ORDER:
        record, run = records.get(label), report["runs"].get(label)
        if record is None or run is None:
            runs[label] = None
            continue
        judgment = run.get("judgment") or {}
        counters = record.get("log_counters") or {}
        runs[label] = {
            "run_error": bool(record.get("error")),
            "judge_error": bool(judgment.get("error")),
            "completeness": (judgment.get("completeness") or {}).get("score"),
            "verdicts": [claim["verdict"] for claim in judgment.get("claims") or []],
            "cited_urls": len(run["cited_urls"]),
            **pdf_counters(record.get("result") or {}),
            "data_sources": data_sources_distinct(record),
            "model_calls": record.get("model_calls"),
            "followup_waves": counters.get("followup_waves", 0),
            "followup_links": counters.get("followup_links", 0),
            "wall_time_s": record.get("wall_time_s"),
            "expected": expected_matches(facts, run["answer"]) if facts else None,
        }
    old, new = runs["old"], runs["new"]
    preference = report.get("preference")
    reference = report.get("reference")
    judgment = (reference or {}).get("judgment") or {}
    return {
        "id": question["id"],
        "kind": question.get("kind", "-"),
        "runs": runs,
        "regression": regression_reasons(old, new) if old and new else [],
        "judge_errors": [label for label, run in runs.items() if run and run["judge_error"]],
        "preference": None if preference is None else preference.get("winner", "pairwise error"),
        "reference": None
        if reference is None
        else {
            "judge_error": bool(judgment.get("error")),
            "completeness": (judgment.get("completeness") or {}).get("score"),
            "verdicts": [claim["verdict"] for claim in judgment.get("claims") or []],
        },
    }


def format_summary(rows: list[dict], reference_run: str | None = None) -> str:
    """Render summary rows as markdown: one table row per question, totals, then regressions and errors.

    With ``reference_run`` (the reference directory's name), two OpenAI reference columns are added.
    """

    def pair(runs, key, fmt=str) -> str:
        return " / ".join("-" if run is None or run[key] is None else fmt(run[key]) for run in runs)

    def pdfs(run: dict | None, prefix: str) -> str:
        return "-" if run is None else "/".join(str(run[key]) for key in PDF_SUMMARY_KEYS[prefix])

    def rate(verdicts: list[str]) -> str:
        value = _supported_rate(verdicts)
        return "-" if value is None else f"{value:.0f}%"

    def seconds(value: float) -> str:
        return f"{value:.0f}"

    def timed(run: dict | None) -> bool:
        return run is not None and not run["run_error"] and run["wall_time_s"] is not None

    lines = [
        "# Web Scout old versus new: question set",
        "",
        "Cells read old / new. PDF cells are found/scraped/failed/irrelevant distinct URLs: found = any bucket, "
        "failed = scrape_failed + source_http_error + bot_detected, irrelevant = scraped_irrelevant. PDF counts "
        "URLs whose path ends in .pdf. PDF-like counts other URLs whose path contains "
        + " or ".join(f"`{pattern}`" for pattern in PDF_LIKE_PATH_PATTERNS)
        + " (covers `/bitstreams/` and `/article/download/`) or whose query contains "
        + " or ".join(f"`{pattern}`" for pattern in PDF_LIKE_QUERY_PATTERNS)
        + "; "
        "a PDF at any other URL is in neither count. Expected = expected figures (3 or more digits, or with a "
        "decimal) found in the answer / expected figures, new then old. Model calls and follow-up counts come "
        "from each run record. Wall times in whole seconds.",
        "",
        "Regression: a run error on either side (no other check then); otherwise new completeness lower (or "
        f"missing while old has one), supported rate more than {SUPPORTED_RATE_TOLERANCE} points lower, or fewer "
        "PDFs scraped when PDF plus PDF-like scraped also dropped. A judge error on either side skips the "
        "completeness and rate checks and is listed under Not judged instead.",
        "",
        "Preference: the eval's pairwise judge compares the new answer with the old one twice, order swapped; "
        "new or old only when both orders agree, else tie. pairwise error = the judge failed (not a loss); "
        "- = not judged or a side has no answer. The preference is not part of the regression flag, which is "
        "unchanged.",
    ]
    if reference_run:
        lines += [
            "",
            f"OpenAI (reference, run {reference_run}): the first non-error OpenAI answer saved by that eval run, "
            "judged here with the same judge and page cache as old and new. It comes from a different run on a "
            "different day, is shown as a reference point only, and is not part of the regression decision. "
            "- = no reference answer for the question.",
        ]
    reference_header = (
        f" OpenAI completeness (reference, run {reference_run}) | OpenAI supported rate (reference, run "
        f"{reference_run}) |"
        if reference_run
        else ""
    )
    lines += [
        "",
        "| id | kind | completeness | supported rate | cited URLs | PDF old | PDF new | PDF-like old | PDF-like new "
        "| new data sources | model calls | follow-up waves | follow-up links | wall s | expected new / old "
        "| regression | preference |" + reference_header,
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|" + ("---|---|" if reference_run else ""),
    ]
    for row in rows:
        runs = [row["runs"]["old"], row["runs"]["new"]]
        new = row["runs"]["new"]
        expected = " / ".join(
            "-" if run is None or run["expected"] is None else "{}/{}".format(*run["expected"]) for run in runs[::-1]
        )
        reference = row.get("reference")
        reference_cells = ""
        if reference_run:
            if reference is None or (reference["completeness"] is None and not reference["judge_error"]):
                reference_cells = " - | - |"
            elif reference["judge_error"]:
                reference_cells = " judge error | judge error |"
            else:
                reference_cells = f" {reference['completeness']} | {rate(reference['verdicts'])} |"
        lines.append(
            f"| {row['id']} | {row['kind']} | {pair(runs, 'completeness')} | {pair(runs, 'verdicts', rate)} | "
            f"{pair(runs, 'cited_urls')} | {pdfs(runs[0], 'pdf')} | {pdfs(runs[1], 'pdf')} | "
            f"{pdfs(runs[0], 'pdf_like')} | {pdfs(runs[1], 'pdf_like')} | "
            f"{'-' if new is None else new['data_sources']} | {pair(runs, 'model_calls')} | "
            f"{pair(runs, 'followup_waves')} | {pair(runs, 'followup_links')} | "
            f"{pair(runs, 'wall_time_s', seconds)} | {expected} | {'yes' if row['regression'] else ''} | "
            f"{row.get('preference') or '-'} |" + reference_cells
        )
    lines += ["", "## Totals", ""]
    lines.append(f"Questions: {len(rows)}. Flagged regressions: {sum(bool(row['regression']) for row in rows)}.")
    preferences = [row.get("preference") for row in rows]
    if any(preferences):
        lines += [
            "",
            f"Head-to-head: new {preferences.count('new')} wins, {preferences.count('tie')} ties, "
            f"{preferences.count('old')} losses. Pairwise errors: {preferences.count('pairwise error')}.",
        ]
    else:
        lines += ["", "Head-to-head: not judged."]
    lines += ["", "| Measure | old | new |", "|---|---|---|"]
    keys = ("cited_urls", "data_sources", "followup_waves", "followup_links", *PDF_SUMMARY_KEYS["pdf"])
    for key in keys + PDF_SUMMARY_KEYS["pdf_like"]:
        totals = [sum(row["runs"][label][key] for row in rows if row["runs"][label]) for label in LABEL_ORDER]
        lines.append(f"| {key.replace('_', ' ')} | {totals[0]} | {totals[1]} |")
    calls = [[row["runs"][label]["model_calls"] for row in rows if row["runs"][label]] for label in LABEL_ORDER]
    lines.append(
        "| model calls, runs with a count | "
        + " | ".join(str(sum(value for value in values if isinstance(value, int))) for values in calls)
        + " |"
    )
    walls = [
        sum(row["runs"][label]["wall_time_s"] for row in rows if timed(row["runs"][label])) for label in LABEL_ORDER
    ]
    lines.append(f"| wall time (s), runs without error | {seconds(walls[0])} | {seconds(walls[1])} |")
    both = [row for row in rows if timed(row["runs"]["old"]) and timed(row["runs"]["new"])]
    medians = [
        seconds(statistics.median(row["runs"][label]["wall_time_s"] for row in both)) if both else "-"
        for label in LABEL_ORDER
    ]
    lines.append(f"| median wall time (s), questions where both ran | {medians[0]} | {medians[1]} |")
    expected = [
        [row["runs"][label]["expected"] for row in rows if row["runs"][label] and row["runs"][label]["expected"]]
        for label in LABEL_ORDER
    ]
    lines.append(
        "| expected figures found | "
        + " | ".join(f"{sum(found for found, _ in pairs)}/{sum(total for _, total in pairs)}" for pairs in expected)
        + " |"
    )
    excluded = [
        sum(row["runs"][label] is not None and not timed(row["runs"][label]) for row in rows) for label in LABEL_ORDER
    ]
    lines += ["", f"Excluded from wall-time totals: old {excluded[0]}, new {excluded[1]}."]
    lines += ["", "## Regressions", ""]
    lines += [f"- {row['id']}: {'; '.join(row['regression'])}" for row in rows if row["regression"]] or ["(none)"]
    judge_errors = [f"- {row['id']} {label}" for row in rows for label in row["judge_errors"]]
    lines += ["", "## Not judged (judge error)", ""] + (judge_errors or ["(none)"])
    run_errors = [
        f"- {row['id']} {label}" for row in rows for label in LABEL_ORDER if (row["runs"][label] or {}).get("run_error")
    ]
    lines += ["", "## Run errors", ""] + (run_errors or ["(none)"])
    report_errors = [f"- {row['id']}: {row['report_error']}" for row in rows if row.get("report_error")]
    lines += ["", "## Report errors", ""] + (report_errors or ["(none)"])
    missing = [
        f"- {row['id']} {label}"
        for row in rows
        if not row.get("report_error")
        for label in LABEL_ORDER
        if row["runs"][label] is None
    ]
    lines += ["", "## Missing runs", ""] + (missing or ["(none)"])
    return "\n".join(lines) + "\n"


def _data_totals(claims: list[dict]) -> str:
    figures = [ok for claim in claims for ok in ((claim.get("data_check") or {}).get("figures") or {}).values()]
    return f"{sum(figures)} / {len(figures)}"


def _data_cell(check: dict | None) -> str:
    if not check:
        return ""
    figures = ", ".join(f"{figure} {'found' if ok else 'missing'}" for figure, ok in check["figures"].items())
    return _cell("; ".join(filter(None, [figures, *check["errors"]])) or "no figures")


def _cell(text: str) -> str:
    return " ".join((text or "").split()).replace("|", "\\|")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two Web Scout versions on eval or set questions.")
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export-old", help="Extract src/ of an older commit with git archive.")
    export.add_argument("--commit", required=True)
    export.add_argument("--dest", type=Path, required=True)

    run = commands.add_parser("run", help="Run one research call with the given src/ first on sys.path.")
    run.add_argument("--src", type=Path, required=True, help="Directory containing the web_scout package.")
    run.add_argument("--label", required=True, help="Run label, e.g. old or new.")
    run.add_argument("--question-id", required=True, help="Question id from the questions file.")
    run.add_argument("--questions", type=Path, default=QUESTIONS_PATH, help="Questions file (default: eval's).")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    run.add_argument("--timeout", type=float, default=1800, help="Seconds before the child is killed.")

    run_many = commands.add_parser("run-set", help="Run old then new on every question of a set, sequentially.")
    run_many.add_argument("--questions", type=Path, required=True, help="Questions file, e.g. compare_questions.json.")
    run_many.add_argument("--include-eval", action="store_true", help="Run every eval question first.")
    run_many.add_argument("--old-src", type=Path, required=True)
    run_many.add_argument("--new-src", type=Path, required=True)
    run_many.add_argument("--out", type=Path, required=True, help="Writes <out>/<id>/old.json, new.json and logs.")
    run_many.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    run_many.add_argument("--timeout", type=float, default=1800, help="Seconds before each child is killed.")

    report = commands.add_parser("report", help="Judge the runs in a directory and write report.md/json.")
    report.add_argument("--dir", type=Path, required=True)
    report.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)

    report_many = commands.add_parser("report-set", help="Report every question directory and write summary.md.")
    report_many.add_argument("--dir", type=Path, required=True)
    report_many.add_argument("--questions", type=Path, default=SET_QUESTIONS_PATH, help="For kind and expected.")
    report_many.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    report_many.add_argument("--no-judge", action="store_true", help="Skip the judge and data checks (no calls).")
    report_many.add_argument(
        "--reference-dir",
        type=Path,
        help="Saved eval result directory (<id>.json); its first non-error OpenAI answer is judged as a reference. "
        "Only read.",
    )

    child = commands.add_parser("_child", help=argparse.SUPPRESS)
    child.add_argument("--src", type=Path, required=True)
    child.add_argument("--query", required=True)
    child.add_argument("--env-file", type=Path, required=True)
    child.add_argument("--result", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "export-old":
        return export_old(args.commit, args.dest)
    if args.command == "run":
        return run_version(
            src=args.src,
            label=args.label,
            question_id=args.question_id,
            out=args.out,
            env_file=args.env_file,
            timeout_s=args.timeout,
            questions_path=args.questions,
        )
    if args.command == "run-set":
        return run_set(
            questions_path=args.questions,
            include_eval=args.include_eval,
            old_src=args.old_src,
            new_src=args.new_src,
            out=args.out,
            env_file=args.env_file,
            timeout_s=args.timeout,
        )
    if args.command == "report":
        return write_report(args.dir, args.env_file)
    if args.command == "report-set":
        return report_set(
            args.dir, args.env_file, args.questions, judge=not args.no_judge, reference_dir=args.reference_dir
        )
    return child_main(src=args.src, query=args.query, env_file=args.env_file, result_path=args.result)


if __name__ == "__main__":
    raise SystemExit(main())
