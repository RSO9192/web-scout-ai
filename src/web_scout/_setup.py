"""Post-install setup: installs the browser needed for web scraping.

Run after ``pip install web-scout-ai``::

    web-scout-setup

This is a thin wrapper around ``crawl4ai-setup`` which installs a
Chromium browser via Playwright.
"""

from __future__ import annotations

import subprocess
import sys


def _install_playwright_chromium() -> None:
    """Install the Chromium browser used by Playwright-backed scrapers."""
    print("web-scout-ai: ensuring Playwright Chromium is installed...")
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=True,
    )


def main() -> None:
    """Install the Playwright browser required by crawl4ai."""
    print("web-scout-ai: installing browser for web scraping (via crawl4ai)...")
    installed_directly = False
    try:
        subprocess.run(
            [sys.executable, "-m", "crawl4ai.install"],
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback: try the CLI entry point directly
        try:
            subprocess.run(["crawl4ai-setup"], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Last resort: install playwright chromium directly
            print("web-scout-ai: crawl4ai setup not found, installing Playwright Chromium directly...")
            _install_playwright_chromium()
            installed_directly = True
    if not installed_directly:
        _install_playwright_chromium()
    print("web-scout-ai: browser setup complete.")


if __name__ == "__main__":
    main()
