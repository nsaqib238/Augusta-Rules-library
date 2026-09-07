"""
Concurrent session limit: max N active sessions per user (e.g. 5) to restrict sharing one account.
A session is identified by a hash of the access token; idle sessions expire after SESSION_IDLE_MINUTES.
"""
import hashlib
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict

from services.redis_client import redis_get, redis_setex
from services.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

MAX_CONCURRENT_SESSIONS = int(os.getenv("MAX_CONCURRENT_SESSIONS", "5"))
SESSION_IDLE_MINUTES = int(os.getenv("SESSION_IDLE_MINUTES", "30"))
SESSION_CHECK_CACHE_SECONDS = int(os.getenv("SESSION_CHECK_CACHE_SECONDS", "30"))

# user_id:session_key -> monotonic expiry time (fallback when Redis unavailable)
_session_check_cache: Dict[str, float] = {}


def _session_key(token: str) -> str:
    """Stable key per token (same token = same session)."""
    return hashlib.sha256(token.encode() if isinstance(token, str) else token).hexdigest()[:32]


def _cache_key(user_id: str, token: str) -> str:
    return f"{user_id}:{_session_key(token)}"


def _redis_session_cache_key(user_id: str, token: str) -> str:
    return f"session:check:{user_id}:{_session_key(token)}"


def _prune_session_cache(now: float) -> None:
    if len(_session_check_cache) < 512:
        return
    stale = [k for k, exp in _session_check_cache.items() if exp <= now]
    for k in stale:
        _session_check_cache.pop(k, None)


def _session_cache_hit(user_id: str, access_token: str) -> bool:
    if SESSION_CHECK_CACHE_SECONDS <= 0:
        return False
    redis_key = _redis_session_cache_key(user_id, access_token)
    if redis_get(redis_key) is not None:
        return True
    now_mono = time.monotonic()
    cache_hit = _session_check_cache.get(_cache_key(user_id, access_token))
    return cache_hit is not None and cache_hit > now_mono


def _session_cache_store(user_id: str, access_token: str) -> None:
    if SESSION_CHECK_CACHE_SECONDS <= 0:
        return
    redis_key = _redis_session_cache_key(user_id, access_token)
    if redis_setex(redis_key, SESSION_CHECK_CACHE_SECONDS, "1"):
        return
    now_mono = time.monotonic()
    _session_check_cache[_cache_key(user_id, access_token)] = now_mono + SESSION_CHECK_CACHE_SECONDS
    _prune_session_cache(now_mono)


def check_and_track_session(user_id: str, access_token: str) -> None:
    """
    Record this session as active, prune stale sessions, and enforce max concurrent per user.
    Raises ValueError with a message if over the limit.

    When SESSION_CHECK_CACHE_SECONDS > 0, skips Supabase round-trips for recent checks
    on the same user+token (in-memory or Redis when REDIS_URL is set).
    """
    if not user_id or not access_token:
        return

    if _session_cache_hit(user_id, access_token):
        return

    key = _session_key(access_token)
    supabase = get_supabase_client()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=SESSION_IDLE_MINUTES)

    try:
        supabase.table("user_sessions").delete().eq("user_id", user_id).lt("last_seen_at", cutoff.isoformat()).execute()
    except Exception as e:
        logger.warning("Session prune failed (table may not exist yet): %s", e)

    try:
        supabase.table("user_sessions").upsert(
            [{"user_id": user_id, "session_key": key, "last_seen_at": now.isoformat()}],
            on_conflict="user_id,session_key",
        ).execute()
    except Exception as e:
        logger.warning("Session upsert failed: %s", e)
        return

    try:
        r = (
            supabase.table("user_sessions")
            .select("session_key")
            .eq("user_id", user_id)
            .gte("last_seen_at", cutoff.isoformat())
            .execute()
        )
        count = len(r.data) if r.data else 0
    except Exception as e:
        logger.warning("Session count failed: %s", e)
        return

    if count > MAX_CONCURRENT_SESSIONS:
        raise ValueError(
            f"Too many devices or browsers logged in with this account (max {MAX_CONCURRENT_SESSIONS}). "
            "Please log out from another device or wait about {} minutes for inactive sessions to expire.".format(
                SESSION_IDLE_MINUTES
            )
        )

    _session_cache_store(user_id, access_token)
