"""Per-user and global inflight limits for /query/* endpoints."""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, Tuple

from fastapi import HTTPException

from services.redis_client import redis_eval

logger = logging.getLogger(__name__)

ASK_MAX_CONCURRENT_PER_USER = max(1, int(os.getenv("ASK_MAX_CONCURRENT_PER_USER", "2")))
ASK_GLOBAL_MAX_INFLIGHT = max(1, int(os.getenv("ASK_GLOBAL_MAX_INFLIGHT", "80")))
ASK_INFLIGHT_TTL_SECONDS = max(60, int(os.getenv("ASK_INFLIGHT_TTL_SECONDS", "3600")))

_ACQUIRE_LUA = """
local global = tonumber(redis.call('GET', KEYS[1]) or '0')
local user = tonumber(redis.call('GET', KEYS[2]) or '0')
if global >= tonumber(ARGV[1]) or user >= tonumber(ARGV[2]) then
  return 0
end
redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('INCR', KEYS[2])
redis.call('EXPIRE', KEYS[2], ARGV[3])
return 1
"""

_RELEASE_LUA = """
local g = redis.call('DECR', KEYS[1])
if g < 0 then redis.call('SET', KEYS[1], '0') end
local u = redis.call('DECR', KEYS[2])
if u < 0 then redis.call('SET', KEYS[2], '0') end
return 1
"""

_memory_lock = threading.Lock()
_memory_global = 0
_memory_user: Dict[str, int] = {}


def _redis_keys(user_id: str) -> Tuple[str, str]:
    return "ask:inflight:global", f"ask:inflight:user:{user_id}"


def _memory_acquire(user_id: str) -> bool:
    global _memory_global
    with _memory_lock:
        user_count = _memory_user.get(user_id, 0)
        if _memory_global >= ASK_GLOBAL_MAX_INFLIGHT:
            return False
        if user_count >= ASK_MAX_CONCURRENT_PER_USER:
            return False
        _memory_global += 1
        _memory_user[user_id] = user_count + 1
        return True


def _memory_release(user_id: str) -> None:
    global _memory_global
    with _memory_lock:
        _memory_global = max(0, _memory_global - 1)
        user_count = _memory_user.get(user_id, 0)
        if user_count <= 1:
            _memory_user.pop(user_id, None)
        else:
            _memory_user[user_id] = user_count - 1


def acquire_ask_slot(user_id: str) -> bool:
    uid = (user_id or "").strip()
    if not uid:
        return True
    global_key, user_key = _redis_keys(uid)
    result = redis_eval(
        _ACQUIRE_LUA,
        [global_key, user_key],
        [ASK_GLOBAL_MAX_INFLIGHT, ASK_MAX_CONCURRENT_PER_USER, ASK_INFLIGHT_TTL_SECONDS],
    )
    if result is not None:
        return bool(result)
    return _memory_acquire(uid)


def release_ask_slot(user_id: str) -> None:
    uid = (user_id or "").strip()
    if not uid:
        return
    global_key, user_key = _redis_keys(uid)
    result = redis_eval(_RELEASE_LUA, [global_key, user_key], [])
    if result is not None:
        return
    _memory_release(uid)


def ask_rate_limit_http_exception() -> HTTPException:
    return HTTPException(
        status_code=429,
        detail=(
            "Too many questions in progress. Please wait for your current question to finish "
            "or try again shortly."
        ),
        headers={"Retry-After": "30"},
    )
