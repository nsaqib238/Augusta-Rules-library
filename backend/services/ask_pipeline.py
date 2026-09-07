"""Shared ask pipeline: one sync entry point used by the /query/ask route and the async job worker (Stage S3b)."""
from __future__ import annotations

import logging
from typing import Any, Dict

from services.rag_ask_service import run_ask_with_retry
from services.rag_settings import rag_settings
from services.rag_trace import RagPipelineTrace

logger = logging.getLogger(__name__)


def clause_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    """Full clause text for client / LLM — not truncated."""
    return {
        "id": row.get("id"),
        "document_id": row.get("document_id"),
        "chunk_index": row.get("chunk_index"),
        "clause_number": row.get("clause_number"),
        "heading": row.get("heading"),
        "page_number": row.get("page_number"),
        "codebook": row.get("codebook"),
        "text": row.get("text") or "",
    }


def run_ask_pipeline(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the full ask pipeline synchronously and return the API response body.
    `payload` mirrors QueryAskRequest fields. Access/plan checks must be done
    by the caller before enqueueing (they need the request context).
    """
    trace = RagPipelineTrace(
        question=(payload.get("question") or "").strip(),
        codebook_id=payload.get("codebook_id") or "",
        user_id=user_id,
    )
    result = run_ask_with_retry(
        user_id,
        payload["question"],
        payload["codebook_id"],
        codebook_custom=payload.get("codebook_custom"),
        codebook_label=payload.get("codebook_label"),
        document_id=payload.get("document_id"),
        top_k=payload.get("top_k") or rag_settings.rag_top_k,
        skip_answer=bool(payload.get("skip_answer")),
        trace=trace,
    )
    return {
        "prepared": result["prepared"].model_dump(),
        "clauses": [clause_payload(c) for c in result["clauses"]],
        "retrieval": result["retrieval"],
        "answer": result["answer"],
        "retry": result["retry"],
        "trace": trace.to_dict(),
    }


def increment_question_usage_sync(user_id: str) -> None:
    """Best-effort usage increment from a worker thread (no running event loop)."""
    import asyncio

    from services.subscription_service import subscription_service

    try:
        asyncio.run(subscription_service.increment_usage(user_id, "questions"))
    except Exception as exc:
        logger.warning("Failed to increment question usage for %s: %s", user_id, exc)
