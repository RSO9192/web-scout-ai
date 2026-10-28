"""Direct ``litellm.acompletion`` calls resolve API keys like ``get_model`` does."""

from types import SimpleNamespace

import litellm
from agents.extensions.models.litellm_model import LitellmModel

from web_scout.scraping import _vision
from web_scout.tools import pdf_extractor
from web_scout.utils import resolve_api_key


def capture_acompletion(monkeypatch, module, content: str) -> list[dict]:
    """Replace ``module.acompletion`` with a fake that records its kwargs."""
    seen: list[dict] = []

    async def _fake_acompletion(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setattr(module, "acompletion", _fake_acompletion)
    return seen


def test_resolve_api_key_skips_unexpanded_reference(monkeypatch):
    monkeypatch.setenv("BEDROCK_MANTLE_API_KEY", "${AWS_BEARER_TOKEN_BEDROCK}")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "tok")
    assert resolve_api_key("bedrock_mantle/openai.gpt-6-luna") == "tok"


def test_resolve_api_key_prefers_model_object_key():
    model = LitellmModel(model="bedrock_mantle/openai.gpt-6-luna", api_key="explicit")
    assert resolve_api_key(model) == "explicit"


def test_resolve_api_key_ignores_placeholder_model_object_key(monkeypatch):
    monkeypatch.setenv("BEDROCK_MANTLE_API_KEY", "${AWS_BEARER_TOKEN_BEDROCK}")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "tok")
    model = LitellmModel(model="bedrock_mantle/openai.gpt-6-luna", api_key="${AWS_BEARER_TOKEN_BEDROCK}")
    assert resolve_api_key(model) == "tok"


def test_resolve_api_key_returns_none_without_provider_or_key():
    assert resolve_api_key("dummy") is None


def test_resolve_api_key_none_for_sigv4_bedrock(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    assert resolve_api_key("bedrock/anthropic.claude-x") is None


def test_resolve_api_key_none_for_vertex_ai(monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    assert resolve_api_key("vertex_ai/gemini-3.7-flash") is None


def test_resolve_api_key_bedrock_mantle_still_resolves(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("BEDROCK_MANTLE_API_KEY", "mantle")
    assert resolve_api_key("bedrock_mantle/openai.gpt-6-luna") == "mantle"


async def test_pdf_llm_json_passes_resolved_key(monkeypatch):
    monkeypatch.setenv("BEDROCK_MANTLE_API_KEY", "${AWS_BEARER_TOKEN_BEDROCK}")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "tok")
    seen = capture_acompletion(
        monkeypatch,
        pdf_extractor.litellm,
        content='{"has_evidence":false,"relevant_content":"[No relevant content found for this query]","evidence":[]}',
    )
    await pdf_extractor._llm_json("bedrock_mantle/openai.gpt-6-luna", "p", pdf_extractor.PdfExtractResult)
    assert seen[0]["api_key"] == "tok"


async def test_vision_calls_pass_resolved_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    # _vision imports litellm inside each function, so patch the module itself.
    seen = capture_acompletion(monkeypatch, litellm, content="text")
    await _vision._call_vision_model(
        image_bytes=b"x",
        mime_type="image/png",
        query="q",
        vision_model="gemini/gemini-3.7-flash",
        prompt_prefix="Extract{query_clause}.",
    )
    assert seen[0]["api_key"] == "g"


async def test_section_visuals_call_passes_resolved_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    seen = capture_acompletion(monkeypatch, litellm, content='{"request_id":"","visuals":[]}')
    await _vision.describe_section_visuals(
        section_markdown="<!-- visual:s0-v0 -->",
        visuals=[{"visual_id": "s0-v0", "png_bytes": b"x"}],
        vision_model="gemini/gemini-3.7-flash",
    )
    assert seen[0]["api_key"] == "g"


async def _describe_one(model: str, **kwargs) -> None:
    await _vision.describe_section_visuals(
        section_markdown="<!-- visual:s0-v0 -->",
        visuals=[{"visual_id": "s0-v0", "png_bytes": b"x"}],
        vision_model=model,
        **kwargs,
    )


async def test_section_visuals_pass_reasoning_effort_when_supported(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    seen = capture_acompletion(monkeypatch, litellm, content='{"request_id":"","visuals":[]}')
    await _describe_one("gemini/gemini-3.8-flash", reasoning_effort="low")
    assert seen[0]["reasoning_effort"] == "low"


async def test_section_visuals_omit_reasoning_effort_when_unsupported(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    seen = capture_acompletion(monkeypatch, litellm, content='{"request_id":"","visuals":[]}')
    await _describe_one("openai/gpt-4o-mini", reasoning_effort="low")
    assert "reasoning_effort" not in seen[0]


async def test_section_visuals_omit_reasoning_effort_when_none(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    seen = capture_acompletion(monkeypatch, litellm, content='{"request_id":"","visuals":[]}')
    await _describe_one("gemini/gemini-3.8-flash", reasoning_effort=None)
    assert "reasoning_effort" not in seen[0]


async def test_section_visuals_omit_reasoning_effort_for_claude(monkeypatch):
    # LiteLLM maps "low" to *enabling* Claude thinking with a small max_tokens cap.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    seen = capture_acompletion(monkeypatch, litellm, content='{"request_id":"","visuals":[]}')
    await _describe_one("anthropic/claude-sonnet-4-5", reasoning_effort="low")
    assert "reasoning_effort" not in seen[0]
