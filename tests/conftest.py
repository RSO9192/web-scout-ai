"""Legacy regressions exercise GPT; Jev tests explicitly select the new backend."""

import pytest


@pytest.fixture(scope="session", autouse=True)
def _prefect_test_server():
    """One temporary Prefect server so task caching does not use the developer profile."""
    from prefect.testing.utilities import prefect_test_harness

    with prefect_test_harness():
        yield


@pytest.fixture(autouse=True)
def _isolate_result_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_SCOUT_CACHE_DIR", str(tmp_path / "web-scout-cache"))


@pytest.fixture(autouse=True)
def classification_backend(monkeypatch):
    monkeypatch.delenv("DISABLE_JEV", raising=False)
    for feature in ("CRAWLER", "COVERAGE", "FOLLOWUP", "PDF_CLAIMS"):
        monkeypatch.delenv(f"WEB_SCOUT_{feature}_BACKEND", raising=False)
    monkeypatch.setenv("WEB_SCOUT_CLASSIFICATION_BACKEND", "gpt")
