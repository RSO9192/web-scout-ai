"""Labeled, live Jev/GPT comparison; run through Poetry in the scout environment.

Prompts live in production code. These held-out examples must not be used for
threshold tuning. Output is JSON with outcomes, timings and service errors.
"""

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

from agents import Agent, Runner
from dotenv import load_dotenv

from web_scout._classification import close_classification_clients, coverage, supported_evidence
from web_scout._pipeline_rules import _build_coverage_prompt
from web_scout._pipeline_types import DEFAULT_WEB_RESEARCH_MODELS, CoverageEvaluation
from web_scout._prompts import COVERAGE_EVALUATOR_INSTRUCTIONS
from web_scout.tools.pdf_extractor import PdfEvidenceItem, _verify_claims_llm
from web_scout.tools.tracker import ResearchTracker
from web_scout.utils import get_model

EVIDENCE_CASES = [
    (
        "The study counted 47 adult cranes in the western reserve during March 2024.",
        "47 adult cranes were recorded in March 2024 in the western reserve.",
        True,
    ),
    (
        "The study counted 47 adult cranes in the western reserve during March 2024.",
        "47 juvenile cranes were recorded in March 2024.",
        False,
    ),
    (
        "The median yield was 6 tonnes per hectare in 2023; the maximum was 9.",
        "The median yield was 9 tonnes per hectare in 2023.",
        False,
    ),
    (
        "The median yield was 6 tonnes per hectare in 2023; the maximum was 9.",
        "The 2023 median yield was six tonnes per hectare.",
        True,
    ),
    ("No field trials were authorized in Tanzania during 2022.", "Tanzania authorized field trials in 2022.", False),
    ("No field trials were authorized in Tanzania during 2022.", "Tanzania did not authorize trials in 2022.", True),
    ("The pilot may begin in 2027 if financing is approved.", "The pilot will begin in 2027.", False),
    (
        "The pilot may begin in 2027 if financing is approved.",
        "A 2027 pilot is conditional on financing approval.",
        True,
    ),
    (
        "Revenue grew 12% in the eastern district; western district revenue fell 2%.",
        "Western district revenue grew 12%.",
        False,
    ),
    (
        "Revenue grew 12% in the eastern district; western district revenue fell 2%.",
        "Eastern district revenue grew 12%.",
        True,
    ),
    ("The report gives a budget of USD 8 million.", 'The report says "USD 80 million".', False),
    ("The report gives a budget of USD 8 million.", 'The report states "USD 8 million".', True),
]
COVERAGE_CASES = [
    (
        "Give 2024 maize yield in Malawi in tonnes per hectare",
        "In Malawi, 2024 maize yield was 2.7 tonnes per hectare.",
        True,
    ),
    (
        "Give 2024 maize yield in Malawi in tonnes per hectare",
        "In Zambia, 2024 maize yield was 2.7 tonnes per hectare.",
        False,
    ),
    (
        "Give 2024 maize yield in Malawi in tonnes per hectare",
        "In Malawi, 2023 maize yield was 2.7 tonnes per hectare.",
        False,
    ),
    (
        "Compare 2023 and 2024 maize yield in Malawi",
        "Malawi maize yield was 2.5 tonnes per hectare in 2023 and 2.7 in 2024.",
        True,
    ),
    ("Compare 2023 and 2024 maize yield in Malawi", "Malawi maize yield was 2.7 tonnes per hectare in 2024.", False),
    (
        "Name the two fish species monitored in the lake",
        "The monitoring programme tracked Nile tilapia and African catfish.",
        True,
    ),
    (
        "Name the two fish species monitored in the lake",
        "The programme explains how species should be monitored.",
        False,
    ),
    ("Give the annual monitoring cost", "The source only describes the programme objectives.", False),
]


class VerificationErrors(logging.Handler):
    """Detect the legacy verifier's fail-open return after a service failure."""

    def __init__(self):
        super().__init__()
        self.failed = False

    def emit(self, record):
        if "claim verification failed" in record.getMessage():
            self.failed = True


async def main(output):
    load_dotenv(Path(__file__).parents[1] / ".env", override=False)
    model = get_model(DEFAULT_WEB_RESEARCH_MODELS["content_extractor"])
    evaluator = Agent(
        name="coverage_evaluator",
        model=model,
        output_type=CoverageEvaluation,
        instructions=COVERAGE_EVALUATOR_INSTRUCTIONS,
    )
    rows = []
    errors = VerificationErrors()
    logging.getLogger("web_scout.tools.pdf_extractor").addHandler(errors)
    for index, (source, claim, expected) in enumerate(EVIDENCE_CASES):
        item = PdfEvidenceItem(text=claim, page_start=1, page_end=1)
        for backend in ("jev", "gpt"):
            started = time.perf_counter()
            row = {"task": "evidence", "case": index, "backend": backend, "expected": expected}
            try:
                if backend == "jev":
                    actual = bool(await supported_evidence([item], {1: source}))
                else:
                    # Preserve the current GPT verifier input and prompt exactly.
                    errors.failed = False
                    verified = await _verify_claims_llm(
                        model=model,
                        relevant_content=claim,
                        evidence=[PdfEvidenceItem(text=source, page_start=1, page_end=1)],
                    )
                    if errors.failed:
                        raise RuntimeError("GPT verification service unavailable")
                    actual = bool(verified.evidence)
                row.update(actual=actual, correct=actual == expected)
            except Exception as exc:
                row["error"] = type(exc).__name__
            row["seconds"] = time.perf_counter() - started
            rows.append(row)
    for index, (query, source, expected) in enumerate(COVERAGE_CASES):
        tracker = ResearchTracker()
        tracker.record_scrape("https://source.test/report", "Report", source)
        for backend in ("jev", "gpt"):
            started = time.perf_counter()
            row = {"task": "coverage", "case": index, "backend": backend, "expected": expected}
            try:
                if backend == "jev":
                    actual, _, _ = await coverage(
                        query, [query], [{"url": "https://source.test/report", "text": source}], []
                    )
                else:
                    result = await Runner.run(evaluator, _build_coverage_prompt(query, tracker))
                    actual = result.final_output_as(CoverageEvaluation).fully_answered
                row.update(actual=actual, correct=actual == expected)
            except Exception as exc:
                row["error"] = type(exc).__name__
            row["seconds"] = time.perf_counter() - started
            rows.append(row)
    logging.getLogger("web_scout.tools.pdf_extractor").removeHandler(errors)
    await close_classification_clients()
    Path(output).write_text(json.dumps(rows, indent=2))
    print(
        json.dumps(
            {
                backend: {
                    "cases": sum(row["backend"] == backend for row in rows),
                    "correct": sum(row.get("correct", False) for row in rows if row["backend"] == backend),
                    "errors": sum("error" in row for row in rows if row["backend"] == backend),
                    "seconds": sum(row["seconds"] for row in rows if row["backend"] == backend),
                }
                for backend in ("jev", "gpt")
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/private/tmp/web-scout-classification-benchmark.json")
    asyncio.run(main(parser.parse_args().output))
