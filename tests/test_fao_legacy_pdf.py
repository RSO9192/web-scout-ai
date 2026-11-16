"""Legacy FAO ``/3/`` document URLs redirect onto an Open Knowledge bitstream.

``https://www.fao.org/3/ca9825en/CA9825EN.pdf`` answers with three redirects:
the www host sends the client to ``openknowledge.fao.org``, that host sends it
to ``/server/api/core/mapping``, and the mapping API sends it to
``/server/api/core/bitstreams/{uuid}/content``. The final response is the PDF
(``Content-Type: application/pdf`` plus a ``Content-Disposition`` filename),
not an HTML landing page.
"""

import pytest

from web_scout import PDF_MAGIC_BYTES, fetch
from web_scout.scraping._parser import _classify_fetch_result

FAO_LEGACY_PDF = "https://www.fao.org/3/ca9825en/CA9825EN.pdf"


@pytest.mark.asyncio
async def test_fetch_fao_legacy_pdf():
    """web-scout follows the legacy redirect chain and returns the PDF bytes."""
    result = await fetch(FAO_LEGACY_PDF)

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
    assert len(body) > 1_000_000
    assert _classify_fetch_result(result) == "document"
