"""PDF path relevance verdict: has_evidence flag and extractor_guidance plumbing.

A PDF whose extraction admits it cannot answer the query (e.g. a generic
guideline asked a country-specific question) must return the no-relevant
sentinel instead of citable filler, and the public-API extractor_guidance
must reach the PDF prompts.
"""

import pytest

import web_scout.tools.pdf_extractor as pdf_mod
from web_scout.scraping.types import PdfDocumentLayout, PdfPageSpan, SourceArtifact
from web_scout.tools.pdf_extractor import (
    _NO_RELEVANT,
    PdfEvidenceItem,
    PdfExtractResult,
    extract_pdf_for_query,
)

_MARKDOWN = (
    "========== page 1 start ==========\n"
    "## Guidelines\n\nGeneric guidance about livestock market assessment.\n"
    "========== page 1 end ==========\n"
)
_LAYOUT = PdfDocumentLayout(
    document_title="Guidelines",
    pages=(PdfPageSpan(page=1, start_line=1, end_line=4),),
)
_ARTIFACT = SourceArtifact(kind="text", title="Guidelines", text_content=_MARKDOWN, layout=_LAYOUT)


def _patch_llm(monkeypatch, result: PdfExtractResult) -> list[str]:
    prompts: list[str] = []

    async def _fake_llm_json(model, prompt, schema):
        prompts.append(prompt)
        return result

    monkeypatch.setattr(pdf_mod, "_llm_json", _fake_llm_json)
    return prompts


@pytest.mark.asyncio
async def test_has_evidence_false_returns_sentinel_despite_evidence_items(monkeypatch):
    _patch_llm(
        monkeypatch,
        PdfExtractResult(
            has_evidence=False,
            relevant_content="The document does not provide country-specific data [p. 1].",
            evidence=[PdfEvidenceItem(text="generic guidance", page_start=1, page_end=1)],
        ),
    )
    title, content, pages = await extract_pdf_for_query(
        artifact=_ARTIFACT, query="Somalia livestock services status", model="dummy"
    )
    assert content == _NO_RELEVANT
    assert pages == []


@pytest.mark.asyncio
async def test_has_evidence_default_true_keeps_existing_behavior(monkeypatch):
    _patch_llm(
        monkeypatch,
        PdfExtractResult(
            relevant_content="Wheat output fell 4 percent [p. 1].",
            evidence=[PdfEvidenceItem(text="wheat output fell", page_start=1, page_end=1)],
        ),
    )
    title, content, pages = await extract_pdf_for_query(
        artifact=_ARTIFACT, query="wheat output", model="dummy"
    )
    assert "Wheat output" in content
    assert pages == [1]


@pytest.mark.asyncio
async def test_extractor_guidance_reaches_pdf_prompt(monkeypatch):
    prompts = _patch_llm(
        monkeypatch,
        PdfExtractResult(
            relevant_content="Fact [p. 1].",
            evidence=[PdfEvidenceItem(text="fact", page_start=1, page_end=1)],
        ),
    )
    await extract_pdf_for_query(
        artifact=_ARTIFACT,
        query="wheat output",
        model="dummy",
        extractor_guidance="Country of interest: Somalia — keep findings explicitly about Somalia.",
    )
    assert any("Country of interest: Somalia" in p for p in prompts)


def _assert_pdf_result_contract(prompt: str) -> None:
    assert "Output contract (required in both evidence and no-evidence cases)" in prompt
    assert "Never return null, omit or rename a field" in prompt
    assert "Valid evidence output:" in prompt
    assert '"has_evidence":true' in prompt
    assert "Valid no-evidence output:" in prompt
    no_evidence_example = (
        '"has_evidence":false,"relevant_content":"[No relevant content found for this query]",'
        '"evidence":[]'
    )
    assert "".join(no_evidence_example) in prompt


@pytest.mark.asyncio
async def test_short_pdf_prompt_includes_complete_output_contract(monkeypatch):
    prompts = _patch_llm(
        monkeypatch,
        PdfExtractResult(
            has_evidence=False,
            relevant_content=_NO_RELEVANT,
            evidence=[],
        ),
    )
    await pdf_mod._short_path_extract(
        model="dummy",
        query="Somalia livestock services status",
        document_title="Guidelines",
        markdown=_MARKDOWN,
    )
    _assert_pdf_result_contract(prompts[0])


@pytest.mark.asyncio
async def test_final_pdf_prompt_includes_complete_output_contract(monkeypatch):
    prompts = _patch_llm(
        monkeypatch,
        PdfExtractResult(
            has_evidence=False,
            relevant_content=_NO_RELEVANT,
            evidence=[],
        ),
    )
    await pdf_mod._final_answer_from_evidence(
        model="dummy",
        query="Somalia livestock services status",
        document_title="Guidelines",
        evidence=[],
    )
    _assert_pdf_result_contract(prompts[0])


@pytest.mark.asyncio
async def test_chunk_prompt_requires_object_and_exact_evidence_fields(monkeypatch):
    prompts: list[str] = []

    async def _fake_llm_json(model, prompt, schema):
        prompts.append(prompt)
        return schema(evidence=[])

    monkeypatch.setattr(pdf_mod, "_llm_json", _fake_llm_json)
    result = await pdf_mod._extract_chunk_evidence(
        model="dummy",
        query="Somalia livestock services status",
        document_summary="Generic livestock guidance.",
        heading_path="Assessment methods",
        chunk_markdown=_MARKDOWN,
    )
    assert result == []
    assert "Return exactly one JSON object with the `evidence` field" in prompts[0]
    assert "Each evidence item must have exactly `text`, `page_start`, and `page_end`" in prompts[0]
    assert 'When there is no evidence, return exactly {"evidence":[]}' in prompts[0]
