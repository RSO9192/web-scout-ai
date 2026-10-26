"""Judge one answer's claims against fetched pages, its completeness, and pairwise preference."""

from __future__ import annotations

import re
from typing import Callable, Literal

from pydantic import BaseModel, Field

from eval.metrics import normalize_url, strip_tracking

JUDGE_MODEL = "gemini/gemini-3.1-pro-preview"
# Whole pages fit the judge's 1M-token context. Trimming by word overlap fails when the claim is English
# and the page is not, so it only applies beyond this limit.
PASSAGE_CHAR_LIMIT = 1_000_000
_WINDOW_CHARS = 1_500
_SEPARATOR = "\n…\n"
_VERDICT_RANK = {"unfetchable": 0, "judge_error": 1, "not_supported": 2, "contradicted": 3, "supported": 4}

EXTRACT_INSTRUCTIONS = """\
Split the answer into atomic factual claims: one name, figure, date, rule, or relation per claim.
Skip framing sentences, hedges, and statements about the research process.
Keep collective statements collective: when a sentence says several places or items together have several
features, that is one claim about the group, not one claim per item and feature.
For each claim, copy the URLs of the links that support it exactly as they appear in the answer.
A link applies to the claims in its sentence, and to the earlier sentences of the same paragraph that have
no link of their own. In a table row or bullet, it applies to that row or bullet.
Use an empty list when the claim has no link.
"""

CHECK_INSTRUCTIONS = """\
You check numbered claims against the text of one web page.
Judge only from the page text. Do not use your own knowledge.
supported: the page states the claim or it follows directly from what the page states; paraphrase, rounding,
translation, and summary wording are fine.
A claim about a group is supported when the page supports it for the group as a whole; not every member needs
every feature.
not_supported: the page text does not contain the claim.
contradicted: the page states something incompatible with the claim.
unreadable: the page text is garbled or binary, a bot, consent, or login wall, or lacks the page's main content.
Return one check per claim number with a one-sentence reason quoting or naming the passage.
"""

COMPLETENESS_INSTRUCTIONS = """\
You score how completely one answer answers a research question, from 1 to 5.
High scores answer every part of the question with the specific names, figures, years, or rules asked for,
from the latest period available as of today's date.
An answer that hedges or says the information was not found scores low on completeness but is not wrong.
Do not reward length or style. Do not check facts against your own knowledge.
The reason is one or two sentences naming what is present or missing.
"""

PAIR_INSTRUCTIONS = """\
You compare two answers to the same research question.
Prefer the answer that gives the reader more of the specific, current, and sourced facts the question asks for.
Do not prefer an answer for length, formatting, or confidence.
Return A, B, or tie, with a one or two sentence reason.
"""


class ExtractedClaim(BaseModel):
    """One atomic claim and the URLs the answer cites for it."""

    claim: str
    urls: list[str] = Field(default_factory=list)


class ExtractedClaims(BaseModel):
    """All claims in one answer."""

    claims: list[ExtractedClaim]


class ClaimCheck(BaseModel):
    """Verdict for one numbered claim against one page."""

    index: int
    verdict: Literal["supported", "not_supported", "contradicted", "unreadable"]
    reason: str


class ClaimChecks(BaseModel):
    """Verdicts for the claims sent with one page."""

    checks: list[ClaimCheck]


class ScoreReason(BaseModel):
    """One 1–5 score and the evidence for it."""

    score: int = Field(ge=1, le=5)
    reason: str


class PairVerdict(BaseModel):
    """Which of two answers is better."""

    winner: Literal["A", "B", "tie"]
    reason: str


def judge_record(*, query: str, record: dict, today: str, get_page: Callable[[str], dict]) -> dict:
    """Return ``{"completeness": {...}, "claims": [...]}`` for one non-error record."""
    answer = strip_tracking(record["answer"])
    cited = set(record.get("cited_urls") or [])
    extracted = _complete(EXTRACT_INSTRUCTIONS, _user(today, f"Answer:\n{answer}"), ExtractedClaims)

    claims = []
    remaining: dict[int, list[str]] = {}
    for item in extracted.claims:
        urls = []
        for url in item.urls:
            normalized = normalize_url(url)
            if normalized in cited and normalized not in urls:
                urls.append(normalized)
        verdict = "unfetchable" if urls else "uncited"
        claims.append({"claim": item.claim, "urls": urls, "verdict": verdict, "reason": "", "checked_url": ""})
        if urls:
            remaining[len(claims) - 1] = list(urls)

    # A claim citing several pages counts as supported if any of them supports it, so each round checks
    # the next fetchable page for every claim not yet supported, and keeps its most informative verdict.
    while remaining:
        groups: dict[str, list[int]] = {}
        for index, urls in list(remaining.items()):
            while urls and not get_page(urls[0]).get("ok"):
                urls.pop(0)
            if not urls:
                del remaining[index]
                continue
            groups.setdefault(urls.pop(0), []).append(index)
        for url, indices in groups.items():
            try:
                results = _check_claims([claims[index]["claim"] for index in indices], get_page(url)["text"], today)
            except Exception as exc:
                # One failed call (oversized page, invalid output) must not discard the rest of the run.
                results = [("judge_error", f"{type(exc).__name__}: {exc}")] * len(indices)
            for index, (verdict, reason) in zip(indices, results):
                claim = claims[index]
                if not claim["checked_url"] or _VERDICT_RANK[verdict] > _VERDICT_RANK[claim["verdict"]]:
                    claim.update(verdict=verdict, reason=reason, checked_url=url)
                if verdict == "supported":
                    remaining.pop(index, None)

    completeness = _complete(
        COMPLETENESS_INSTRUCTIONS, _user(today, f"Question:\n{query}\n\nAnswer:\n{answer}"), ScoreReason
    )
    return {"completeness": completeness.model_dump(), "claims": claims}


def judge_pair(*, query: str, answer: str, baseline_answer: str, today: str) -> tuple[str, list[str]]:
    """Compare twice with the order swapped; return ("system" | "baseline" | "tie", reasons)."""
    answer, baseline_answer = strip_tracking(answer), strip_tracking(baseline_answer)
    first = _complete(PAIR_INSTRUCTIONS, _pair_prompt(today, query, answer, baseline_answer), PairVerdict)
    second = _complete(PAIR_INSTRUCTIONS, _pair_prompt(today, query, baseline_answer, answer), PairVerdict)
    first_winner = {"A": "system", "B": "baseline"}.get(first.winner, "tie")
    second_winner = {"A": "baseline", "B": "system"}.get(second.winner, "tie")
    winner = first_winner if first_winner == second_winner else "tie"
    return winner, [first.reason, second.reason]


def select_passages(text: str, claims: list[str], limit: int = PASSAGE_CHAR_LIMIT) -> str:
    """Return ``text`` if it fits, else the windows sharing the most words with the claims, in page order."""
    if len(text) <= limit:
        return text
    claim_tokens = set(_tokens(" ".join(claims)))
    windows = [text[start : start + _WINDOW_CHARS] for start in range(0, len(text), _WINDOW_CHARS)]
    scored = [(len(claim_tokens & set(_tokens(window))), index) for index, window in enumerate(windows)]
    chosen = []
    used = 0
    for score, index in sorted(scored, key=lambda item: (-item[0], item[1])):
        if score == 0:
            break
        cost = len(windows[index]) + (len(_SEPARATOR) if chosen else 0)
        if used + cost > limit:
            continue
        chosen.append(index)
        used += cost
    if not chosen:
        return text[:limit]
    return _SEPARATOR.join(windows[index] for index in sorted(chosen))


def _check_claims(claims: list[str], page_text: str, today: str) -> list[tuple[str, str]]:
    numbered = "\n".join(f"{number}. {claim}" for number, claim in enumerate(claims, start=1))
    passages = select_passages(page_text, claims)
    checks = _complete(CHECK_INSTRUCTIONS, _user(today, f"Claims:\n{numbered}\n\nPage text:\n{passages}"), ClaimChecks)
    by_index = {check.index: check for check in checks.checks}
    results = []
    for number in range(1, len(claims) + 1):
        check = by_index.get(number)
        if check is None:
            results.append(("judge_error", ""))
        elif check.verdict == "unreadable":
            # A garbled extraction says nothing about the claim, so it counts with pages that could not be fetched.
            results.append(("unfetchable", check.reason))
        else:
            results.append((check.verdict, check.reason))
    return results


def _pair_prompt(today: str, query: str, answer_a: str, answer_b: str) -> str:
    return _user(today, f"Question:\n{query}\n\nAnswer A:\n{answer_a}\n\nAnswer B:\n{answer_b}")


def _user(today: str, body: str) -> str:
    return f"Today's date: {today}.\n\n{body}"


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w{3,}", text.lower())


def _complete(system: str, user: str, schema: type[BaseModel]) -> BaseModel:
    import litellm

    response = litellm.completion(
        model=JUDGE_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format=schema,
    )
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Judge returned no structured output.")
    return schema.model_validate_json(content)
