"""Exercise Parallel through backend selection, MCP HTTP and result consumption."""

import asyncio
import json
from unittest.mock import patch

import httpx2
import pytest

from web_scout._pipeline_flow import _build_search_backend, _execute_searches, _select_search_urls
from web_scout.search_backends import ParallelBackend, SerperBackend
from web_scout.tools import ResearchTracker
from web_scout.tools.search import create_web_search


@pytest.fixture
def mcp_http(monkeypatch):
    requests = []
    clients = []
    state = {
        "payload": {"results": [{"title": "Reef threats", "url": "https://noaa.gov/reefs", "excerpts": ["Warming."]}]},
        "is_error": False,
        "failure": None,
        "started": asyncio.Event(),
        "block": False,
        "structured": False,
        "status": 200,
    }

    async def respond(request):
        requests.append(request)
        if request.method != "POST":
            return httpx2.Response(405)
        message = json.loads(request.content)
        method = message["method"]
        if method == "notifications/initialized":
            return httpx2.Response(202)
        if method == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1"},
            }
        elif method == "server/discover":
            result = {
                "supportedVersions": ["2026-07-28"],
                "capabilities": {"tools": {}},
            }
        elif method == "tools/list":
            result = {
                "tools": [{"name": "web_search", "inputSchema": {"type": "object"}}],
                "cacheScope": "private",
                "resultType": "complete",
                "ttlMs": 0,
            }
        else:
            assert method == "tools/call"
            assert message["params"]["name"] == "web_search"
            state["started"].set()
            if state["block"]:
                await asyncio.Event().wait()
            if state["failure"]:
                raise state["failure"]
            if state["status"] != 200:
                return httpx2.Response(state["status"], json={"error": "unavailable"})
            result = {
                "content": [{"type": "text", "text": json.dumps(state["payload"])}],
                "isError": state["is_error"],
                "resultType": "complete",
            }
            if state["structured"]:
                result["structuredContent"] = state["payload"]
                result["content"] = []
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    def client_factory(headers=None, timeout=None, auth=None):
        client = httpx2.AsyncClient(
            headers=headers, timeout=timeout, auth=auth, transport=httpx2.MockTransport(respond)
        )
        clients.append(client)
        return client

    monkeypatch.setattr("agents.mcp.server._create_default_streamable_http_client", client_factory)
    yield state, requests
    assert clients and all(client.is_closed for client in clients)
    for request in requests:
        assert str(request.url) == "https://search.parallel.ai/mcp"
        assert request.headers["user-agent"] == "web-scout-ai (+https://github.com/RSO9192/web-scout-ai)"
        assert "authorization" not in request.headers


def tool_arguments(requests):
    return [
        json.loads(r.content)["params"]["arguments"]
        for r in requests
        if r.method == "POST" and json.loads(r.content)["method"] == "tools/call"
    ]


async def test_selected_backend_feeds_pipeline_and_tool_without_search_keys(mcp_http, monkeypatch):
    for key in ("SERPER_API_KEY", "EXA_API_KEY", "PARALLEL_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    state, requests = mcp_http
    backend = _build_search_backend("parallel")
    assert isinstance(backend, ParallelBackend)
    tracker = ResearchTracker()
    responses = await _execute_searches(
        backend=backend,
        search_queries=["coral reef threats"],
        include_domains=None,
        tracker=tracker,
    )
    assert responses[0].results[0].snippet == "Warming."
    assert tracker.queries[0].query == "coral reef threats"
    assert _select_search_urls(
        query="coral reef threats",
        include_domains=None,
        depth={"urls_first": 3},
        iteration=0,
        tracker=tracker,
        search_results=responses,
    ) == ["https://noaa.gov/reefs"]
    from agents.tool_context import ToolContext

    args = '{"query": "coral reef threats"}'
    ctx = ToolContext(context=None, tool_name="web_search", tool_call_id="call-1", tool_arguments=args)
    rendered = await create_web_search(backend=backend).on_invoke_tool(ctx, args)
    assert "Reef threats" in rendered and "https://noaa.gov/reefs" in rendered and "Warming." in rendered
    calls = tool_arguments(requests)
    assert calls[0]["search_queries"] == ["coral reef threats"]
    assert calls[0]["session_id"] == calls[1]["session_id"]
    assert len(calls[0]["session_id"]) == 32


@pytest.mark.parametrize("structured", [False, True])
async def test_filters_before_cap_and_preserves_metadata(mcp_http, structured):
    state, requests = mcp_http
    state["structured"] = structured
    state["payload"] = {
        "results": [
            {"url": "https://evilnoaa.gov/other", "excerpts": []},
            {"url": "https://blocked.noaa.gov/other", "excerpts": []},
            {"url": "file:///tmp/local", "excerpts": []},
            {"title": None, "excerpts": []},
            {"title": "Reefs", "url": "https://ocean.noaa.gov/reefs", "excerpts": [" One ", "", " Two "]},
            {"url": "https://noaa.gov/extra", "excerpts": []},
        ]
    }
    response = await _build_search_backend("parallel").search(
        "coral reef threats",
        max_results=1,
        include_domains=["NOAA.gov"],
        exclude_domains=["blocked.noaa.gov"],
        country_code="US",
        language_code="en",
    )
    assert [(r.title, r.url, r.snippet) for r in response.results] == [
        ("Reefs", "https://ocean.noaa.gov/reefs", "One … Two"),
    ]
    assert (response.query, response.country_code, response.language_code) == ("coral reef threats", "US", "en")
    objective = tool_arguments(requests)[0]["objective"]
    assert all(hint in objective for hint in ("NOAA.gov", "blocked.noaa.gov", "US", "en"))


@pytest.mark.parametrize(
    "payload,is_error,exception",
    [
        ({"message": "quota exceeded"}, True, RuntimeError),
        ({"errors": ["search unavailable"], "results": []}, False, RuntimeError),
        ({"unexpected": []}, False, ValueError),
    ],
)
async def test_mcp_errors_do_not_become_empty_success(mcp_http, payload, is_error, exception):
    state, _ = mcp_http
    state.update(payload=payload, is_error=is_error)
    with pytest.raises(exception):
        await _build_search_backend("parallel").search("coral reef threats")


async def test_empty_results_are_a_valid_response(mcp_http):
    state, _ = mcp_http
    state["payload"] = {"results": []}
    assert (await _build_search_backend("parallel").search("coral reef threats")).results == []


async def test_transport_timeout_uses_existing_pipeline_failure_path(mcp_http):
    state, _ = mcp_http
    state["failure"] = httpx2.ReadTimeout("fixture timed out")
    tracker = ResearchTracker()
    responses = await _execute_searches(
        backend=_build_search_backend("parallel"),
        search_queries=["coral reef threats"],
        include_domains=None,
        tracker=tracker,
    )
    assert responses == [None]
    assert tracker.queries == []


@pytest.mark.parametrize("status", [429, 500])
async def test_http_failure_does_not_cancel_research(mcp_http, status):
    state, requests = mcp_http
    state["status"] = status
    tracker = ResearchTracker()
    responses = await _execute_searches(
        backend=_build_search_backend("parallel"),
        search_queries=["coral reef threats"],
        include_domains=None,
        tracker=tracker,
    )
    assert responses == [None]
    assert tracker.queries == []
    assert len(tool_arguments(requests)) == 1


async def test_cancellation_propagates_and_closes_transport(mcp_http):
    state, _ = mcp_http
    state["block"] = True
    task = asyncio.create_task(
        _execute_searches(
            backend=_build_search_backend("parallel"),
            search_queries=["coral reef threats"],
            include_domains=None,
            tracker=ResearchTracker(),
        )
    )
    await asyncio.wait_for(state["started"].wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_default_and_explicit_backends_are_preserved(monkeypatch):
    import inspect

    from web_scout import run_web_research
    from web_scout.search_backends import ExaBackend

    assert inspect.signature(run_web_research).parameters["search_backend"].default == "serper"
    monkeypatch.setenv("SERPER_API_KEY", "existing-serper")
    monkeypatch.setenv("EXA_API_KEY", "existing-exa")
    assert isinstance(_build_search_backend("serper"), SerperBackend)
    assert isinstance(_build_search_backend("exa"), ExaBackend)
    with patch("agents.mcp.MCPServerStreamableHttp") as server:
        _build_search_backend("serper")
        _build_search_backend("exa")
        server.assert_not_called()
