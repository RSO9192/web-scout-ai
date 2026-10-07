"""Post-install setup: installs the browser needed for web scraping.

Run after ``pip install web-scout-ai``::

    web-scout-setup

Installs the Patchright-managed Chromium browser and its OS-level
dependencies so the stealthy scraper works out of the box.
"""

import subprocess
import sys


def _run(cmd: list[str], **kwargs) -> None:
    subprocess.run(cmd, check=True, **kwargs)


def _install_chromium() -> None:
    """Install the Patchright-managed Chromium binary."""
    print("web-scout-ai: installing Patchright Chromium...")
    _run([sys.executable, "-m", "patchright", "install", "chromium"])


def _install_chromium_deps() -> None:
    """Install OS-level libraries required by the Chromium binary."""
    print("web-scout-ai: installing Chromium system dependencies (may require sudo)...")
    _run([sys.executable, "-m", "patchright", "install-deps", "chromium"])


def main() -> None:
    """Install Patchright Chromium and its system dependencies."""
    print("web-scout-ai: setting up browser for web scraping...")
    _install_chromium()
    _install_chromium_deps()
    print("web-scout-ai: browser setup complete.")


if __name__ == "__main__":
    main()
