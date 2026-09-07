"""Multi-signal chunk retrieval: FTS, vector-proxy, fuzzy, heading → RRF merge."""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from rank_bm25 import BM25Okapi
from rapidfuzz import fuzz

from services.rag_query_service import PreparedQuery
from services.rag_settings import rag_settings
from services.rag_trace import RagPipelineTrace, chunk_summary
from services.shared_library_service import is_shared_library_codebook, shared_library_document_ids
from services.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> List[str]:
    return [t.lower() for t in TOKEN_RE.findall(text or "")]


def _chunk_key(row: Dict[str, Any]) -> str:
    return str(row.get("id") or "")


def _chunk_search_text(row: Dict[str, Any]) -> str:
    parts = [
        row.get("text") or "",
        row.get("heading") or "",
        row.get("clause_number") or "",
    ]
    return " ".join(p for p in parts if p)


def _user_can_search_document(row: Dict[str, Any], user_id: str) -> bool:
    """Owner, shared library, or active company seat on documents.company_id."""
    if bool(row.get("is_shared_library")):
        return True
    if row.get("user_id") == user_id:
        return True
    company_id = row.get("company_id")
    if not company_id:
        return False
    try:
        from services.company_service import get_active_company_id_for_user

        return get_active_company_id_for_user(user_id) == company_id
    except Exception as e:
        logger.warning("company access check failed for user %s: %s", user_id, e)
        return False


def _document_ids_for_search(
    user_id: str,
    codebook_id: str,
    document_id: Optional[str] = None,
) -> List[str]:
    supabase = get_supabase_client()
    if document_id:
        doc = (
            supabase.table("documents")
            .select("id, status, codebook, is_shared_library, user_id, company_id")
            .eq("id", document_id)
            .eq("status", "ready_for_search")
            .execute()
        )
        rows = doc.data or []
        if not rows:
            return []
        row = rows[0]
        if not _user_can_search_document(row, user_id):
            return []
        if row.get("codebook") and row["codebook"] != codebook_id:
            logger.warning(
                "document %s codebook %s != requested %s",
                document_id,
                row.get("codebook"),
                codebook_id,
            )
        return [document_id]

    seen: Set[str] = set()
    doc_ids: List[str] = []

    def _add_rows(rows: Optional[List[Dict[str, Any]]]) -> None:
        for d in rows or []:
            did = d.get("id")
            if did and did not in seen:
                seen.add(did)
                doc_ids.append(did)

    own = (
        supabase.table("documents")
        .select("id")
        .eq("user_id", user_id)
        .eq("codebook", codebook_id)
        .eq("status", "ready_for_search")
        .execute()
    )
    _add_rows(own.data)

    try:
        from services.company_service import get_active_company_id_for_user

        company_id = get_active_company_id_for_user(user_id)
    except Exception as e:
        logger.warning("company id lookup failed for user %s: %s", user_id, e)
        company_id = None

    if company_id:
        company_docs = (
            supabase.table("documents")
            .select("id")
            .eq("company_id", company_id)
            .eq("codebook", codebook_id)
            .eq("status", "ready_for_search")
            .execute()
        )
        _add_rows(company_docs.data)

    if is_shared_library_codebook(codebook_id):
        for shared_id in shared_library_document_ids(codebook_id):
            if shared_id not in seen:
                seen.add(shared_id)
                doc_ids.append(shared_id)

    return doc_ids


def _load_chunks_for_codebook(
    user_id: str,
    codebook_id: str,
    document_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    doc_ids = _document_ids_for_search(user_id, codebook_id, document_id)
    if not doc_ids:
        return []

    supabase = get_supabase_client()
    rows: List[Dict[str, Any]] = []
    page_size = 1000
    offset = 0
    while True:
        batch = (
            supabase.table("chunks")
            .select(
                "id, document_id, chunk_index, text, heading, clause_number, "
                "page_number, codebook, jurisdiction, metadata"
            )
            .in_("document_id", doc_ids)
            .order("chunk_index")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        data = batch.data or []
        rows.extend(data)
        if len(data) < page_size:
            break
        offset += page_size
    return rows


def _rrf_merge(ranked_lists: List[List[str]], k: int) -> List[Tuple[str, float]]:
    scores: Dict[str, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, chunk_id in enumerate(ranked, start=1):
            scores[chunk_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def _signal_fts_bm25(chunks: List[Dict], queries: List[str], limit: int) -> List[str]:
    if not chunks or not queries:
        return []
    corpus = [_tokenize(_chunk_search_text(c)) for c in chunks]
    bm25 = BM25Okapi(corpus)
    combined_scores = [0.0] * len(chunks)
    for q in queries:
        if not q.strip():
            continue
        scores = bm25.get_scores(_tokenize(q))
        for i, s in enumerate(scores):
            combined_scores[i] += float(s)
    ranked = sorted(range(len(chunks)), key=lambda i: combined_scores[i], reverse=True)
    return [_chunk_key(chunks[i]) for i in ranked[:limit] if combined_scores[i] > 0]


def _signal_vector(
    user_id: str,
    codebook_id: str,
    queries: List[str],
    limit: int,
    document_id: Optional[str] = None,
) -> List[str]:
    """Semantic search via chunk_embeddings + search_chunks_vector RPC."""
    from services.chunk_embedding_service import embed_query_text, embeddings_enabled

    if not embeddings_enabled() or not queries:
        return []

    supabase = get_supabase_client()
    ranked: List[str] = []
    seen: set[str] = set()

    for q in queries:
        if not q.strip():
            continue
        vec = embed_query_text(q)
        if not vec:
            continue
        try:
            result = supabase.rpc(
                "search_chunks_vector",
                {
                    "p_query_embedding": _vector_literal(vec),
                    "p_user_id": user_id,
                    "p_document_id": document_id,
                    "p_limit": limit,
                    "p_codebook": codebook_id,
                    "p_jurisdiction": None,
                },
            ).execute()
        except Exception as exc:
            logger.warning("search_chunks_vector RPC failed: %s", exc)
            return []
        for row in result.data or []:
            cid = str(row.get("chunk_id") or "")
            if cid and cid not in seen:
                ranked.append(cid)
                seen.add(cid)
        if len(ranked) >= limit:
            break
    return ranked[:limit]


def _vector_literal(values: List[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"


def _signal_vector_proxy(chunks: List[Dict], queries: List[str], limit: int) -> List[str]:
    """Fallback when pgvector / embeddings unavailable."""
    if not chunks or not queries:
        return []
    scored: List[Tuple[str, float]] = []
    for row in chunks:
        text = _chunk_search_text(row)
        best = max(
            (fuzz.token_set_ratio(q, text) for q in queries if q.strip()),
            default=0,
        )
        if best > 15:
            scored.append((_chunk_key(row), best))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [cid for cid, _ in scored[:limit]]


def _signal_fuzzy(chunks: List[Dict], terms: List[str], limit: int) -> List[str]:
    if not chunks or not terms:
        return []
    scored: List[Tuple[str, float]] = []
    for row in chunks:
        text = _chunk_search_text(row).lower()
        best = max(
            (fuzz.partial_ratio(t.lower(), text) for t in terms if t.strip()),
            default=0,
        )
        if best > 40:
            scored.append((_chunk_key(row), best))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [cid for cid, _ in scored[:limit]]


def _signal_heading(chunks: List[Dict], headings: List[str], limit: int) -> List[str]:
    if not chunks:
        return []
    needles = [h.strip().lower() for h in headings if h and h.strip()]
    if not needles:
        return []
    hits: List[Tuple[str, int]] = []
    for row in chunks:
        clause = (row.get("clause_number") or "").strip().lower()
        heading = (row.get("heading") or "").strip().lower()
        score = 0
        for n in needles:
            if clause and (clause == n or clause.startswith(n + ".") or n.startswith(clause)):
                score += 100
            elif heading and n in heading:
                score += 80
            elif n in (row.get("text") or "").lower()[:200]:
                score += 20
        if score > 0:
            hits.append((_chunk_key(row), score))
    hits.sort(key=lambda x: x[1], reverse=True)
    return [cid for cid, _ in hits[:limit]]


def _signal_supabase_fts(
    user_id: str,
    codebook_id: str,
    query: str,
    limit: int,
    document_id: Optional[str] = None,
) -> List[str]:
    """Postgres full-text via Supabase textSearch (exact-term signal)."""
    if not query.strip():
        return []
    try:
        supabase = get_supabase_client()
        doc_ids = _document_ids_for_search(user_id, codebook_id, document_id)
        if not doc_ids:
            return []
        result = (
            supabase.table("chunks")
            .select("id")
            .in_("document_id", doc_ids)
            .textSearch("text", query)
            .limit(limit)
            .execute()
        )
        return [str(r["id"]) for r in (result.data or [])]
    except Exception as exc:
        logger.warning("Supabase FTS signal failed: %s", exc)
        return []


def _summarize_signal_ranking(
    chunk_ids: List[str],
    by_id: Dict[str, Dict[str, Any]],
    signal_name: str,
    *,
    scores: Optional[Dict[str, float]] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rank, cid in enumerate(chunk_ids[:limit], start=1):
        row = by_id.get(cid)
        if not row:
            continue
        out.append(
            chunk_summary(
                row,
                rank=rank,
                score=scores.get(cid) if scores else None,
                signal=signal_name,
            )
        )
    return out


def _filter_excluded_ids(
    chunk_ids: List[str],
    exclude: Set[str],
    limit: int,
) -> List[str]:
    if not exclude:
        return chunk_ids[:limit]
    out: List[str] = []
    for cid in chunk_ids:
        if cid in exclude:
            continue
        out.append(cid)
        if len(out) >= limit:
            break
    return out


def clause_chunk_ids(clauses: List[Dict[str, Any]]) -> Set[str]:
    return {_chunk_key(c) for c in clauses if _chunk_key(c)}


def retrieve_clauses(
    user_id: str,
    prepared: PreparedQuery,
    *,
    top_k: Optional[int] = None,
    trace: Optional[RagPipelineTrace] = None,
    document_id: Optional[str] = None,
    exclude_chunk_ids: Optional[Set[str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Run multi-signal retrieval and return top_k full chunk rows (not truncated).
    exclude_chunk_ids: omit these chunk ids from the search pool (retry passes).
    """
    k = top_k or rag_settings.rag_top_k
    candidate_limit = rag_settings.rag_retrieval_candidate_limit
    exclude = {str(x) for x in (exclude_chunk_ids or set()) if str(x)}

    doc_ids = _document_ids_for_search(user_id, prepared.codebook_id, document_id)
    try:
        from services.search_hybrid import retrieve_hybrid_clauses, should_use_hybrid_search

        if should_use_hybrid_search(user_id, prepared.codebook_id, doc_ids, document_id):
            return retrieve_hybrid_clauses(
                user_id,
                prepared,
                document_ids=doc_ids,
                document_id=document_id,
                top_k=k,
                candidate_limit=candidate_limit,
                exclude=exclude,
                trace=trace,
                signal_vector_fn=_signal_vector,
                signal_supabase_fts_fn=_signal_supabase_fts,
                rrf_merge_fn=_rrf_merge,
                filter_excluded_fn=_filter_excluded_ids,
                summarize_signal_fn=_summarize_signal_ranking,
            )
    except Exception as exc:
        logger.warning("Hybrid search failed; falling back to in-memory retrieval: %s", exc)

    chunks = _load_chunks_for_codebook(user_id, prepared.codebook_id, document_id)
    if not chunks:
        scope = f"document {document_id}" if document_id else f"codebook {prepared.codebook_id}"
        empty_meta = {
            "chunk_pool": 0,
            "signals": {},
            "message": f"No ready chunks found for {scope}",
            "document_id": document_id,
        }
        if trace:
            trace.add(
                "search_empty",
                {
                    "codebook_id": prepared.codebook_id,
                    "document_id": document_id,
                    "reason": empty_meta["message"],
                },
            )
        return [], empty_meta

    search_chunks = [c for c in chunks if _chunk_key(c) not in exclude]
    if exclude and not search_chunks:
        empty_meta = {
            "chunk_pool": len(chunks),
            "search_pool": 0,
            "excluded_count": len(exclude),
            "signals": {},
            "message": "No chunks remain after excluding prior attempt hits",
            "document_id": document_id,
        }
        if trace:
            trace.add(
                "search_empty",
                {
                    "codebook_id": prepared.codebook_id,
                    "document_id": document_id,
                    "excluded_count": len(exclude),
                    "reason": empty_meta["message"],
                },
            )
        return [], empty_meta

    by_id = {_chunk_key(c): c for c in search_chunks}
    vector_fetch_limit = min(
        max(candidate_limit, k + len(exclude)),
        len(search_chunks),
    )
    fts_fetch_limit = min(
        max(candidate_limit, k + len(exclude)),
        len(search_chunks),
    )

    fts_queries = [prepared.rephrased_query] + prepared.fts_terms
    vector_queries = prepared.vector_queries or [prepared.rephrased_query]
    supabase_fts_query = " | ".join(prepared.fts_terms[:5]) or prepared.rephrased_query

    if trace:
        trace.add(
            "search_input",
            {
                "chunk_pool": len(chunks),
                "search_pool": len(search_chunks),
                "excluded_count": len(exclude),
                "document_id": document_id,
                "fts_queries": fts_queries,
                "vector_queries": vector_queries,
                "fuzzy_terms": prepared.fuzzy_terms,
                "heading_clauses": prepared.heading_clauses,
                "supabase_fts_query": supabase_fts_query,
                "top_k": k,
                "candidate_limit": candidate_limit,
            },
        )

    vector_ids = _signal_vector(
        user_id,
        prepared.codebook_id,
        vector_queries,
        vector_fetch_limit,
        document_id=document_id,
    )
    vector_ids = _filter_excluded_ids(vector_ids, exclude, vector_fetch_limit)
    vector_source = "pgvector"
    if not vector_ids:
        vector_ids = _signal_vector_proxy(search_chunks, vector_queries, vector_fetch_limit)
        vector_source = "fuzzy_proxy"

    signal_lists = {
        "fts_bm25": _signal_fts_bm25(search_chunks, fts_queries, fts_fetch_limit),
        "vector": vector_ids,
        "fuzzy": _signal_fuzzy(search_chunks, prepared.fuzzy_terms, fts_fetch_limit),
        "heading": _signal_heading(search_chunks, prepared.heading_clauses, fts_fetch_limit),
        "supabase_fts": _filter_excluded_ids(
            _signal_supabase_fts(
                user_id,
                prepared.codebook_id,
                supabase_fts_query,
                fts_fetch_limit,
                document_id=document_id,
            ),
            exclude,
            fts_fetch_limit,
        ),
    }

    merged = _rrf_merge(list(signal_lists.values()), rag_settings.rag_rrf_k)
    rrf_scores = {cid: score for cid, score in merged}
    top_ids = [cid for cid, _ in merged if cid in by_id][:k]

    # Fill remaining slots if RRF returned fewer than k
    if len(top_ids) < k:
        seen: Set[str] = set(top_ids)
        for cid in signal_lists["fts_bm25"] + signal_lists["vector"]:
            if cid not in seen and cid in by_id:
                top_ids.append(cid)
                seen.add(cid)
            if len(top_ids) >= k:
                break

    results = [by_id[cid] for cid in top_ids if cid in by_id]

    signal_rankings = {
        name: _summarize_signal_ranking(ids, by_id, name, limit=10)
        for name, ids in signal_lists.items()
    }
    rrf_ranking = [
        chunk_summary(by_id[cid], rank=i + 1, score=rrf_scores.get(cid), signal="rrf")
        for i, cid in enumerate(top_ids)
        if cid in by_id
    ]
    selected = [chunk_summary(row, rank=i + 1, signal="selected") for i, row in enumerate(results)]

    meta = {
        "chunk_pool": len(chunks),
        "search_pool": len(search_chunks),
        "excluded_count": len(exclude),
        "signals": {name: len(ids) for name, ids in signal_lists.items()},
        "vector_source": vector_source,
        "top_k": k,
        "returned": len(results),
        "signal_rankings": signal_rankings,
        "rrf_ranking": rrf_ranking,
        "selected": selected,
    }

    if trace:
        trace.add(
            "search_signals",
            {
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
        trace.add(
            "search_selected",
            {
                "count": len(selected),
                "chunks": selected,
            },
        )

    return results, meta


def merge_retrieved_clauses(
    first: List[Dict[str, Any]],
    second: List[Dict[str, Any]],
    *,
    top_k: int,
) -> List[Dict[str, Any]]:
    """Prefer retry hits first, then first-attempt hits; dedupe by chunk id."""
    seen: Set[str] = set()
    merged: List[Dict[str, Any]] = []
    for row in second + first:
        key = _chunk_key(row)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(row)
        if len(merged) >= top_k:
            break
    return merged


def clause_hint_lines(clauses: List[Dict[str, Any]], limit: int = 12) -> List[str]:
    """Short labels from retrieved clauses for retry prepare context."""
    hints: List[str] = []
    for row in clauses[:limit]:
        clause = (row.get("clause_number") or "").strip()
        heading = (row.get("heading") or "").strip()
        if clause and heading:
            hints.append(f"{clause} — {heading}")
        elif clause:
            hints.append(clause)
        elif heading:
            hints.append(heading)
    return hints
