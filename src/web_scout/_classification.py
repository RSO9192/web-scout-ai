"""Jev decisions with explicit backend selection and no generative fallback."""

import asyncio
import json
import logging
import math
import os
import re
import time
from weakref import WeakKeyDictionary

from typesafe_sdk import AsyncTypeSafeClient, Noul, RetryPolicy, TypeSafeError

logger = logging.getLogger(__name__)
JEV_MODEL = "jev-1.13.0"
ACCEPT = 0.8
_clients = WeakKeyDictionary()


class ClassificationError(RuntimeError):
    """Configuration, authentication, or exhausted classification request failure."""


def backend(feature=None):
    if os.getenv("DISABLE_JEV", "false").strip().lower() == "true":
        return "gpt"
    variable = f"WEB_SCOUT_{feature.upper()}_BACKEND" if feature else "WEB_SCOUT_CLASSIFICATION_BACKEND"
    value = os.getenv(variable, os.getenv("WEB_SCOUT_CLASSIFICATION_BACKEND", "jev")).strip().lower()
    if value not in {"jev", "gpt"}:
        raise ClassificationError(f"{variable} must be jev or gpt")
    return value


def require_credentials():
    if not os.getenv("TYPESAFE_API_KEY", "").strip():
        raise ClassificationError("Jev requires TYPESAFE_API_KEY")


async def judge(state, questions, *, model=JEV_MODEL):
    from .scraping._resources import model_admission

    require_credentials()
    loop = asyncio.get_running_loop()
    key = (model, os.getenv("TYPESAFE_BASE_URL", ""))
    clients = _clients.setdefault(loop, {})
    if key not in clients:
        clients[key] = AsyncTypeSafeClient(
            model=model,
            timeout=30,
            retry=RetryPolicy(max_retries=2, http_statuses={404, 408, 429, *range(500, 600)}),
        )
    started = time.perf_counter()
    try:
        async with asyncio.timeout(100), model_admission():
            response = await clients[key].system_one(state, questions)
        if set(response.nouls) != set(questions):
            raise ClassificationError("Jev returned incomplete judgments")
        scores = {name: answer.noul for name, answer in response.nouls.items()}
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in scores.values()):
            raise ClassificationError("Jev returned invalid probabilities")
        return scores
    except (TypeSafeError, TimeoutError) as exc:
        raise ClassificationError(f"Jev classification failed: {type(exc).__name__}") from exc
    finally:
        logger.debug(
            "[classification-timing] model=%s questions=%d seconds=%.3f",
            model,
            len(questions),
            time.perf_counter() - started,
        )


async def close_classification_clients():
    for client in _clients.pop(asyncio.get_running_loop(), {}).values():
        await client.aclose()


def _source_batches(sources):
    """Bound serialized evidence bytes, retaining URLs and overlapping passages.

    UTF-8 bytes are a conservative token upper bound; leave ample room for
    query, requirements and questions within Jev's 32k per-question context.
    """
    batch = []
    size = 2
    for source in sources:
        text = source["text"]
        # Character slices keep Unicode intact; overlap preserves boundary context.
        for start in range(0, max(1, len(text)), 1800):
            fragment = {**source, "text": text[start : start + 2000]}
            length = len(json.dumps(fragment, ensure_ascii=False).encode("utf-8")) + 1
            if batch and size + length > 16000:
                yield batch
                batch, size = [], 2
            batch.append(fragment)
            size += length
    if batch:
        yield batch


async def coverage(query, requirements, sources, candidates):
    """Only scraped evidence fills requirements; candidates are routing hints."""
    probabilities = {i: 0.0 for i in range(len(requirements))}
    for source_batch in _source_batches(sources):
        for start in range(0, len(requirements), 16):
            batch = requirements[start : start + 16]
            probabilities.update(
                {
                    start + int(key[1:]): max(probabilities[start + int(key[1:])], value)
                    for key, value in (
                        await judge(
                            {"query": query, "requirements": batch, "scraped_sources": source_batch},
                            {
                                f"r{i}": Noul(
                                    instructions=f"Do `scraped_sources` fully answer `requirements[{i}]` of `query`?",
                                    criteria={
                                        "true": (
                                            'Explicit facts supply every requested entity, geography, time period, '
                                            'measurement and '
                                            'qualifier. Numerical claims retain their units and scope. A cited source '
                                            'supports the '
                                            "answer."
                                        ),
                                        "false": (
                                            'Any requested detail is absent, contradicted or merely inferred. General '
                                            'guidance, '
                                            'navigation, titles and search snippets are not evidence. '
                                        'Similar countries, '
                                            'years or '
                                            "quantities do not fulfill the requirement."
                                        ),
                                    },
                                )
                                for i in range(len(batch))
                            },
                        )
                    ).items()
                }
            )
    missing = [req for i, req in enumerate(requirements) if probabilities[i] < ACCEPT]
    selected = []
    if missing:
        for start in range(0, len(candidates), 16):
            batch = candidates[start : start + 16]
            scores = await judge(
                {"query": query, "missing_requirements": missing, "candidates": batch},
                {
                    f"c{i}": Noul(
                        instructions=(
                            f"Is `candidates[{i}]` likely to provide a source answering one of "
                            "`missing_requirements`? Judge its title, snippet and URL as routing hints only."
                        ),
                        criteria={
                            "true": (
                                "Candidate specifically matches a missing requirement and its geography/time scope."
                            ),
                            "false": (
                                "Generic, irrelevant, duplicate, wrong geography/time, or insufficient information to "
                                "expect useful evidence."
                            ),
                        },
                    )
                    for i in range(len(batch))
                },
            )
            selected.extend(item["url"] for i, item in enumerate(batch) if scores[f"c{i}"] >= 0.5)
    return not missing, "; ".join(missing), selected


def _normalized(text):
    return re.sub(r"\s+", " ", text.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"}))).strip()


async def supported_evidence(evidence, page_texts):
    """Classify each evidence item against the actual cited pages."""
    kept = []
    for start in range(0, len(evidence), 8):
        batch = evidence[start : start + 8]
        items = []
        original = []
        for item in batch:
            source = "\n\n".join(page_texts.get(page, "") for page in range(item.page_start, item.page_end + 1))
            if not source:
                continue
            quotes = re.findall(r'["“]([^"“”]+)["”]', item.text)
            if any(_normalized(quote) not in _normalized(source) for quote in quotes):
                continue
            items.append({"claim": item.text, "source": source})
            original.append(item)
        if not items:
            continue
        scores = await judge(
            {"items": items},
            {
                f"e{i}": Noul(
                    instructions=f"Does `items[{i}].source` support every factual assertion in `items[{i}].claim`?",
                    criteria={
                        "true": (
                            "Source explicitly supports the claim or a faithful paraphrase, with matching entities, "
                            "geography, dates, quantities, units and qualifiers."
                        ),
                        "false": (
                            "Any claim is absent, fabricated, contradicted, attributed to another entity, or changes "
                            "a number, unit, time, negation, uncertainty or geographic scope. Mere related subject "
                            "matter is insufficient."
                        ),
                    },
                )
                for i in range(len(items))
            },
        )
        kept.extend(item for i, item in enumerate(original) if scores[f"e{i}"] >= ACCEPT)
    return kept
