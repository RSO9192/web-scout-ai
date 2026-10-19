"""MDPI serves article PDFs at extensionless ``/pdf`` paths behind an interstitial."""

import pytest

from web_scout import PDF_MAGIC_BYTES, fetch
from web_scout.scraping._parser import _classify_fetch_result

MDPI_PDF = "https://www.mdpi.com/2072-4292/15/7/1816/pdf"


@pytest.mark.asyncio
async def test_fetch_mdpi_pdf():
    """web-scout downloads the PDF bytes and routes them as a document."""
    result = await fetch(MDPI_PDF)

    body = result.body or b""
    assert result.error is None, (
        f"error={result.error!r} status={result.status} "
        f"content_type={result.content_type!r} "
        f"content_disposition={result.content_disposition!r} "
        f"used_browser={result.used_browser} body={body[:40]!r}"
    )
    assert result.used_browser is False
    assert result.content_type == "application/pdf"
    assert body.startswith(PDF_MAGIC_BYTES)
    assert len(body) > 100_000
    assert _classify_fetch_result(result) == "document"
