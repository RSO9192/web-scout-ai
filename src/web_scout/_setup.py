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


def _setup_crawl4ai() -> None:
    """Run crawl4ai's own post-install setup (optional dependency)."""
    try:
        subprocess.run([sys.executable, "-m", "crawl4ai.install"], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        try:
            subprocess.run(["crawl4ai-setup"], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("web-scout-ai: crawl4ai not found, skipping crawl4ai setup.")


def main() -> None:
    """Install Patchright Chromium, its system dependencies, and run crawl4ai setup."""
    print("web-scout-ai: setting up browser for web scraping...")
    _setup_crawl4ai()
    _install_chromium()
    _install_chromium_deps()
    print("web-scout-ai: browser setup complete.")


if __name__ == "__main__":
    main()
