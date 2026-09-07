"""Generate and store chunk embeddings (all-MiniLM-L6-v2, 384-dim) in Supabase pgvector."""
from __future__ import annotations

import logging
import os
import json
import hashlib
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence

from services.rag_settings import rag_settings
from services.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

_MODEL: Optional[Any] = None


def embeddings_enabled() -> bool:
    return os.getenv("DISABLE_CHUNK_EMBEDDINGS", "").lower() not in ("1", "true", "yes")


def embedding_model_name() -> str:
    return rag_settings.embedding_model


def embedding_dimension() -> int:
    return rag_settings.embedding_dimension


def _vector_literal(values: Sequence[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"


@lru_cache(maxsize=1)
def _load_model():
    if not embeddings_enabled():
        return None
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(embedding_model_name())
        logger.info("Loaded embedding model: %s", embedding_model_name())
        return model
    except Exception as exc:
        logger.warning("Could not load sentence-transformers model: %s", exc)
        return None


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Encode texts to unit-normalized vectors."""
    if not texts:
        return []
    model = _load_model()
    if model is None:
        return []
    vectors = model.encode(
        texts,
        batch_size=rag_settings.embedding_batch_size,
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    return [v.tolist() for v in vectors]


def preload_embedding_model() -> bool:
    """Load sentence-transformers model at worker startup (S2)."""
    return _load_model() is not None


def embed_query_text(text: str) -> Optional[List[float]]:
    normalized = (text or "").strip()
    if not normalized:
        return None

    cache_key = f"embed:query:{hashlib.sha256(normalized.encode()).hexdigest()}"
    ttl = max(3600, int(os.getenv("EMBED_QUERY_CACHE_SECONDS", "86400")))
    try:
        from services.redis_client import redis_get, redis_setex

        cached = redis_get(cache_key)
        if cached:
            parsed = json.loads(cached)
            if isinstance(parsed, list):
                return [float(x) for x in parsed]
    except Exception:
        pass

    vecs = embed_texts([normalized])
    if not vecs:
        return None
    vector = vecs[0]
    try:
        from services.redis_client import redis_setex

        redis_setex(cache_key, ttl, json.dumps(vector))
    except Exception:
        pass
    return vector


def _chunk_embed_text(row: Dict[str, Any]) -> str:
    parts = [
        row.get("heading") or "",
        row.get("clause_number") or "",
        row.get("text") or "",
    ]
    return "\n".join(p for p in parts if p).strip()


def upsert_embeddings_for_rows(
    rows: List[Dict[str, Any]],
    *,
    document_id: str,
    user_id: str,
) -> int:
    """Upsert chunk_embeddings for chunk rows that include id + text."""
    if not rows or not embeddings_enabled():
        return 0

    model_name = embedding_model_name()
    payload_rows: List[Dict[str, Any]] = []
    texts: List[str] = []

    for row in rows:
        chunk_id = row.get("id")
        text = _chunk_embed_text(row)
        if not chunk_id or not text:
            continue
        texts.append(text)
        payload_rows.append(row)

    if not texts:
        return 0

    vectors = embed_texts(texts)
    if len(vectors) != len(payload_rows):
        logger.warning("Embedding count mismatch: %s vs %s", len(vectors), len(payload_rows))
        return 0

    supabase = get_supabase_client()
    upsert_batch: List[Dict[str, Any]] = []
    for row, vec in zip(payload_rows, vectors):
        upsert_batch.append(
            {
                "chunk_id": row["id"],
                "document_id": document_id,
                "user_id": user_id,
                "embedding": _vector_literal(vec),
                "model": model_name,
            }
        )

    written = 0
    batch_size = rag_settings.embedding_batch_size
    for i in range(0, len(upsert_batch), batch_size):
        batch = upsert_batch[i : i + batch_size]
        try:
            supabase.table("chunk_embeddings").upsert(
                batch,
                on_conflict="chunk_id,model",
            ).execute()
            written += len(batch)
        except Exception as exc:
            logger.error("chunk_embeddings upsert failed: %s", exc)
            raise

    logger.info("Upserted %s chunk embeddings for document %s", written, document_id)
    return written


def sync_document_embeddings(document_id: str, user_id: str) -> int:
    """
    Embed all chunks for a document (idempotent upsert per chunk_id + model).
    """
    if not embeddings_enabled():
        return 0

    supabase = get_supabase_client()
    rows: List[Dict[str, Any]] = []
    page_size = 500
    offset = 0
    while True:
        batch = (
            supabase.table("chunks")
            .select("id, text, heading, clause_number, chunk_index")
            .eq("document_id", document_id)
            .eq("user_id", user_id)
            .order("chunk_index")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        data = batch.data or []
        rows.extend(data)
        if len(data) < page_size:
            break
        offset += page_size

    if not rows:
        return 0

    return upsert_embeddings_for_rows(rows, document_id=document_id, user_id=user_id)


def delete_document_embeddings(document_id: str) -> None:
    try:
        supabase = get_supabase_client()
        supabase.table("chunk_embeddings").delete().eq("document_id", document_id).execute()
    except Exception as exc:
        logger.warning("delete_document_embeddings(%s): %s", document_id, exc)
