"""Step 1: OpenAI query preparation for multi-signal RAG retrieval."""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from pydantic import BaseModel, Field

from services.codebooks import _CODEBOOK_BY_ID, resolve_upload_codebook
from services.rag_llm import chat_json, render_prompt
from services.rag_trace import RagPipelineTrace

logger = logging.getLogger(__name__)


class PreparedQuery(BaseModel):
    codebook_id: str
    codebook_label: str
    original_question: str
    rephrased_query: str
    fts_terms: List[str] = Field(default_factory=list)
    vector_queries: List[str] = Field(default_factory=list)
    fuzzy_terms: List[str] = Field(default_factory=list)
    heading_clauses: List[str] = Field(default_factory=list)
    clause_topics: List[str] = Field(default_factory=list)
    rationale: str = ""
    attempt: int = 1


def _resolve_codebook(
    codebook_id: str,
    *,
    codebook_custom: Optional[str] = None,
    codebook_label: Optional[str] = None,
) -> tuple[str, str]:
    try:
        return resolve_upload_codebook(
            codebook_id,
            codebook_custom=codebook_custom,
            codebook_label=codebook_label,
        )
    except ValueError:
        raise
    except Exception:
        cid = (codebook_id or "AS3000").strip().upper()
        known = _CODEBOOK_BY_ID.get(cid)
        label = codebook_label or (known["label"] if known else cid)
        return cid, label


def prepare_rag_query(
    question: str,
    codebook_id: str,
    *,
    codebook_custom: Optional[str] = None,
    codebook_label: Optional[str] = None,
    trace: Optional[RagPipelineTrace] = None,
) -> PreparedQuery:
    """Send user question + codebook to OpenAI; return structured search signals."""
    q = (question or "").strip()
    if not q:
        raise ValueError("Question is required")

    cid, label = _resolve_codebook(
        codebook_id,
        codebook_custom=codebook_custom,
        codebook_label=codebook_label,
    )

    if trace:
        trace.codebook_label = label
        trace.add(
            "prepare_input",
            {
                "original_question": q,
                "codebook_id": cid,
                "codebook_label": label,
            },
        )

    system = render_prompt(
        "rag_query_prepare.txt",
        codebook_id=cid,
        codebook_label=label,
    )
    user_payload = json.dumps(
        {"question": q, "codebook_id": cid, "codebook_label": label},
        ensure_ascii=False,
    )
    data = chat_json(system_prompt=system, user_content=user_payload)

    rephrased = (data.get("rephrased_query") or q).strip()
    prepared = PreparedQuery(
        codebook_id=cid,
        codebook_label=label,
        original_question=q,
        rephrased_query=rephrased,
        fts_terms=_as_str_list(data.get("fts_terms")),
        vector_queries=_as_str_list(data.get("vector_queries")) or [rephrased],
        fuzzy_terms=_as_str_list(data.get("fuzzy_terms")),
        heading_clauses=_as_str_list(data.get("heading_clauses")),
        clause_topics=_as_str_list(data.get("clause_topics")),
        rationale=(data.get("rationale") or "").strip(),
    )

    if trace:
        trace.add(
            "prepare_output",
            {
                "rephrased_query": prepared.rephrased_query,
                "fts_terms": prepared.fts_terms,
                "vector_queries": prepared.vector_queries,
                "fuzzy_terms": prepared.fuzzy_terms,
                "heading_clauses": prepared.heading_clauses,
                "clause_topics": prepared.clause_topics,
                "rationale": prepared.rationale,
            },
        )

    return prepared


def prepare_rag_query_retry(
    question: str,
    codebook_id: str,
    *,
    first_prepared: PreparedQuery,
    insufficient_reason: str = "",
    what_would_improve: str = "",
    gaps: Optional[List[str]] = None,
    first_clause_summaries: Optional[List[str]] = None,
    table_catalog: Optional[List[str]] = None,
    table_layout: str = "embedded_chunks",
    codebook_custom: Optional[str] = None,
    codebook_label: Optional[str] = None,
    trace: Optional[RagPipelineTrace] = None,
) -> PreparedQuery:
    """Second prepare pass with alternate search signals after insufficient_evidence."""
    q = (question or "").strip()
    if not q:
        raise ValueError("Question is required")

    cid, label = _resolve_codebook(
        codebook_id,
        codebook_custom=codebook_custom,
        codebook_label=codebook_label,
    )

    gap_list = [g.strip() for g in (gaps or []) if g and str(g).strip()]
    clause_hints = [s.strip() for s in (first_clause_summaries or []) if s and str(s).strip()]
    catalog = [s.strip() for s in (table_catalog or []) if s and str(s).strip()]
    layout_label = (
        "standard_tables (NCC/SIR separate tables index)"
        if table_layout == "standard_tables"
        else "embedded_chunks (AS/NZS tables inside clause CSV)"
    )

    if trace:
        trace.add(
            "prepare_retry_input",
            {
                "original_question": q,
                "codebook_id": cid,
                "codebook_label": label,
                "table_layout": table_layout,
                "table_catalog_count": len(catalog),
                "first_rephrased_query": first_prepared.rephrased_query,
                "first_fts_terms": first_prepared.fts_terms,
                "first_vector_queries": first_prepared.vector_queries,
                "first_fuzzy_terms": first_prepared.fuzzy_terms,
                "first_heading_clauses": first_prepared.heading_clauses,
                "insufficient_reason": insufficient_reason,
                "what_would_improve": what_would_improve,
                "gaps": gap_list,
                "first_clause_hints": clause_hints[:12],
                "table_catalog_sample": catalog[:15],
            },
        )

    system = render_prompt(
        "rag_query_prepare_retry.txt",
        codebook_id=cid,
        codebook_label=label,
        table_layout=layout_label,
    )
    user_payload = json.dumps(
        {
            "question": q,
            "codebook_id": cid,
            "codebook_label": label,
            "table_layout": table_layout,
            "available_table_catalog": catalog[:40],
            "first_attempt": {
                "rephrased_query": first_prepared.rephrased_query,
                "fts_terms": first_prepared.fts_terms,
                "vector_queries": first_prepared.vector_queries,
                "fuzzy_terms": first_prepared.fuzzy_terms,
                "heading_clauses": first_prepared.heading_clauses,
                "rationale": first_prepared.rationale,
            },
            "insufficient_reason": insufficient_reason,
            "what_would_improve_answer": what_would_improve,
            "gaps": gap_list,
            "retrieved_clause_hints": clause_hints[:12],
        },
        ensure_ascii=False,
    )
    data = chat_json(system_prompt=system, user_content=user_payload)

    rephrased = (data.get("rephrased_query") or q).strip()
    heading_clauses = _as_str_list(data.get("heading_clauses"))
    heading_clauses.extend(_as_str_list(data.get("target_table_refs")))
    heading_clauses.extend(_as_str_list(data.get("target_clause_refs")))
    heading_clauses = list(dict.fromkeys(h for h in heading_clauses if h))

    prepared = PreparedQuery(
        codebook_id=cid,
        codebook_label=label,
        original_question=q,
        rephrased_query=rephrased,
        fts_terms=_as_str_list(data.get("fts_terms")),
        vector_queries=_as_str_list(data.get("vector_queries")) or [rephrased],
        fuzzy_terms=_as_str_list(data.get("fuzzy_terms")),
        heading_clauses=heading_clauses,
        clause_topics=_as_str_list(data.get("clause_topics")),
        rationale=(data.get("rationale") or "").strip(),
        attempt=2,
    )

    if trace:
        trace.add(
            "prepare_retry_output",
            {
                "rephrased_query": prepared.rephrased_query,
                "fts_terms": prepared.fts_terms,
                "vector_queries": prepared.vector_queries,
                "fuzzy_terms": prepared.fuzzy_terms,
                "heading_clauses": prepared.heading_clauses,
                "clause_topics": prepared.clause_topics,
                "rationale": prepared.rationale,
            },
        )

    return prepared


def _as_str_list(value) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return []
