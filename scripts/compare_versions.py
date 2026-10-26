"""Run two Web Scout versions on the same eval question and report the difference.

Subcommands:
    export-old  Extract ``src/`` of an older commit with ``git archive`` (no branch or worktree is created).
    run         Run one research call with a given ``src/`` first on ``sys.path``, in a fresh process with a
                fresh empty result cache, and save the run record as ``<out>/<label>.json``.
    report      Judge every run record in a directory with ``eval/judge.py`` and write ``report.md`` and
                ``report.json``.

Usage:
    python scripts/compare_versions.py export-old --commit cb36b2b --dest /tmp/ws_compare/old
    python scripts/compare_versions.py run --src /tmp/ws_compare/old/src --label old --question-id brazil \
        --out /tmp/ws_compare/brazil
    python scripts/compare_versions.py run --src "$PWD/src" --label new --question-id brazil \
        --out /tmp/ws_compare/brazil
    python scripts/compare_versions.py report --dir /tmp/ws_compare/brazil
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = REPO_ROOT / "eval" / "questions.json"
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


def load_question(question_id: str) -> dict:
    """Return the eval question with this id from ``eval/questions.json``."""
    for question in json.loads(QUESTIONS_PATH.read_text(encoding="utf-8")):
        if question["id"] == question_id:
            return question
    raise SystemExit(f"Unknown question id {question_id!r} in {QUESTIONS_PATH}.")


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


def run_version(*, src: Path, label: str, question_id: str, out: Path, env_file: Path, timeout_s: float) -> int:
    """Run one research call in a child process and write ``<out>/<label>.json`` and ``<out>/<label>.log``.

    A child still running after ``timeout_s`` is killed with its process group and recorded as an error.
    """
    question = load_question(question_id)
    src = src.resolve()
    if not (src / "web_scout" / "__init__.py").is_file():
        print(f"{src} does not contain web_scout/__init__.py.", file=sys.stderr)
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


def write_report(directory: Path, env_file: Path) -> int:
    """Judge every run record in ``directory`` and write ``report.json`` and ``report.md`` there."""
    from dotenv import load_dotenv

    load_dotenv(env_file)
    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set. The eval judge uses Gemini.", file=sys.stderr)
        return 2
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    import httpx

    from eval.fetch import USER_AGENT, fetch_page
    from eval.judge import judge_record
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
        if answer.strip():
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

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question_id": next(iter(records.values())).get("question_id"),
        "query": next(iter(records.values())).get("query"),
        "eval_sha256": eval_files_sha256(),
        "run_eval_sha256": {label: record.get("eval_sha256") for label, record in records.items()},
        "runs": runs,
    }
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "report.md").write_text(format_report(report), encoding="utf-8")
    print(directory / "report.md")
    return 0


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
            lines.append("(not judged: no answer)")
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
    parser = argparse.ArgumentParser(description="Compare two Web Scout versions on one eval question.")
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export-old", help="Extract src/ of an older commit with git archive.")
    export.add_argument("--commit", required=True)
    export.add_argument("--dest", type=Path, required=True)

    run = commands.add_parser("run", help="Run one research call with the given src/ first on sys.path.")
    run.add_argument("--src", type=Path, required=True, help="Directory containing the web_scout package.")
    run.add_argument("--label", required=True, help="Run label, e.g. old or new.")
    run.add_argument("--question-id", required=True, help="Question id from eval/questions.json.")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    run.add_argument("--timeout", type=float, default=1800, help="Seconds before the child is killed.")

    report = commands.add_parser("report", help="Judge the runs in a directory and write report.md/json.")
    report.add_argument("--dir", type=Path, required=True)
    report.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)

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
        )
    if args.command == "report":
        return write_report(args.dir, args.env_file)
    return child_main(src=args.src, query=args.query, env_file=args.env_file, result_path=args.result)


if __name__ == "__main__":
    raise SystemExit(main())
