"""Tests for search_backends.py — backend response parsing and query construction."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web_scout.search_backends import ExaBackend, SerperBackend


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
async def test_exclude_domains_ignored_by_serper():
    """Serper has no native domain exclusion: the query is sent unchanged
    and callers rely on post-search URL filtering instead."""
    backend = SerperBackend(api_key="test-key")
    resp = _mock_http_response({"organic": [], "relatedSearches": []})
    cm, client = _make_client_mock(resp)

    with patch("httpx.AsyncClient", return_value=cm):
        await backend.search("fish production", exclude_domains=["youtube.com", "reddit.com"])

    _, kwargs = client.post.call_args
    assert kwargs["json"]["q"] == "fish production"


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
            {
                "title": "FAO Report",
                "link": "https://fao.org/report",
                "snippet": "Fish data",
                "date": "2024-01",
                "position": 1,
            },
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
    # date/position were dropped from the contract: unused downstream
    assert not hasattr(r, "date")
    assert not hasattr(r, "position")


@pytest.mark.asyncio
async def test_skips_organic_results_without_link():
    """Results with no link field are excluded from output."""
    backend = SerperBackend(api_key="test-key")
    payload = {
        "organic": [
            {"title": "No Link Result", "snippet": "some text"},
            {
                "title": "Good Result",
                "link": "https://fao.org/report",
                "snippet": "data",
            },
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
            {
                "title": f"Result {i}",
                "link": f"https://example.com/{i}",
                "snippet": "data",
            }
            for i in range(10)
        ],
        "relatedSearches": [],
    }
    cm, _ = _make_client_mock(_mock_http_response(payload))

    with patch("httpx.AsyncClient", return_value=cm):
        result = await backend.search("fish", max_results=3)

    assert len(result.results) == 3


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retries_on_429_then_succeeds():
    """A 429 response triggers one retry and ultimately returns results."""
    backend = SerperBackend(api_key="test-key")

    rate_limited = _mock_http_response({}, status_code=429)
    rate_limited.raise_for_status = MagicMock()  # don't raise on 429

    success_payload = {
        "organic": [{"title": "OK", "link": "https://fao.org", "snippet": ""}],
        "relatedSearches": [],
    }
    ok_resp = _mock_http_response(success_payload)

    client = AsyncMock()
    client.post = AsyncMock(side_effect=[rate_limited, ok_resp])
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("httpx.AsyncClient", return_value=cm),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await backend.search("fish")

    assert client.post.call_count == 2
    assert len(result.results) == 1


@pytest.mark.asyncio
async def test_retries_on_5xx_then_succeeds():
    """A 503 response triggers retry; subsequent success is returned."""
    backend = SerperBackend(api_key="test-key")

    server_error = _mock_http_response({}, status_code=503)
    server_error.raise_for_status = MagicMock()

    success_payload = {
        "organic": [{"title": "OK", "link": "https://fao.org", "snippet": ""}],
        "relatedSearches": [],
    }
    ok_resp = _mock_http_response(success_payload)

    client = AsyncMock()
    client.post = AsyncMock(side_effect=[server_error, ok_resp])
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("httpx.AsyncClient", return_value=cm),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await backend.search("fish")

    assert client.post.call_count == 2
    assert result.results[0].title == "OK"


# ---------------------------------------------------------------------------
# Backend factory
# ---------------------------------------------------------------------------


def test_build_search_backend_exa(monkeypatch):
    """search_backend='exa' builds an ExaBackend from EXA_API_KEY."""
    from web_scout._pipeline_flow import _build_search_backend

    monkeypatch.setenv("EXA_API_KEY", "exa-key")
    assert isinstance(_build_search_backend("exa"), ExaBackend)


def test_build_search_backend_exa_requires_key(monkeypatch):
    """search_backend='exa' without EXA_API_KEY raises a clear error."""
    from web_scout._pipeline_flow import _build_search_backend

    monkeypatch.delenv("EXA_API_KEY", raising=False)
    with pytest.raises(ValueError, match="EXA_API_KEY"):
        _build_search_backend("exa")


# ---------------------------------------------------------------------------
# exclude_domains threading through the pipeline search flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_searches_forwards_exclude_domains():
    """_execute_searches passes exclude_domains to backend.search."""
    from web_scout._pipeline_flow import _execute_searches
    from web_scout.search_backends import SearchResponse

    backend = MagicMock()
    backend.search = AsyncMock(return_value=SearchResponse(results=[]))

    await _execute_searches(
        backend=backend,
        search_queries=["q1"],
        include_domains=None,
        exclude_domains=["youtube.com"],
        tracker=MagicMock(),
    )

    assert backend.search.call_args.kwargs["exclude_domains"] == ["youtube.com"]


@pytest.mark.asyncio
async def test_search_and_scrape_iteration_forwards_exclude_domains(monkeypatch):
    """_search_and_scrape_iteration threads exclude_domains into _execute_searches."""
    from web_scout import _pipeline_flow as flow

    captured = {}

    async def fake_generate(**kwargs):
        return ["q1"]

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return []

    def fake_select(**kwargs):
        return []

    async def fake_scrape(scrape_tool, urls, empty_log_message):
        return "iteration-result"

    monkeypatch.setattr(flow, "_generate_search_queries", fake_generate)
    monkeypatch.setattr(flow, "_execute_searches", fake_execute)
    monkeypatch.setattr(flow, "_select_search_urls", fake_select)
    monkeypatch.setattr(flow, "_scrape_urls", fake_scrape)

    result = await flow._search_and_scrape_iteration(
        query="q",
        include_domains=None,
        exclude_domains=["youtube.com"],
        depth={},
        iteration=0,
        missing_info="",
        query_gen_agent=None,
        backend=None,
        tracker=None,
        scrape_tool=None,
    )

    assert result == "iteration-result"
    assert captured["exclude_domains"] == ["youtube.com"]


@pytest.mark.asyncio
async def test_run_search_mode_passes_effective_exclude_set_to_search():
    """The full effective exclude set (default blocklist or user list) is
    forwarded to the search iteration for backends with native exclusion."""
    from web_scout._pipeline_flow import _run_search_mode_impl

    captured = {}

    async def fake_iteration(**kwargs):
        captured.update(kwargs)
        return "iteration-result"

    await _run_search_mode_impl(
        query="q",
        include_domains=None,
        search_backend="serper",
        domain_expertise=None,
        depth={"max_iterations": 1},
        query_gen_model=None,
        evaluator_model=None,
        followup_model=None,
        tracker=MagicMock(),
        scrape_tool=None,
        exclude_domains=frozenset({"youtube.com", "reddit.com"}),
        build_search_backend=lambda name: object(),
        build_query_agents=lambda **kwargs: (None, None),
        search_and_scrape_iteration=fake_iteration,
        evaluate_search_coverage=None,
    )

    assert captured["exclude_domains"] == ["reddit.com", "youtube.com"]


@pytest.mark.asyncio
async def test_web_search_tool_forwards_exclude_domains():
    """The web_search function tool accepts and forwards exclude_domains."""
    from web_scout.search_backends import SearchResponse
    from web_scout.tools.search import create_web_search

    backend = MagicMock()
    backend.search = AsyncMock(return_value=SearchResponse(results=[]))
    tool = create_web_search(backend=backend)

    from agents.tool_context import ToolContext

    args = '{"query": "fish", "exclude_domains": ["youtube.com"]}'
    ctx = ToolContext(context=None, tool_name="web_search", tool_call_id="call-1", tool_arguments=args)
    await tool.on_invoke_tool(ctx, args)

    assert backend.search.call_args is not None, "backend.search was never called"
    assert backend.search.call_args.kwargs["exclude_domains"] == ["youtube.com"]


# ---------------------------------------------------------------------------
# ExaBackend — request construction
# ---------------------------------------------------------------------------


def _exa_payload(results: list[dict]) -> dict:
    return {"requestId": "req-1", "results": results}


@pytest.mark.asyncio
async def test_exa_request_shape():
    """ExaBackend posts query, numResults, type=auto and highlights contents."""
    backend = ExaBackend(api_key="exa-key")
    cm, client = _make_client_mock(_mock_http_response(_exa_payload([])))

    with patch("httpx.AsyncClient", return_value=cm):
        await backend.search("fish production", max_results=7)

    args, kwargs = client.post.call_args
    assert args[0] == "https://api.exa.ai/search"
    assert kwargs["headers"]["x-api-key"] == "exa-key"
    body = kwargs["json"]
    assert body["query"] == "fish production"
    assert body["numResults"] == 7
    assert body["type"] == "auto"
    assert body["contents"] == {"highlights": {"maxCharacters": 1000}}
    assert "includeDomains" not in body
    assert "excludeDomains" not in body


@pytest.mark.asyncio
async def test_exa_passes_native_domain_filters():
    """include/exclude domains map to Exa's native includeDomains/excludeDomains."""
    backend = ExaBackend(api_key="exa-key")
    cm, client = _make_client_mock(_mock_http_response(_exa_payload([])))

    with patch("httpx.AsyncClient", return_value=cm):
        await backend.search(
            "fish production",
            include_domains=["fao.org"],
            exclude_domains=["youtube.com"],
        )

    _, kwargs = client.post.call_args
    body = kwargs["json"]
    assert body["includeDomains"] == ["fao.org"]
    assert body["excludeDomains"] == ["youtube.com"]
    assert body["query"] == "fish production"  # no site: operators injected


# ---------------------------------------------------------------------------
# ExaBackend — response parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exa_parses_results_into_contract():
    """Exa results map onto SearchResult with highlights joined as snippet."""
    backend = ExaBackend(api_key="exa-key")
    payload = _exa_payload(
        [
            {
                "title": "FAO Report",
                "url": "https://fao.org/report",
                "highlights": ["Fish output rose 3%.", "Aquaculture leads growth."],
            },
        ]
    )
    cm, _ = _make_client_mock(_mock_http_response(payload))

    with patch("httpx.AsyncClient", return_value=cm):
        result = await backend.search("fish")

    assert len(result.results) == 1
    r = result.results[0]
    assert r.title == "FAO Report"
    assert r.url == "https://fao.org/report"
    assert r.snippet == "Fish output rose 3%. … Aquaculture leads growth."
    # related_searches was dropped from the contract: unused downstream
    assert not hasattr(result, "related_searches")


@pytest.mark.asyncio
async def test_exa_handles_missing_highlights():
    """Results without highlights still map cleanly."""
    backend = ExaBackend(api_key="exa-key")
    payload = _exa_payload([{"title": "Bare", "url": "https://example.org"}])
    cm, _ = _make_client_mock(_mock_http_response(payload))

    with patch("httpx.AsyncClient", return_value=cm):
        result = await backend.search("fish")

    assert result.results[0].snippet == ""


@pytest.mark.asyncio
async def test_exa_skips_results_without_url():
    """Results with no url are excluded from output."""
    backend = ExaBackend(api_key="exa-key")
    payload = _exa_payload(
        [
            {"title": "No URL"},
            {"title": "Good", "url": "https://fao.org/x"},
        ]
    )
    cm, _ = _make_client_mock(_mock_http_response(payload))

    with patch("httpx.AsyncClient", return_value=cm):
        result = await backend.search("fish")

    assert [r.title for r in result.results] == ["Good"]


@pytest.mark.asyncio
async def test_exa_respects_max_results_cap():
    """Results list is truncated to max_results even if API returns more."""
    backend = ExaBackend(api_key="exa-key")
    payload = _exa_payload([{"title": f"R{i}", "url": f"https://example.com/{i}"} for i in range(10)])
    cm, _ = _make_client_mock(_mock_http_response(payload))

    with patch("httpx.AsyncClient", return_value=cm):
        result = await backend.search("fish", max_results=3)

    assert len(result.results) == 3


# ---------------------------------------------------------------------------
# ExaBackend — retry behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exa_retries_on_429_then_succeeds():
    """A 429 response triggers one retry and ultimately returns results."""
    backend = ExaBackend(api_key="exa-key")

    rate_limited = _mock_http_response({}, status_code=429)
    ok_resp = _mock_http_response(_exa_payload([{"title": "OK", "url": "https://fao.org"}]))

    client = AsyncMock()
    client.post = AsyncMock(side_effect=[rate_limited, ok_resp])
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("httpx.AsyncClient", return_value=cm),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await backend.search("fish")

    assert client.post.call_count == 2
    assert result.results[0].title == "OK"
