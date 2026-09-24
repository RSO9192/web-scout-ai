import os

import litellm
from agents.extensions.models.litellm_model import LitellmModel

from web_scout.utils import get_litellm_base_url, get_model


def test_openai_mantle_models_share_litellm_us_east_1_route(monkeypatch):
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
