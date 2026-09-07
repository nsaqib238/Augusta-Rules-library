"""Daily free-ask budget for Sole (free) users.

Professional subscribers bypass these limits. Counters reset at midnight in
FREE_ASK_TIMEZONE (default Australia/Sydney).

Env:
  FREE_ASK_QUOTA_ENABLED=true
  FREE_ASK_DAILY_GLOBAL_LIMIT=80
  FREE_ASK_DAILY_PER_USER_LIMIT=5
  FREE_ASK_TIMEZONE=Australia/Sydney
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from services.redis_client import redis_eval

logger = logging.getLogger(__name__)


def _env_truthy(name: str, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def free_ask_quota_enabled() -> bool:
    return _env_truthy("FREE_ASK_QUOTA_ENABLED", default=True)


def free_ask_global_limit() -> int:
    return max(0, int(os.getenv("FREE_ASK_DAILY_GLOBAL_LIMIT", "80")))


def free_ask_per_user_limit() -> int:
    return max(0, int(os.getenv("FREE_ASK_DAILY_PER_USER_LIMIT", "5")))


def free_ask_timezone() -> str:
    return (os.getenv("FREE_ASK_TIMEZONE") or "Australia/Sydney").strip() or "Australia/Sydney"


_RESERVE_LUA = """
local global = tonumber(redis.call('GET', KEYS[1]) or '0')
local user = tonumber(redis.call('GET', KEYS[2]) or '0')
local global_limit = tonumber(ARGV[1])
local user_limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
if global_limit > 0 and global >= global_limit then
  return -1
end
if user_limit > 0 and user >= user_limit then
  return -2
end
redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ttl)
redis.call('INCR', KEYS[2])
redis.call('EXPIRE', KEYS[2], ttl)
return 1
"""

_memory_lock = threading.Lock()
_memory_global: Dict[str, int] = {}
_memory_user: Dict[str, int] = {}


class FreeAskQuotaExceeded(Exception):
    def __init__(self, message: str, *, scope: str):
        super().__init__(message)
        self.message = message
        self.scope = scope  # "global" | "user"


def _day_and_ttl() -> Tuple[str, int]:
    try:
        tz = ZoneInfo(free_ask_timezone())
    except Exception:
        tz = ZoneInfo("Australia/Sydney")
    now = datetime.now(tz)
    day = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    ttl = max(60, int((tomorrow - now).total_seconds()))
    return day, ttl


def _keys(user_id: str, day: str) -> Tuple[str, str]:
    return f"ask:free:global:{day}", f"ask:free:user:{user_id}:{day}"


def _global_message(limit: int) -> str:
    return (
        f"Today's free questions are used up ({limit}/{limit}). "
        "Please subscribe for more, or try again tomorrow."
    )


def _user_message(limit: int) -> str:
    return (
        f"You've used your free questions for today ({limit}/{limit}). "
        "Please subscribe for more, or try again tomorrow."
    )


def _memory_reserve(user_id: str, day: str, global_limit: int, user_limit: int) -> int:
    """Return 1 ok, -1 global, -2 user."""
    gkey = f"g:{day}"
    ukey = f"u:{day}:{user_id}"
    with _memory_lock:
        g = _memory_global.get(gkey, 0)
        u = _memory_user.get(ukey, 0)
        if global_limit > 0 and g >= global_limit:
            return -1
        if user_limit > 0 and u >= user_limit:
            return -2
        _memory_global[gkey] = g + 1
        _memory_user[ukey] = u + 1
        return 1


def reserve_free_ask(user_id: str, *, is_professional: bool) -> None:
    """
    Atomically consume one free-ask slot for Sole users.
    No-op for Professional or when quota is disabled.
    Raises FreeAskQuotaExceeded when blocked.
    """
    if not free_ask_quota_enabled() or is_professional:
        return
    uid = (user_id or "").strip()
    if not uid:
        return

    global_limit = free_ask_global_limit()
    user_limit = free_ask_per_user_limit()
    if global_limit <= 0 and user_limit <= 0:
        return

    day, ttl = _day_and_ttl()
    gkey, ukey = _keys(uid, day)
    result = redis_eval(
        _RESERVE_LUA,
        [gkey, ukey],
        [global_limit, user_limit, ttl],
    )
    if result is None:
        result = _memory_reserve(uid, day, global_limit, user_limit)

    code = int(result)
    if code == 1:
        return
    if code == -1:
        raise FreeAskQuotaExceeded(_global_message(global_limit), scope="global")
    if code == -2:
        raise FreeAskQuotaExceeded(_user_message(user_limit), scope="user")
    raise FreeAskQuotaExceeded(_global_message(global_limit), scope="global")


def free_ask_quota_http_exception(exc: FreeAskQuotaExceeded) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail=exc.message,
        headers={"X-Free-Ask-Quota": exc.scope},
    )


def free_ask_quota_status(user_id: Optional[str] = None) -> Dict[str, object]:
    """Optional debug/status helper (used later if exposed)."""
    day, ttl = _day_and_ttl()
    global_limit = free_ask_global_limit()
    user_limit = free_ask_per_user_limit()
    from services.redis_client import redis_get

    g_raw = redis_get(f"ask:free:global:{day}")
    used_global = int(g_raw) if g_raw and str(g_raw).isdigit() else 0
    used_user = 0
    if user_id:
        u_raw = redis_get(f"ask:free:user:{user_id}:{day}")
        used_user = int(u_raw) if u_raw and str(u_raw).isdigit() else 0
    return {
        "enabled": free_ask_quota_enabled(),
        "day": day,
        "timezone": free_ask_timezone(),
        "ttl_seconds": ttl,
        "global_limit": global_limit,
        "global_used": used_global,
        "per_user_limit": user_limit,
        "user_used": used_user,
    }
