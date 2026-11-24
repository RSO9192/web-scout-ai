"""Setup installs the browser and downloads Docling model weights."""

from pathlib import Path
from unittest.mock import Mock, call

from web_scout import _setup


def test_setup_installs_browser_and_docling_models(monkeypatch):
    run = Mock()
    monkeypatch.setattr(_setup, "_run", run)
    _setup.main()
    script = str(Path(_setup.sys.executable).with_name("pdf_extractor_download_models"))
    assert run.call_args_list == [
        call([_setup.sys.executable, "-m", "patchright", "install", "chromium"]),
        call([_setup.sys.executable, "-m", "patchright", "install-deps", "chromium"]),
        call([script]),
    ]
