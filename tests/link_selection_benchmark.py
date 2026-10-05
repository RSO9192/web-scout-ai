"""Benchmark follow-up URL selection against hand-picked relevant links.

For each case the runner fetches the parent page, collects the links the
fetcher extracted, and asks a selector which of those links are relevant to
the query. A selected URL that is in the hand-picked set is a true positive.
Every other selected URL is a false positive: it does not add information for
the query, but the pipeline would still fetch it.

Two selectors are timed: gpt-6-luna, and a TypeSafe Jev yes-or-no per link.
The recorded time is the selector call. Fetching the parent page is shared
and is not included.

Usage:
    poetry run python tests/link_selection_benchmark.py
    poetry run python tests/link_selection_benchmark.py --selector jev
    poetry run python tests/link_selection_benchmark.py --case el-nino --selector luna

Writes tests/benchmark_results/link_selection_YYYYMMDD_HHMMSS.{json,md}
"""

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from agents import Agent, ModelSettings, Runner
from dotenv import load_dotenv

from web_scout._pipeline_types import DEFAULT_WEB_RESEARCH_MODELS, FollowupSelection
from web_scout.jev_link_selector import JEV_MODEL, select_links_with_jev
from web_scout.scraping import fetch_and_parse_url
from web_scout.utils import get_model

OUTPUT_DIR = Path(__file__).parent / "benchmark_results"
PARENT_EXCERPT_CHARS = 1800
SELECTORS = ("luna", "jev")
# Agents posts Mantle OpenAI models to /v1/chat/completions unless this base is set.
# That route rejects gpt-6-luna. The openai/v1 base is the route that accepts it.
_LUNA_OPENAI_BASE_URL = "https://bedrock-mantle.us-east-1.api.aws/openai/v1"

SELECTOR_INSTRUCTIONS = (
    "You select follow-up URLs for a web research pipeline. "
    "Only select from the provided candidates. "
    "Return every URL that is relevant to the research query. "
    "Leave out URLs that do not add information for the query."
)


@dataclass(frozen=True)
class LinkSelectionCase:
    name: str
    query: str
    url: str
    relevant_urls: tuple[str, ...]


CASES: tuple[LinkSelectionCase, ...] = (
    LinkSelectionCase(
        name="el-nino",
        query="el nino",
        url="https://www.fao.org/el-nino/en",
        relevant_urls=(
            "https://www.fao.org/climate-change/",
            "https://www.fao.org/emergencies/our-focus/anticipatory-action/en",
            "https://doi.org/10.4060/cd5248en",
            "https://www.fao.org/newsroom/detail/bracing-for-el-ni%C3%B1o--fao-and-wfp-launch-joint-appeal-to-protect-8.8-million-people-from-extreme-weather-events/en",
            "https://www.fao.org/newsroom/story/when-el-nino-brings-drought-every-minute-counts/",
            "https://data.apps.fao.org/?lang=en&share=f-4116a39f-965d-4192-bdd0-22780092f979",
            "https://www.climatechangenews.com/2026/09/28/as-el-nino-intensifies-we-should-be-investing-more-in-the-worlds-farmers/",
            "https://news.un.org/en/story/2026/09/1168404",
            "https://www.fao.org/newsroom/detail/bracing-for-el-ni%C3%B1o--fao-and-wfp-launch-joint-appeal-to-protect-8.8-million-people-from-extreme-weather-events/en",
            "https://www.fao.org/climate-change/news/news-detail/el-ni%C3%B1o-s-hidden-losses--when-one-season-s-damage-fuels-the-next-crisis/en",
            "https://www.fao.org/climate-change/news/news-detail/el-ni%C3%B1o--climate-change-and-the-rising-risks-to-aquatic-food-systems/en",
            "https://www.fao.org/climate-change/news/news-detail/extreme-heat-in-an-el-ni%C3%B1o-year--how-agrifood-systems-can-prepare/en",
            "https://openknowledge.fao.org/handle/20.500.14283/ce0220en",
            "https://openknowledge.fao.org/handle/20.500.14283/cd9804en",
            "https://doi.org/10.4060/cd9394en",
            "https://www.unocha.org/publications/report/indonesia/asia-and-pacific-snapshot-el-nino-middle-east-crisis-12-june-2026",
            "https://doi.org/10.4060/cd5248en",
            "https://openknowledge.fao.org/handle/20.500.14283/cd7352en",
        ),
    ),
    LinkSelectionCase(
        name="forestry",
        query="forestry",
        url="https://openknowledge.fao.org/items/3758005c-1267-40be-a267-75d442633f62",
        relevant_urls=(
            "https://openknowledge.fao.org/bitstreams/2266fca9-6b6b-4296-855e-4a93e57f4118/download",
            "https://openknowledge.fao.org/bitstreams/1ccb3e6d-a6e6-42b7-87a8-ec8f40016909/download",
            "https://openknowledge.fao.org/items/d6e3823c-b0d2-4ff9-9ead-c28bd2d9375b",
            "https://openknowledge.fao.org/items/8f5bffe6-da40-490a-9d74-26313799db94",
            "https://openknowledge.fao.org/items/6fdffc38-25b2-4e16-85cc-094c64c5035b",
        ),
    ),
    LinkSelectionCase(
        name="biodiversity",
        query="biodiversity",
        url="https://www.bbc.com/news/topics/c9grgwq2krzt",
        relevant_urls=(
            "https://www.bbc.com/news/articles/c6lyez3q3j9wo",
            "https://www.bbc.com/news/articles/ck4gl6e0737wo",
            "https://www.bbc.com/news/videos/c9dw0yx1wwvo",
            "https://www.bbc.com/news/articles/cd6853427l0o",
            "https://www.bbc.com/news/articles/cd947jkqvk9o",
            "https://www.bbc.com/news/articles/cp3kn2k1ypxo",
            "https://www.bbc.com/news/articles/cdewzk08x8ro",
            "https://www.bbc.com/news/articles/cp8xl72jngpo",
            "https://www.bbc.com/news/articles/cm2rm7kgr7vo",
            "https://www.bbc.com/news/topics/c9grgwq2krzt?page=2",
        ),
    ),
)


def normalize_benchmark_url(url: str) -> str:
    """Canonical form used to match hand-picked URLs with fetched and selected ones.

    Decodes percent-encoding, ignores a leading ``www.``, drops the fragment and
    trailing slash, and sorts query parameters.
    """
    text = unquote(url.strip())
    parsed = urlparse(text)
    scheme = "https" if parsed.scheme in ("http", "https") else parsed.scheme
    netloc = parsed.netloc.lower().removeprefix("www.")
    path = unquote(parsed.path).rstrip("/")
    if parsed.query:
        pairs = parse_qsl(unquote(parsed.query), keep_blank_values=True)
        pairs.sort()
        query = urlencode(pairs)
    else:
        query = ""
    return urlunparse((scheme, netloc, path, "", query, ""))


def unique_urls(urls: list[str] | tuple[str, ...]) -> list[str]:
    """Drop blank and duplicate URLs, keeping the first spelling of each."""
    indexed: dict[str, str] = {}
    for url in urls:
        if not url or not url.strip():
            continue
        key = normalize_benchmark_url(url)
        if key and key not in indexed:
            indexed[key] = url
    return list(indexed.values())


@dataclass
class SelectionScore:
    """Set overlap between hand-picked relevant URLs and the model's selection."""

    true_positives: list[str] = field(default_factory=list)
    false_positives: list[str] = field(default_factory=list)
    false_negatives: list[str] = field(default_factory=list)
    not_in_candidates: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        denom = len(self.true_positives) + len(self.false_positives)
        if denom == 0:
            return 0.0
        return len(self.true_positives) / denom

    @property
    def recall(self) -> float:
        denom = len(self.true_positives) + len(self.false_negatives)
        if denom == 0:
            return 0.0
        return len(self.true_positives) / denom


def score_selection(
    gold: list[str] | tuple[str, ...],
    selected: list[str],
    candidates: list[str],
) -> SelectionScore:
    """Score a selection. URLs outside the hand-picked set are false positives."""
    gold_index = {normalize_benchmark_url(url): url for url in unique_urls(gold)}
    selected_index = {normalize_benchmark_url(url): url for url in unique_urls(selected)}
    candidate_keys = {normalize_benchmark_url(url) for url in unique_urls(candidates)}

    true_keys = [key for key in gold_index if key in selected_index]
    false_keys = [key for key in selected_index if key not in gold_index]
    missed_keys = [key for key in gold_index if key not in selected_index]
    return SelectionScore(
        true_positives=[gold_index[key] for key in true_keys],
        false_positives=[selected_index[key] for key in false_keys],
        false_negatives=[gold_index[key] for key in missed_keys],
        not_in_candidates=[gold_index[key] for key in missed_keys if key not in candidate_keys],
    )


def _selection_prompt(query: str, parent_url: str, parent_content: str, candidates: list[str]) -> str:
    excerpt = " ".join(parent_content.split())[:PARENT_EXCERPT_CHARS]
    lines = "\n".join(f"- {url}" for url in candidates)
    return (
        f"Research query: {query}\n"
        f"Parent page: {parent_url}\n"
        f"Parent excerpt:\n{excerpt}\n\n"
        "Select every URL from this candidate list that is relevant to the query.\n"
        "A relevant URL is one that is likely to contain information answering the query.\n"
        "Do not select URLs that add no new information for the query, including navigation, "
        "language switches, share buttons, homepages, and unrelated pages.\n"
        "Every extra URL increases how many pages must be fetched.\n"
        "Return exact URLs from the list only.\n\n"
        f"Candidates:\n{lines}"
    )


async def select_relevant_urls(
    *,
    query: str,
    parent_url: str,
    parent_content: str,
    candidates: list[str],
    model: str,
) -> tuple[list[str], Optional[str]]:
    """Ask the follow-up selector which fetched links are relevant.

    URLs the model returns that were not in ``candidates`` are dropped. The
    pipeline would not follow them.
    """
    shortlist = unique_urls(candidates)
    parent_key = normalize_benchmark_url(parent_url)
    shortlist = [url for url in shortlist if normalize_benchmark_url(url) != parent_key]
    if not shortlist:
        return [], None

    by_key = {normalize_benchmark_url(url): url for url in shortlist}
    selector = Agent(
        name="followup_selector",
        model=_luna_agents_model(model),
        output_type=FollowupSelection,
        model_settings=ModelSettings(),
        instructions=SELECTOR_INSTRUCTIONS,
    )
    try:
        result = await Runner.run(selector, _selection_prompt(query, parent_url, parent_content, shortlist))
        raw_selected = result.final_output_as(FollowupSelection).selected_urls
    except Exception as exc:
        return [], str(exc)

    selected: list[str] = []
    for url in raw_selected:
        original = by_key.get(normalize_benchmark_url(url))
        if original and original not in selected:
            selected.append(original)
    return selected, None


def _luna_agents_model(model_name: str):
    """Build the Luna model and point Mantle OpenAI models at the working route."""
    model = get_model(model_name)
    if isinstance(model, str) or model.base_url is not None:
        return model
    if model_name.startswith("bedrock_mantle/openai.gpt-"):
        model.base_url = _LUNA_OPENAI_BASE_URL
    return model


def _alias_bedrock_key() -> None:
    """The repo .env stores the Mantle key as AWS_BEDROCK_API_KEY."""
    key = os.getenv("AWS_BEDROCK_API_KEY")
    if not key:
        return
    os.environ.setdefault("BEDROCK_MANTLE_API_KEY", key)
    os.environ.setdefault("AWS_BEARER_TOKEN_BEDROCK", key)


@dataclass
class CaseResult:
    name: str
    query: str
    url: str
    selector: str
    model: str
    candidate_count: int
    score: SelectionScore
    elapsed_seconds: float
    selected_urls: list[str] = field(default_factory=list)
    error: Optional[str] = None


def _score_payload(score: SelectionScore) -> dict:
    payload = asdict(score)
    payload["precision"] = round(score.precision, 4)
    payload["recall"] = round(score.recall, 4)
    payload["identified"] = len(score.true_positives)
    payload["gold"] = len(score.true_positives) + len(score.false_negatives)
    payload["false_positive_count"] = len(score.false_positives)
    return payload


def _selector_model_name(selector: str, luna_model: str) -> str:
    if selector == "jev":
        return JEV_MODEL
    return luna_model


async def _select(
    selector: str,
    *,
    query: str,
    parent_url: str,
    parent_content: str,
    candidates: list[str],
    luna_model: str,
) -> tuple[list[str], Optional[str]]:
    if selector == "luna":
        return await select_relevant_urls(
            query=query,
            parent_url=parent_url,
            parent_content=parent_content,
            candidates=candidates,
            model=luna_model,
        )
    return await select_links_with_jev(
        query=query,
        parent_url=parent_url,
        parent_content=parent_content,
        candidates=candidates,
    )


async def run_case(case: LinkSelectionCase, *, selectors: tuple[str, ...], luna_model: str) -> list[CaseResult]:
    """Fetch one parent page and score each selector's relevant links."""
    fetch_result, parse_result = await fetch_and_parse_url(case.url)
    error = fetch_result.error or parse_result.error
    candidates = list(parse_result.links)
    if error and not candidates:
        score = score_selection(case.relevant_urls, [], [])
        return [
            CaseResult(
                name=case.name,
                query=case.query,
                url=case.url,
                selector=selector,
                model=_selector_model_name(selector, luna_model),
                candidate_count=0,
                score=score,
                elapsed_seconds=0.0,
                error=error,
            )
            for selector in selectors
        ]

    results: list[CaseResult] = []
    for selector in selectors:
        started = time.perf_counter()
        selected, select_error = await _select(
            selector,
            query=case.query,
            parent_url=case.url,
            parent_content=parse_result.text_content,
            candidates=candidates,
            luna_model=luna_model,
        )
        elapsed = time.perf_counter() - started
        results.append(
            CaseResult(
                name=case.name,
                query=case.query,
                url=case.url,
                selector=selector,
                model=_selector_model_name(selector, luna_model),
                candidate_count=len(unique_urls(candidates)),
                score=score_selection(case.relevant_urls, selected, candidates),
                elapsed_seconds=elapsed,
                selected_urls=selected,
                error=select_error or error,
            )
        )
    return results


def _format_url_list(urls: list[str]) -> list[str]:
    if not urls:
        return ["- (none)"]
    return [f"- {url}" for url in urls]


def _totals(results: list[CaseResult]) -> tuple[int, int, int, float]:
    identified = 0
    gold = 0
    false_positives = 0
    elapsed = 0.0
    for result in results:
        score = result.score
        identified += len(score.true_positives)
        gold += len(score.true_positives) + len(score.false_negatives)
        false_positives += len(score.false_positives)
        elapsed += result.elapsed_seconds
    return identified, gold, false_positives, elapsed


def build_report(results: list[CaseResult]) -> str:
    """Render a markdown report of identified, missed, and extra URLs."""
    lines = [
        "# Link selection benchmark",
        "",
        "Hand-picked URLs the selector marked relevant are hits. Every other selected URL is a false positive.",
        "Selection time is the selector call only. Fetching the parent page is not included.",
        "",
    ]
    for result in results:
        score = result.score
        lines.extend(
            [
                f"## {result.name} · {result.selector}",
                "",
                f"- Query: `{result.query}`",
                f"- URL: {result.url}",
                f"- Selector: `{result.selector}`",
                f"- Model: `{result.model}`",
                f"- Links extracted: {result.candidate_count}",
                f"- Selection time: {result.elapsed_seconds:.2f}s",
                (
                    f"- Identified: {len(score.true_positives)}/"
                    f"{len(score.true_positives) + len(score.false_negatives)}"
                ),
                f"- False positives: {len(score.false_positives)}",
                f"- Precision: {score.precision:.2f}",
                f"- Recall: {score.recall:.2f}",
            ]
        )
        if result.error:
            lines.append(f"- Error: {result.error}")
        missed_by_model = [url for url in score.false_negatives if url not in score.not_in_candidates]
        lines.extend(["", "### Identified", ""])
        lines.extend(_format_url_list(score.true_positives))
        lines.extend(["", "### Missed by the model", ""])
        lines.extend(_format_url_list(missed_by_model))
        lines.extend(["", "### Not in the fetched links", ""])
        lines.extend(_format_url_list(score.not_in_candidates))
        lines.extend(["", "### False positives", ""])
        lines.extend(_format_url_list(score.false_positives))
        lines.append("")

    lines.extend(["## Overall", ""])
    selectors: list[str] = []
    for result in results:
        if result.selector not in selectors:
            selectors.append(result.selector)
    for selector in selectors:
        group = [result for result in results if result.selector == selector]
        identified, gold, false_positives, elapsed = _totals(group)
        precision = identified / (identified + false_positives) if identified + false_positives else 0.0
        recall = identified / gold if gold else 0.0
        lines.extend(
            [
                f"### {selector}",
                "",
                f"- Model: `{group[0].model}`",
                f"- Selection time: {elapsed:.2f}s",
                f"- Identified: {identified}/{gold}",
                f"- False positives: {false_positives}",
                f"- Precision: {precision:.2f}",
                f"- Recall: {recall:.2f}",
                "",
            ]
        )
    return "\n".join(lines)


def _result_payload(result: CaseResult) -> dict:
    return {
        "name": result.name,
        "query": result.query,
        "url": result.url,
        "selector": result.selector,
        "model": result.model,
        "candidate_count": result.candidate_count,
        "elapsed_seconds": round(result.elapsed_seconds, 3),
        "selected_urls": result.selected_urls,
        "error": result.error,
        "score": _score_payload(result.score),
    }


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Score follow-up URL selection against hand-picked links.")
    parser.add_argument("--case", action="append", default=[], help="Case name to run. Repeatable. Default: all.")
    parser.add_argument("--limit", type=int, default=0, help="Run only the first N selected cases. 0 runs all.")
    parser.add_argument("--model", default=DEFAULT_WEB_RESEARCH_MODELS["followup_selector"])
    parser.add_argument(
        "--selector",
        choices=(*SELECTORS, "both"),
        default="both",
        help="Which selector to time. Default: luna and jev.",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="Dotenv file with API keys. Defaults to the repo .env when present.",
    )
    args = parser.parse_args()

    env_file = Path(args.env_file) if args.env_file else Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        load_dotenv(env_file)
    _alias_bedrock_key()

    selectors = SELECTORS if args.selector == "both" else (args.selector,)
    selected = list(CASES)
    if args.case:
        wanted = set(args.case)
        known = {case.name for case in CASES}
        unknown = sorted(wanted - known)
        if unknown:
            names = ", ".join(case.name for case in CASES)
            raise SystemExit(f"Unknown case(s): {', '.join(unknown)}. Known cases: {names}")
        selected = [case for case in CASES if case.name in wanted]
    if args.limit > 0:
        selected = selected[: args.limit]

    results: list[CaseResult] = []
    for case in selected:
        print(f"Running {case.name}: {case.url}")
        case_results = await run_case(case, selectors=selectors, luna_model=args.model)
        for result in case_results:
            score = result.score
            gold_count = len(score.true_positives) + len(score.false_negatives)
            print(
                f"  {result.selector}: identified {len(score.true_positives)}/{gold_count}, "
                f"false positives {len(score.false_positives)}, "
                f"precision {score.precision:.2f}, recall {score.recall:.2f}, "
                f"{result.elapsed_seconds:.2f}s"
            )
            if result.error:
                print(f"    error: {result.error}")
        results.extend(case_results)

    report = build_report(results)
    print("\n" + report)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = OUTPUT_DIR / f"link_selection_{timestamp}.json"
    md_path = OUTPUT_DIR / f"link_selection_{timestamp}.md"
    json_path.write_text(json.dumps([_result_payload(result) for result in results], indent=2) + "\n")
    md_path.write_text(report)
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    asyncio.run(_main())
