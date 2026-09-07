"""Postgres-native hybrid retrieval (FTS, trgm, heading, vector) — no full corpus load."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional, Set, Tuple

from services.rag_query_service import PreparedQuery
from services.rag_settings import rag_settings
from services.rag_trace import RagPipelineTrace, chunk_summary
from services.redis_client import redis_get, redis_setex
from services.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

_RETRIEVAL_CACHE_TTL = max(60, int(os.getenv("HYBRID_RETRIEVAL_CACHE_SECONDS", "900")))


def hybrid_search_enabled() -> bool:
    return (os.getenv("HYBRID_SEARCH_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


def hybrid_search_threshold() -> int:
    raw = (os.getenv("HYBRID_SEARCH_THRESHOLD") or "500").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 500


def _chunk_key(row: Dict[str, Any]) -> str:
    return str(row.get("id") or "")


def _estimate_visible_chunk_count(user_id: str, document_ids: List[str], codebook_id: str) -> int:
    if not document_ids:
        return 0
    try:
        result = (
            get_supabase_client()
            .rpc(
                "count_visible_chunks",
                {
                    "p_user_id": user_id,
                    "p_document_ids": document_ids,
                    "p_codebook": codebook_id,
                },
            )
            .execute()
        )
        if result.data is None:
            return 0
        if isinstance(result.data, int):
            return result.data
        return int(result.data)
    except Exception as exc:
        logger.warning("count_visible_chunks RPC failed (%s); assuming large corpus", exc)
        return hybrid_search_threshold() + 1


def should_use_hybrid_search(
    user_id: str,
    codebook_id: str,
    document_ids: List[str],
    document_id: Optional[str],
) -> bool:
    if not hybrid_search_enabled():
        return False
    if not document_ids:
        return False
    if document_id:
        return True
    try:
        count = _estimate_visible_chunk_count(user_id, document_ids, codebook_id)
        return count >= hybrid_search_threshold()
    except Exception:
        return True


def invalidate_retrieval_cache_for_document(document_id: str) -> None:
    if not document_id:
        return
    try:
        from services.redis_client import get_redis_client

        client = get_redis_client()
        if client is None:
            return
        for key in client.scan_iter(match=f"retrieval:v1:{document_id}:*", count=100):
            client.delete(key)
    except Exception as exc:
        logger.debug("Retrieval cache invalidation failed for %s: %s", document_id, exc)


def _retrieval_cache_key(
    user_id: str,
    codebook_id: str,
    document_id: Optional[str],
    prepared: PreparedQuery,
    top_k: int,
    exclude: Set[str],
) -> str:
    payload = {
        "user_id": user_id,
        "codebook_id": codebook_id,
        "document_id": document_id,
        "prepared": prepared.model_dump(),
        "top_k": top_k,
        "exclude": sorted(exclude),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24]
    scope = document_id or f"codebook:{codebook_id}"
    return f"retrieval:v1:{scope}:{digest}"


def _rpc_chunk_ids(result_data: Any, id_field: str = "chunk_id") -> List[str]:
    if not result_data:
        return []
    if isinstance(result_data, list):
        rows = result_data
    else:
        rows = [result_data]
    out: List[str] = []
    for row in rows:
        if isinstance(row, dict):
            cid = row.get(id_field) or row.get("id")
            if cid:
                out.append(str(cid))
    return out


def _signal_fts_rpc(
    user_id: str,
    document_ids: List[str],
    codebook_id: str,
    queries: List[str],
    limit: int,
) -> List[str]:
    supabase = get_supabase_client()
    ranked: List[str] = []
    seen: set[str] = set()
    for query in queries:
        q = (query or "").strip()
        if not q:
            continue
        try:
            result = supabase.rpc(
                "search_chunks_fts",
                {
                    "p_user_id": user_id,
                    "p_document_ids": document_ids,
                    "p_query": q,
                    "p_codebook": codebook_id,
                    "p_limit": limit,
                },
            ).execute()
        except Exception as exc:
            logger.warning("search_chunks_fts RPC failed: %s", exc)
            return []
        for cid in _rpc_chunk_ids(result.data):
            if cid not in seen:
                ranked.append(cid)
                seen.add(cid)
        if len(ranked) >= limit:
            break
    return ranked[:limit]


def _signal_trgm_rpc(
    user_id: str,
    document_ids: List[str],
    codebook_id: str,
    terms: List[str],
    limit: int,
) -> List[str]:
    supabase = get_supabase_client()
    ranked: List[str] = []
    seen: set[str] = set()
    for term in terms:
        t = (term or "").strip()
        if not t:
            continue
        try:
            result = supabase.rpc(
                "search_chunks_trgm",
                {
                    "p_user_id": user_id,
                    "p_document_ids": document_ids,
                    "p_term": t,
                    "p_codebook": codebook_id,
                    "p_limit": limit,
                },
            ).execute()
        except Exception as exc:
            logger.warning("search_chunks_trgm RPC failed: %s", exc)
            return []
        for cid in _rpc_chunk_ids(result.data):
            if cid not in seen:
                ranked.append(cid)
                seen.add(cid)
    return ranked[:limit]


def _signal_heading_rpc(
    user_id: str,
    document_ids: List[str],
    codebook_id: str,
    headings: List[str],
    limit: int,
) -> List[str]:
    supabase = get_supabase_client()
    ranked: List[str] = []
    seen: set[str] = set()
    for needle in headings:
        n = (needle or "").strip()
        if not n:
            continue
        try:
            result = supabase.rpc(
                "search_chunks_heading",
                {
                    "p_user_id": user_id,
                    "p_document_ids": document_ids,
                    "p_needle": n,
                    "p_codebook": codebook_id,
                    "p_limit": limit,
                },
            ).execute()
        except Exception as exc:
            logger.warning("search_chunks_heading RPC failed: %s", exc)
            return []
        for cid in _rpc_chunk_ids(result.data):
            if cid not in seen:
                ranked.append(cid)
                seen.add(cid)
    return ranked[:limit]


def _load_chunks_by_ids(chunk_ids: List[str]) -> List[Dict[str, Any]]:
    if not chunk_ids:
        return []
    supabase = get_supabase_client()
    rows: List[Dict[str, Any]] = []
    batch_size = 200
    for i in range(0, len(chunk_ids), batch_size):
        batch_ids = chunk_ids[i : i + batch_size]
        result = (
            supabase.table("chunks")
            .select(
                "id, document_id, chunk_index, text, heading, clause_number, "
                "page_number, codebook, jurisdiction, metadata"
            )
            .in_("id", batch_ids)
            .execute()
        )
        rows.extend(result.data or [])
    order = {cid: idx for idx, cid in enumerate(chunk_ids)}
    rows.sort(key=lambda r: order.get(str(r.get("id")), 10**9))
    return rows


def retrieve_hybrid_clauses(
    user_id: str,
    prepared: PreparedQuery,
    *,
    document_ids: List[str],
    document_id: Optional[str],
    top_k: int,
    candidate_limit: int,
    exclude: Set[str],
    trace: Optional[RagPipelineTrace],
    signal_vector_fn,
    signal_supabase_fts_fn,
    rrf_merge_fn,
    filter_excluded_fn,
    summarize_signal_fn,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Hybrid retrieval using Postgres RPCs + RRF merge on candidate ids only."""
    cache_key = _retrieval_cache_key(
        user_id, prepared.codebook_id, document_id, prepared, top_k, exclude
    )
    cached = redis_get(cache_key)
    if cached:
        try:
            payload = json.loads(cached)
            chunk_ids = payload.get("chunk_ids") or []
            if chunk_ids:
                chunks = _load_chunks_by_ids(chunk_ids)
                meta = payload.get("meta") or {}
                meta["retrieval_cache"] = "hit"
                if trace:
                    trace.add("search_hybrid_cache", {"hit": True, "key": cache_key})
                return chunks, meta
        except Exception:
            pass

    chunk_pool = _estimate_visible_chunk_count(user_id, document_ids, prepared.codebook_id)
    fetch_limit = min(max(candidate_limit, top_k + len(exclude)), max(chunk_pool, 1))

    fts_queries = [prepared.rephrased_query] + prepared.fts_terms
    vector_queries = prepared.vector_queries or [prepared.rephrased_query]
    supabase_fts_query = " | ".join(prepared.fts_terms[:5]) or prepared.rephrased_query

    if trace:
        trace.add(
            "search_input",
            {
                "mode": "hybrid_db",
                "chunk_pool": chunk_pool,
                "document_id": document_id,
                "document_count": len(document_ids),
                "fts_queries": fts_queries,
                "vector_queries": vector_queries,
                "fuzzy_terms": prepared.fuzzy_terms,
                "heading_clauses": prepared.heading_clauses,
                "supabase_fts_query": supabase_fts_query,
                "top_k": top_k,
                "candidate_limit": fetch_limit,
                "excluded_count": len(exclude),
            },
        )

    fts_ids = _signal_fts_rpc(user_id, document_ids, prepared.codebook_id, fts_queries, fetch_limit)
    vector_ids = signal_vector_fn(
        user_id,
        prepared.codebook_id,
        vector_queries,
        fetch_limit,
        document_id=document_id,
    )
    vector_ids = filter_excluded_fn(vector_ids, exclude, fetch_limit)
    vector_source = "pgvector"
    if not vector_ids:
        vector_ids = _signal_trgm_rpc(
            user_id,
            document_ids,
            prepared.codebook_id,
            vector_queries,
            fetch_limit,
        )
        vector_source = "trgm_proxy"

    signal_lists = {
        "fts_bm25": fts_ids,
        "vector": vector_ids,
        "fuzzy": _signal_trgm_rpc(
            user_id, document_ids, prepared.codebook_id, prepared.fuzzy_terms, fetch_limit
        ),
        "heading": _signal_heading_rpc(
            user_id, document_ids, prepared.codebook_id, prepared.heading_clauses, fetch_limit
        ),
        "supabase_fts": filter_excluded_fn(
            signal_supabase_fts_fn(
                user_id,
                prepared.codebook_id,
                supabase_fts_query,
                fetch_limit,
                document_id=document_id,
            ),
            exclude,
            fetch_limit,
        ),
    }

    for name, ids in list(signal_lists.items()):
        signal_lists[name] = filter_excluded_fn(ids, exclude, fetch_limit)

    merged = rrf_merge_fn(list(signal_lists.values()), rag_settings.rag_rrf_k)
    rrf_scores = {cid: score for cid, score in merged}

    candidate_ids: List[str] = []
    seen: Set[str] = set()
    for cid, _ in merged:
        if cid not in seen:
            candidate_ids.append(cid)
            seen.add(cid)
    for ids in signal_lists.values():
        for cid in ids:
            if cid not in seen:
                candidate_ids.append(cid)
                seen.add(cid)

    chunks = _load_chunks_by_ids(candidate_ids)
    by_id = {_chunk_key(c): c for c in chunks}
    top_ids = [cid for cid, _ in merged if cid in by_id][:top_k]

    if len(top_ids) < top_k:
        fill_seen = set(top_ids)
        for cid in signal_lists["fts_bm25"] + signal_lists["vector"]:
            if cid not in fill_seen and cid in by_id:
                top_ids.append(cid)
                fill_seen.add(cid)
            if len(top_ids) >= top_k:
                break

    results = [by_id[cid] for cid in top_ids if cid in by_id]

    signal_rankings = {
        name: summarize_signal_fn(ids, by_id, name, limit=10)
        for name, ids in signal_lists.items()
    }
    rrf_ranking = [
        chunk_summary(by_id[cid], rank=i + 1, score=rrf_scores.get(cid), signal="rrf")
        for i, cid in enumerate(top_ids)
        if cid in by_id
    ]
    selected = [chunk_summary(row, rank=i + 1, signal="selected") for i, row in enumerate(results)]

    meta = {
        "mode": "hybrid_db",
        "chunk_pool": chunk_pool,
        "search_pool": len(by_id),
        "excluded_count": len(exclude),
        "signals": {name: len(ids) for name, ids in signal_lists.items()},
        "vector_source": vector_source,
        "top_k": top_k,
        "returned": len(results),
        "signal_rankings": signal_rankings,
        "rrf_ranking": rrf_ranking,
        "selected": selected,
        "document_id": document_id,
        "retrieval_cache": "miss",
    }

    if document_id and results:
        try:
            redis_setex(
                cache_key,
                _RETRIEVAL_CACHE_TTL,
                json.dumps({"chunk_ids": top_ids, "meta": {k: v for k, v in meta.items() if k != "signal_rankings"}}),
            )
        except Exception:
            pass

    if trace:
        trace.add(
            "search_signals",
            {
                "mode": "hybrid_db",
                "vector_source": vector_source,
                "hit_counts": meta["signals"],
                "signal_rankings": signal_rankings,
            },
        )
        trace.add(
            "search_rrf",
            {
                "rrf_k": rag_settings.rag_rrf_k,
                "merged_candidates": len(merged),
                "rrf_ranking": rrf_ranking,
            },
        )
        trace.add("search_selected", {"count": len(selected), "chunks": selected})

    return results, meta
