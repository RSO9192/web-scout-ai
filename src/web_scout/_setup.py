"""Post-install setup: installs the browser needed for web scraping.

Run after ``pip install web-scout-ai``::

    web-scout-setup

This is a thin wrapper around ``crawl4ai-setup`` which installs a
Chromium browser via Playwright.
"""

from __future__ import annotations

import subprocess
import sys


def main() -> None:
    """Install the Playwright browser required by crawl4ai."""
    print("web-scout-ai: installing browser for web scraping (via crawl4ai)...")
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
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=True,
            )
    print("web-scout-ai: browser setup complete.")


if __name__ == "__main__":
    main()
