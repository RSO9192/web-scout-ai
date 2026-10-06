import os

import litellm
import pytest
from agents.extensions.models.litellm_model import LitellmModel

from web_scout.utils import get_litellm_base_url, get_model


def test_openai_mantle_models_share_litellm_us_east_1_route(monkeypatch):
    monkeypatch.setitem(litellm.model_cost, "bedrock_mantle/openai.gpt-5.6-luna", {"use_openai_responses_path": True})
    monkeypatch.setenv("BEDROCK_MANTLE_REGION", "us-east-2")
    monkeypatch.delenv("BEDROCK_MANTLE_API_KEY", raising=False)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "test-bearer")

    for model_name in (
        "bedrock_mantle/openai.gpt-5.6-luna",
        "bedrock_mantle/openai.gpt-6-luna",
    ):
        assert get_litellm_base_url(model_name) is None
        model = get_model(model_name)
        assert isinstance(model, LitellmModel)
        assert model.base_url is None
        assert os.environ["BEDROCK_MANTLE_REGION"] == "us-east-1"
        assert model_name in litellm.model_cost
        assert litellm.model_cost[model_name]["use_openai_responses_path"] is True


def test_unexpanded_mantle_alias_is_skipped(monkeypatch):
    monkeypatch.setenv("BEDROCK_MANTLE_API_KEY", "${AWS_BEARER_TOKEN_BEDROCK}")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "test-bearer")

    model = get_model("bedrock_mantle/openai.gpt-5.6-luna")
    assert isinstance(model, LitellmModel)
    assert model.api_key == "test-bearer"


def test_other_providers_do_not_receive_mantle_route(monkeypatch):
    monkeypatch.setenv("BEDROCK_MANTLE_REGION", "eu-central-1")
    assert get_litellm_base_url("gemini/gemini-3.7-flash") is None
    assert os.environ["BEDROCK_MANTLE_REGION"] == "eu-central-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model, allowed_params",
    [
        (LitellmModel(model="bedrock_mantle/openai.gpt-6-luna"), ["reasoning_effort"]),
        (LitellmModel(model="gemini/gemini-3.7-flash"), None),
        ("gpt-6-luna", None),
    ],
)
async def test_synthesis_forwards_mantle_reasoning_despite_missing_capability_metadata(
    monkeypatch, model, allowed_params
):
    from types import SimpleNamespace

    from web_scout import _pipeline_flow as flow
    from web_scout.models import WebResearchResultRaw
    from web_scout.tools import ResearchTracker

    global_drop_params = litellm.drop_params

    async def run(agent, prompt):
        settings = agent.model_settings
        assert settings.reasoning.effort == "high"
        assert (settings.extra_args or {}).get("allowed_openai_params") == allowed_params
        assert "drop_params" not in (settings.extra_args or {})
        if allowed_params:
            from litellm.llms.bedrock_mantle.chat.transformation import BedrockMantleChatConfig

            monkeypatch.setattr(
                BedrockMantleChatConfig, "get_supported_openai_params", lambda self, model: ["response_format"]
            )
            with pytest.raises(litellm.UnsupportedParamsError, match="reasoning_effort"):
                litellm.utils.get_optional_params(
                    model="openai.gpt-6-luna",
                    custom_llm_provider="bedrock_mantle",
                    reasoning_effort="high",
                    drop_params=False,
                )
            params = litellm.utils.get_optional_params(
                model="openai.gpt-6-luna",
                custom_llm_provider="bedrock_mantle",
                reasoning_effort=settings.reasoning.effort,
                response_format={"type": "json_object"},
                **settings.extra_args,
            )
            assert params["reasoning_effort"] == "high"
            assert params["response_format"] == {"type": "json_object"}
        return SimpleNamespace(final_output_as=lambda _: WebResearchResultRaw(synthesis="Answer"))

    monkeypatch.setattr(flow.Runner, "run", run)
    result = await flow._synthesise_result(
        query="query",
        tracker=ResearchTracker(),
        synth_model=model,
        domain_expertise=None,
    )
    assert result.synthesis == "Answer"
    assert litellm.drop_params is global_drop_params


@pytest.mark.parametrize("metadata", [None, {}, {"use_openai_responses_path": False, "input_cost_per_token": 0.123}])
def test_mantle_openai_route_works_without_template_or_with_stale_metadata(monkeypatch, metadata):
    from litellm.llms.bedrock_mantle.chat.transformation import BedrockMantleChatConfig

    model_name = "bedrock_mantle/openai.gpt-6-luna"
    costs = {} if metadata is None else {model_name: dict(metadata)}
    monkeypatch.setattr(litellm, "model_cost", costs)
    monkeypatch.delenv("BEDROCK_MANTLE_API_BASE", raising=False)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "test-bearer")
    model = get_model(model_name)
    assert model.model == model_name
    base, _ = BedrockMantleChatConfig()._get_openai_compatible_provider_info(
        api_base=model.base_url,
        api_key="test-bearer",
        model="openai.gpt-6-luna",
    )
    assert base == "https://bedrock-mantle.us-east-1.api.aws/openai/v1"
    if metadata and "input_cost_per_token" in metadata:
        assert litellm.model_cost[model_name]["input_cost_per_token"] == metadata["input_cost_per_token"]


def test_gpt_oss_keeps_the_standard_mantle_route(monkeypatch):
    from litellm.llms.bedrock_mantle.common_utils import mantle_base_segment

    model_name = "bedrock_mantle/openai.gpt-oss-120b"
    monkeypatch.setattr(litellm, "model_cost", {})
    get_model(model_name)
    assert mantle_base_segment("openai.gpt-oss-120b", litellm.model_cost) == "v1"
