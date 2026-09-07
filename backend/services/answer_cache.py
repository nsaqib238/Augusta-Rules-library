"""Redis cache for shared-library (NCC/SIR) ask answers.

Only caches asks without a private document_id. Keyed by codebook + normalized
question + RAG config version. Skipped when ANSWER_CACHE_ENABLED=false or Redis
is unavailable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, Optional

from services.redis_client import get_redis_client, redis_enabled
from services.rag_settings import rag_settings

logger = logging.getLogger(__name__)

ANSWER_CACHE_VERSION = "v1"


def _env_truthy(name: str, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def answer_cache_enabled() -> bool:
    return _env_truthy("ANSWER_CACHE_ENABLED", default=True)


def answer_cache_ttl_seconds() -> int:
    return max(60, int(os.getenv("ANSWER_CACHE_TTL_SECONDS", str(24 * 3600))))


def _normalize_question(question: str) -> str:
    q = (question or "").strip().lower()
    q = re.sub(r"\s+", " ", q)
    return q


def is_cacheable_ask(payload: Dict[str, Any]) -> bool:
    """Shared-library asks only — never cache private uploaded documents."""
    if not answer_cache_enabled():
        return False
    doc_id = (payload.get("document_id") or "").strip()
    if doc_id:
        return False
    codebook = (payload.get("codebook_id") or "").strip()
    if not codebook:
        return False
    question = _normalize_question(payload.get("question") or "")
    if len(question) < 3:
        return False
    if payload.get("skip_answer"):
        return False
    return True


def _config_fingerprint() -> str:
    parts = [
        ANSWER_CACHE_VERSION,
        rag_settings.rag_top_k,
        rag_settings.rag_parcel_size,
        rag_settings.rag_max_parallel_parcels,
        rag_settings.rag_router_model,
        rag_settings.rag_synthesis_model,
        rag_settings.gemini_router_model,
        rag_settings.gemini_synthesis_model,
        rag_settings.llm_primary_provider,
    ]
    return "|".join(str(p) for p in parts)


def cache_key_for_payload(payload: Dict[str, Any]) -> str:
    codebook = (payload.get("codebook_id") or "").strip().lower()
    question = _normalize_question(payload.get("question") or "")
    raw = f"{codebook}\n{question}\n{_config_fingerprint()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]
    return f"ask:answer:{ANSWER_CACHE_VERSION}:{digest}"


def get_cached_answer(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not is_cacheable_ask(payload) or not redis_enabled():
        return None
    client = get_redis_client()
    if client is None:
        return None
    key = cache_key_for_payload(payload)
    try:
        raw = client.get(key)
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict) or "answer" not in data:
            return None
        data = dict(data)
        data["cached"] = True
        logger.info("Answer cache HIT key=%s codebook=%s", key, payload.get("codebook_id"))
        return data
    except Exception as exc:
        logger.warning("Answer cache get failed: %s", exc)
        return None


def store_cached_answer(payload: Dict[str, Any], result: Dict[str, Any]) -> None:
    if not is_cacheable_ask(payload) or not redis_enabled():
        return
    answer = result.get("answer")
    if not isinstance(answer, dict):
        return
    # Do not cache insufficient / error-shaped answers
    conclusion = (answer.get("conclusion") or "").strip().lower()
    if conclusion in ("insufficient_evidence", "error"):
        return
    markdown = (answer.get("answer_markdown") or "").strip()
    if len(markdown) < 40:
        return

    client = get_redis_client()
    if client is None:
        return
    key = cache_key_for_payload(payload)
    try:
        to_store = {
            "prepared": result.get("prepared"),
            "clauses": result.get("clauses"),
            "retrieval": result.get("retrieval"),
            "answer": result.get("answer"),
            "retry": result.get("retry"),
            "trace": result.get("trace"),
            "cached": True,
        }
        client.setex(key, answer_cache_ttl_seconds(), json.dumps(to_store, default=str))
        logger.info("Answer cache STORE key=%s codebook=%s", key, payload.get("codebook_id"))
    except Exception as exc:
        logger.warning("Answer cache store failed: %s", exc)
