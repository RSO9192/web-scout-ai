"""Post-install setup: browser for scraping and Docling model weights.

Run after ``pip install web-scout-ai``::

    web-scout-setup

Installs the Patchright-managed Chromium browser and its OS-level
dependencies, then downloads Docling and RapidOCR weights with
``pdf_extractor_download_models``.
"""

import subprocess
import sys
from pathlib import Path


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


def _download_docling_models() -> None:
    """Download Docling and RapidOCR weights via pdf-extractor-ai."""
    print("web-scout-ai: downloading Docling models...")
    script = Path(sys.executable).with_name("pdf_extractor_download_models")
    _run([str(script)])


def main() -> None:
    """Install Patchright Chromium, its system dependencies, and Docling models."""
    print("web-scout-ai: setting up browser for web scraping...")
    _install_chromium()
    _install_chromium_deps()
    _download_docling_models()
    print("web-scout-ai: setup complete.")


if __name__ == "__main__":
    main()
