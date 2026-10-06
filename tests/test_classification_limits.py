"""Coverage must inspect large evidence without sending unbounded state."""

import json

import pytest

from web_scout import _classification as classification


@pytest.mark.asyncio
async def test_coverage_bounds_unicode_sources_and_finds_late_evidence(monkeypatch):
    sources = [{"url": "https://source.test", "text": "氣候 " * 30000 + "Kenya complete answer"}]
    seen = []

    async def judge(state, questions):
        seen.append(state)
        assert len(json.dumps(state, ensure_ascii=False).encode()) < 20000
        return {key: 0.99 if "Kenya complete answer" in str(state) else 0.1 for key in questions}

    monkeypatch.setattr(classification, "judge", judge)
    assert await classification.coverage("Kenya", ["complete answer"], sources, []) == (True, "", [])
    assert len(seen) > 1
    assert all(source["url"] == "https://source.test" for state in seen for source in state["scraped_sources"])
    assert sources[0]["text"].endswith("Kenya complete answer")


@pytest.mark.asyncio
async def test_partial_batches_do_not_accumulate_into_completion(monkeypatch):
    async def judge(state, questions):
        return {key: 0.79 for key in questions}

    monkeypatch.setattr(classification, "judge", judge)
    assert await classification.coverage("Kenya", ["all risks"], [{"text": "partial " * 10000}], []) == (
        False, "all risks", []
    )
    assert await classification.coverage("Kenya", ["all risks"], [], []) == (False, "all risks", [])


def test_source_windows_preserve_boundary_context():
    text = "a" * 1790 + "boundary evidence" + "b" * 10000
    batches = list(classification._source_batches([{"text": text}]))
    fragments = [source["text"] for batch in batches for source in batch]
    assert any("boundary evidence" in fragment for fragment in fragments)
    rebuilt = fragments[0] + "".join(fragment[200:] for fragment in fragments[1:])
    assert rebuilt == text
