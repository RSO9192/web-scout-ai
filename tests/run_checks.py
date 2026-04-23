"""Run lint, unit tests, and optional live probes with saved artifacts.

Usage:
    mamba run -n web-agent python tests/run_checks.py quick
    mamba run -n web-agent python tests/run_checks.py unit
    mamba run -n web-agent python tests/run_checks.py behavior
    mamba run -n web-agent python tests/run_checks.py all

Artifacts are written under tests/run_results/<timestamp>/ and include:
    - one .log file per step
    - pytest JUnit XML for pytest steps
    - manifest.json summary
    - summary.md human-readable report
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = Path(__file__).resolve().parent / "run_results"


@dataclass(frozen=True)
class StepSpec:
    name: str
    command: list[str]
    required_env: tuple[str, ...] = ()
    junit_name: str | None = None
    description: str = ""


@dataclass
class StepResult:
    name: str
    description: str
    command: list[str]
    started_at: str
    elapsed_seconds: float
    status: str
    return_code: int | None
    log_path: str
    junit_path: str | None = None
    missing_env: list[str] = field(default_factory=list)


def _step_catalog(run_dir: Path, env_file: str | None) -> dict[str, StepSpec]:
    python_cmd = [sys.executable]
    env_file_args = ["--env-file", env_file] if env_file else []

    return {
        "lint": StepSpec(
            name="lint",
            description="Static lint check for src and tests.",
            command=["ruff", "check", "src", "tests"],
        ),
        "unit-fast": StepSpec(
            name="unit-fast",
            description="Targeted unit slice for pipeline, URL normalization, and dedupe behavior.",
            command=[
                "pytest",
                "tests/test_pipeline.py",
                "tests/test_scrape_tool_dedupe.py",
                "tests/test_url_utils.py",
                "-q",
            ],
            junit_name="unit-fast.xml",
        ),
        "unit-all": StepSpec(
            name="unit-all",
            description="Full local pytest suite with runtime warnings promoted to errors.",
            command=["pytest", "-W", "error::RuntimeWarning"],
            junit_name="unit-all.xml",
        ),
        "query-probe": StepSpec(
            name="query-probe",
            description="Search + scrape smoke probe without LLM synthesis.",
            command=python_cmd
            + [
                "tests/query_probe.py",
                "--max-results",
                "6",
                "--max-scrapes",
                "4",
                "--output-dir",
                str(run_dir),
            ],
            required_env=("SERPER_API_KEY",),
        ),
        "matrix-probe": StepSpec(
            name="matrix-probe",
            description="Mixed open/domain/direct live probe across a small case set.",
            command=python_cmd
            + [
                "tests/matrix_probe.py",
                "--limit",
                "2",
                "--search-backend",
                "serper",
                "--research-depth",
                "standard",
                "--output-dir",
                str(run_dir),
            ]
            + env_file_args,
            required_env=("SERPER_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"),
        ),
        "full-query-probe": StepSpec(
            name="full-query-probe",
            description="End-to-end live research probe with synthesis on one manual query.",
            command=python_cmd
            + [
                "tests/full_query_probe.py",
                "--limit",
                "1",
                "--search-backend",
                "serper",
                "--research-depth",
                "standard",
                "--output-dir",
                str(run_dir),
            ]
            + env_file_args,
            required_env=("SERPER_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"),
        ),
    }


PRESETS = {
    "quick": ["lint", "unit-fast"],
    "unit": ["lint", "unit-all"],
    "behavior": ["query-probe", "matrix-probe", "full-query-probe"],
    "all": ["lint", "unit-all", "query-probe", "matrix-probe", "full-query-probe"],
}


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _shell_line(command: Sequence[str]) -> str:
    return shlex.join(command)


def _load_env_file(env_file: str | None) -> dict[str, str]:
    if not env_file:
        return {}
    path = Path(env_file)
    if not path.exists():
        return {}

    loaded: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        loaded[key.strip()] = value.strip().strip("'").strip('"')
    return loaded


def _run_step(step: StepSpec, run_dir: Path, extra_env: dict[str, str]) -> StepResult:
    started_at = datetime.now().isoformat(timespec="seconds")
    log_path = run_dir / f"{step.name}.log"
    junit_path = run_dir / step.junit_name if step.junit_name else None
    command = list(step.command)
    if junit_path:
        command.extend(["--junitxml", str(junit_path)])

    effective_env = os.environ.copy()
    effective_env.update(extra_env)
    missing_env = [name for name in step.required_env if not effective_env.get(name)]
    if missing_env:
        message = (
            f"SKIPPED {step.name}\n"
            f"Missing environment variables: {', '.join(missing_env)}\n"
            f"Command: {_shell_line(command)}\n"
        )
        log_path.write_text(message, encoding="utf-8")
        return StepResult(
            name=step.name,
            description=step.description,
            command=command,
            started_at=started_at,
            elapsed_seconds=0.0,
            status="skipped",
            return_code=None,
            log_path=str(log_path.relative_to(ROOT)),
            junit_path=str(junit_path.relative_to(ROOT)) if junit_path else None,
            missing_env=missing_env,
        )

    start = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write(f"$ {_shell_line(command)}\n\n")
        log_file.flush()
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=effective_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            log_file.write(line)
        return_code = process.wait()
    elapsed = round(time.perf_counter() - start, 2)
    return StepResult(
        name=step.name,
        description=step.description,
        command=command,
        started_at=started_at,
        elapsed_seconds=elapsed,
        status="passed" if return_code == 0 else "failed",
        return_code=return_code,
        log_path=str(log_path.relative_to(ROOT)),
        junit_path=str(junit_path.relative_to(ROOT)) if junit_path else None,
    )


def _write_summary(run_dir: Path, preset: str, results: list[StepResult]) -> None:
    summary_path = run_dir / "summary.md"
    lines = [
        f"# web-scout-ai checks: {preset}",
        "",
        f"- Timestamp: {run_dir.name}",
        f"- Repository: `{ROOT}`",
        "",
        "| Step | Status | Time (s) | Artifacts |",
        "| --- | --- | ---: | --- |",
    ]
    for result in results:
        artifacts = [result.log_path]
        if result.junit_path:
            artifacts.append(result.junit_path)
        lines.append(
            f"| `{result.name}` | `{result.status}` | {result.elapsed_seconds:.2f} | "
            + ", ".join(f"`{item}`" for item in artifacts)
            + " |"
        )
    lines.extend(["", "## Commands", ""])
    for result in results:
        lines.append(f"- `{result.name}`: `{_shell_line(result.command)}`")
        if result.missing_env:
            lines.append(f"  Missing env: `{', '.join(result.missing_env)}`")
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run web-scout-ai checks with saved logs.")
    parser.add_argument("preset", choices=sorted(PRESETS), help="Check preset to run.")
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_ROOT),
        help="Base directory for timestamped run artifacts.",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional dotenv file forwarded to live probe scripts that support it.",
    )
    args = parser.parse_args()

    base_dir = Path(args.output_dir)
    run_dir = base_dir / _timestamp()
    run_dir.mkdir(parents=True, exist_ok=True)

    catalog = _step_catalog(run_dir, args.env_file)
    extra_env = _load_env_file(args.env_file)
    results: list[StepResult] = []

    print(f"Writing artifacts to {run_dir}")
    for step_name in PRESETS[args.preset]:
        step = catalog[step_name]
        print(f"\n==> {step.name}: {step.description}")
        result = _run_step(step, run_dir, extra_env)
        results.append(result)
        print(f"--> {result.status} ({result.elapsed_seconds:.2f}s)")
        if result.status == "failed":
            break

    manifest_path = run_dir / "manifest.json"
    manifest = {
        "preset": args.preset,
        "run_dir": str(run_dir),
        "results": [asdict(item) for item in results],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _write_summary(run_dir, args.preset, results)

    failed = any(item.status == "failed" for item in results)
    print(f"\nSummary: {run_dir / 'summary.md'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
