"""Unit tests for interactive browser navigation tools."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.tool import ToolContext

from web_scout.tools import _build_extractor_agent


def _make_ctx(tool_name: str = "tool") -> ToolContext:
    """Create a minimal ToolContext suitable for unit-testing tool invocations."""
    return ToolContext(
        context=None,
        tool_name=tool_name,
        tool_call_id="test-id",
        tool_arguments="{}",
    )


def _make_extractor_agent(url="https://example.org/portal", query="fish production"):
    """Build extractor agent with test fixtures; returns (agent, cleanup)."""
    return _build_extractor_agent(
        model="dummy",
        query=query,
        url=url,
        wait_for=None,
    )


def _find_tool(agent, name: str):
    """Locate a tool on an agent by function name."""
    for tool in agent.tools:
        fn = getattr(tool, "on_invoke_tool", None) or getattr(tool, "fn", None)
        if fn and getattr(fn, "__name__", None) == name:
            return tool
        # openai-agents wraps tools; try the name attribute
        if getattr(tool, "name", None) == name:
            return tool
    raise AssertionError(f"Tool '{name}' not found on agent. Available: {[getattr(t, 'name', t) for t in agent.tools]}")


# ---------------------------------------------------------------------------
# list_interactive_elements
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_interactive_elements_returns_numbered_list():
    """Happy path: Playwright page returns two elements; tool formats them."""
    agent, cleanup = _make_extractor_agent()
    tool = _find_tool(agent, "list_interactive_elements")

    fake_elements = [
        {"tag": "tab", "text": "Data by Year"},
        {"tag": "button", "text": "Load more results"},
    ]

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=fake_elements)

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        result = await tool.on_invoke_tool(_make_ctx(), "{}")

    assert "[1] tab: \"Data by Year\"" in result
    assert "[2] button: \"Load more results\"" in result
    assert "Interactive elements on page:" in result

    await cleanup()


@pytest.mark.asyncio
async def test_list_interactive_elements_no_elements():
    """Page with no interactive elements returns informative message."""
    agent, cleanup = _make_extractor_agent()
    tool = _find_tool(agent, "list_interactive_elements")

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=[])

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        result = await tool.on_invoke_tool(_make_ctx(), "{}")

    assert "No interactive elements found" in result

    await cleanup()


@pytest.mark.asyncio
async def test_list_interactive_elements_playwright_error():
    """Playwright launch failure returns a readable error string."""
    agent, cleanup = _make_extractor_agent()
    tool = _find_tool(agent, "list_interactive_elements")

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(side_effect=RuntimeError("browser crashed"))
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        result = await tool.on_invoke_tool(_make_ctx(), "{}")

    assert "list_interactive_elements failed" in result
    assert "browser crashed" in result

    await cleanup()


# ---------------------------------------------------------------------------
# click_element
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_click_element_returns_page_content():
    """After a successful click, tool returns updated page text."""
    agent, cleanup = _make_extractor_agent()

    fake_elements = [{"tag": "tab", "text": "Data by Year"}]

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(side_effect=[fake_elements, True])
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.url = "https://example.org/portal"
    mock_page.inner_text = AsyncMock(return_value="Year 2023: 1,200 tonnes\nYear 2022: 1,100 tonnes\n" * 30)

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    list_tool = _find_tool(agent, "list_interactive_elements")
    click_tool = _find_tool(agent, "click_element")

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        await list_tool.on_invoke_tool(_make_ctx(), "{}")
        result = await click_tool.on_invoke_tool(_make_ctx(), '{"index": 1}')

    assert "2023" in result
    assert "tonnes" in result

    await cleanup()


@pytest.mark.asyncio
async def test_click_element_enforces_limit():
    """click_element refuses after 5 clicks."""
    agent, cleanup = _make_extractor_agent()

    fake_elements = [{"tag": "tab", "text": "Tab A"}]

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=fake_elements)
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.inner_text = AsyncMock(return_value="content " * 200)

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    list_tool = _find_tool(agent, "list_interactive_elements")
    click_tool = _find_tool(agent, "click_element")

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        await list_tool.on_invoke_tool(_make_ctx(), "{}")
        for _ in range(5):
            await click_tool.on_invoke_tool(_make_ctx(), '{"index": 1}')
        result = await click_tool.on_invoke_tool(_make_ctx(), '{"index": 1}')

    assert "INTERACTION LIMIT REACHED" in result

    await cleanup()


@pytest.mark.asyncio
async def test_click_element_stale_index():
    """click_element returns descriptive message when index exceeds element count."""
    agent, cleanup = _make_extractor_agent()

    fake_elements = [{"tag": "tab", "text": "Tab A"}]

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(side_effect=[fake_elements, False])
    mock_page.wait_for_load_state = AsyncMock()

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    list_tool = _find_tool(agent, "list_interactive_elements")
    click_tool = _find_tool(agent, "click_element")

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        await list_tool.on_invoke_tool(_make_ctx(), "{}")
        result = await click_tool.on_invoke_tool(_make_ctx(), '{"index": 99}')

    assert "no longer visible" in result or "not found" in result.lower()

    await cleanup()


@pytest.mark.asyncio
async def test_click_element_thin_content_warning():
    """click_element appends warning when post-click content is under 500 chars."""
    agent, cleanup = _make_extractor_agent()

    fake_elements = [{"tag": "tab", "text": "Tab A"}]

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(side_effect=[fake_elements, True])
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.url = "https://example.org/portal"
    mock_page.inner_text = AsyncMock(return_value="short content")  # under 500 chars

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    list_tool = _find_tool(agent, "list_interactive_elements")
    click_tool = _find_tool(agent, "click_element")

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        await list_tool.on_invoke_tool(_make_ctx(), "{}")
        result = await click_tool.on_invoke_tool(_make_ctx(), '{"index": 1}')

    assert "Content still thin" in result

    await cleanup()


@pytest.mark.asyncio
async def test_click_element_called_without_session_raises():
    """click_element without a prior list_interactive_elements call returns error."""
    agent, cleanup = _make_extractor_agent()
    click_tool = _find_tool(agent, "click_element")

    result = await click_tool.on_invoke_tool(_make_ctx(), '{"index": 1}')

    assert "call list_interactive_elements" in result.lower()

    await cleanup()


@pytest.mark.asyncio
async def test_cleanup_closes_browser():
    """cleanup() closes the Playwright browser when it was opened."""
    agent, cleanup = _make_extractor_agent()

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=[{"tag": "tab", "text": "X"}])

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    list_tool = _find_tool(agent, "list_interactive_elements")

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        await list_tool.on_invoke_tool(_make_ctx(), "{}")
        await cleanup()

    mock_browser.close.assert_called_once()
    mock_pw_cm.__aexit__.assert_called_once()


@pytest.mark.asyncio
async def test_click_element_load_state_timeout_still_returns_content():
    """click_element returns content even when wait_for_load_state times out."""
    agent, cleanup = _make_extractor_agent()

    fake_elements = [{"tag": "tab", "text": "Tab A"}]

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(side_effect=[fake_elements, True])
    mock_page.wait_for_load_state = AsyncMock(side_effect=TimeoutError("networkidle timed out"))
    mock_page.url = "https://example.org/portal"
    mock_page.inner_text = AsyncMock(return_value="Year 2023: fish data\n" * 40)

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    list_tool = _find_tool(agent, "list_interactive_elements")
    click_tool = _find_tool(agent, "click_element")

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        await list_tool.on_invoke_tool(_make_ctx(), "{}")
        result = await click_tool.on_invoke_tool(_make_ctx(), '{"index": 1}')

    assert "2023" in result
    assert "fish data" in result

    await cleanup()


# ---------------------------------------------------------------------------
# Domain-restricted applications
# ---------------------------------------------------------------------------

def _make_extractor_agent_with_domains(
    url="https://example.org/portal",
    query="fish production",
    allowed_domains: frozenset = frozenset({"example.org"}),
):
    return _build_extractor_agent(
        model="dummy",
        query=query,
        url=url,
        wait_for=None,
        allowed_domains=allowed_domains,
    )


def _make_mock_browser(page):
    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = AsyncMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

    return mock_pw_cm


@pytest.mark.asyncio
async def test_click_element_blocks_navigation_to_blocked_domain():
    """click_element rejects content when a click navigates to a blocked domain (e.g. youtube.com)."""
    agent, cleanup = _make_extractor_agent_with_domains()

    fake_elements = [{"tag": "a", "text": "Watch video"}]

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(side_effect=[fake_elements, True])
    mock_page.wait_for_load_state = AsyncMock()
    # After click, the page navigated to a blocked domain
    mock_page.url = "https://www.youtube.com/watch?v=xyz"
    mock_page.go_back = AsyncMock()

    mock_pw_cm = _make_mock_browser(mock_page)

    list_tool = _find_tool(agent, "list_interactive_elements")
    click_tool = _find_tool(agent, "click_element")

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        await list_tool.on_invoke_tool(_make_ctx(), "{}")
        result = await click_tool.on_invoke_tool(_make_ctx(), '{"index": 1}')

    assert "blocked" in result.lower()
    assert "youtube.com" in result
    mock_page.go_back.assert_called_once()

    await cleanup()


@pytest.mark.asyncio
async def test_click_element_allows_navigation_within_allowed_domain():
    """click_element returns content when navigation stays within the allowed domain."""
    agent, cleanup = _make_extractor_agent_with_domains(
        url="https://example.org/portal",
        allowed_domains=frozenset({"example.org"}),
    )

    fake_elements = [{"tag": "tab", "text": "Production Data"}]

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(side_effect=[fake_elements, True])
    mock_page.wait_for_load_state = AsyncMock()
    # Navigation stays within allowed domain (not blocked)
    mock_page.url = "https://example.org/portal/data"
    mock_page.inner_text = AsyncMock(return_value="Production 2023: 1,500 tonnes\n" * 40)

    mock_pw_cm = _make_mock_browser(mock_page)

    list_tool = _find_tool(agent, "list_interactive_elements")
    click_tool = _find_tool(agent, "click_element")

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        await list_tool.on_invoke_tool(_make_ctx(), "{}")
        result = await click_tool.on_invoke_tool(_make_ctx(), '{"index": 1}')

    assert "2023" in result
    assert "tonnes" in result

    await cleanup()


@pytest.mark.asyncio
async def test_click_element_unblocked_domain_allowed_via_allowed_domains():
    """click_element allows navigation to a normally-blocked domain when it is in allowed_domains."""
    # reddit.com is in _BLOCKED_DOMAINS by default; passing it in allowed_domains unblocks it
    agent, cleanup = _make_extractor_agent_with_domains(
        url="https://reddit.com/r/dataisbeautiful",
        allowed_domains=frozenset({"reddit.com"}),
    )

    fake_elements = [{"tag": "button", "text": "Load comments"}]

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(side_effect=[fake_elements, True])
    mock_page.wait_for_load_state = AsyncMock()
    # Still on reddit.com — allowed
    mock_page.url = "https://www.reddit.com/r/dataisbeautiful/comments/abc"
    mock_page.inner_text = AsyncMock(return_value="Great chart! Source: FAO data\n" * 40)

    mock_pw_cm = _make_mock_browser(mock_page)

    list_tool = _find_tool(agent, "list_interactive_elements")
    click_tool = _find_tool(agent, "click_element")

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        await list_tool.on_invoke_tool(_make_ctx(), "{}")
        result = await click_tool.on_invoke_tool(_make_ctx(), '{"index": 1}')

    assert "FAO data" in result
    assert "blocked" not in result.lower()

    await cleanup()


@pytest.mark.asyncio
async def test_click_element_no_domain_restriction_allows_any_navigation():
    """Without allowed_domains, navigation to non-blocked external domains is allowed."""
    # No allowed_domains set — only standard _BLOCKED_DOMAINS applies
    agent, cleanup = _make_extractor_agent(url="https://fao.org/fishery/portal")

    fake_elements = [{"tag": "tab", "text": "Download"}]

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(side_effect=[fake_elements, True])
    mock_page.wait_for_load_state = AsyncMock()
    # Navigates to a different non-blocked domain
    mock_page.url = "https://ourworldindata.org/fish-production"
    mock_page.inner_text = AsyncMock(return_value="World fish production 2023\n" * 40)

    mock_pw_cm = _make_mock_browser(mock_page)

    list_tool = _find_tool(agent, "list_interactive_elements")
    click_tool = _find_tool(agent, "click_element")

    with patch("web_scout.tools.async_playwright", return_value=mock_pw_cm):
        await list_tool.on_invoke_tool(_make_ctx(), "{}")
        result = await click_tool.on_invoke_tool(_make_ctx(), '{"index": 1}')

    assert "2023" in result
    assert "blocked" not in result.lower()

    await cleanup()
