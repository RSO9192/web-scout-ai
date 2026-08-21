"""Per-host shared stealth browser sessions (private).

Replaces the one-browser-per-fetch pattern: the first fetch for a host
launches an ``AsyncStealthySession`` (patchright Chromium) and passes the
bot wall; subsequent fetches for the same host reuse pooled pages inside
that browser, where the fingerprint/cookies that satisfied Cloudflare live.

Design (see docs/superpowers/specs/2026-08-21-cf-session-reuse-design.md):
- single-flight: a per-host lock guards session *creation* only
- a global semaphore caps concurrent browser fetches; it is acquired
  before ``session.fetch`` so queue wait never consumes the page timeout
- at most MAX_SESSIONS idle sessions per event loop (LRU-evicted);
  busy sessions are never evicted, the cap overshoots instead
- sessions are per-event-loop (a Playwright object is unusable from
  another loop); pipeline teardown calls ``close_stealthy_sessions()``
"""

import asyncio
import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Dict
from urllib.parse import urlparse
from weakref import WeakKeyDictionary

logger = logging.getLogger(__name__)

MAX_SESSIONS = 3
MAX_CONCURRENT_BROWSER_FETCHES = 4
SESSION_MAX_PAGES = 3

# Keys accepted only by the AsyncStealthySession constructor, not by
# session.fetch() (scrapling 0.4.14 StealthSession vs StealthFetchParams).
_SESSION_ONLY_KEYS = frozenset({"headless", "max_pages", "retries", "retry_delay"})


def _session_factory(**kwargs):
    """Construct an AsyncStealthySession. Indirection point for tests."""
    from scrapling.engines._browsers._stealth import AsyncStealthySession

    return AsyncStealthySession(**kwargs)


@dataclass
class _Entry:
    session: Any
    last_used: int
    in_flight: int = 0


@dataclass
class _LoopState:
    sessions: Dict[str, _Entry] = field(default_factory=dict)
    locks: Dict[str, asyncio.Lock] = field(default_factory=dict)
    semaphore: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(MAX_CONCURRENT_BROWSER_FETCHES)
    )
    counter: Any = field(default_factory=itertools.count)


_registry: "WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopState]" = WeakKeyDictionary()


def _loop_state() -> _LoopState:
    loop = asyncio.get_running_loop()
    state = _registry.get(loop)
    if state is None:
        state = _LoopState()
        _registry[loop] = state
    return state


async def _close_entry(host: str, entry: _Entry) -> None:
    try:
        await entry.session.close()
    except Exception as exc:  # closing must never mask the original failure
        logger.debug("[stealth-session] close failed for %s: %s", host, exc)


async def _evict_idle_lru(state: _LoopState) -> None:
    idle = [(e.last_used, h) for h, e in state.sessions.items() if e.in_flight == 0]
    if not idle:
        # Every session is mid-fetch: overshoot the cap rather than kill one.
        return
    _, host = min(idle)
    entry = state.sessions.pop(host)
    logger.info("[stealth-session] evicting idle session for %s", host)
    await _close_entry(host, entry)


async def _get_or_create(state: _LoopState, host: str, session_kwargs: dict) -> _Entry:
    lock = state.locks.setdefault(host, asyncio.Lock())
    async with lock:
        entry = state.sessions.get(host)
        if entry is None:
            while len(state.sessions) >= MAX_SESSIONS:
                before = len(state.sessions)
                await _evict_idle_lru(state)
                if len(state.sessions) == before:
                    break  # all busy — accept overshoot
            session = _session_factory(max_pages=SESSION_MAX_PAGES, **session_kwargs)
            async with state.semaphore:  # a launch is browser work too
                await session.start()
            entry = _Entry(session=session, last_used=next(state.counter))
            state.sessions[host] = entry
            logger.info("[stealth-session] new browser session for %s", host)
        entry.last_used = next(state.counter)
        return entry


async def fetch_via_session(url: str, **kwargs: Any):
    """Fetch *url* through the shared browser session for its host.

    Session-constructor kwargs (headless, retries, ...) apply on first
    creation for the host; per-fetch kwargs are passed to every fetch.
    On fetch failure the session is closed and evicted so the next call
    for this host starts fresh; the exception propagates to the caller.
    """
    host = urlparse(url).netloc.lower()
    state = _loop_state()
    session_kwargs = {k: v for k, v in kwargs.items() if k in _SESSION_ONLY_KEYS}
    fetch_kwargs = {k: v for k, v in kwargs.items() if k not in _SESSION_ONLY_KEYS}

    entry = await _get_or_create(state, host, session_kwargs)
    entry.in_flight += 1
    try:
        async with state.semaphore:
            result = await entry.session.fetch(url, **fetch_kwargs)
    except Exception:
        if state.sessions.get(host) is entry:
            state.sessions.pop(host, None)
            await _close_entry(host, entry)
        raise
    finally:
        entry.in_flight -= 1
    entry.last_used = next(state.counter)
    return result


async def close_stealthy_sessions() -> None:
    """Close every stealth session belonging to the current event loop."""
    loop = asyncio.get_running_loop()
    state = _registry.pop(loop, None)
    if state is None:
        return
    for host, entry in list(state.sessions.items()):
        await _close_entry(host, entry)
    state.sessions.clear()
