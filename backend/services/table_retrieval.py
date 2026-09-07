"""Table-focused retrieval for retry passes (NCC/SIR standard_tables + AS/NZS table rows in chunks)."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

from rank_bm25 import BM25Okapi
from rapidfuzz import fuzz

from services.chunk_retrieval import (
    _document_ids_for_search,
    _filter_excluded_ids,
    _rrf_merge,
    _tokenize,
)
from services.rag_query_service import PreparedQuery
from services.rag_settings import rag_settings
from services.rag_trace import RagPipelineTrace, chunk_summary
from services.shared_library_service import get_edition, is_shared_library_codebook
from services.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

TABLE_LABEL_RE = re.compile(r"\btable\s+[\d\.]+[a-z]?", re.IGNORECASE)


def resolve_table_layout(codebook_id: str) -> str:
    """
    Where table content lives for this codebook:
    - standard_tables: NCC / SIR shared library (separate tables CSV)
    - embedded_chunks: AS/NZS-style tables ingested as clause rows
    - both: search both pools when present
    """
    if is_shared_library_codebook(codebook_id):
        meta = get_edition(codebook_id) or {}
        if meta.get("family") in ("NCC", "SIR"):
            return "standard_tables"
    return "embedded_chunks"


def is_table_like_chunk(row: Dict[str, Any]) -> bool:
    clause = (row.get("clause_number") or "").strip()
    heading = (row.get("heading") or "").strip()
    combined = f"{clause} {heading}".lower()
    if "table" in combined:
        return True
    if TABLE_LABEL_RE.search(clause) or TABLE_LABEL_RE.search(heading):
        return True
    meta = row.get("metadata")
    if isinstance(meta, dict) and meta.get("is_table"):
        return True
    return False


def _table_search_text(row: Dict[str, Any]) -> str:
    parts = [
        row.get("table_number") or "",
        row.get("clause_number") or "",
        row.get("heading") or "",
        row.get("title") or "",
        row.get("source_clause_number") or "",
        row.get("text") or "",
    ]
    return " ".join(p for p in parts if p)


def _standard_table_as_clause(row: Dict[str, Any], codebook_id: str) -> Dict[str, Any]:
    table_number = (row.get("table_number") or "").strip()
    title = (row.get("title") or "").strip()
    source_clause = (row.get("source_clause_number") or "").strip()
    heading = title or "Table"
    if table_number and table_number.lower() not in heading.lower():
        heading = f"{table_number} — {heading}" if title else table_number
    return {
        "id": row.get("id"),
        "document_id": row.get("document_id"),
        "chunk_index": None,
        "clause_number": table_number or source_clause or "Table",
        "heading": heading,
        "page_number": None,
        "codebook": codebook_id,
        "text": row.get("text") or "",
        "evidence_kind": "standard_table",
        "table_number": table_number,
        "source_clause_number": source_clause,
    }


def _embedded_table_as_clause(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    out["evidence_kind"] = "embedded_table"
    out["table_number"] = (row.get("clause_number") or row.get("heading") or "").strip()
    out["source_clause_number"] = (row.get("clause_number") or "").strip()
    return out


def _load_standard_tables(
    user_id: str,
    codebook_id: str,
    document_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    doc_ids = _document_ids_for_search(user_id, codebook_id, document_id)
    if not doc_ids:
        return []
    supabase = get_supabase_client()
    rows: List[Dict[str, Any]] = []
    page_size = 500
    offset = 0
    while True:
        batch = (
            supabase.table("standard_tables")
            .select(
                "id, document_id, table_id, table_number, title, "
                "source_clause_number, text, standard_name, metadata"
            )
            .in_("document_id", doc_ids)
            .order("table_number")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        data = batch.data or []
        rows.extend(data)
        if len(data) < page_size:
            break
        offset += page_size
    return rows


def _load_table_like_chunks(
    user_id: str,
    codebook_id: str,
    document_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    from services.chunk_retrieval import _load_chunks_for_codebook

    chunks = _load_chunks_for_codebook(user_id, codebook_id, document_id)
    return [c for c in chunks if is_table_like_chunk(c)]


def _table_catalog_lines(tables: List[Dict[str, Any]], *, limit: int = 40) -> List[str]:
    lines: List[str] = []
    for row in tables[:limit]:
        num = (row.get("table_number") or row.get("clause_number") or "").strip()
        title = (row.get("title") or row.get("heading") or "").strip()
        src = (row.get("source_clause_number") or "").strip()
        if num and title:
            line = f"{num} — {title}"
        elif num:
            line = num
        elif title:
            line = title
        else:
            continue
        if src:
            line += f" (clause {src})"
        lines.append(line)
    return lines


def _signal_table_bm25(rows: List[Dict[str, Any]], queries: List[str], limit: int) -> List[str]:
    if not rows or not queries:
        return []
    corpus = [_tokenize(_table_search_text(r)) for r in rows]
    bm25 = BM25Okapi(corpus)
    combined_scores = [0.0] * len(rows)
    for q in queries:
        if not q.strip():
            continue
        scores = bm25.get_scores(_tokenize(q))
        for i, s in enumerate(scores):
            combined_scores[i] += float(s)
    ranked = sorted(range(len(rows)), key=lambda i: combined_scores[i], reverse=True)
    return [str(rows[i].get("id") or "") for i in ranked[:limit] if combined_scores[i] > 0]


def _signal_table_heading(rows: List[Dict[str, Any]], needles: List[str], limit: int) -> List[str]:
    if not rows or not needles:
        return []
    terms = [n.strip().lower() for n in needles if n and n.strip()]
    hits: List[tuple[str, int]] = []
    for row in rows:
        table_num = (row.get("table_number") or row.get("clause_number") or "").strip().lower()
        title = (row.get("title") or row.get("heading") or "").strip().lower()
        text_head = (row.get("text") or "").lower()[:300]
        score = 0
        for n in terms:
            if table_num and (table_num == n or n in table_num or table_num in n):
                score += 120
            elif title and n in title:
                score += 90
            elif "table" in n and table_num:
                score += 70
            elif n in text_head:
                score += 25
        if score > 0:
            hits.append((str(row.get("id") or ""), score))
    hits.sort(key=lambda x: x[1], reverse=True)
    return [cid for cid, _ in hits[:limit] if cid]


def _signal_table_fuzzy(rows: List[Dict[str, Any]], terms: List[str], limit: int) -> List[str]:
    if not rows or not terms:
        return []
    scored: List[tuple[str, float]] = []
    for row in rows:
        text = _table_search_text(row).lower()
        best = max(
            (fuzz.partial_ratio(t.lower(), text) for t in terms if t.strip()),
            default=0,
        )
        if best > 35:
            scored.append((str(row.get("id") or ""), best))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [cid for cid, _ in scored[:limit] if cid]


def retrieve_tables_for_retry(
    user_id: str,
    prepared: PreparedQuery,
    *,
    document_id: Optional[str] = None,
    exclude_chunk_ids: Optional[Set[str]] = None,
    top_k: Optional[int] = None,
    trace: Optional[RagPipelineTrace] = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Retry-only table search across standard_tables and/or embedded table chunks.
    Returns clause-shaped evidence rows for the answer synthesizer.
    """
    k = top_k or rag_settings.rag_retry_table_top_k
    exclude = {str(x) for x in (exclude_chunk_ids or set()) if str(x)}
    layout = resolve_table_layout(prepared.codebook_id)

    standard_rows = _load_standard_tables(user_id, prepared.codebook_id, document_id)
    embedded_rows = _load_table_like_chunks(user_id, prepared.codebook_id, document_id)

    pool: List[Dict[str, Any]] = list(standard_rows)
    if layout != "standard_tables" or not standard_rows:
        pool.extend(embedded_rows)
    elif embedded_rows:
        pool.extend(embedded_rows)

    if exclude:
        pool = [r for r in pool if str(r.get("id") or "") not in exclude]

    if not pool:
        empty = {
            "table_layout": layout,
            "standard_table_count": len(standard_rows),
            "embedded_table_count": len(embedded_rows),
            "returned": 0,
            "message": "No table rows available for retry search",
        }
        if trace:
            trace.add("table_search_empty", empty)
        return [], empty

    table_queries = (
        [prepared.rephrased_query]
        + list(prepared.heading_clauses or [])
        + list(prepared.fts_terms or [])
    )
    heading_needles = list(prepared.heading_clauses or []) + [
        t for t in (prepared.fts_terms or []) if "table" in t.lower()
    ]
    fuzzy_terms = list(prepared.fuzzy_terms or []) + [
        t for t in (prepared.heading_clauses or []) if t.strip()
    ]

    fetch_limit = min(max(k * 3, 20), len(pool))
    by_id = {str(r.get("id") or ""): r for r in pool if r.get("id")}

    signal_lists = [
        _signal_table_bm25(pool, table_queries, fetch_limit),
        _signal_table_heading(pool, heading_needles, fetch_limit),
        _signal_table_fuzzy(pool, fuzzy_terms, fetch_limit),
    ]
    merged = _rrf_merge(signal_lists, rag_settings.rag_rrf_k)
    top_ids = _filter_excluded_ids([cid for cid, _ in merged], exclude, k)

    results: List[Dict[str, Any]] = []
    for cid in top_ids:
        row = by_id.get(cid)
        if not row:
            continue
        if "table_number" in row and row.get("table_number") is not None:
            results.append(_standard_table_as_clause(row, prepared.codebook_id))
        else:
            results.append(_embedded_table_as_clause(row))

    meta = {
        "table_layout": layout,
        "standard_table_count": len(standard_rows),
        "embedded_table_count": len(embedded_rows),
        "search_pool": len(pool),
        "excluded_count": len(exclude),
        "returned": len(results),
        "table_queries": table_queries[:8],
        "selected": [chunk_summary(r, rank=i + 1, signal="table_retry") for i, r in enumerate(results)],
    }

    if trace:
        trace.add("table_search", meta)

    return results, meta


def table_catalog_for_prepare(
    user_id: str,
    codebook_id: str,
    document_id: Optional[str] = None,
    *,
    limit: int = 40,
) -> List[str]:
    """Short table index for retry prepare (numbers + titles only)."""
    standard = _load_standard_tables(user_id, codebook_id, document_id)
    if standard:
        return _table_catalog_lines(standard, limit=limit)
    embedded = _load_table_like_chunks(user_id, codebook_id, document_id)
    return _table_catalog_lines(embedded, limit=limit)
