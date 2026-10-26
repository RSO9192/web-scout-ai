"""Render judged eval rows as the markdown summary."""

from __future__ import annotations

EXAMPLE_QUESTION_ID = "guatemala"
_CHECKED = ("supported", "not_supported", "contradicted")


def summarize_runs(records: list[dict]) -> dict:
    """Aggregate one system's runs on one or more questions.

    Error runs are counted in ``errors`` and excluded elsewhere. The supported
    rate pools claims across runs and counts only claims the judge checked
    against a fetched page.
    """
    ok = [record for record in records if not record.get("error")]
    judged = [record for record in ok if _claims(record) is not None]
    counts = {verdict: 0 for verdict in (*_CHECKED, "uncited", "unfetchable")}
    for record in judged:
        for claim in _claims(record):
            if claim.get("verdict") in counts:
                counts[claim["verdict"]] += 1
    checked = sum(counts[verdict] for verdict in _CHECKED)
    scores = [
        record["judgment"]["completeness"]["score"]
        for record in judged
        if (record["judgment"].get("completeness") or {}).get("score") is not None
    ]
    return {
        "completeness": _mean(scores),
        "supported_rate": counts["supported"] / checked if checked else None,
        "unsupported": (counts["not_supported"] + counts["contradicted"]) / len(judged) if judged else None,
        "uncited": counts["uncited"] / len(judged) if judged else None,
        "unfetchable": counts["unfetchable"] / len(judged) if judged else None,
        "pages": _mean([len(record.get("cited_urls") or []) for record in ok]),
        "errors": len(records) - len(ok),
        "runs": len(records),
        "judged": len(judged),
    }


def format_report(rows: list[dict], *, run_date: str, systems: list[str], labels: dict[str, str]) -> str:
    """Render the table, pairwise summary, per-question notes, and the worked example."""
    names = " / ".join(labels[system] for system in systems)
    lines = [
        "# Evaluation",
        "",
        f"Run on {run_date}. Cells are {names}, averaged over repeated runs.",
        "",
        "Every page an answer cites is downloaded by an independent fetcher that shares no code with Web Scout. "
        "A judge (`gemini/gemini-3.1-pro-preview`, given today's date) splits each answer into claims and checks "
        "each claim against the text of the page it cites. Claims on pages that could not be fetched are reported "
        "separately and left out of the supported rate. Completeness (1–5) is scored without the system's name. "
        "The pairwise preference shows both answers twice with the order swapped; only a consistent verdict counts "
        "as a win. Deep questions need facts from inside documents; control questions can be answered from a "
        "search snippet. Unfetchable claims and runs judged show what each figure rests on.",
        "",
        "| Question | Kind | Completeness | Claims supported by cited page | Unsupported or contradicted "
        "| Uncited claims | Pages cited | Unfetchable claims | Runs judged |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        summaries = [summarize_runs(row["runs"].get(system) or []) for system in systems]
        lines.append(_table_row(row["title"], row.get("kind", ""), summaries))
    for kind, title in (("deep", "**Deep questions**"), ("control", "**Control questions**")):
        kind_rows = [row for row in rows if row.get("kind") == kind]
        if kind_rows:
            summaries = [
                summarize_runs([record for row in kind_rows for record in row["runs"].get(system) or []])
                for system in systems
            ]
            lines.append(_table_row(title, "", summaries))
    lines.append("")
    _append_pairwise(lines, rows, systems, labels)
    _append_notes(lines, rows, systems, labels)
    _append_example(lines, rows, systems, labels)
    return "\n".join(lines)


def _table_row(title: str, kind: str, summaries: list[dict]) -> str:
    cells = [
        _cells(summaries, "completeness", lambda value: f"{value:.1f}"),
        _cells(summaries, "supported_rate", lambda value: f"{value:.0%}"),
        _cells(summaries, "unsupported", lambda value: f"{value:.1f}"),
        _cells(summaries, "uncited", lambda value: f"{value:.1f}"),
        _cells(summaries, "pages", lambda value: f"{value:.1f}"),
        _cells(summaries, "unfetchable", lambda value: f"{value:.1f}"),
        " / ".join(f"{summary['judged']}/{summary['runs']}" for summary in summaries),
    ]
    return f"| {title} | {kind} | " + " | ".join(cells) + " |"


def _cells(summaries: list[dict], key: str, fmt) -> str:
    values = []
    for summary in summaries:
        value = summary[key]
        if value is not None:
            values.append(fmt(value))
        elif summary["errors"] and summary["pages"] is None:
            values.append("error")
        else:
            values.append("—")
    return " / ".join(values)


def _append_pairwise(lines: list[str], rows: list[dict], systems: list[str], labels: dict[str, str]) -> None:
    pairs = [(systems[0], baseline) for baseline in systems[1:]]
    if not pairs:
        return
    lines.extend(["## Pairwise preference", ""])
    for system, baseline in pairs:
        tally = {"win": 0, "tie": 0, "loss": 0}
        eligible = 0
        for row in rows:
            for record, baseline_record in zip(row["runs"].get(system) or [], row["runs"].get(baseline) or []):
                eligible += not record.get("error") and not baseline_record.get("error")
            for entry in row.get("pairwise") or []:
                if (entry["system"], entry["baseline"]) != (system, baseline):
                    continue
                if entry["winner"] == system:
                    tally["win"] += 1
                elif entry["winner"] == baseline:
                    tally["loss"] += 1
                else:
                    tally["tie"] += 1
        judged = sum(tally.values())
        lines.append(
            f"- {labels[system]} vs {labels[baseline]}: {_plural(tally['win'], 'win', 'wins')}, "
            f"{_plural(tally['tie'], 'tie', 'ties')}, {_plural(tally['loss'], 'loss', 'losses')} "
            f"({judged} of {eligible} run pairs judged)"
        )
    lines.append("")


def _append_notes(lines: list[str], rows: list[dict], systems: list[str], labels: dict[str, str]) -> None:
    lines.extend(["## Notes per question", ""])
    for row in rows:
        lines.extend([f"### {row['title']}", ""])
        for system in systems:
            for number, record in enumerate(row["runs"].get(system) or [], start=1):
                prefix = f"- {labels[system]}, run {number}:"
                if record.get("error"):
                    lines.append(f"{prefix} error.")
                    continue
                judgment = record.get("judgment") or {}
                if judgment.get("error") or not judgment:
                    lines.append(f"{prefix} not judged.")
                    continue
                completeness = judgment.get("completeness") or {}
                lines.append(f"{prefix} completeness {completeness.get('score')}: {completeness.get('reason', '')}")
                unfetchable = sorted(
                    {url for claim in _claims(record) if claim["verdict"] == "unfetchable" for url in claim["urls"]}
                )
                if unfetchable:
                    lines.append(f"  - Unfetchable: {', '.join(unfetchable)}")
                for claim in _claims(record):
                    if claim["verdict"] == "contradicted":
                        lines.append(
                            f"  - Contradicted: “{claim['claim']}” ({claim['checked_url']}): {claim['reason']}"
                        )
        lines.append("")


def _append_example(lines: list[str], rows: list[dict], systems: list[str], labels: dict[str, str]) -> None:
    example = next((row for row in rows if row.get("id") == EXAMPLE_QUESTION_ID), None)
    if example is None:
        return
    lines.extend([f"## Example: {example['title']}", "", example.get("query") or "", ""])
    for system in systems:
        record = next((run for run in example["runs"].get(system) or [] if not run.get("error")), None)
        if record is None:
            continue
        lines.extend([f"### {labels[system]}", ""])
        queries = (record.get("extras") or {}).get("queries") or []
        if queries:
            lines.append("Searches:")
            lines.append("")
            for query in queries:
                lines.append(f"- `{query.get('query', '')}` ({query.get('language_code') or 'unspecified'})")
            lines.append("")
        lines.append("Pages cited:")
        lines.append("")
        lines.extend(f"- {url}" for url in record.get("cited_urls") or [])
        lines.extend(["", "Answer:", "", record.get("answer") or "", ""])
        supported = [claim for claim in _claims(record) or [] if claim["verdict"] == "supported"][:3]
        if supported:
            lines.append("Claims checked against the page:")
            lines.append("")
            for claim in supported:
                lines.append(f"- “{claim['claim']}” — supported by {claim['checked_url']}: {claim['reason']}")
            lines.append("")


def _claims(record: dict) -> list[dict] | None:
    judgment = record.get("judgment") or {}
    if not judgment or judgment.get("error"):
        return None
    return judgment.get("claims") or []


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"
