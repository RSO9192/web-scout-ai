"""Tests for search_backends.py — SerperBackend response parsing and query construction."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web_scout.search_backends import SerperBackend, SearchResult, KnowledgeGraph, PeopleAlsoAsk


def _mock_http_response(json_data: dict, status_code: int = 200):
    """Build a minimal httpx-like response mock."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data)
    resp.raise_for_status = MagicMock()
    return resp


def _make_client_mock(response):
    """Return an async context-manager mock for httpx.AsyncClient."""
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, client


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_include_domains_builds_site_clause():
    """include_domains prepends site: operators joined with OR before the query."""
    backend = SerperBackend(api_key="test-key")
    resp = _mock_http_response({"organic": [], "relatedSearches": []})
    cm, client = _make_client_mock(resp)

    with patch("httpx.AsyncClient", return_value=cm):
        await backend.search("fish production", include_domains=["fao.org", "worldbank.org"])

    _, kwargs = client.post.call_args
    sent_query = kwargs["json"]["q"]
    assert "site:fao.org" in sent_query
    assert "site:worldbank.org" in sent_query
    assert "fish production" in sent_query
    assert " OR " in sent_query


@pytest.mark.asyncio
async def test_no_include_domains_sends_query_unchanged():
    """Without include_domains the query is sent verbatim."""
    backend = SerperBackend(api_key="test-key")
    resp = _mock_http_response({"organic": [], "relatedSearches": []})
    cm, client = _make_client_mock(resp)

    with patch("httpx.AsyncClient", return_value=cm):
        await backend.search("fish production")

    _, kwargs = client.post.call_args
    assert kwargs["json"]["q"] == "fish production"


# ---------------------------------------------------------------------------
# Organic result parsing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parses_organic_results():
    """Organic results are mapped to SearchResult dataclasses."""
    backend = SerperBackend(api_key="test-key")
    payload = {
        "organic": [
            {"title": "FAO Report", "link": "https://fao.org/report", "snippet": "Fish data", "date": "2024-01", "position": 1},
        ],
        "relatedSearches": [],
    }
    cm, _ = _make_client_mock(_mock_http_response(payload))

    with patch("httpx.AsyncClient", return_value=cm):
        result = await backend.search("fish")

    assert len(result.results) == 1
    r = result.results[0]
    assert r.title == "FAO Report"
    assert r.url == "https://fao.org/report"
    assert r.snippet == "Fish data"
    assert r.date == "2024-01"
    assert r.position == 1


@pytest.mark.asyncio
async def test_skips_organic_results_without_link():
    """Results with no link field are excluded from output."""
    backend = SerperBackend(api_key="test-key")
    payload = {
        "organic": [
            {"title": "No Link Result", "snippet": "some text"},
            {"title": "Good Result", "link": "https://fao.org/report", "snippet": "data"},
        ],
        "relatedSearches": [],
    }
    cm, _ = _make_client_mock(_mock_http_response(payload))

    with patch("httpx.AsyncClient", return_value=cm):
        result = await backend.search("fish")

    assert len(result.results) == 1
    assert result.results[0].title == "Good Result"


@pytest.mark.asyncio
async def test_respects_max_results_cap():
    """Results list is truncated to max_results even if API returns more."""
    backend = SerperBackend(api_key="test-key")
    payload = {
        "organic": [
            {"title": f"Result {i}", "link": f"https://example.com/{i}", "snippet": "data"}
            for i in range(10)
        ],
        "relatedSearches": [],
    }
    cm, _ = _make_client_mock(_mock_http_response(payload))

    with patch("httpx.AsyncClient", return_value=cm):
        result = await backend.search("fish", max_results=3)

    assert len(result.results) == 3


# ---------------------------------------------------------------------------
# Related searches
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parses_related_searches():
    """relatedSearches queries are extracted into related_searches list."""
    backend = SerperBackend(api_key="test-key")
    payload = {
        "organic": [],
        "relatedSearches": [
            {"query": "global fish catch 2023"},
            {"query": "FAO aquaculture production"},
        ],
    }
    cm, _ = _make_client_mock(_mock_http_response(payload))

    with patch("httpx.AsyncClient", return_value=cm):
        result = await backend.search("fish")

    assert "global fish catch 2023" in result.related_searches
    assert "FAO aquaculture production" in result.related_searches


@pytest.mark.asyncio
async def test_skips_related_searches_without_query_field():
    """Related search entries with no query field are silently dropped."""
    backend = SerperBackend(api_key="test-key")
    payload = {
        "organic": [],
        "relatedSearches": [{"bad": "no query key"}, {"query": "good query"}],
    }
    cm, _ = _make_client_mock(_mock_http_response(payload))

    with patch("httpx.AsyncClient", return_value=cm):
        result = await backend.search("fish")

    assert result.related_searches == ["good query"]


# ---------------------------------------------------------------------------
# Knowledge Graph
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parses_knowledge_graph():
    """knowledgeGraph block is mapped to a KnowledgeGraph dataclass."""
    backend = SerperBackend(api_key="test-key")
    payload = {
        "organic": [],
        "relatedSearches": [],
        "knowledgeGraph": {
            "title": "Tuna",
            "type": "Fish",
            "description": "A large migratory fish.",
            "attributes": {"Family": "Scombridae", "Order": "Scombriformes"},
        },
    }
    cm, _ = _make_client_mock(_mock_http_response(payload))

    with patch("httpx.AsyncClient", return_value=cm):
        result = await backend.search("tuna")

    kg = result.knowledge_graph
    assert kg is not None
    assert kg.title == "Tuna"
    assert kg.entity_type == "Fish"
    assert kg.description == "A large migratory fish."
    assert kg.attributes["Family"] == "Scombridae"


@pytest.mark.asyncio
async def test_knowledge_graph_absent_when_not_in_response():
    """knowledge_graph is None when the API returns no knowledgeGraph block."""
    backend = SerperBackend(api_key="test-key")
    payload = {"organic": [], "relatedSearches": []}
    cm, _ = _make_client_mock(_mock_http_response(payload))

    with patch("httpx.AsyncClient", return_value=cm):
        result = await backend.search("fish")

    assert result.knowledge_graph is None


# ---------------------------------------------------------------------------
# People Also Ask
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parses_people_also_ask():
    """peopleAlsoAsk entries are mapped to PeopleAlsoAsk dataclasses."""
    backend = SerperBackend(api_key="test-key")
    payload = {
        "organic": [],
        "relatedSearches": [],
        "peopleAlsoAsk": [
            {"question": "How much fish is caught globally?", "snippet": "About 90Mt/yr", "link": "https://fao.org"},
        ],
    }
    cm, _ = _make_client_mock(_mock_http_response(payload))

    with patch("httpx.AsyncClient", return_value=cm):
        result = await backend.search("fish catch")

    assert len(result.people_also_ask) == 1
    paa = result.people_also_ask[0]
    assert paa.question == "How much fish is caught globally?"
    assert paa.snippet == "About 90Mt/yr"
    assert paa.link == "https://fao.org"


@pytest.mark.asyncio
async def test_skips_people_also_ask_without_question():
    """PAA entries missing the question field are dropped."""
    backend = SerperBackend(api_key="test-key")
    payload = {
        "organic": [],
        "relatedSearches": [],
        "peopleAlsoAsk": [
            {"snippet": "no question key"},
            {"question": "Valid question?", "snippet": "answer"},
        ],
    }
    cm, _ = _make_client_mock(_mock_http_response(payload))

    with patch("httpx.AsyncClient", return_value=cm):
        result = await backend.search("fish")

    assert len(result.people_also_ask) == 1
    assert result.people_also_ask[0].question == "Valid question?"


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retries_on_429_then_succeeds():
    """A 429 response triggers one retry and ultimately returns results."""
    backend = SerperBackend(api_key="test-key")

    rate_limited = _mock_http_response({}, status_code=429)
    rate_limited.raise_for_status = MagicMock()  # don't raise on 429

    success_payload = {"organic": [{"title": "OK", "link": "https://fao.org", "snippet": ""}], "relatedSearches": []}
    ok_resp = _mock_http_response(success_payload)

    client = AsyncMock()
    client.post = AsyncMock(side_effect=[rate_limited, ok_resp])
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=cm), patch("asyncio.sleep", new_callable=AsyncMock):
        result = await backend.search("fish")

    assert client.post.call_count == 2
    assert len(result.results) == 1


@pytest.mark.asyncio
async def test_retries_on_5xx_then_succeeds():
    """A 503 response triggers retry; subsequent success is returned."""
    backend = SerperBackend(api_key="test-key")

    server_error = _mock_http_response({}, status_code=503)
    server_error.raise_for_status = MagicMock()

    success_payload = {"organic": [{"title": "OK", "link": "https://fao.org", "snippet": ""}], "relatedSearches": []}
    ok_resp = _mock_http_response(success_payload)

    client = AsyncMock()
    client.post = AsyncMock(side_effect=[server_error, ok_resp])
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=cm), patch("asyncio.sleep", new_callable=AsyncMock):
        result = await backend.search("fish")

    assert client.post.call_count == 2
    assert result.results[0].title == "OK"
