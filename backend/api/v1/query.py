"""RAG query endpoints: prepare → retrieve → answer (parcel synthesis)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from middleware.subscription_check import check_question_limit, get_current_user
from services.subscription_service import subscription_service
from services.ask_job_service import (
    ask_async_default,
    ask_async_enabled,
    ask_job_workers_enabled,
    enqueue_ask_job,
    enqueue_design_compliance_job,
    get_ask_job,
    job_status_payload,
)
from services.ask_pipeline import run_ask_pipeline
from services.async_utils import run_blocking, run_blocking_ask
from services.chunk_retrieval import retrieve_clauses
from services.rag_query_service import PreparedQuery, prepare_rag_query
from services.rag_settings import rag_settings
from services.rag_trace import RagPipelineTrace
from services.ask_rate_limit import acquire_ask_slot, ask_rate_limit_http_exception, release_ask_slot
from services.free_ask_quota import (
    FreeAskQuotaExceeded,
    free_ask_quota_http_exception,
    reserve_free_ask,
)
from services.answer_cache import get_cached_answer, store_cached_answer
from services.design_compliance_service import (
    MAX_REQUIREMENTS,
    finalize_project_report,
    load_selected_documents,
    normalize_discipline,
    plan_report,
    run_report,
    validate_report_scope_or_raise,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class QueryPrepareRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    codebook_id: str = Field(..., min_length=1, max_length=64)
    codebook_custom: Optional[str] = None
    codebook_label: Optional[str] = None
    document_id: Optional[str] = Field(None, max_length=64)


class QuerySearchRequest(QueryPrepareRequest):
    prepared: Optional[PreparedQuery] = None
    top_k: Optional[int] = Field(None, ge=1, le=50)


class QueryAskRequest(QueryPrepareRequest):
    skip_answer: bool = False
    top_k: Optional[int] = Field(None, ge=1, le=50)
    async_mode: Optional[bool] = None


class DesignCompliancePlanRequest(BaseModel):
    discipline: str = Field(..., min_length=2, max_length=40)
    question: str = Field(..., min_length=1, max_length=6000)
    document_ids: List[str] = Field(..., min_length=1, max_length=24)
    scoping_answers: Dict[str, str] = Field(default_factory=dict)


class DesignComplianceRunRequest(DesignCompliancePlanRequest):
    plan: Dict[str, Any]
    requirements: Optional[List[Dict[str, Any]]] = Field(None, max_length=MAX_REQUIREMENTS)
    async_mode: Optional[bool] = None


class DesignComplianceFinalizeSection(BaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    label: str = Field(..., min_length=1, max_length=240)
    code_labels: List[str] = Field(default_factory=list, max_length=24)
    report: Dict[str, Any]


class DesignComplianceFinalizeRequest(BaseModel):
    discipline: str = Field(..., min_length=2, max_length=40)
    question: str = Field(..., min_length=1, max_length=6000)
    scoping_answers: Dict[str, str] = Field(default_factory=dict)
    sections: List[DesignComplianceFinalizeSection] = Field(..., min_length=1, max_length=12)


def _clause_payload(row: Dict[str, Any]) -> Dict[str, Any]:
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


def _new_trace(body: QueryPrepareRequest, user_id: str) -> RagPipelineTrace:
    return RagPipelineTrace(
        question=body.question.strip(),
        codebook_id=body.codebook_id,
        user_id=user_id,
    )


async def _guard_query_access(body: QueryPrepareRequest, user_id: str) -> None:
    """Enforce sole vs professional codebook access server-side."""
    try:
        await subscription_service.enforce_sole_codebook_access(user_id, body.codebook_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


async def _guard_design_compliance_access(user_id: str) -> None:
    access = await subscription_service.check_user_access(user_id)
    if (access.get("account_type") or "sole").strip().lower() != "professional":
        raise HTTPException(
            status_code=403,
            detail="Design Compliance reports require Professional access.",
        )


async def _enforce_free_ask_quota(user_id: str) -> None:
    """Sole/free users consume the daily free-ask budget; Professional bypasses."""
    access = await subscription_service.check_user_access(user_id)
    account_type = (access.get("account_type") or "sole").strip().lower()
    is_professional = account_type == "professional"
    try:
        reserve_free_ask(user_id, is_professional=is_professional)
    except FreeAskQuotaExceeded as exc:
        raise free_ask_quota_http_exception(exc) from exc


@router.post("/prepare")
async def prepare_query(
    body: QueryPrepareRequest,
    current_user: str = Depends(check_question_limit),
):
    """
    Step 1: Send codebook + question to OpenAI.
    Returns rephrased query and per-signal search terms for RRF retrieval.
    """
    trace = _new_trace(body, current_user)
    await _guard_query_access(body, current_user)
    if not acquire_ask_slot(current_user):
        raise ask_rate_limit_http_exception()
    try:
        prepared = await run_blocking_ask(
            lambda: prepare_rag_query(
                body.question,
                body.codebook_id,
                codebook_custom=body.codebook_custom,
                codebook_label=body.codebook_label,
                trace=trace,
            )
        )
        return {"prepared": prepared.model_dump(), "trace": trace.to_dict()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("prepare_query failed")
        raise HTTPException(status_code=500, detail=f"Query preparation failed: {exc}") from exc
    finally:
        release_ask_slot(current_user)


@router.post("/search")
async def search_clauses(
    body: QuerySearchRequest,
    current_user: str = Depends(check_question_limit),
):
    """
    Step 2: Multi-signal retrieval (FTS, vector, fuzzy, heading) merged with RRF.
    Returns top_k full clauses (not truncated).
    """
    trace = _new_trace(body, current_user)
    await _guard_query_access(body, current_user)
    if not acquire_ask_slot(current_user):
        raise ask_rate_limit_http_exception()
    try:
        if body.prepared:
            prepared = body.prepared
            trace.add("prepare_skipped", {"reason": "client supplied prepared query"})
        else:
            prepared = await run_blocking_ask(
                lambda: prepare_rag_query(
                    body.question,
                    body.codebook_id,
                    codebook_custom=body.codebook_custom,
                    codebook_label=body.codebook_label,
                    trace=trace,
                )
            )

        clauses, meta = await run_blocking_ask(
            lambda: retrieve_clauses(
                current_user,
                prepared,
                top_k=body.top_k or rag_settings.rag_top_k,
                trace=trace,
                document_id=body.document_id,
            )
        )
        return {
            "prepared": prepared.model_dump(),
            "clauses": [_clause_payload(c) for c in clauses],
            "retrieval": meta,
            "trace": trace.to_dict(),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("search_clauses failed")
        raise HTTPException(status_code=500, detail=f"Search failed: {exc}") from exc
    finally:
        release_ask_slot(current_user)


@router.post("/design-compliance/plan")
async def plan_design_compliance(
    body: DesignCompliancePlanRequest,
    current_user: str = Depends(get_current_user),
):
    """Create dynamic scoping questions, requirements, and code recommendations."""
    try:
        await _guard_design_compliance_access(current_user)
        normalize_discipline(body.discipline)
        documents = await run_blocking(load_selected_documents, current_user, body.document_ids)
        for codebook_id in {document.codebook for document in documents}:
            await _guard_query_access(
                QueryPrepareRequest(
                    question=body.question,
                    codebook_id=codebook_id,
                ),
                current_user,
            )
        return await run_blocking(
            plan_report,
            discipline=body.discipline,
            question=body.question,
            documents=documents,
            scoping_answers=body.scoping_answers,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("design compliance planning failed")
        raise HTTPException(status_code=500, detail=f"Planning failed: {exc}") from exc


@router.post("/design-compliance/run")
async def run_design_compliance(
    body: DesignComplianceRunRequest,
    current_user: str = Depends(check_question_limit),
):
    """Run routed RAG for planned requirements and return a classified report."""
    await _guard_design_compliance_access(current_user)
    await _enforce_free_ask_quota(current_user)
    async_ok = ask_async_enabled() and ask_job_workers_enabled()
    want_async = body.async_mode if body.async_mode is not None else True
    slot_acquired = False
    try:
        normalize_discipline(body.discipline)
        documents = await run_blocking(load_selected_documents, current_user, body.document_ids)
        for codebook_id in {document.codebook for document in documents}:
            await _guard_query_access(
                QueryPrepareRequest(
                    question=body.question,
                    codebook_id=codebook_id,
                ),
                current_user,
            )
        plan = dict(body.plan)
        if body.requirements is not None:
            plan["requirements"] = body.requirements
        requirements = plan.get("requirements") or []
        validate_report_scope_or_raise(requirements, documents)
        # Design reports fan out over many LLM calls. When the server's worker
        # queue is enabled, queue by default so nginx does not time out.
        if async_ok and want_async:
            job = await run_blocking(
                enqueue_design_compliance_job,
                current_user,
                {
                    "discipline": body.discipline,
                    "question": body.question,
                    "documents": [document.model_dump() for document in documents],
                    "plan": plan,
                    "scoping_answers": body.scoping_answers,
                },
            )
            return JSONResponse(
                status_code=202,
                content={
                    "status": "queued",
                    "job_id": job["job_id"],
                    "queue_position": job.get("queue_position"),
                    "poll_url": f"/api/v1/query/design-compliance/jobs/{job['job_id']}",
                },
            )
        if not acquire_ask_slot(current_user):
            raise ask_rate_limit_http_exception()
        slot_acquired = True
        result = await run_blocking(
            run_report,
            user_id=current_user,
            discipline=body.discipline,
            question=body.question,
            documents=documents,
            plan=plan,
            scoping_answers=body.scoping_answers,
        )
        try:
            await subscription_service.increment_usage(current_user, "questions")
        except Exception as exc:
            logger.warning("Failed to increment design report usage for %s: %s", current_user, exc)
        return result
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("design compliance report failed")
        raise HTTPException(status_code=500, detail=f"Report generation failed: {exc}") from exc
    finally:
        if slot_acquired:
            release_ask_slot(current_user)


@router.post("/design-compliance/finalize")
async def finalize_design_compliance_project(
    body: DesignComplianceFinalizeRequest,
    current_user: str = Depends(get_current_user),
):
    """Merge saved multi-run sections into one unified design planning report."""
    try:
        await _guard_design_compliance_access(current_user)
        return await run_blocking(
            finalize_project_report,
            discipline=body.discipline,
            brief=body.question,
            scoping_answers=body.scoping_answers,
            sections=[section.model_dump() for section in body.sections],
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("design compliance finalize failed")
        raise HTTPException(status_code=500, detail=f"Finalize failed: {exc}") from exc


@router.get("/design-compliance/jobs/{job_id}")
async def get_design_compliance_job_status(
    job_id: str,
    current_user: str = Depends(get_current_user),
):
    """Poll a queued design compliance report."""
    job = await run_blocking(get_ask_job, job_id)
    if not job or job.get("user_id") != current_user or job.get("job_type") != "design_compliance":
        raise HTTPException(status_code=404, detail="Design compliance job not found")
    return job_status_payload(job)


def _ask_job_payload(body: QueryAskRequest) -> Dict[str, Any]:
    return {
        "question": body.question,
        "codebook_id": body.codebook_id,
        "codebook_custom": body.codebook_custom,
        "codebook_label": body.codebook_label,
        "document_id": body.document_id,
        "top_k": body.top_k,
        "skip_answer": body.skip_answer,
    }


def _accepted_job_response(job: Dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        status_code=202,
        content={
            "status": "queued",
            "job_id": job["job_id"],
            "queue_position": job.get("queue_position"),
            "poll_url": f"/api/v1/query/ask/jobs/{job['job_id']}",
        },
    )


@router.post("/ask")
async def ask_question(
    body: QueryAskRequest,
    current_user: str = Depends(check_question_limit),
):
    """
    Full pipeline: prepare → RRF search (top 20) → parcel LLM synthesis.
    On insufficient_evidence, retries with alternate search signals and merged retrieval.

    When ASK_ASYNC_ENABLED=true and the client requests async (or ASK_ASYNC_DEFAULT=true),
    returns 202 + job_id; poll GET /query/ask/jobs/{job_id} for the result. Sync requests
    that hit the inflight limit also fall back to the queue instead of 429.
    """
    await _guard_query_access(body, current_user)

    payload = _ask_job_payload(body)
    cached = await run_blocking(get_cached_answer, payload)
    if cached is not None:
        return cached

    await _enforce_free_ask_quota(current_user)

    async_ok = ask_async_enabled()
    want_async = body.async_mode if body.async_mode is not None else ask_async_default()
    if async_ok and want_async:
        job = await run_blocking(enqueue_ask_job, current_user, payload)
        return _accepted_job_response(job)

    if not acquire_ask_slot(current_user):
        if async_ok:
            # S3b.5: burst fallback — queue the ask instead of rejecting with 429.
            job = await run_blocking(enqueue_ask_job, current_user, payload)
            return _accepted_job_response(job)
        raise ask_rate_limit_http_exception()
    try:
        result = await run_blocking_ask(run_ask_pipeline, current_user, payload)
        await run_blocking(store_cached_answer, payload, result)

        try:
            await subscription_service.increment_usage(current_user, "questions")
        except Exception as exc:
            logger.warning("Failed to increment question usage for %s: %s", current_user, exc)

        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("ask_question failed")
        raise HTTPException(status_code=500, detail=f"Ask failed: {exc}") from exc
    finally:
        release_ask_slot(current_user)


@router.get("/ask/jobs/{job_id}")
async def get_ask_job_status(
    job_id: str,
    current_user: str = Depends(get_current_user),
):
    """Poll an async ask job. Returns the full ask response in `result` once done."""
    job = await run_blocking(get_ask_job, job_id)
    if not job or job.get("user_id") != current_user:
        raise HTTPException(status_code=404, detail="Ask job not found")
    return job_status_payload(job)
