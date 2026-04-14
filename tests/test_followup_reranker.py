import pytest

from web_scout.agent import FollowupSelection, _rerank_followup_urls


class _FakeRunResult:
    def __init__(self, output):
        self._output = output

    def final_output_as(self, _output_type):
        return self._output


@pytest.mark.asyncio
async def test_rerank_followup_urls_uses_model_selection(monkeypatch):
    from web_scout import agent

    async def _fake_run(selector, prompt, max_turns=1):
        return _FakeRunResult(
            FollowupSelection(
                selected_urls=[
                    "https://example.org/reports/climate-report-2025",
                    "https://example.org/downloads/report.pdf",
                ]
            )
        )

    monkeypatch.setattr(agent.Runner, "run", _fake_run)

    result = await _rerank_followup_urls(
        query="test query",
        parent_url="https://example.org/root",
        parent_content="Parent content",
        candidates=[
            "https://example.org/home",
            "https://example.org/reports/climate-report-2025",
            "https://example.org/downloads/report.pdf",
        ],
        cap=2,
        model="dummy",
    )

    assert result == [
        "https://example.org/reports/climate-report-2025",
        "https://example.org/downloads/report.pdf",
    ]


@pytest.mark.asyncio
async def test_rerank_followup_urls_falls_back_when_model_returns_invalid_urls(monkeypatch):
    from web_scout import agent

    async def _fake_run(selector, prompt, max_turns=1):
        return _FakeRunResult(
            FollowupSelection(
                selected_urls=[
                    "https://invalid.example.com/not-in-candidates",
                ]
            )
        )

    monkeypatch.setattr(agent.Runner, "run", _fake_run)

    candidates = [
        "https://example.org/reports/a",
        "https://example.org/reports/b",
        "https://example.org/reports/c",
    ]
    result = await _rerank_followup_urls(
        query="test query",
        parent_url="https://example.org/root",
        parent_content="Parent content",
        candidates=candidates,
        cap=2,
        model="dummy",
    )

    assert result == candidates[:2]
