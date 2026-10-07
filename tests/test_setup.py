"""Browser setup installs only the Scrapling/Patchright browser requirements."""

from unittest.mock import Mock, call

from web_scout import _setup


def test_setup_only_installs_patchright_chromium(monkeypatch):
    run = Mock()
    monkeypatch.setattr(_setup, "_run", run)
    _setup.main()
    assert run.call_args_list == [
        call([_setup.sys.executable, "-m", "patchright", "install", "chromium"]),
        call([_setup.sys.executable, "-m", "patchright", "install-deps", "chromium"]),
    ]
