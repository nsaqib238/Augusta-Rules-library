"""Optional Redis client for scaling (session cache, ask rate limits)."""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_client = None
_redis_import_error: Optional[str] = None


def redis_enabled() -> bool:
    return bool((os.getenv("REDIS_URL") or "").strip())


def get_redis_client():
    """Return a sync Redis client or None when REDIS_URL is unset."""
    global _client, _redis_import_error
    url = (os.getenv("REDIS_URL") or "").strip()
    if not url:
        return None
    if _client is not None:
        return _client
    try:
        import redis
    except ImportError as exc:
        _redis_import_error = str(exc)
        logger.warning("redis package not installed; Redis features disabled")
        return None
    # socket_timeout must exceed BLPOP wait (ask job workers use 2s); otherwise
    # redis-py raises "Timeout reading from socket" on idle blocking reads.
    _client = redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=10,
        health_check_interval=30,
    )
    return _client


def redis_ping() -> bool:
    client = get_redis_client()
    if client is None:
        return False
    try:
        return bool(client.ping())
    except Exception as exc:
        logger.warning("Redis ping failed: %s", exc)
        return False


def redis_get(key: str) -> Optional[str]:
    client = get_redis_client()
    if client is None:
        return None
    try:
        return client.get(key)
    except Exception as exc:
        logger.warning("Redis GET %s failed: %s", key, exc)
        return None


def redis_setex(key: str, ttl_seconds: int, value: str) -> bool:
    client = get_redis_client()
    if client is None:
        return False
    try:
        client.setex(key, max(1, ttl_seconds), value)
        return True
    except Exception as exc:
        logger.warning("Redis SETEX %s failed: %s", key, exc)
        return False


def redis_eval(script: str, keys: list[str], args: list) -> Optional[int]:
    client = get_redis_client()
    if client is None:
        return None
    try:
        return client.eval(script, len(keys), *keys, *args)
    except Exception as exc:
        logger.warning("Redis EVAL failed: %s", exc)
        return None
