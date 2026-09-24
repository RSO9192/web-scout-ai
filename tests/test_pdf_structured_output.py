"""Strict Structured Outputs and validation retry for PDF extraction calls."""

from types import SimpleNamespace

import pytest

import web_scout.tools.pdf_extractor as pdf_mod
from web_scout.tools.pdf_extractor import PdfExtractResult


def _response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


@pytest.mark.asyncio
async def test_llm_json_sends_strict_pydantic_json_schema(monkeypatch):
    calls = []

    async def _fake_acompletion(**kwargs):
        calls.append(kwargs)
        return _response(
            '{"has_evidence":false,'
            '"relevant_content":"[No relevant content found for this query]",'
            '"evidence":[]}'
        )

    monkeypatch.setattr(pdf_mod.litellm, "acompletion", _fake_acompletion)
    result = await pdf_mod._llm_json("dummy", "Extract evidence as JSON.", PdfExtractResult)

    assert result.has_evidence is False
    response_format = calls[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"has_evidence", "relevant_content", "evidence"}
    evidence_schema = schema["$defs"]["PdfEvidenceItem"]
    assert evidence_schema["additionalProperties"] is False
    assert set(evidence_schema["required"]) == {"text", "page_start", "page_end"}


@pytest.mark.asyncio
async def test_llm_json_retries_schema_invalid_response_with_feedback(monkeypatch):
    calls = []
    responses = iter(
        [
            _response(
                '{"has_evidence":false,"relevant_content":null,"evidence":[]}'
            ),
            _response(
                '{"has_evidence":false,'
                '"relevant_content":"[No relevant content found for this query]",'
                '"evidence":[]}'
            ),
        ]
    )

    async def _fake_acompletion(**kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(pdf_mod.litellm, "acompletion", _fake_acompletion)
    result = await pdf_mod._llm_json("dummy", "Extract evidence as JSON.", PdfExtractResult)

    assert result.has_evidence is False
    assert len(calls) == 2
    retry_messages = calls[1]["messages"]
    assert retry_messages[1]["role"] == "assistant"
    assert retry_messages[2]["role"] == "user"
    assert "did not match the required schema" in retry_messages[2]["content"]
    assert calls[1]["response_format"] == calls[0]["response_format"]


@pytest.mark.asyncio
async def test_llm_json_raises_after_validation_retries(monkeypatch):
    calls = []

    async def _fake_acompletion(**kwargs):
        calls.append(kwargs)
        return _response(
            '{"has_evidence":false,"relevant_content":null,"evidence":[]}'
        )

    monkeypatch.setattr(pdf_mod.litellm, "acompletion", _fake_acompletion)
    with pytest.raises(ValueError):
        await pdf_mod._llm_json("dummy", "Extract evidence as JSON.", PdfExtractResult)

    assert len(calls) == pdf_mod._JSON_SCHEMA_ATTEMPTS
