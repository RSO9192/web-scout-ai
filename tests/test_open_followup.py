"""Tests for following extractor links in open (no include_domains) search mode."""

import logging
from types import SimpleNamespace

import pytest

from web_scout import _pipeline_flow
from web_scout._extractor_contract import ExtractorOutcome
from web_scout._pipeline_types import SearchIterationResult
from web_scout.tools import ResearchTracker

GOV = "https://www.gov.br/inpe/pt-br/assuntos/desmatamento"
PDF1 = "https://www.gov.br/inpe/pt-br/assuntos/nota-tecnica-prodes-2024.pdf"
PDF2 = "https://www.gov.br/inpe/pt-br/assuntos/apresentacao-prodes-2024.pdf"
ATTEMPTED = "https://www.gov.br/inpe/pt-br/assuntos/relatorio-anterior.pdf"
BLOCKED = "https://blocked.org/files/report-2024.pdf"
L3 = "https://terrabrasilis.dpi.inpe.br/downloads/report-amazon-2024.pdf"
L4 = "https://www.gov.br/inpe/pt-br/assuntos/boletim-cerrado-2024.pdf"
L5 = "https://www.gov.br/inpe/pt-br/assuntos/analise-estados-2024.pdf"

LOG_PREFIX = "[pipeline] open-mode follow-up on"


def failure_outcome(url, *, links):
    return ExtractorOutcome(
        url=url,
        status="failure",
        rendered_text=f"[scraped but irrelevant: {url}]",
        failure_kind="scraped_irrelevant",
        relevant_links=list(links),
    )


def success_outcome(url, *, links=()):
    return ExtractorOutcome(
        url=url,
        status="success",
        rendered_text=f"content of {url}",
        content=f"content of {url}",
        relevant_links=list(links),
    )


def iteration_with_outcomes(outcomes):
    urls = list(outcomes)
    return SearchIterationResult(
        extracted_contents=[o.rendered_text for o in outcomes.values()],
        iter_results=[(u, outcomes[u].rendered_text) for u in urls],
        outcomes_by_url=dict(outcomes),
    )


def tracker_with_attempted(*urls):
    tracker = ResearchTracker()
    for url in urls:
        tracker.record_scrape(url, "t", "content")
    return tracker


class RecordingTool:
    """Async scrape tool fake: records calls, records into a tracker, serves cached outcomes."""

    def __init__(self, scraped, tracker=None, outcome_for=None):
        self.scraped = scraped
        self.tracker = tracker
        self.outcome_for = outcome_for or (lambda url: success_outcome(url))
        self._outcome_cache = {}

    async def __call__(self, url):
        self.scraped.append(url)
        outcome = self.outcome_for(url)
        self._outcome_cache[ResearchTracker.normalize_url(url)] = outcome
        if self.tracker is not None:
            self.tracker.record_scrape(url, "t", outcome.rendered_text)
        return outcome.rendered_text


def recording_tool(scraped):
    return RecordingTool(scraped)


@pytest.mark.asyncio
async def test_links_from_rejected_pages_are_followed_with_cap(monkeypatch):
    it = iteration_with_outcomes(
        {
            GOV: failure_outcome(GOV, links=[PDF1, PDF2, GOV, ATTEMPTED, BLOCKED, L3, L4, L5]),
        }
    )
    tracker = tracker_with_attempted(ATTEMPTED)
    scraped = []
    res = await _pipeline_flow._follow_extractor_links(
        query="q",
        iteration_result=it,
        depth={"urls_followup": 4},
        followup_model=None,
        followup_backend="gpt",
        tracker=tracker,
        scrape_tool=recording_tool(scraped),
        exclude_domains=frozenset({"blocked.org"}),
    )
    assert set(scraped) <= {PDF1, PDF2, L3, L4, L5} and len(scraped) == 4
    assert GOV not in scraped and ATTEMPTED not in scraped and BLOCKED not in scraped
    assert [url for url, _ in res.iter_results] == scraped


@pytest.mark.asyncio
async def test_no_candidates_scrapes_nothing_and_does_not_log(caplog):
    it = iteration_with_outcomes({GOV: success_outcome(GOV, links=[GOV])})
    scraped = []
    with caplog.at_level(logging.INFO, logger="web_scout.agent"):
        res = await _pipeline_flow._follow_extractor_links(
            query="q",
            iteration_result=it,
            depth={"urls_followup": 4},
            followup_model=None,
            followup_backend="gpt",
            tracker=ResearchTracker(),
            scrape_tool=recording_tool(scraped),
            exclude_domains=None,
        )
    assert scraped == []
    assert res.iter_results == []
    assert LOG_PREFIX not in caplog.text


async def run_search_mode_with_fakes(monkeypatch, caplog, *, include_domains, rounds, followed_pages_have_links=False):
    """Run ``_run_search_mode_impl`` with fakes; round 1 searches, later rounds scrape the backlog."""
    tracker = ResearchTracker()
    scraped = []
    hub_urls = {f"https://www.gov.br/inpe/pt-br/assuntos/round-{i}" for i in range(rounds)}

    def outcome_for(url):
        if url in hub_urls:
            idx = url.rsplit("-", 1)[-1]
            return failure_outcome(url, links=[f"https://www.gov.br/inpe/pt-br/assuntos/nota-tecnica-{idx}.pdf"])
        if followed_pages_have_links:
            return success_outcome(url, links=[url.replace(".pdf", "-anexo.pdf")])
        return success_outcome(url)

    scrape_tool = RecordingTool(scraped, tracker=tracker, outcome_for=outcome_for)

    async def fake_search_and_scrape_iteration(**kwargs):
        url = f"https://www.gov.br/inpe/pt-br/assuntos/round-{kwargs['iteration']}"
        return await _pipeline_flow._scrape_urls(scrape_tool, [url], empty_log_message="none")

    coverage_seen = []

    async def fake_coverage(*, state, tracker, **kwargs):
        coverage_seen.append(sorted(u for u in scraped if tracker.has_attempted_url(u)))
        state.needs_new_searches = False
        state.promising_urls_from_evaluator = [f"https://www.gov.br/inpe/pt-br/assuntos/round-{len(coverage_seen)}"]
        return False

    async def fake_prewarm(_url):
        return None

    async def fake_deepen(**kwargs):
        return None

    monkeypatch.setattr("web_scout.scraping._stealth_session.prewarm_host", fake_prewarm)
    monkeypatch.setattr(_pipeline_flow, "_deepen_domain_iteration", fake_deepen)

    with caplog.at_level(logging.INFO, logger="web_scout.agent"):
        await _pipeline_flow._run_search_mode_impl(
            query="desmatamento prodes",
            include_domains=include_domains,
            search_backend="serper",
            domain_expertise=None,
            depth={"max_iterations": rounds, "urls_followup": 4},
            query_gen_model=None,
            evaluator_model=None,
            followup_model=None,
            followup_backend="gpt",
            tracker=tracker,
            scrape_tool=scrape_tool,
            exclude_domains=None,
            build_search_backend=lambda name: object(),
            build_query_agents=lambda **kwargs: (None, None),
            search_and_scrape_iteration=fake_search_and_scrape_iteration,
            evaluate_search_coverage=fake_coverage,
        )

    waves = sum(1 for record in caplog.records if record.getMessage().startswith(LOG_PREFIX))
    return SimpleNamespace(followup_waves=waves, scraped=scraped, coverage_seen=coverage_seen)


@pytest.mark.asyncio
async def test_open_mode_runs_one_wave_per_round(monkeypatch, caplog):
    calls = await run_search_mode_with_fakes(monkeypatch, caplog, include_domains=None, rounds=2)
    assert calls.followup_waves == 2
    # Round 2 came from the backlog branch and is the last round; both waves still ran.
    assert "https://www.gov.br/inpe/pt-br/assuntos/nota-tecnica-1.pdf" in calls.scraped
    # The coverage check after round 1 already sees the followed page.
    assert "https://www.gov.br/inpe/pt-br/assuntos/nota-tecnica-0.pdf" in calls.coverage_seen[0]


@pytest.mark.asyncio
async def test_domain_mode_does_not_run_open_followup(monkeypatch, caplog):
    calls = await run_search_mode_with_fakes(monkeypatch, caplog, include_domains=["inpe.br"], rounds=1)
    assert calls.followup_waves == 0


@pytest.mark.asyncio
async def test_followed_pages_do_not_start_another_wave(monkeypatch, caplog):
    calls = await run_search_mode_with_fakes(
        monkeypatch, caplog, include_domains=None, rounds=1, followed_pages_have_links=True
    )
    assert calls.followup_waves == 1
    assert not any(url.endswith("-anexo.pdf") for url in calls.scraped)
