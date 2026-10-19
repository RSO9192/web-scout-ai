"""Akamai interstitial pass is host-agnostic; the PDF check stays on headers and bytes."""

import pytest

from web_scout.scraping._akamai import akamai_interstitial_solution, follow_akamai_interstitial
from web_scout.scraping._download import _ScraplingFetcher
from web_scout.scraping._parser import _classify_fetch_result
from web_scout.scraping._resources import http_get, resources
from web_scout.scraping.page_classifier import looks_like_pdf_resource
from web_scout.scraping.types import FetchResult

_CHALLENGE = """<!DOCTYPE html><html><body><script>
var i = 1792418675; var j = i + Number("7433" + "67683");
xhr.open("POST", "/_sec/verify?provider=interstitial", false);
xhr.send(JSON.stringify({"bm-verify": "TOKEN", "pow": j}));
</script></body></html>"""


def test_solution_reads_proof_from_challenge_html():
    solution = akamai_interstitial_solution(_CHALLENGE.encode())

    assert solution == {
        "bm-verify": "TOKEN",
        "pow": 1792418675 + 743367683,
        "verify_path": "/_sec/verify?provider=interstitial",
    }


def test_solution_ignores_pdf_bytes_and_ordinary_html():
    assert akamai_interstitial_solution(b"%PDF-1.7 not a challenge") is None
    assert akamai_interstitial_solution(b"<html><body>article text</body></html>") is None


def test_extensionless_pdf_is_recognized_from_disposition_and_magic_bytes():
    """A ``/pdf`` path needs no special case once the file itself has arrived."""
    url = "https://publisher.example/2072-4292/15/7/1816/pdf"
    disposition = 'attachment; filename="remotesensing-15-01816.pdf"'

    assert looks_like_pdf_resource(url, "", disposition) is True
    result = FetchResult(
        url=url,
        status=200,
        content_type="",
        content_disposition=disposition,
        html_content=None,
        body=b"%PDF-1.7 paper",
        headers={"content-disposition": disposition},
        used_browser=False,
    )
    assert _classify_fetch_result(result) == "document"


class _Body:
    def __init__(self, body: bytes, *, status: int = 200):
        self.body = body
        self.content = body
        self.status = status
        self.status_code = status


class _Session:
    def __init__(self, verify_body: bytes = b'{"reload": true}', *, status: int = 200):
        self.posts: list[tuple[str, dict]] = []
        self.verify_body = verify_body
        self.status = status

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs.get("json")))
        return _Body(self.verify_body, status=self.status)


@pytest.mark.asyncio
async def test_follow_posts_proof_to_the_request_host():
    session = _Session()

    next_url = await follow_akamai_interstitial(
        session, "https://publisher.example/article/99/pdf", _CHALLENGE.encode()
    )

    assert next_url == "https://publisher.example/article/99/pdf"
    assert session.posts == [
        (
            "https://publisher.example/_sec/verify?provider=interstitial",
            {"bm-verify": "TOKEN", "pow": 1792418675 + 743367683},
        )
    ]


@pytest.mark.asyncio
async def test_follow_uses_verify_location_when_the_challenge_returns_one():
    session = _Session(b'{"location": "https://cdn.publisher.example/files/paper.pdf"}')

    next_url = await follow_akamai_interstitial(
        session, "https://other.example/download/pdf", _CHALLENGE.encode()
    )

    assert next_url == "https://cdn.publisher.example/files/paper.pdf"
    assert session.posts[0][0] == "https://other.example/_sec/verify?provider=interstitial"


class _HttpResponse:
    def __init__(self, body: bytes, headers: dict[str, str]):
        self.status = 200
        self.body = body
        self.headers = headers
        self.html_content = body.decode() if body.startswith(b"<") else ""


class _HttpSession:
    def __init__(self):
        self.posts: list[str] = []
        self.gets: list[str] = []

    async def get(self, url, **kwargs):
        self.gets.append(url)
        if len(self.gets) == 1:
            return _HttpResponse(_CHALLENGE.encode(), {"content-type": "text/html"})
        return _HttpResponse(
            b"%PDF-1.7 journal",
            {"content-disposition": 'attachment; filename="journal.pdf"'},
        )

    async def post(self, url, **kwargs):
        self.posts.append(url)
        return _Body(b'{"reload": true}')


@pytest.mark.asyncio
async def test_http_get_refetches_after_interstitial_on_any_host():
    state = resources()
    previous = (state.http_session, state.http_manager)
    session = _HttpSession()
    state.http_session = session
    state.http_manager = object()
    try:
        response = await http_get("https://journal.example/15/7/1816/pdf", timeout=5)
    finally:
        state.http_session, state.http_manager = previous

    assert session.posts == ["https://journal.example/_sec/verify?provider=interstitial"]
    assert session.gets == [
        "https://journal.example/15/7/1816/pdf",
        "https://journal.example/15/7/1816/pdf",
    ]
    assert response.body.startswith(b"%PDF")


class _Stream:
    def __init__(self, payload: bytes):
        self.status_code = 200
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_content(self):
        yield self._payload


class _BinarySession:
    def __init__(self):
        self.urls: list[str] = []
        self.posts: list[str] = []

    def stream(self, method, url, **kwargs):
        self.urls.append(url)
        if len(self.urls) == 1:
            return _Stream(_CHALLENGE.encode())
        return _Stream(b"%PDF-1.7 from-bytes")

    async def post(self, url, **kwargs):
        self.posts.append(url)
        return _Body(b'{"reload": true}')


@pytest.mark.asyncio
async def test_pdf_downloader_passes_interstitial_before_magic_byte_check(monkeypatch):
    session = _BinarySession()
    monkeypatch.setattr("web_scout.scraping._resources.binary_session", lambda: session)

    data = await _ScraplingFetcher()._attempt("https://repository.example/record.pdf")

    assert session.posts == ["https://repository.example/_sec/verify?provider=interstitial"]
    assert data == b"%PDF-1.7 from-bytes"
