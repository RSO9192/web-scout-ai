"""Legacy regressions exercise GPT; Jev tests explicitly select the new backend."""

import pytest


@pytest.fixture(autouse=True)
def classification_backend(monkeypatch):
    monkeypatch.delenv("DISABLE_JEV", raising=False)
    for feature in ("CRAWLER", "COVERAGE", "FOLLOWUP", "PDF_CLAIMS"):
        monkeypatch.delenv(f"WEB_SCOUT_{feature}_BACKEND", raising=False)
    monkeypatch.setenv("WEB_SCOUT_CLASSIFICATION_BACKEND", "gpt")
