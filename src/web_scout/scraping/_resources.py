"""Event-loop-owned transport resources and independent admission limits."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary

from playwright.async_api import async_playwright


@dataclass
class _Resources:
    http_limit: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(12))
    pdf_limit: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))
    model_limit: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(6))
    http_session: object = None
    http_manager: object = None
    http_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    search_client: object = None
    binary_session: object = None
    browser: object = None
    playwright: object = None
    browser_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    interactive_limit: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(3))
    interactive_active: int = 0
    browser_idle_handle: object = None


_resources = WeakKeyDictionary()
_pdf_admitted = ContextVar("web_scout_pdf_admitted", default=False)
_model_admitted = ContextVar("web_scout_model_admitted", default=False)


def resources():
    loop = asyncio.get_running_loop()
    if loop not in _resources:
        _resources[loop] = _Resources()
    return _resources[loop]


async def http_get(url, **kwargs):
    from scrapling.fetchers import FetcherSession

    from .utils import log_fetch

    state = resources()
    async with state.http_limit:
        async with state.http_lock:
            if state.http_session is None:
                session = FetcherSession(retries=1)
                state.http_session = await session.__aenter__()
                state.http_manager = session
        try:
            response = await state.http_session.get(url, **kwargs)
        except Exception as exc:
            log_fetch(url, status="error", via="http", error=f"{type(exc).__name__}: {exc}")
            raise
        status = getattr(response, "status", None) or getattr(response, "status_code", "?")
        body = getattr(response, "body", None)
        html = getattr(response, "html_content", None) or ""
        size = len(body) if isinstance(body, (bytes, bytearray)) else len(html)
        log_fetch(url, status=status, via="http", bytes_=size)
        return response


def search_client():
    import httpx

    state = resources()
    if state.search_client is None:
        state.search_client = httpx.AsyncClient(
            timeout=15, limits=httpx.Limits(max_connections=12, max_keepalive_connections=6)
        )
    return state.search_client


@asynccontextmanager
async def interactive_context(**kwargs):
    state = resources()
    async with state.interactive_limit:
        async with state.browser_lock:
            if state.browser_idle_handle is not None:
                state.browser_idle_handle.cancel()
                state.browser_idle_handle = None
            if state.browser is None or not state.browser.is_connected():
                if state.playwright is not None:
                    await state.playwright.__aexit__(None, None, None)
                state.playwright = async_playwright()
                pw = await state.playwright.__aenter__()
                state.browser = await pw.chromium.launch(headless=True)
            state.interactive_active += 1
        try:
            context = await state.browser.new_context(**kwargs)
            try:
                yield context
            finally:
                await context.close()
        finally:
            async with state.browser_lock:
                state.interactive_active -= 1
                if not state.interactive_active:
                    loop = asyncio.get_running_loop()
                    state.browser_idle_handle = loop.call_later(
                        120, lambda: loop.create_task(_close_idle_browser(state))
                    )


async def _close_idle_browser(state):
    async with state.browser_lock:
        if state.interactive_active:
            return
        if state.browser is not None:
            await state.browser.close()
            state.browser = None
        if state.playwright is not None:
            await state.playwright.__aexit__(None, None, None)
            state.playwright = None
        state.browser_idle_handle = None


async def close_resources():
    """Close this loop's resources after its requests have finished."""
    state = _resources.get(asyncio.get_running_loop())
    if state is not None and state.interactive_active:
        raise RuntimeError("Cannot shut down an active interactive browser")
    from web_scout._classification import close_classification_clients

    from ._stealth_session import close_stealthy_sessions

    await close_stealthy_sessions()
    await close_classification_clients()
    if state is None:
        return
    _resources.pop(asyncio.get_running_loop(), None)
    if state.browser_idle_handle is not None:
        state.browser_idle_handle.cancel()
    if state.http_session is not None:
        await state.http_manager.__aexit__(None, None, None)
    if state.search_client is not None:
        await state.search_client.aclose()
    if state.binary_session is not None:
        await state.binary_session.close()
    if state.browser is not None:
        await state.browser.close()
    if state.playwright is not None:
        await state.playwright.__aexit__(None, None, None)


@asynccontextmanager
async def pdf_admission():
    if _pdf_admitted.get():
        yield
        return
    async with resources().pdf_limit:
        token = _pdf_admitted.set(True)
        try:
            yield
        finally:
            _pdf_admitted.reset(token)


def binary_session():
    from curl_cffi.requests import AsyncSession

    state = resources()
    if state.binary_session is None:
        state.binary_session = AsyncSession(max_clients=4)
    return state.binary_session


@asynccontextmanager
async def model_admission():
    # Agent tools may invoke another model while the parent agent owns a slot.
    if _model_admitted.get():
        yield
        return
    async with resources().model_limit:
        token = _model_admitted.set(True)
        try:
            yield
        finally:
            _model_admitted.reset(token)
