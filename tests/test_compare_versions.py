"""Pure parts of the old-versus-new comparison runner in ``scripts/compare_versions.py``."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import compare_versions  # noqa: E402
from compare_versions import (  # noqa: E402
    bucket_summary,
    check_figures_in_body,
    claim_figures,
    data_sources_distinct,
    expected_matches,
    format_summary,
    load_question,
    parse_log_counters,
    parse_log_urls,
    pdf_counters,
    regression_reasons,
    run_record_done,
    set_questions,
    summary_row,
)

LOG = (
    "[2026-10-26 18:00:00] INFO: [pipeline] open-mode follow-up on 2 links: "
    "['https://t.org/a', 'https://t.org/b']\n"
    "[2026-10-26 18:00:01] INFO: [extract] data_source_opened D1 https://t.org/a.json\n"
    "[2026-10-26 18:00:02] INFO: [extract] data_quote_dropped D7 unknown source\n"
)


def test_log_counters():
    assert parse_log_counters(LOG) == {
        "followup_waves": 1,
        "followup_links": 2,
        "data_sources_opened": 1,
        "data_quotes_recorded": 0,
        "data_quotes_dropped": 1,
    }


def test_log_counters_default_to_zero_for_old_logs():
    old_log = "[2026-10-26 18:00:00] INFO: [pipeline] Round 1 search\n"
    assert parse_log_counters(old_log) == dict.fromkeys(
        ["followup_waves", "followup_links", "data_sources_opened", "data_quotes_recorded", "data_quotes_dropped"], 0
    )


def test_log_counters_count_recorded_quotes_and_waves():
    log = (
        "[pipeline] open-mode follow-up on 1 links: ['https://x.org/1']\n"
        "[pipeline] open-mode follow-up on 3 links: ['https://x.org/2', 'https://x.org/3', 'https://x.org/4']\n"
        "[extract] data_quote_recorded D1 https://x.org/d.json\n"
        "[extract] data_quote_recorded D2 https://x.org/e.csv\n"
    )
    counters = parse_log_counters(log)
    assert counters["followup_waves"] == 2
    assert counters["followup_links"] == 4
    assert counters["data_quotes_recorded"] == 2


def test_log_urls():
    assert parse_log_urls(LOG) == {
        "followup_links": ["https://t.org/a", "https://t.org/b"],
        "data_sources_opened": ["https://t.org/a.json"],
    }


def test_data_file_citation_check_uses_raw_body():
    assert check_figures_in_body(["155.8", "999.9"], '{"area": 155.79873}') == {"155.8": True, "999.9": False}


def test_figure_check_tries_both_separator_readings():
    body = "municipio;area\nAltamira;1234.5\nPorto Velho;98765\n"
    assert check_figures_in_body(["1,234.5", "1.234,5", "98,765", "98.765", "1234"], body) == {
        "1,234.5": True,
        "1.234,5": True,
        "98,765": True,
        "98.765": True,
        "1234": False,
    }


def test_integer_figure_does_not_match_by_rounding():
    assert check_figures_in_body(["156"], '{"area": 155.79873}') == {"156": False}


def test_claim_figures_skip_years():
    claim = "In 2025, Altamira lost 155.8 km² and São Félix do Xingu lost 1,234 km² of forest."
    assert claim_figures(claim) == ["155.8", "1,234"]


def test_bucket_summary():
    summary = bucket_summary({"scraped": [{"url": "a"}], "scraped_irrelevant": [{"url": "b"}, {"url": "c"}]})
    assert summary == {"scraped": 1, "scraped_irrelevant": 2}


def test_claim_figures_skip_units_codes_and_crop_years():
    claim = "Region R12 lost 5,816 km2 in 2025/26, with PM2.5 levels rising."
    assert claim_figures(claim) == ["5,816"]


def test_claim_figures_keep_full_number_before_glued_unit():
    assert claim_figures("Altamira lost 155.8km² in 2025.") == ["155.8"]
    assert claim_figures("Funding reached US$1.2bn total.") == ["1.2"]
    assert claim_figures("It built 5,816km of road.") == ["5,816"]


def _entry(url):
    return {"url": url, "title": "", "content": "", "reference": ""}


def test_pdf_counters_count_distinct_pdf_urls_per_bucket():
    result = {
        "scraped": [
            _entry("https://a.org/report.pdf"),
            _entry("https://a.org/page"),
            _entry("https://a.org/x.PDF?v=2"),
        ],
        "scrape_failed": [_entry("https://b.org/f.pdf")],
        "source_http_error": [_entry("https://b.org/g.pdf#page=3")],
        "bot_detected": [_entry("https://b.org/h.pdf")],
        "scraped_irrelevant": [_entry("https://c.org/i.pdf"), _entry("https://c.org/pdf-guide")],
        "snippet_only": [_entry("https://a.org/report.pdf"), _entry("https://d.org/j.pdf")],
        "blocked_by_policy": [],
        "queries": ["q"],
        "synthesis": "s",
    }
    counters = pdf_counters(result)
    assert {key: counters[key] for key in ("pdf_urls_found", "pdf_scraped", "pdf_failed", "pdf_irrelevant")} == {
        "pdf_urls_found": 7,
        "pdf_scraped": 2,
        "pdf_failed": 3,
        "pdf_irrelevant": 1,
    }


def test_pdf_like_counters_count_download_patterns_separately():
    result = {
        "scraped": [
            _entry("https://repo.org/bitstream/handle/123/4/file"),
            _entry("https://repo.org/server/api/core/bitstreams/abc/content"),
            _entry("https://j.org/index.php/x/article/download/12/34"),
            _entry("https://a.org/report.pdf"),
            _entry("https://a.org/news"),
        ],
        "scrape_failed": [_entry("https://b.org/getfile?id=9&format=PDF"), _entry("https://b.org/files/download")],
        "scraped_irrelevant": [_entry("https://c.org/download/report.pdf")],
    }
    assert pdf_counters(result) == {
        "pdf_urls_found": 2,
        "pdf_scraped": 1,
        "pdf_failed": 0,
        "pdf_irrelevant": 1,
        "pdf_like_found": 5,
        "pdf_like_scraped": 3,
        "pdf_like_failed": 2,
        "pdf_like_irrelevant": 0,
    }


def test_pdf_counters_empty_result():
    assert set(pdf_counters({}).values()) == {0}


def test_data_sources_distinct():
    record = {
        "log_urls": {"data_sources_opened": ["https://t.org/a.json", "https://t.org/b.csv", "https://t.org/a.json"]}
    }
    assert data_sources_distinct(record) == 2
    assert data_sources_distinct({}) == 0


def _run(completeness=None, verdicts=(), pdf_scraped=0, pdf_like_scraped=0, run_error=False, judge_error=False):
    return {
        "completeness": completeness,
        "verdicts": list(verdicts),
        "pdf_scraped": pdf_scraped,
        "pdf_like_scraped": pdf_like_scraped,
        "run_error": run_error,
        "judge_error": judge_error,
    }


def test_no_regression_when_new_is_equal_or_better():
    old = _run(3, ["supported", "not_supported"], 1)
    new = _run(4, ["supported", "supported"], 2)
    assert regression_reasons(old, new) == []
    assert regression_reasons(old, old) == []


def test_regression_on_lower_completeness():
    assert regression_reasons(_run(4), _run(3)) == ["completeness 4 -> 3"]


def test_regression_on_supported_rate_drop_over_ten_points():
    old = _run(3, ["supported"] * 9 + ["not_supported"])  # 90%
    within = _run(3, ["supported"] * 8 + ["not_supported"] * 2)  # 80%, exactly 10 points lower
    beyond = _run(3, ["supported"] * 7 + ["not_supported"] * 3)  # 70%
    assert regression_reasons(old, within) == []
    assert regression_reasons(old, beyond) == ["supported rate 90% -> 70%"]


def test_regression_on_fewer_pdfs_scraped():
    assert regression_reasons(_run(pdf_scraped=2), _run(pdf_scraped=1)) == ["pdf_scraped 2 -> 1"]


def test_no_pdf_regression_when_pdf_like_makes_up_the_drop():
    old = _run(pdf_scraped=2, pdf_like_scraped=0)
    assert regression_reasons(old, _run(pdf_scraped=1, pdf_like_scraped=1)) == []
    assert regression_reasons(old, _run(pdf_scraped=1, pdf_like_scraped=3)) == []


def test_judge_error_on_either_side_skips_judged_checks():
    assert regression_reasons(_run(4, ["supported"]), _run(None, [], judge_error=True)) == []
    assert regression_reasons(_run(None, [], judge_error=True), _run(1, ["not_supported"])) == []
    assert regression_reasons(_run(4, pdf_scraped=2), _run(judge_error=True, pdf_scraped=1)) == ["pdf_scraped 2 -> 1"]


def test_run_error_is_the_reason_and_skips_derived_checks():
    assert regression_reasons(_run(4, ["supported"], pdf_scraped=2), _run(run_error=True)) == ["new run error"]
    assert regression_reasons(_run(run_error=True), _run(4, pdf_scraped=2)) == ["old run error"]
    assert regression_reasons(_run(run_error=True), _run(run_error=True)) == ["old run error", "new run error"]


def test_regression_when_new_has_no_completeness_but_old_has():
    assert regression_reasons(_run(3), _run(None)) == ["completeness 3 -> none"]


def test_no_rate_comparison_without_claims():
    assert regression_reasons(_run(3, ["supported"]), _run(3, [])) == []


def test_expected_matches_either_separator_reading():
    facts = [
        "Brazil: 3,256,050 ha/year (largest of any country) in 2020-2025.",
        "Pará: 1953.0813510747153 km² (about 1,953.1 km²).",
    ]
    answer = "Brazil loses 3.256.050 ha a year; Pará lost 1953,1 km² in 2025."
    assert expected_matches(facts, answer) == (2, 3)


def test_expected_matches_needs_exact_value_not_rounding():
    assert expected_matches(["Latest value: 58.5468405650061%."], "About 58.5% of land.") == (0, 1)


def test_expected_matches_ignores_one_and_two_digit_integers():
    assert expected_matches(["Top 3 states, 12 municipalities, 27.61 km²."], "3 states, 12 places, 27.61") == (1, 1)


def test_expected_matches_counts_each_figure_once():
    assert expected_matches(["India 588,000.", "India: 588,000 ha."], "India 588,000 ha") == (1, 1)


QUESTIONS = [
    {"id": "guatemala", "kind": "eval", "query": "Q guatemala", "expected": None},
    {"id": "owid", "kind": "dashboard", "query": "Q owid", "expected": {"facts": ["Brazil 3,256,050"]}},
]


def test_load_question_from_questions_file(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps(QUESTIONS), encoding="utf-8")
    assert load_question("owid", path)["query"] == "Q owid"
    with pytest.raises(SystemExit):
        load_question("brazil", path)


def test_load_question_defaults_to_eval_questions():
    assert load_question("brazil")["id"] == "brazil"


def test_set_questions_puts_eval_first_and_skips_duplicates(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps(QUESTIONS), encoding="utf-8")
    with_eval = set_questions(path, include_eval=True)
    ids = [question["id"] for question, _ in with_eval]
    assert ids[0] == "guatemala" and ids.count("guatemala") == 1 and ids[-1] == "owid"
    assert "brazil" in ids
    assert dict((question["id"], source) for question, source in with_eval)["owid"] == path
    assert [(question["id"], source) for question, source in set_questions(path, include_eval=False)] == [
        ("guatemala", path),
        ("owid", path),
    ]


def test_run_record_done(tmp_path):
    record = tmp_path / "old.json"
    assert not run_record_done(record)
    record.write_text(json.dumps({"label": "old", "error": None, "result": {"synthesis": "x"}}), encoding="utf-8")
    assert run_record_done(record)
    record.write_text(json.dumps({"label": "old", "error": "Timed out", "result": None}), encoding="utf-8")
    assert not run_record_done(record)
    record.write_text("{truncated", encoding="utf-8")
    assert not run_record_done(record)


def _record(label, *, error=None, wall=100.0, scraped=(), calls=5, waves=0, links=0):
    return {
        "label": label,
        "error": error,
        "wall_time_s": wall,
        "model_calls": calls,
        "result": {"synthesis": "x", "scraped": [_entry(url) for url in scraped]},
        "log_counters": {"followup_waves": waves, "followup_links": links},
        "log_urls": {"data_sources_opened": []},
    }


def _report_run(score=None, verdicts=(), judge_error=False):
    if judge_error:
        judgment = {"error": "boom"}
    elif score is None:
        judgment = None
    else:
        judgment = {"completeness": {"score": score}, "claims": [{"verdict": v} for v in verdicts]}
    return {"answer": "x", "cited_urls": [], "judgment": judgment}


def _row(question_id, old_record, new_record, old_run, new_run):
    return summary_row(
        {"id": question_id, "kind": "deep"},
        {"runs": {"old": old_run, "new": new_run}},
        {"old": old_record, "new": new_record},
    )


def test_summary_row_judge_error_is_listed_not_flagged():
    row = _row("q", _record("old"), _record("new"), _report_run(4, ["supported"]), _report_run(judge_error=True))
    assert row["regression"] == []
    assert row["judge_errors"] == ["new"]
    row = _row("q", _record("old"), _record("new"), _report_run(judge_error=True), _report_run(2, ["supported"]))
    assert row["regression"] == [] and row["judge_errors"] == ["old"]
    summary = format_summary([row])
    assert "## Not judged (judge error)" in summary and "- q old" in summary


def test_summary_row_run_error_reason():
    row = _row(
        "q",
        _record("old", scraped=["https://a.org/x.pdf"]),
        _record("new", error="Timed out", wall=None),
        _report_run(4, ["supported"]),
        _report_run(),
    )
    assert row["regression"] == ["new run error"]


def test_summary_wall_time_totals_exclude_failed_runs_and_report_median():
    rows = [
        _row("a", _record("old", wall=100.4), _record("new", wall=120.6), _report_run(), _report_run()),
        _row("b", _record("old", wall=300.0), _record("new", wall=200.0), _report_run(), _report_run()),
        _row(
            "c", _record("old", wall=50.0), _record("new", error="Timed out", wall=None), _report_run(), _report_run()
        ),
    ]
    summary = format_summary(rows)
    assert "| wall time (s), runs without error | 450 | 321 |" in summary
    assert "Excluded from wall-time totals: old 0, new 1." in summary
    assert "| median wall time (s), questions where both ran | 200 | 160 |" in summary
    assert "| a | deep |" in summary and " 100 / 121 " in summary


def test_run_set_skips_done_runs(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "web_scout").mkdir(parents=True)
    (src / "web_scout" / "__init__.py").write_text("", encoding="utf-8")
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps(QUESTIONS), encoding="utf-8")
    out = tmp_path / "out"
    (out / "owid").mkdir(parents=True)
    (out / "owid" / "old.json").write_text(json.dumps({"label": "old", "error": None, "result": {}}), encoding="utf-8")
    calls = []

    def fake_run_version(**kwargs):
        calls.append((kwargs["question_id"], kwargs["label"], kwargs["questions_path"]))
        return 0

    monkeypatch.setattr(compare_versions, "run_version", fake_run_version)
    status = compare_versions.run_set(
        questions_path=questions,
        include_eval=False,
        old_src=src,
        new_src=src,
        out=out,
        env_file=tmp_path / ".env",
        timeout_s=1,
    )
    assert status == 0
    assert calls == [("guatemala", "old", questions), ("guatemala", "new", questions), ("owid", "new", questions)]
