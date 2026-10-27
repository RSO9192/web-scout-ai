"""Compare Web Scout with snippet-based OpenAI web search on deep and control questions.

Web Scout runs on its default GPT-6 Luna pipeline. The baseline is one
Responses API call to gpt-6-astra at high reasoning effort, with the built-in
web_search tool at high search context and an unlimited return-token budget,
authenticated by OPENAI_API_KEY.

Each question runs on every system ``--repeats`` times. Every page an answer
cites is fetched by ``eval/fetch.py``, and ``eval/judge.py`` checks each claim
against that page, scores completeness blind, and compares the answers
pairwise with the order swapped. The judge uses GEMINI_API_KEY.

Usage:
    python eval/compare_search.py
    python eval/compare_search.py --only guatemala --repeats 1
    python eval/compare_search.py --judge-dir eval/results/2026-10-26
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.fetch import fetch_page  # noqa: E402
from eval.judge import judge_pair, judge_record  # noqa: E402
from eval.metrics import record_error, record_openai_response, record_web_scout  # noqa: E402
from eval.report import format_report  # noqa: E402

OPENAI_MODEL = "gpt-6-astra"
REASONING_EFFORT = "high"
QUESTIONS_PATH = REPO_ROOT / "eval" / "questions.json"
QUESTION_KINDS = {"deep", "control"}
LABELS = {"web_scout": "Web Scout", "openai": "OpenAI web search"}
# (system, baseline) pairs for the pairwise preference. A new baseline adds one pair.
SYSTEM_PAIRS = [("web_scout", "openai")]


def load_questions(path: Path) -> list[dict]:
    """Load and validate the question list."""
    questions = json.loads(Path(path).read_text(encoding="utf-8"))
    seen = set()
    for question in questions:
        missing = {"id", "title", "kind", "query"} - set(question)
        if missing:
            raise ValueError(f"Question {question.get('id', '?')} is missing {', '.join(sorted(missing))}.")
        if question["kind"] not in QUESTION_KINDS:
            raise ValueError(f"Question {question['id']} has kind {question['kind']!r}; use deep or control.")
        if question["id"] in seen:
            raise ValueError(f"Question id {question['id']} is a duplicate.")
        seen.add(question["id"])
    return questions


async def run_web_scout(query: str) -> dict:
    """Run Web Scout with its defaults and return a record."""
    try:
        from web_scout import run_web_research

        result = await run_web_research(query)
    except Exception as exc:
        return record_error(f"{exc}\n{traceback.format_exc()}")
    return record_web_scout(result.model_dump(mode="json"))


async def run_openai(query: str) -> dict:
    """Run one OpenAI Responses call with web search and return a record."""
    return await asyncio.to_thread(_call_openai, query)


SYSTEMS = {"web_scout": run_web_scout, "openai": run_openai}


def score_row(row: dict, *, today: str, pages_dir: Path) -> dict:
    """Attach a judgment to every non-error run and the pairwise verdicts per run index."""
    pages: dict[str, dict] = {}

    # Transient failures are not cached on disk, so without this a slow server is retried once per claim.
    def get_page(url: str) -> dict:
        if url not in pages:
            pages[url] = fetch_page(url, pages_dir)
        return pages[url]

    for records in row["runs"].values():
        for record in records:
            if record.get("error"):
                continue
            try:
                record["judgment"] = judge_record(
                    query=row["query"],
                    record=record,
                    today=today,
                    get_page=get_page,
                )
            except Exception as exc:
                record["judgment"] = {"error": f"{exc}\n{traceback.format_exc()}"}

    pairwise = []
    for system, baseline in SYSTEM_PAIRS:
        system_runs = row["runs"].get(system) or []
        baseline_runs = row["runs"].get(baseline) or []
        for index, (record, baseline_record) in enumerate(zip(system_runs, baseline_runs)):
            if record.get("error") or baseline_record.get("error"):
                continue
            try:
                winner, reasons = judge_pair(
                    query=row["query"],
                    answer=record["answer"],
                    baseline_answer=baseline_record["answer"],
                    today=today,
                )
            except Exception as exc:
                print(f"Pairwise judge failed for {row['id']} run {index}: {exc}", file=sys.stderr)
                continue
            pairwise.append(
                {
                    "run": index,
                    "system": system,
                    "baseline": baseline,
                    "winner": {"system": system, "baseline": baseline}.get(winner, "tie"),
                    "reasons": reasons,
                }
            )
    row["pairwise"] = pairwise
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare Web Scout with OpenAI web search.")
    parser.add_argument("--only", action="append", default=[], help="Question id to run. Repeatable.")
    parser.add_argument("--systems", action="append", default=[], help="System to run. Repeatable. Default: all.")
    parser.add_argument("--repeats", type=int, default=2, help="Runs per question and system.")
    parser.add_argument("--output", type=Path, default=None, help="Directory for the report and JSON.")
    parser.add_argument("--skip-judge", action="store_true")
    parser.add_argument(
        "--judge-dir",
        type=Path,
        default=None,
        help="Score the runs already saved in this directory instead of running the research again.",
    )
    args = parser.parse_args(argv)

    _load_env_file(REPO_ROOT / ".env")
    questions = load_questions(QUESTIONS_PATH)
    unknown = sorted(set(args.only) - {question["id"] for question in questions})
    unknown_systems = sorted(set(args.systems) - set(SYSTEMS))
    if unknown or unknown_systems:
        print(f"Unknown question or system id(s): {', '.join(unknown + unknown_systems)}", file=sys.stderr)
        return 2
    selected = [question for question in questions if not args.only or question["id"] in args.only]
    systems = args.systems or list(SYSTEMS)
    today = date.today().isoformat()

    if (args.judge_dir or not args.skip_judge) and not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set. The judge uses Gemini.", file=sys.stderr)
        return 2
    if args.judge_dir:
        rows = _judge_saved(args.judge_dir, selected, today=today)
        present = [system for system in SYSTEMS if any(system in row["runs"] for row in rows)]
        _write_report(args.judge_dir, rows, today, present)
        return 0
    if "openai" in systems and not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set. The baseline uses the native OpenAI key.", file=sys.stderr)
        return 2

    output_dir = args.output or (REPO_ROOT / "eval" / "results" / today)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = asyncio.run(
        _run_all(selected, systems=systems, repeats=args.repeats, skip_judge=args.skip_judge, output_dir=output_dir)
    )
    _write_report(output_dir, rows, today, systems)
    return 0


async def _run_all(
    questions: list[dict], *, systems: list[str], repeats: int, skip_judge: bool, output_dir: Path
) -> list[dict]:
    rows = []
    for question in questions:
        row = {**question, "runs": {system: [] for system in systems}, "pairwise": []}
        for repeat in range(repeats):
            for system in systems:
                print(f"Running {question['id']} on {system}, run {repeat + 1}/{repeats}...", flush=True)
                row["runs"][system].append(await SYSTEMS[system](question["query"]))
                # Written after every run so a crash keeps the finished runs.
                _write_row(output_dir, row)
        if not skip_judge:
            print(f"Judging {question['id']}...", flush=True)
            row = await asyncio.to_thread(
                score_row, row, today=date.today().isoformat(), pages_dir=output_dir / "pages"
            )
            _write_row(output_dir, row)
        rows.append(row)
    return rows


def _judge_saved(output_dir: Path, questions: list[dict], *, today: str) -> list[dict]:
    rows = []
    for question in questions:
        path = output_dir / f"{question['id']}.json"
        if not path.is_file():
            continue
        row = json.loads(path.read_text(encoding="utf-8"))
        if "runs" not in row:
            print(f"Skipping {path}: saved by the previous eval format.", file=sys.stderr)
            continue
        print(f"Judging {question['id']}...", flush=True)
        row = score_row(row, today=today, pages_dir=output_dir / "pages")
        _write_row(output_dir, row)
        rows.append(row)
    return rows


def _write_report(output_dir: Path, rows: list[dict], today: str, systems: list[str]) -> None:
    report_path = output_dir / "summary.md"
    report_path.write_text(format_report(rows, run_date=today, systems=systems, labels=LABELS), encoding="utf-8")
    print(report_path)


def _write_row(output_dir: Path, row: dict) -> None:
    (output_dir / f"{row['id']}.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")


def _call_openai(query: str) -> dict:
    try:
        from openai import OpenAI

        response = OpenAI().responses.create(
            model=OPENAI_MODEL,
            reasoning={"effort": REASONING_EFFORT},
            tools=[{"type": "web_search", "search_context_size": "high", "return_token_budget": "unlimited"}],
            include=["web_search_call.action.sources"],
            input=(
                "Use the web search tool to answer the question. "
                "Search as needed, answer in English, and cite the sources you use. "
                "Only state what the search results support.\n\n"
                f"Question: {query}"
            ),
        )
        payload = response.model_dump(mode="json")
    except Exception as exc:
        return record_error(f"{exc}\n{traceback.format_exc()}")
    record = record_openai_response(payload)
    record["extras"]["response"] = payload
    return record


def _load_env_file(path: Path) -> None:
    # python-dotenv expands ${VAR} references (BEDROCK_MANTLE_API_KEY=${AWS_BEARER_TOKEN_BEDROCK}); a literal
    # placeholder reaches litellm's own key lookup and Bedrock rejects it as an invalid bearer token.
    from dotenv import load_dotenv

    load_dotenv(path)


if __name__ == "__main__":
    raise SystemExit(main())
