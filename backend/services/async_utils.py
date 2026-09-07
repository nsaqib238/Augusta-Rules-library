"""
Tiny helpers for keeping the FastAPI event loop responsive under concurrency.

The supabase-py client and boto3 are synchronous: when they are called from
inside an `async def` route they block the **entire event loop**, which on a
single-worker uvicorn deployment serialises every other in-flight request and
makes Nginx return 504 to whichever request didn't get a chance to respond
within `proxy_read_timeout`.

`run_blocking` hands the blocking call to the default asyncio thread pool so
the loop can keep handling other requests while waiting on Supabase / S3 / etc.

`run_blocking_ask` uses a dedicated pool so long Q&A work cannot exhaust the
default pool (~6–8 threads) and stall auth, uploads, and polls.
"""

from __future__ import annotations

import asyncio
import functools
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable, Optional, TypeVar

T = TypeVar("T")

_ASK_EXECUTOR: Optional[ThreadPoolExecutor] = None


def _ask_executor_max_workers() -> int:
    return max(1, int(os.getenv("ASK_EXECUTOR_MAX_WORKERS", "32")))


def get_ask_executor() -> ThreadPoolExecutor:
    global _ASK_EXECUTOR
    if _ASK_EXECUTOR is None:
        _ASK_EXECUTOR = ThreadPoolExecutor(
            max_workers=_ask_executor_max_workers(),
            thread_name_prefix="ask-pool",
        )
    return _ASK_EXECUTOR


def shutdown_ask_executor() -> None:
    global _ASK_EXECUTOR
    if _ASK_EXECUTOR is not None:
        _ASK_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        _ASK_EXECUTOR = None


async def run_blocking(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a sync callable on the asyncio default thread pool."""
    if kwargs:
        bound = functools.partial(fn, *args, **kwargs)
        return await asyncio.to_thread(bound)
    return await asyncio.to_thread(fn, *args)


async def run_blocking_ask(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run Q&A / RAG sync work on the dedicated ask thread pool."""
    loop = asyncio.get_running_loop()
    if kwargs:
        bound = functools.partial(fn, *args, **kwargs)
        return await loop.run_in_executor(get_ask_executor(), bound)
    return await loop.run_in_executor(get_ask_executor(), fn, *args)


async def gather_first_error(*awaitables: Awaitable[Any]) -> list[Any]:
    """Run awaitables concurrently; cancel and surface the first error."""
    return await asyncio.gather(*awaitables)
