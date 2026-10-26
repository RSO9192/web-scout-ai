"""Tests for the Web Scout vs OpenAI web-search eval."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.metrics import (  # noqa: E402
    markdown_link_urls,
    normalize_url,
    record_error,
    record_openai_response,
    record_web_scout,
)


def test_normalize_url_strips_only_tracking_params_and_trailing_slash():
    assert normalize_url("https://a.org/x/?utm_source=openai") == "https://a.org/x"
    assert normalize_url("https://a.org/p?id=3&utm_medium=x") == "https://a.org/p?id=3"
    assert normalize_url("  ") == ""
    assert normalize_url(None) == ""


def test_markdown_link_urls_keeps_parentheses_and_dedupes():
    text = (
        "See [Petén](https://en.wikipedia.org/wiki/Pet%C3%A9n_(department)). "
        "Also [gov.br](https://www.gov.br/inpe/x/?utm_source=openai) and [again](https://www.gov.br/inpe/x)."
    )
    assert markdown_link_urls(text) == [
        "https://en.wikipedia.org/wiki/Pet%C3%A9n_(department)",
        "https://www.gov.br/inpe/x",
    ]


def test_record_web_scout_keeps_cited_urls_and_queries_only():
    result = {
        "synthesis": "Maya [CONAP](https://conap.gob.gt/sigap).",
        "scraped": [{"url": "https://conap.gob.gt/sigap", "title": "SIGAP", "content": "x"}],
        "bot_detected": [{"url": "https://b.org", "content": "Cloudflare"}],
        "queries": [{"query": "q", "language_code": "es"}],
    }

    record = record_web_scout(result)

    assert record["answer"] == "Maya [CONAP](https://conap.gob.gt/sigap)."
    assert record["cited_urls"] == ["https://conap.gob.gt/sigap"]
    assert set(record["extras"]) == {"queries"}
    assert record["extras"]["queries"][0]["language_code"] == "es"
    assert record["error"] is None


def _openai_payload(text: str, annotations: list[dict]) -> dict:
    return {
        "output": [
            {
                "type": "web_search_call",
                "action": {
                    "type": "search",
                    "sources": [
                        {"type": "url", "url": "https://www.iucn.org/hotspots"},
                        {"type": "oai-weather", "name": "weather"},
                    ],
                },
            },
            {"type": "web_search_call", "action": {"type": "open_page", "url": "https://conap.gob.gt/sigap"}},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": annotations}],
            },
        ]
    }


def test_record_openai_uses_answer_links_and_keeps_consulted():
    text = "CONAP lists the Maya Biosphere. ([conap](https://conap.gob.gt/sigap/?utm_source=openai))"
    payload = _openai_payload(
        text, [{"type": "url_citation", "url": "https://conap.gob.gt/sigap/?utm_source=openai", "title": "SIGAP"}]
    )

    record = record_openai_response(payload)

    assert record["answer"] == text
    assert record["cited_urls"] == ["https://conap.gob.gt/sigap"]
    assert "https://www.iucn.org/hotspots" in record["extras"]["consulted_urls"]
    assert record["extras"]["open_page_urls"] == ["https://conap.gob.gt/sigap"]
    assert record["error"] is None


def test_record_openai_appends_annotation_urls_missing_from_links():
    payload = _openai_payload("Plain text.", [{"type": "url_citation", "url": "https://a.example/"}])

    assert record_openai_response(payload)["cited_urls"] == ["https://a.example"]


def test_record_empty_answer_is_error():
    assert record_web_scout({"synthesis": ""})["error"]
    assert record_openai_response({"output": []})["error"]
    assert record_error("boom") == {"answer": "", "cited_urls": [], "extras": {}, "error": "boom"}


def test_is_pdf_by_magic_bytes():
    from eval.fetch import is_pdf

    assert is_pdf(b"%PDF-1.7 ...", "application/octet-stream")
    assert is_pdf(b"", "application/pdf")
    assert not is_pdf(b"<html>", "text/html")


def test_html_to_text_drops_scripts_and_navigation():
    from eval.fetch import html_to_text

    text = html_to_text(
        "<html><body><nav>Menu</nav><script>var x=1;</script><h1>Zona inti</h1>"
        "<p>No-take zone.</p><footer>Copyright</footer></body></html>"
    )
    assert "Zona inti" in text and "No-take zone." in text
    assert "Menu" not in text and "var x" not in text and "Copyright" not in text


def test_fetch_page_caches_and_does_not_refetch(tmp_path):
    import httpx

    from eval.fetch import fetch_page

    calls = []
    body = "<html><body><h1>Zona inti</h1><p>" + "No-take zone for all fishing gear. " * 10 + "</p></body></html>"

    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    first = fetch_page("https://kkprajaampat.id/x/?utm_source=openai", tmp_path, client=client)
    second = fetch_page("https://kkprajaampat.id/x", tmp_path, client=client)

    assert first["ok"] and "No-take zone" in first["text"]
    assert first["url"] == "https://kkprajaampat.id/x" and first["fetched_at"]
    assert second == first and len(calls) == 1


def test_fetch_page_marks_403_and_short_text_as_unfetchable(tmp_path):
    import httpx

    from eval.fetch import fetch_page

    blocked = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, text="blocked")))
    page = fetch_page("https://x.org", tmp_path, client=blocked)
    assert page["ok"] is False and "403" in page["error"]

    shell = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, headers={"content-type": "text/html"}, text="<p>Loading…</p>")
        )
    )
    page = fetch_page("https://y.org", tmp_path, client=shell)
    assert page["ok"] is False and page["error"]


def test_fetch_page_routes_octet_stream_pdf_to_pdf_converter(tmp_path, monkeypatch):
    import httpx

    import eval.fetch as fetch

    monkeypatch.setattr(fetch, "pdf_to_text", lambda content: "Peraturan zonasi " * 30)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, headers={"content-type": "application/octet-stream"}, content=b"%PDF-1.4 x")
        )
    )
    page = fetch.fetch_page("https://jdih.kkp.go.id/download?id=7", tmp_path, client=client)
    assert page["ok"] and page["text"].startswith("Peraturan zonasi")


TODAY = "2026-10-26"


def _fake_complete(*, check_indices=None, pair_winners=None, captured=None):
    """Stand-in for eval.judge._complete returning schema instances keyed on schema type."""
    import re

    from eval import judge

    pair_winners = list(pair_winners or [])

    def complete(system, user, schema):
        if captured is not None:
            captured.append(user)
        if schema is judge.ExtractedClaims:
            return judge.ExtractedClaims(
                claims=[
                    judge.ExtractedClaim(claim="c1", urls=["https://a.org/"]),
                    judge.ExtractedClaim(claim="c2", urls=["https://b.org"]),
                    judge.ExtractedClaim(claim="c3", urls=[]),
                    judge.ExtractedClaim(claim="c4", urls=["https://not-cited.org"]),
                    judge.ExtractedClaim(claim="c5", urls=["https://b.org", "https://a.org"]),
                ]
            )
        if schema is judge.ClaimChecks:
            numbers = [int(n) for n in re.findall(r"^(\d+)\. ", user, flags=re.M)]
            keep = numbers if check_indices is None else check_indices
            return judge.ClaimChecks(
                checks=[judge.ClaimCheck(index=n, verdict="supported", reason="on page") for n in keep]
            )
        if schema is judge.ScoreReason:
            return judge.ScoreReason(score=4, reason="Names the reserves.")
        if schema is judge.PairVerdict:
            return judge.PairVerdict(winner=pair_winners.pop(0), reason="More specific.")
        raise AssertionError(schema)

    return complete


def _fake_pages(url):
    if url == "https://a.org":
        return {"url": url, "ok": True, "text": "c1 and c5 are on this page.", "error": ""}
    return {"url": url, "ok": False, "text": "", "error": "HTTP 403"}


_RECORD = {"answer": "x", "cited_urls": ["https://a.org", "https://b.org"], "error": None}


def test_judge_record_assigns_code_verdicts(monkeypatch):
    from eval import judge

    monkeypatch.setattr(judge, "_complete", _fake_complete())

    judgment = judge.judge_record(query="q", record=_RECORD, today=TODAY, get_page=_fake_pages)

    verdicts = {claim["claim"]: claim["verdict"] for claim in judgment["claims"]}
    assert verdicts == {"c1": "supported", "c2": "unfetchable", "c3": "uncited", "c4": "uncited", "c5": "supported"}
    c5 = next(claim for claim in judgment["claims"] if claim["claim"] == "c5")
    assert c5["checked_url"] == "https://a.org"
    assert judgment["completeness"] == {"score": 4, "reason": "Names the reserves."}


def test_judge_record_marks_missing_verdicts_as_judge_error(monkeypatch):
    from eval import judge

    monkeypatch.setattr(judge, "_complete", _fake_complete(check_indices=[1]))

    judgment = judge.judge_record(query="q", record=_RECORD, today=TODAY, get_page=_fake_pages)

    verdicts = {claim["claim"]: claim["verdict"] for claim in judgment["claims"]}
    assert verdicts["c1"] == "supported"
    assert verdicts["c5"] == "judge_error"


def test_every_prompt_carries_today(monkeypatch):
    from eval import judge

    captured = []
    monkeypatch.setattr(judge, "_complete", _fake_complete(pair_winners=["A", "B"], captured=captured))

    judge.judge_record(query="q", record=_RECORD, today=TODAY, get_page=_fake_pages)
    judge.judge_pair(query="q", answer="x", baseline_answer="y", today=TODAY)

    assert captured and all(user.startswith(f"Today's date: {TODAY}.") for user in captured)


def test_judge_pair_needs_agreement_across_swapped_order(monkeypatch):
    from eval import judge

    monkeypatch.setattr(judge, "_complete", _fake_complete(pair_winners=["A", "A"]))
    assert judge.judge_pair(query="q", answer="x", baseline_answer="y", today=TODAY)[0] == "tie"

    monkeypatch.setattr(judge, "_complete", _fake_complete(pair_winners=["A", "B"]))
    winner, reasons = judge.judge_pair(query="q", answer="x", baseline_answer="y", today=TODAY)
    assert winner == "system" and len(reasons) == 2

    monkeypatch.setattr(judge, "_complete", _fake_complete(pair_winners=["B", "A"]))
    assert judge.judge_pair(query="q", answer="x", baseline_answer="y", today=TODAY)[0] == "baseline"


def test_select_passages_keeps_short_text_and_trims_long_text():
    from eval.judge import select_passages

    assert select_passages("short", ["anything"]) == "short"
    long_text = ("filler " * 20_000) + "Apuí 1,230 km² deforestation" + (" filler" * 20_000)
    picked = select_passages(long_text, ["Apuí deforestation 1,230 km²"], limit=3_000)
    assert "Apuí 1,230 km²" in picked and len(picked) <= 3_000


def _judged_run(verdicts, score=4, urls=("u1", "u2")):
    return {
        "answer": "answer",
        "cited_urls": list(urls),
        "extras": {},
        "error": None,
        "judgment": {
            "completeness": {"score": score, "reason": f"completeness {score}"},
            "claims": [
                {"claim": f"claim {i}", "urls": ["u1"], "verdict": v, "reason": f"reason {v}", "checked_url": "u1"}
                for i, v in enumerate(verdicts)
            ],
        },
    }


def test_summarize_runs_excludes_unfetchable_uncited_and_judge_errors_from_rate():
    from eval.report import summarize_runs

    run = _judged_run(
        ["supported", "supported", "not_supported", "contradicted", "unfetchable", "uncited", "judge_error"]
    )
    summary = summarize_runs([run, {"error": "boom"}])

    assert summary["supported_rate"] == 0.5
    assert summary["unsupported"] == 2 and summary["uncited"] == 1 and summary["unfetchable"] == 1
    assert summary["completeness"] == 4 and summary["pages"] == 2 and summary["errors"] == 1


def test_summarize_runs_all_errors_gives_none():
    from eval.report import summarize_runs

    summary = summarize_runs([{"error": "boom"}, {"error": "boom"}])
    assert summary["completeness"] is None and summary["supported_rate"] is None and summary["errors"] == 2


def _report_rows():
    deep_ws = [_judged_run(["supported", "supported"], score=5), _judged_run(["supported", "not_supported"], score=4)]
    deep_ws[0]["extras"] = {"queries": [{"query": "puntos calientes Guatemala", "language_code": "es"}]}
    deep_oai = [_judged_run(["supported", "contradicted", "unfetchable"], score=3), _judged_run(["uncited"], score=3)]
    return [
        {
            "id": "guatemala",
            "title": "Guatemala hotspots",
            "kind": "deep",
            "query": "What are the main biodiversity hotspots in Guatemala?",
            "runs": {"web_scout": deep_ws, "openai": deep_oai},
            "pairwise": [
                {"run": 0, "system": "web_scout", "baseline": "openai", "winner": "web_scout", "reasons": ["r", "r"]},
                {"run": 1, "system": "web_scout", "baseline": "openai", "winner": "tie", "reasons": ["r", "r"]},
            ],
        },
        {
            "id": "ifad_hq",
            "title": "IFAD headquarters",
            "kind": "control",
            "query": "Where is IFAD based?",
            "runs": {"web_scout": [_judged_run(["supported"], score=5)], "openai": [{"error": "boom"}]},
            "pairwise": [],
        },
    ]


_LABELS = {"web_scout": "Web Scout", "openai": "OpenAI web search"}


def test_report_has_kind_totals_pairwise_and_error_cells():
    from eval.report import format_report

    md = format_report(_report_rows(), run_date=TODAY, systems=["web_scout", "openai"], labels=_LABELS)

    assert (
        "| Question | Kind | Completeness | Claims supported by cited page | Unsupported or contradicted "
        "| Uncited claims | Pages cited |" in md
    )
    assert "| Guatemala hotspots | deep | 4.5 / 3.0 | 75% / 50% |" in md
    assert "| IFAD headquarters | control | 5.0 / error |" in md
    assert "| **Deep questions** |" in md and "| **Control questions** |" in md
    assert "Web Scout vs OpenAI web search: 1 win, 1 tie, 0 losses" in md


def test_report_notes_and_worked_example():
    from eval.report import format_report

    md = format_report(_report_rows(), run_date=TODAY, systems=["web_scout", "openai"], labels=_LABELS)

    assert "reason contradicted" in md  # contradicted claims carry the judge's reason
    assert "Unfetchable" in md and "u1" in md
    assert "completeness 5" in md
    assert "## Example: Guatemala hotspots" in md
    assert "`puntos calientes Guatemala` (es)" in md


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_load_questions_validates_kind_and_unique_ids(tmp_path):
    import pytest

    from eval.compare_search import load_questions

    duplicate = tmp_path / "dup.json"
    duplicate.write_text(
        '[{"id": "a", "title": "A", "kind": "deep", "query": "q"},'
        ' {"id": "a", "title": "B", "kind": "deep", "query": "q"}]'
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_questions(duplicate)

    bad_kind = tmp_path / "kind.json"
    bad_kind.write_text('[{"id": "a", "title": "A", "kind": "easy", "query": "q"}]')
    with pytest.raises(ValueError, match="kind"):
        load_questions(bad_kind)


def test_bundled_questions_have_deep_and_control():
    from eval.compare_search import load_questions

    questions = load_questions(REPO_ROOT / "eval" / "questions.json")
    assert {question["kind"] for question in questions} == {"deep", "control"}
    assert {"guatemala", "brazil", "senegal", "indonesia"} <= {question["id"] for question in questions}


def _ok_record(answer):
    return {"answer": answer, "cited_urls": [], "extras": {}, "error": None}


def test_score_row_skips_error_runs_in_pairwise(monkeypatch, tmp_path):
    from eval import compare_search

    pair_calls = []
    monkeypatch.setattr(
        compare_search, "judge_record", lambda **kwargs: {"completeness": {"score": 3, "reason": ""}, "claims": []}
    )

    def fake_pair(**kwargs):
        pair_calls.append(kwargs)
        return "system", ["first", "second"]

    monkeypatch.setattr(compare_search, "judge_pair", fake_pair)
    row = {
        "id": "x",
        "query": "q",
        "runs": {
            "web_scout": [_ok_record("ws0"), _ok_record("ws1")],
            "openai": [_ok_record("oa0"), {"answer": "", "cited_urls": [], "extras": {}, "error": "boom"}],
        },
    }

    scored = compare_search.score_row(row, today=TODAY, pages_dir=tmp_path)

    assert scored["pairwise"] == [
        {"run": 0, "system": "web_scout", "baseline": "openai", "winner": "web_scout", "reasons": ["first", "second"]}
    ]
    assert pair_calls[0]["answer"] == "ws0" and pair_calls[0]["baseline_answer"] == "oa0"
    assert "judgment" not in scored["runs"]["openai"][1]
    assert scored["runs"]["web_scout"][1]["judgment"]["completeness"]["score"] == 3


def test_score_row_stores_judge_exceptions(monkeypatch, tmp_path):
    from eval import compare_search

    def boom(**kwargs):
        raise RuntimeError("judge down")

    monkeypatch.setattr(compare_search, "judge_record", boom)
    monkeypatch.setattr(compare_search, "judge_pair", boom)
    row = {"id": "x", "query": "q", "runs": {"web_scout": [_ok_record("ws0")], "openai": [_ok_record("oa0")]}}

    scored = compare_search.score_row(row, today=TODAY, pages_dir=tmp_path)

    assert "judge down" in scored["runs"]["web_scout"][0]["judgment"]["error"]
    assert scored["pairwise"] == []


def test_fetch_page_rejects_non_text_content(tmp_path):
    import httpx

    from eval.fetch import fetch_page

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"\xff\xd8\xff" * 500)
        )
    )
    page = fetch_page("https://theseapeople.org/zonation.jpg", tmp_path, client=client)
    assert page["ok"] is False and "image/jpeg" in page["error"]


def test_judge_record_treats_unreadable_page_as_unfetchable(monkeypatch):
    from eval import judge

    fake = _fake_complete()

    def complete(system, user, schema):
        if schema is judge.ClaimChecks:
            return judge.ClaimChecks(
                checks=[
                    judge.ClaimCheck(index=1, verdict="unreadable", reason="Garbled OCR."),
                    judge.ClaimCheck(index=2, verdict="supported", reason="on page"),
                ]
            )
        return fake(system, user, schema)

    monkeypatch.setattr(judge, "_complete", complete)

    judgment = judge.judge_record(query="q", record=_RECORD, today=TODAY, get_page=_fake_pages)

    c1 = next(claim for claim in judgment["claims"] if claim["claim"] == "c1")
    assert c1["verdict"] == "unfetchable" and c1["reason"] == "Garbled OCR."


def test_select_passages_default_keeps_a_long_non_english_document_whole():
    from eval.judge import select_passages

    # A 179-page Indonesian zoning plan: English claims share no words with it,
    # so trimming by word overlap would drop the table that supports them.
    document = "| 26 | Bubu tradisional (Catatan: Dengan Kuota) | X | Dengan Izin* |\n" * 11_000
    assert len(document) > 700_000
    assert select_passages(document, ["Traditional bubu traps require permission"]) == document


def test_judge_record_tries_every_cited_page_before_not_supported(monkeypatch):
    import re

    from eval import judge

    def complete(system, user, schema):
        if schema is judge.ExtractedClaims:
            return judge.ExtractedClaims(
                claims=[
                    judge.ExtractedClaim(claim="no-go zone", urls=["https://a.org", "https://d.org"]),
                    judge.ExtractedClaim(claim="absent everywhere", urls=["https://a.org", "https://d.org"]),
                ]
            )
        if schema is judge.ClaimChecks:
            numbers = [(int(n), text) for n, text in re.findall(r"^(\d+)\. (.*)$", user, flags=re.M)]
            on_d = "Page text:\nD page" in user
            return judge.ClaimChecks(
                checks=[
                    judge.ClaimCheck(
                        index=n,
                        verdict="supported" if on_d and text == "no-go zone" else "not_supported",
                        reason="d" if on_d else "a",
                    )
                    for n, text in numbers
                ]
            )
        return judge.ScoreReason(score=3, reason="")

    pages = {
        "https://a.org": {"ok": True, "text": "A page"},
        "https://d.org": {"ok": True, "text": "D page"},
    }
    monkeypatch.setattr(judge, "_complete", complete)
    record = {"answer": "x", "cited_urls": ["https://a.org", "https://d.org"], "error": None}

    judgment = judge.judge_record(query="q", record=record, today=TODAY, get_page=pages.__getitem__)

    by_claim = {claim["claim"]: claim for claim in judgment["claims"]}
    assert by_claim["no-go zone"]["verdict"] == "supported"
    assert by_claim["no-go zone"]["checked_url"] == "https://d.org"
    assert by_claim["absent everywhere"]["verdict"] == "not_supported"
    assert by_claim["absent everywhere"]["checked_url"] == "https://a.org"


# --- Final review fixes ---


def test_normalize_url_leaves_other_query_bytes_untouched():
    assert normalize_url("https://x.gov/d?MjAyNA==") == "https://x.gov/d?MjAyNA=="
    assert normalize_url("https://x.gov/d?q=a%20b&path=x/y") == "https://x.gov/d?q=a%20b&path=x/y"
    assert normalize_url("https://x.gov/d?flag") == "https://x.gov/d?flag"
    assert normalize_url("https://x.gov/d?a=1;b=2") == "https://x.gov/d?a=1;b=2"
    assert normalize_url("https://x.gov/d?id=7&utm_source=openai&x=1") == "https://x.gov/d?id=7&x=1"


def test_record_openai_joins_every_text_part():
    payload = {
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "First part.", "annotations": []}]},
            {"type": "message", "content": [{"type": "output_text", "text": "Second part.", "annotations": []}]},
        ]
    }
    answer = record_openai_response(payload)["answer"]
    assert "First part." in answer and "Second part." in answer


def test_blind_judges_never_see_tracking_params(monkeypatch):
    from eval import judge

    captured = []
    monkeypatch.setattr(judge, "_complete", _fake_complete(pair_winners=["A", "B"], captured=captured))
    answer = "Maya ([conap.gob.gt](https://conap.gob.gt/sigap/?utm_source=openai))"
    record = {"answer": answer, "cited_urls": ["https://conap.gob.gt/sigap"], "error": None}

    judge.judge_record(query="q", record=record, today=TODAY, get_page=_fake_pages)
    judge.judge_pair(query="q", answer=answer, baseline_answer="y", today=TODAY)

    assert captured and not any("utm_source" in user for user in captured)


def test_judge_record_keeps_other_pages_when_one_check_call_fails(monkeypatch):
    import re

    from eval import judge

    def complete(system, user, schema):
        if schema is judge.ExtractedClaims:
            return judge.ExtractedClaims(
                claims=[
                    judge.ExtractedClaim(claim="on a", urls=["https://a.org"]),
                    judge.ExtractedClaim(claim="on d", urls=["https://d.org"]),
                ]
            )
        if schema is judge.ClaimChecks:
            if "Page text:\nD page" in user:
                raise RuntimeError("context length exceeded")
            numbers = [int(n) for n in re.findall(r"^(\d+)\. ", user, flags=re.M)]
            return judge.ClaimChecks(
                checks=[judge.ClaimCheck(index=n, verdict="supported", reason="") for n in numbers]
            )
        return judge.ScoreReason(score=3, reason="")

    pages = {"https://a.org": {"ok": True, "text": "A page"}, "https://d.org": {"ok": True, "text": "D page"}}
    monkeypatch.setattr(judge, "_complete", complete)
    record = {"answer": "x", "cited_urls": ["https://a.org", "https://d.org"], "error": None}

    judgment = judge.judge_record(query="q", record=record, today=TODAY, get_page=pages.__getitem__)

    by_claim = {claim["claim"]: claim for claim in judgment["claims"]}
    assert by_claim["on a"]["verdict"] == "supported"
    assert by_claim["on d"]["verdict"] == "judge_error"
    assert "context length exceeded" in by_claim["on d"]["reason"]
    assert judgment["completeness"]["score"] == 3


def test_check_instructions_treat_walls_and_shells_as_unreadable():
    from eval.judge import CHECK_INSTRUCTIONS

    unreadable = next(line for line in CHECK_INSTRUCTIONS.splitlines() if line.startswith("unreadable:"))
    assert "bot" in unreadable and "consent" in unreadable and "login" in unreadable
    assert "main content" in unreadable


def test_fetch_page_does_not_cache_transient_failures(tmp_path):
    import httpx

    from eval.fetch import fetch_page

    statuses = [503, 200]
    body = "<p>" + "Anchoveta quota for the first season. " * 10 + "</p>"

    def handler(request):
        status = statuses.pop(0)
        return httpx.Response(status, headers={"content-type": "text/html"}, text=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_page("https://produce.gob.pe/x", tmp_path, client=client)["ok"] is False
    assert fetch_page("https://produce.gob.pe/x", tmp_path, client=client)["ok"] is True

    def raise_timeout(request):
        raise httpx.ConnectTimeout("timed out")

    timeouts = httpx.Client(transport=httpx.MockTransport(raise_timeout))
    fetch_page("https://slow.gov/x", tmp_path, client=timeouts)
    assert not list(tmp_path.glob("*.json")) or all(
        "slow.gov" not in path.read_text() for path in tmp_path.glob("*.json")
    )


def test_fetch_page_caches_permanent_failures(tmp_path):
    import httpx

    from eval.fetch import fetch_page

    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404, text="missing")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetch_page("https://gone.org", tmp_path, client=client)
    fetch_page("https://gone.org", tmp_path, client=client)
    assert len(calls) == 1


def test_report_shows_unfetchable_claims_and_runs_judged():
    from eval.report import format_report

    md = format_report(_report_rows(), run_date=TODAY, systems=["web_scout", "openai"], labels=_LABELS)

    assert "| Pages cited | Unfetchable claims | Runs judged |" in md
    assert (
        "| IFAD headquarters | control | 5.0 / error | 100% / error | 0.0 / error | 0.0 / error | 2.0 / error "
        "| 0.0 / error | 1/1 / 0/1 |" in md
    )
    assert "(2 of 2 run pairs judged)" in md


def test_load_env_file_expands_references(tmp_path, monkeypatch):
    from eval.compare_search import _load_env_file

    monkeypatch.delenv("EVAL_TEST_TOKEN", raising=False)
    monkeypatch.delenv("EVAL_TEST_ALIAS", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("EVAL_TEST_TOKEN=abc123\nEVAL_TEST_ALIAS=${EVAL_TEST_TOKEN}\n")

    _load_env_file(env_file)

    import os

    assert os.environ["EVAL_TEST_ALIAS"] == "abc123"


def test_prompts_keep_collective_statements_collective():
    from eval.judge import CHECK_INSTRUCTIONS, EXTRACT_INSTRUCTIONS

    # "X, Y and Z protect mangroves and support tapirs" must not become "each of X, Y, Z supports tapirs".
    assert "collective" in EXTRACT_INSTRUCTIONS.lower()
    assert "as a whole" in CHECK_INSTRUCTIONS.lower()


def test_check_instructions_accept_direct_inference_but_not_outside_knowledge():
    from eval.judge import CHECK_INSTRUCTIONS

    supported = next(line for line in CHECK_INSTRUCTIONS.splitlines() if line.startswith("supported:"))
    assert "follows directly" in supported
    assert "own knowledge" in CHECK_INSTRUCTIONS
