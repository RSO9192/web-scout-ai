"""Legacy regressions exercise GPT; Jev tests explicitly select the new backend."""

import pytest


@pytest.fixture(autouse=True)
def classification_backend(monkeypatch):
    monkeypatch.setenv("WEB_SCOUT_CLASSIFICATION_BACKEND", "gpt")
