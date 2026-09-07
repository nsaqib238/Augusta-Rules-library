"""Step 3: Answer synthesis from retrieved clauses in parcels (context window safe)."""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from services.rag_llm import chat_json, render_prompt
from services.rag_formatter import format_structured_answer
from services.rag_query_service import PreparedQuery
from services.rag_settings import rag_settings
from services.rag_trace import RagPipelineTrace, chunk_summary
from services.table_retrieval import resolve_table_layout

logger = logging.getLogger(__name__)


def _format_clause_block(row: Dict[str, Any], index: int) -> str:
    kind = (row.get("evidence_kind") or "clause").strip()
    clause = row.get("clause_number") or "—"
    heading = row.get("heading") or ""
    page = row.get("page_number")
    text = row.get("text") or ""
    if kind == "standard_table":
        header = f"### Evidence {index + 1} — {clause} [standard_table]"
    elif kind == "embedded_table":
        header = f"### Evidence {index + 1} — {clause} [embedded_table]"
    else:
        header = f"### Evidence {index + 1} — Clause {clause}"
    if heading:
        header += f" — {heading}"
    source_clause = (row.get("source_clause_number") or "").strip()
    if source_clause and source_clause != clause:
        header += f" (referenced in clause {source_clause})"
    if page is not None:
        header += f" (page {page})"
    return f"{header}\n\n{text}"


def _parcel_chunks(clauses: List[Dict[str, Any]], parcel_size: int) -> List[List[Dict[str, Any]]]:
    if not clauses:
        return []
    size = max(1, parcel_size)
    return [clauses[i : i + size] for i in range(0, len(clauses), size)]


def _synthesize_parcel(
    *,
    parcel_idx: int,
    parcel: List[Dict[str, Any]],
    parcel_total: int,
    prepared: PreparedQuery,
    system_parcel: str,
) -> Tuple[int, Dict[str, Any], List[Dict[str, Any]]]:
    evidence = "\n\n---\n\n".join(
        _format_clause_block(row, i) for i, row in enumerate(parcel)
    )
    parcel_clause_summaries = [
        chunk_summary(row, rank=i + 1, signal=f"parcel_{parcel_idx + 1}")
        for i, row in enumerate(parcel)
    ]
    user_content = json.dumps(
        {
            "question": prepared.original_question,
            "rephrased_query": prepared.rephrased_query,
            "parcel_index": parcel_idx + 1,
            "parcel_total": parcel_total,
            "evidence": evidence,
        },
        ensure_ascii=False,
    )
    try:
        result = chat_json(
            system_prompt=system_parcel,
            user_content=user_content,
            model=rag_settings.rag_synthesis_model,
        )
    except Exception as exc:
        logger.error("Parcel %s LLM failed: %s", parcel_idx + 1, exc)
        result = {
            "answer_excerpt": "",
            "cited_clauses": [],
            "confidence": "low",
            "gaps": str(exc),
        }
    entry = {
        "parcel_index": parcel_idx + 1,
        "clause_count": len(parcel),
        "clause_ids": [str(r.get("id")) for r in parcel],
        "clauses": parcel_clause_summaries,
        "evidence_chars": len(evidence),
        "result": result,
    }
    return parcel_idx, entry, parcel_clause_summaries


def _run_parcels_parallel(
    *,
    parcels: List[List[Dict[str, Any]]],
    prepared: PreparedQuery,
    system_parcel: str,
    trace: Optional[RagPipelineTrace],
) -> List[Dict[str, Any]]:
    max_workers = min(len(parcels), max(1, rag_settings.rag_max_parallel_parcels))
    ordered: List[Optional[Dict[str, Any]]] = [None] * len(parcels)

    if trace:
        trace.add(
            "answer_parcels_parallel",
            {
                "parcel_count": len(parcels),
                "max_parallel": max_workers,
            },
        )

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="rag-parcel") as pool:
        futures = [
            pool.submit(
                _synthesize_parcel,
                parcel_idx=parcel_idx,
                parcel=parcel,
                parcel_total=len(parcels),
                prepared=prepared,
                system_parcel=system_parcel,
            )
            for parcel_idx, parcel in enumerate(parcels)
        ]
        for future in as_completed(futures):
            parcel_idx, entry, parcel_clause_summaries = future.result()
            ordered[parcel_idx] = entry
            if trace:
                trace.add(
                    "answer_parcel_send",
                    {
                        "parcel_index": parcel_idx + 1,
                        "parcel_total": len(parcels),
                        "evidence_chars": entry["evidence_chars"],
                        "clauses": parcel_clause_summaries,
                    },
                )
                result = entry["result"]
                trace.add(
                    "answer_parcel_result",
                    {
                        "parcel_index": parcel_idx + 1,
                        "answer_excerpt": (result.get("answer_excerpt") or "")[:500],
                        "cited_clauses": result.get("cited_clauses") or [],
                        "confidence": result.get("confidence"),
                        "gaps": result.get("gaps") or "",
                    },
                )

    return [entry for entry in ordered if entry is not None]


def _run_parcels_sequential(
    *,
    parcels: List[List[Dict[str, Any]]],
    prepared: PreparedQuery,
    system_parcel: str,
    trace: Optional[RagPipelineTrace],
) -> List[Dict[str, Any]]:
    parcel_results: List[Dict[str, Any]] = []
    for parcel_idx, parcel in enumerate(parcels):
        if trace:
            evidence = "\n\n---\n\n".join(
                _format_clause_block(row, i) for i, row in enumerate(parcel)
            )
            parcel_clause_summaries = [
                chunk_summary(row, rank=i + 1, signal=f"parcel_{parcel_idx + 1}")
                for i, row in enumerate(parcel)
            ]
            trace.add(
                "answer_parcel_send",
                {
                    "parcel_index": parcel_idx + 1,
                    "parcel_total": len(parcels),
                    "evidence_chars": len(evidence),
                    "clauses": parcel_clause_summaries,
                },
            )
        _, entry, _ = _synthesize_parcel(
            parcel_idx=parcel_idx,
            parcel=parcel,
            parcel_total=len(parcels),
            prepared=prepared,
            system_parcel=system_parcel,
        )
        parcel_results.append(entry)
        if trace:
            result = entry["result"]
            trace.add(
                "answer_parcel_result",
                {
                    "parcel_index": parcel_idx + 1,
                    "answer_excerpt": (result.get("answer_excerpt") or "")[:500],
                    "cited_clauses": result.get("cited_clauses") or [],
                    "confidence": result.get("confidence"),
                    "gaps": result.get("gaps") or "",
                },
            )
    return parcel_results


def answer_from_clauses_parcels(
    prepared: PreparedQuery,
    clauses: List[Dict[str, Any]],
    *,
    parcel_size: Optional[int] = None,
    trace: Optional[RagPipelineTrace] = None,
) -> Dict[str, Any]:
    """
    Process top clauses in parcels so each LLM call stays within context limits.
    Returns merged markdown answer + parcel audit trail.
    """
    if not clauses:
        return {
            "answer_markdown": (
                f"No clauses were retrieved for **{prepared.codebook_label}**. "
                "Upload and process a PDF for this codebook first."
            ),
            "cited_clauses": [],
            "confidence": "low",
            "parcels": [],
        }

    size = parcel_size or rag_settings.rag_parcel_size
    parcels = _parcel_chunks(clauses, size)

    if trace:
        trace.add(
            "answer_input",
            {
                "parcel_size": size,
                "parcel_count": len(parcels),
                "total_clauses": len(clauses),
                "max_parallel_parcels": rag_settings.rag_max_parallel_parcels,
                "clauses": [chunk_summary(c, rank=i + 1, signal="to_llm") for i, c in enumerate(clauses)],
            },
        )

    system_parcel = render_prompt(
        "rag_answer_parcel.txt",
        codebook_label=prepared.codebook_label,
    )

    if len(parcels) > 1 and rag_settings.rag_max_parallel_parcels > 1:
        parcel_results = _run_parcels_parallel(
            parcels=parcels,
            prepared=prepared,
            system_parcel=system_parcel,
            trace=trace,
        )
    else:
        parcel_results = _run_parcels_sequential(
            parcels=parcels,
            prepared=prepared,
            system_parcel=system_parcel,
            trace=trace,
        )

    merge_system = render_prompt(
        "rag_answer_merge.txt",
        codebook_label=prepared.codebook_label,
        table_layout=resolve_table_layout(prepared.codebook_id),
    )
    merge_payload = json.dumps(
        {
            "question": prepared.original_question,
            "codebook": prepared.codebook_label,
            "parcel_excerpts": [
                {
                    "parcel": p["parcel_index"],
                    "excerpt": p["result"].get("answer_excerpt", ""),
                    "cited_clauses": p["result"].get("cited_clauses", []),
                    "confidence": p["result"].get("confidence", "low"),
                    "gaps": p["result"].get("gaps", ""),
                }
                for p in parcel_results
            ],
        },
        ensure_ascii=False,
    )
    merged = chat_json(
        system_prompt=merge_system,
        user_content=merge_payload,
        model=rag_settings.rag_synthesis_model,
    )

    all_cited: List[str] = []
    for p in parcel_results:
        all_cited.extend(p["result"].get("cited_clauses") or [])

    if merged.get("answer_markdown") and not merged.get("conclusion") and not merged.get("short_answer"):
        answer_markdown = merged.get("answer_markdown") or ""
    else:
        if not merged.get("citations") and all_cited:
            merged["cited_clauses"] = all_cited
        answer_markdown = format_structured_answer(merged, clauses)

    merged_cited = merged.get("cited_clauses") or all_cited
    for c in merged.get("citations") or []:
        if isinstance(c, dict) and c.get("clause_id"):
            merged_cited.append(str(c["clause_id"]))
    merged_cited = list(dict.fromkeys(str(c) for c in merged_cited if c))

    if trace:
        trace.add(
            "answer_merge",
            {
                "confidence": merged.get("confidence"),
                "conclusion": merged.get("conclusion"),
                "cited_clauses": merged_cited,
                "answer_preview": answer_markdown[:600],
                "notes": merged.get("notes") or merged.get("what_would_improve_answer") or "",
            },
        )

    return {
        "answer_markdown": answer_markdown,
        "cited_clauses": merged_cited,
        "confidence": merged.get("confidence") or "medium",
        "conclusion": merged.get("conclusion") or "depends",
        "insufficient_reason": (merged.get("insufficient_reason") or "").strip(),
        "notes": merged.get("notes") or merged.get("what_would_improve_answer") or "",
        "required_tables": merged.get("required_tables") or [],
        "missing_clause_refs": merged.get("missing_clause_refs") or [],
        "structured": {
            k: merged.get(k)
            for k in (
                "conclusion",
                "short_answer",
                "conditions",
                "exceptions_or_edge_cases",
                "required_actions",
                "unknowns_to_confirm",
                "citations",
                "required_tables",
                "missing_clause_refs",
            )
            if merged.get(k)
        },
        "parcels": parcel_results,
    }
