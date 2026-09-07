"""Full ask pipeline with optional retry after insufficient_evidence."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from services.chunk_retrieval import (
    clause_chunk_ids,
    clause_hint_lines,
    retrieve_clauses,
)
from services.rag_answer_service import answer_from_clauses_parcels
from services.rag_query_service import PreparedQuery, prepare_rag_query, prepare_rag_query_retry
from services.rag_settings import rag_settings
from services.rag_trace import RagPipelineTrace
from services.table_retrieval import (
    resolve_table_layout,
    retrieve_tables_for_retry,
    table_catalog_for_prepare,
)

_CONCLUSION_RANK = {
    "permitted": 4,
    "not_permitted": 4,
    "depends": 3,
    "insufficient_evidence": 1,
}
_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def collect_answer_gaps(answer: Dict[str, Any]) -> List[str]:
    gaps: List[str] = []
    for parcel in answer.get("parcels") or []:
        if not isinstance(parcel, dict):
            continue
        gap = (parcel.get("result") or {}).get("gaps") or ""
        if isinstance(gap, str) and gap.strip():
            gaps.append(gap.strip())
    return gaps


def _answer_score(answer: Dict[str, Any]) -> Tuple[int, int, int]:
    conclusion = (answer.get("conclusion") or "").strip().lower()
    confidence = (answer.get("confidence") or "low").strip().lower()
    cited = len(answer.get("cited_clauses") or [])
    return (
        _CONCLUSION_RANK.get(conclusion, 0),
        _CONFIDENCE_RANK.get(confidence, 0),
        cited,
    )


def pick_better_answer(first: Dict[str, Any], second: Dict[str, Any]) -> int:
    """Return 1 or 2 — whichever answer scores higher."""
    if _answer_score(second) > _answer_score(first):
        return 2
    return 1


def should_retry_for_insufficient(answer: Dict[str, Any]) -> bool:
    if not rag_settings.rag_retry_on_insufficient:
        return False
    return (answer.get("conclusion") or "").strip().lower() == "insufficient_evidence"


def retrieval_is_strong(retrieval: Dict[str, Any], *, top_k: int) -> bool:
    """True when first-pass retrieval already returned a full, high-confidence RRF set."""
    returned = int(retrieval.get("returned") or 0)
    min_returned = max(1, int(top_k * rag_settings.rag_retry_min_returned_ratio))
    if returned < min_returned:
        return False

    rrf_ranking = retrieval.get("rrf_ranking") or []
    if not rrf_ranking or not isinstance(rrf_ranking[0], dict):
        return False

    top_score = rrf_ranking[0].get("score")
    if top_score is None:
        return False
    return float(top_score) >= rag_settings.rag_retry_min_top_rrf_score


def should_skip_retry_for_strong_retrieval(retrieval: Dict[str, Any], *, top_k: int) -> bool:
    if not rag_settings.rag_retry_skip_when_retrieval_strong:
        return False
    return retrieval_is_strong(retrieval, top_k=top_k)


def merge_retry_evidence(
    first_clauses: List[Dict[str, Any]],
    new_clauses: List[Dict[str, Any]],
    new_tables: List[Dict[str, Any]],
    *,
    top_k: int,
) -> List[Dict[str, Any]]:
    """Retry merge: new tables first, then new clauses, then attempt-1 context."""
    seen: set[str] = set()
    merged: List[Dict[str, Any]] = []
    for group in (new_tables, new_clauses, first_clauses):
        for row in group:
            key = str(row.get("id") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(row)
            if len(merged) >= top_k:
                return merged
    return merged


def run_ask_with_retry(
    user_id: str,
    question: str,
    codebook_id: str,
    *,
    codebook_custom: Optional[str] = None,
    codebook_label: Optional[str] = None,
    document_id: Optional[str] = None,
    top_k: Optional[int] = None,
    skip_answer: bool = False,
    trace: Optional[RagPipelineTrace] = None,
) -> Dict[str, Any]:
    k = top_k or rag_settings.rag_top_k

    prepared = prepare_rag_query(
        question,
        codebook_id,
        codebook_custom=codebook_custom,
        codebook_label=codebook_label,
        trace=trace,
    )
    clauses, retrieval = retrieve_clauses(
        user_id,
        prepared,
        top_k=k,
        trace=trace,
        document_id=document_id,
    )

    answer: Optional[Dict[str, Any]] = None
    retry_info: Dict[str, Any] = {"attempted": False, "selected_attempt": 1}

    if not skip_answer:
        answer = answer_from_clauses_parcels(prepared, clauses, trace=trace)

        if should_retry_for_insufficient(answer):
            if should_skip_retry_for_strong_retrieval(retrieval, top_k=k):
                retry_info["attempted"] = False
                retry_info["skipped"] = True
                retry_info["skip_reason"] = "strong_retrieval"
                retry_info["retrieval_returned"] = retrieval.get("returned")
                top_rrf = (retrieval.get("rrf_ranking") or [{}])[0]
                retry_info["top_rrf_score"] = top_rrf.get("score") if isinstance(top_rrf, dict) else None
                if trace:
                    trace.add(
                        "retry_skipped",
                        {
                            "reason": "strong_retrieval",
                            "retrieval_returned": retrieval.get("returned"),
                            "top_rrf_score": retry_info["top_rrf_score"],
                            "first_conclusion": answer.get("conclusion"),
                        },
                    )
            else:
                retry_info["attempted"] = True
                retry_info["first_conclusion"] = answer.get("conclusion")
                retry_info["first_confidence"] = answer.get("confidence")

                if trace:
                    trace.add(
                        "retry_trigger",
                        {
                            "reason": "insufficient_evidence",
                            "insufficient_reason": answer.get("insufficient_reason") or "",
                            "notes": answer.get("notes") or "",
                            "gaps": collect_answer_gaps(answer),
                        },
                    )

                table_layout = resolve_table_layout(codebook_id)
                table_catalog = table_catalog_for_prepare(
                    user_id,
                    codebook_id,
                    document_id,
                )

                prepared_retry = prepare_rag_query_retry(
                    question,
                    codebook_id,
                    first_prepared=prepared,
                    insufficient_reason=answer.get("insufficient_reason") or "",
                    what_would_improve=answer.get("notes") or "",
                    gaps=collect_answer_gaps(answer),
                    first_clause_summaries=clause_hint_lines(clauses),
                    table_catalog=table_catalog,
                    table_layout=table_layout,
                    codebook_custom=codebook_custom,
                    codebook_label=codebook_label,
                    trace=trace,
                )
                first_attempt_ids = clause_chunk_ids(clauses)
                tables_retry, table_meta = retrieve_tables_for_retry(
                    user_id,
                    prepared_retry,
                    document_id=document_id,
                    exclude_chunk_ids=first_attempt_ids,
                    trace=trace,
                )
                clauses_retry, retrieval_retry = retrieve_clauses(
                    user_id,
                    prepared_retry,
                    top_k=rag_settings.rag_retry_top_k,
                    trace=trace,
                    document_id=document_id,
                    exclude_chunk_ids=first_attempt_ids,
                )
                new_retry_ids = clause_chunk_ids(clauses_retry)
                new_table_ids = clause_chunk_ids(tables_retry)
                merged_clauses = merge_retry_evidence(
                    clauses,
                    clauses_retry,
                    tables_retry,
                    top_k=rag_settings.rag_retry_merged_top_k,
                )
                answer_retry = answer_from_clauses_parcels(
                    prepared_retry,
                    merged_clauses,
                    trace=trace,
                )

                retry_info["second_conclusion"] = answer_retry.get("conclusion")
                retry_info["second_confidence"] = answer_retry.get("confidence")
                retry_info["first_clause_count"] = len(clauses)
                retry_info["second_clause_count"] = len(clauses_retry)
                retry_info["second_table_count"] = len(tables_retry)
                retry_info["second_new_clause_count"] = len(new_retry_ids)
                retry_info["second_new_table_count"] = len(new_table_ids)
                retry_info["table_layout"] = table_layout
                retry_info["excluded_first_attempt_ids"] = len(first_attempt_ids)
                retry_info["merged_clause_count"] = len(merged_clauses)
                retry_info["second_rephrased_query"] = prepared_retry.rephrased_query

                selected = pick_better_answer(answer, answer_retry)
                retry_info["selected_attempt"] = selected

                if trace:
                    trace.add(
                        "retry_result",
                        {
                            "selected_attempt": selected,
                            "first_conclusion": answer.get("conclusion"),
                            "second_conclusion": answer_retry.get("conclusion"),
                            "excluded_first_attempt_ids": len(first_attempt_ids),
                            "second_new_clause_count": len(new_retry_ids),
                            "second_new_table_count": len(new_table_ids),
                            "second_table_count": len(tables_retry),
                            "table_layout": table_layout,
                            "merged_clause_count": len(merged_clauses),
                        },
                    )

                if selected == 2:
                    prepared = prepared_retry
                    clauses = merged_clauses
                    retrieval = {
                        **retrieval,
                        "retry": retrieval_retry,
                        "retry_tables": table_meta,
                        "merged_from_attempts": [
                            len(tables_retry),
                            len(clauses_retry),
                            retry_info["first_clause_count"],
                        ],
                    }
                    answer = answer_retry
                    retry_info["used"] = True
                else:
                    retry_info["used"] = False
    elif trace:
        trace.add("answer_skipped", {"reason": "skip_answer=true"})

    return {
        "prepared": prepared,
        "clauses": clauses,
        "retrieval": retrieval,
        "answer": answer,
        "retry": retry_info,
    }
