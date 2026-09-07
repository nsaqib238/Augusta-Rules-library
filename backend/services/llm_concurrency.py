"""Global OpenAI in-flight slot limit across API workers (Stage S3)."""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from services.redis_client import redis_eval

logger = logging.getLogger(__name__)

RAG_GLOBAL_LLM_INFLIGHT = max(1, int(os.getenv("RAG_GLOBAL_LLM_INFLIGHT", "60")))
LLM_SLOT_TTL_SECONDS = max(30, int(os.getenv("LLM_SLOT_TTL_SECONDS", "300")))
LLM_SLOT_WAIT_SECONDS = max(1.0, float(os.getenv("LLM_SLOT_WAIT_SECONDS", "120")))

_ACQUIRE_LUA = """
local n = tonumber(redis.call('GET', KEYS[1]) or '0')
if n >= tonumber(ARGV[1]) then
  return 0
end
redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""

_RELEASE_LUA = """
local n = redis.call('DECR', KEYS[1])
if n < 0 then redis.call('SET', KEYS[1], '0') end
return 1
"""

_REDIS_KEY = "llm:inflight:global"
_memory_lock = threading.Lock()
_memory_inflight = 0


def _memory_try_acquire() -> bool:
    global _memory_inflight
    with _memory_lock:
        if _memory_inflight >= RAG_GLOBAL_LLM_INFLIGHT:
            return False
        _memory_inflight += 1
        return True


def _memory_release() -> None:
    global _memory_inflight
    with _memory_lock:
        _memory_inflight = max(0, _memory_inflight - 1)


def _redis_try_acquire() -> Optional[bool]:
    result = redis_eval(
        _ACQUIRE_LUA,
        [_REDIS_KEY],
        [RAG_GLOBAL_LLM_INFLIGHT, LLM_SLOT_TTL_SECONDS],
    )
    if result is None:
        return None
    return bool(result)


def _redis_release() -> bool:
    result = redis_eval(_RELEASE_LUA, [_REDIS_KEY], [])
    return result is not None


def try_acquire_llm_slot() -> bool:
    """Acquire one global LLM slot without blocking."""
    redis_result = _redis_try_acquire()
    if redis_result is not None:
        return redis_result
    return _memory_try_acquire()


def release_llm_slot() -> None:
    if _redis_release():
        return
    _memory_release()


def acquire_llm_slot(*, timeout: Optional[float] = None) -> bool:
    """
    Block until a slot is available or timeout elapses.
    Returns False when the wait budget is exhausted.
    """
    deadline = time.monotonic() + (timeout if timeout is not None else LLM_SLOT_WAIT_SECONDS)
    while time.monotonic() < deadline:
        if try_acquire_llm_slot():
            return True
        time.sleep(0.05)
    logger.warning(
        "LLM slot wait timed out after %.1fs (limit=%s)",
        timeout if timeout is not None else LLM_SLOT_WAIT_SECONDS,
        RAG_GLOBAL_LLM_INFLIGHT,
    )
    return False


@contextmanager
def llm_slot(*, timeout: Optional[float] = None) -> Iterator[None]:
    """Context manager that holds one global LLM slot for an OpenAI call."""
    if not acquire_llm_slot(timeout=timeout):
        raise RuntimeError(
            f"Timed out waiting for an OpenAI slot (global limit {RAG_GLOBAL_LLM_INFLIGHT})"
        )
    try:
        yield
    finally:
        release_llm_slot()
