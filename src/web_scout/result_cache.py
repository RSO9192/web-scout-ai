"""Prefect-backed caches for PDF parses and URL fetches.

Callers pass a writable Prefect block (for example a GCS bucket). When they do
not, records are stored under ``~/.cache/web-scout`` and survive a new process.
Cache records and result payloads use separate folders so they do not overwrite
each other. A process-wide lock makes concurrent workers wait for the same PDF
or URL instead of doing the work twice.

These cache tasks commit on their own. They do not join the caller's Prefect
transaction, so a failed fetch or PDF parse cannot roll back or abort the
caller's commit.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

os.environ.setdefault("PREFECT_LOGGING_TO_API_WHEN_MISSING_FLOW", "ignore")

_STORAGE: ContextVar[Any] = ContextVar("web_scout_cache_storage", default=None)
_REFRESH_PDF: ContextVar[bool] = ContextVar("web_scout_refresh_pdf_cache", default=False)
_REFRESH_URL: ContextVar[bool] = ContextVar("web_scout_refresh_url_cache", default=False)

_LOCK_MANAGER: Any = None
_CACHE_NAMESPACE = uuid.UUID("8f3e2a1c-6b47-4d9e-9c15-2a7b0e4f6d18")


def _lock_manager():
    global _LOCK_MANAGER
    if _LOCK_MANAGER is None:
        from prefect.locking.memory import MemoryLockManager

        _LOCK_MANAGER = MemoryLockManager()
    return _LOCK_MANAGER


def refresh_pdf_cache_enabled() -> bool:
    return _REFRESH_PDF.get()


def refresh_url_cache_enabled() -> bool:
    return _REFRESH_URL.get()


@contextmanager
def use_result_cache(
    storage: Any = None,
    *,
    refresh_pdf_cache: bool = False,
    refresh_url_cache: bool = False,
) -> Iterator[None]:
    """Apply caller cache settings for the current task and its children."""
    storage_token = _STORAGE.set(storage)
    pdf_token = _REFRESH_PDF.set(refresh_pdf_cache)
    url_token = _REFRESH_URL.set(refresh_url_cache)
    try:
        yield
    finally:
        _STORAGE.reset(storage_token)
        _REFRESH_PDF.reset(pdf_token)
        _REFRESH_URL.reset(url_token)


def _is_block_slug(value: str) -> bool:
    return len(value.split("/")) == 2 and not value.startswith((".", "~", "/"))


def _default_root(kind: str) -> Path:
    configured = os.environ.get("WEB_SCOUT_CACHE_DIR")
    base = Path(configured).expanduser() if configured else Path.home() / ".cache" / "web-scout"
    path = base / f"{kind}-cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stamp_block(block: Any) -> Any:
    """Let Prefect accept an unsaved block without registering it on a server."""
    if getattr(block, "_block_document_id", None) is not None:
        return block
    identity = ":".join(
        str(getattr(block, field, ""))
        for field in ("basepath", "bucket", "bucket_folder")
    )
    block._block_document_id = uuid.uuid5(
        _CACHE_NAMESPACE,
        f"{type(block).__module__}.{type(block).__qualname__}:{identity}",
    )
    return block


def _with_prefix(storage: Any, prefix: str) -> Any:
    if isinstance(storage, Path) or (isinstance(storage, str) and not _is_block_slug(storage)):
        path = Path(storage).expanduser() / prefix
        path.mkdir(parents=True, exist_ok=True)
        return path

    if isinstance(storage, str):
        from prefect.blocks.core import Block

        storage = Block.load(storage)

    fields = getattr(type(storage), "model_fields", {})
    if "basepath" in fields:
        base = Path(str(storage.basepath)).expanduser() / prefix
        base.mkdir(parents=True, exist_ok=True)
        cloned = storage.model_copy(update={"basepath": str(base)})
        return _stamp_block(cloned)
    if "bucket_folder" in fields:
        folder = str(getattr(storage, "bucket_folder") or "").strip("/")
        cloned = storage.model_copy(update={"bucket_folder": f"{folder}/{prefix}" if folder else prefix})
        return _stamp_block(cloned)
    raise TypeError(
        "cache_storage must be a directory path or a Prefect filesystem block "
        "with a basepath or bucket_folder (for example GcsBucket)."
    )


def cache_locations(kind: str) -> tuple[Any, Any]:
    """Return ``(cache records, result payloads)`` storage for ``kind``."""
    configured = _STORAGE.get()
    root = _with_prefix(configured, kind) if configured is not None else _default_root(kind)
    return _with_prefix(root, "records"), _with_prefix(root, "results")


@asynccontextmanager
async def _outside_caller_transaction() -> AsyncIterator[None]:
    """Hide the caller's Prefect transaction for one cache task.

    A failed task rolls its transaction back, and ``reset()`` attaches that
    child to the parent. The parent commit then commits the child, which
    releases the same lock again and discards the caller's persisted result.
    """
    from prefect.transactions import BaseTransaction

    token = BaseTransaction.__var__.set(None)
    try:
        yield
    finally:
        BaseTransaction.__var__.reset(token)


async def call_cached_task(task: Any, *, kind: str, refresh: bool, exclude: tuple[str, ...] = (), **kwargs: Any):
    """Run a Prefect task with this process's lock and the resolved storage."""
    from prefect.cache_policies import INPUTS
    from prefect.transactions import IsolationLevel

    records, results = cache_locations(kind)
    policy = INPUTS
    for name in exclude:
        policy = policy - name
    policy = policy.configure(
        key_storage=records,
        isolation_level=IsolationLevel.SERIALIZABLE,
        lock_manager=_lock_manager(),
    )
    bound = task.with_options(
        cache_policy=policy,
        result_storage=results,
        persist_result=True,
        refresh_cache=refresh,
    )
    async with _outside_caller_transaction():
        return await bound(**kwargs)
