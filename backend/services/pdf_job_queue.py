"""
PDF processing queue: global (Postgres) or in-process (memory).

Production (default PDF_QUEUE_BACKEND=postgres):
- One queue across all uvicorn workers via Supabase/Postgres RPCs.
- Limits: PDF_JOB_MAX_CONCURRENT (global), PDF_JOB_MAX_PER_USER (per user).
- Stale running jobs are failed after PDF_JOB_STALE_MINUTES (worker crash recovery).

Local / tests: PDF_QUEUE_BACKEND=memory uses asyncio semaphores (per process only).
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from services.async_utils import run_blocking
from services.pdf_job_recovery import (
    mark_document_failed_for_job,
    reconcile_stale_pdf_jobs,
)
from services.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0.25, float(raw))
    except ValueError:
        return default


def _queue_backend() -> str:
    return (os.getenv("PDF_QUEUE_BACKEND") or "postgres").strip().lower()


def _worker_mode() -> str:
    return (os.getenv("WORKER_MODE") or "api").strip().lower()


def external_pdf_worker_enabled() -> bool:
    return (os.getenv("EXTERNAL_PDF_WORKER") or "").strip().lower() in ("1", "true", "yes", "on")


def _worker_id() -> str:
    host = (socket.gethostname() or "host")[:64]
    return f"{host}:{os.getpid()}"


def _should_enqueue_only() -> bool:
    return external_pdf_worker_enabled() and _worker_mode() != "pdf"


@dataclass
class _MemoryPdfJobQueue:
    _global_sem: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(_env_int("PDF_JOB_MAX_CONCURRENT", 1))
    )
    _user_sem: Dict[str, asyncio.Semaphore] = field(default_factory=dict)
    _user_max: int = field(default_factory=lambda: _env_int("PDF_JOB_MAX_PER_USER", 1))
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def _user(self, user_id: str) -> asyncio.Semaphore:
        async with self._lock:
            sem = self._user_sem.get(user_id)
            if sem is None:
                sem = asyncio.Semaphore(self._user_max)
                self._user_sem[user_id] = sem
            return sem

    async def run(
        self,
        user_id: str,
        coro_factory: Callable[[], Any],
        *,
        document_id: Optional[str] = None,
        upload_id: Optional[str] = None,
        on_queue_tick: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Any:
        uid = (user_id or "").strip() or "anonymous"
        await self._global_sem.acquire()
        user_sem = await self._user(uid)
        await user_sem.acquire()
        try:
            out = coro_factory()
            if asyncio.iscoroutine(out):
                return await out
            return out
        finally:
            user_sem.release()
            self._global_sem.release()


@dataclass
class _PostgresPdfJobQueue:
    _max_global: int = field(default_factory=lambda: _env_int("PDF_JOB_MAX_CONCURRENT", 1))
    _max_per_user: int = field(default_factory=lambda: _env_int("PDF_JOB_MAX_PER_USER", 1))
    _poll_seconds: float = field(default_factory=lambda: _env_float("PDF_JOB_QUEUE_POLL_SECONDS", 2.0))
    _stale_minutes: int = field(default_factory=lambda: _env_int("PDF_JOB_STALE_MINUTES", 90))
    _worker: str = field(default_factory=_worker_id)

    def _supabase(self):
        return get_supabase_client()

    def _enqueue_sync(self, *, document_id: str, user_id: str, upload_id: Optional[str]) -> str:
        row = {
            "document_id": document_id,
            "user_id": user_id,
            "upload_id": upload_id,
            "status": "queued",
        }
        result = self._supabase().table("pdf_processing_jobs").insert(row).execute()
        if not result.data:
            raise RuntimeError("Failed to enqueue pdf_processing_jobs row")
        return str(result.data[0]["id"])

    def _rpc_claim_sync(self, job_id: str, user_id: str) -> Dict[str, Any]:
        result = (
            self._supabase()
            .rpc(
                "try_claim_pdf_processing_job",
                {
                    "p_job_id": job_id,
                    "p_user_id": user_id,
                    "p_max_global": self._max_global,
                    "p_max_per_user": self._max_per_user,
                    "p_worker_id": self._worker,
                    "p_stale_minutes": self._stale_minutes,
                },
            )
            .execute()
        )
        data = result.data
        if isinstance(data, list) and data:
            return dict(data[0]) if isinstance(data[0], dict) else {"claimed": bool(data[0])}
        if isinstance(data, dict):
            return data
        return {"claimed": False}

    def _rpc_finish_sync(self, job_id: str, *, success: bool, error_message: Optional[str] = None) -> None:
        self._supabase().rpc(
            "finish_pdf_processing_job",
            {
                "p_job_id": job_id,
                "p_success": success,
                "p_error_message": (error_message or "")[:2000] or None,
            },
        ).execute()

    def _is_missing_relation_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return (
            "pdf_processing_jobs" in msg
            and ("does not exist" in msg or "could not find" in msg or "pgrst205" in msg)
        ) or "try_claim_pdf_processing_job" in msg

    async def run(
        self,
        user_id: str,
        coro_factory: Callable[[], Any],
        *,
        document_id: Optional[str] = None,
        upload_id: Optional[str] = None,
        on_queue_tick: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Any:
        if not document_id:
            raise ValueError("document_id is required for postgres PDF queue")

        uid = (user_id or "").strip() or "anonymous"
        job_id: Optional[str] = None
        try:
            job_id = await run_blocking(
                lambda: self._enqueue_sync(document_id=document_id, user_id=uid, upload_id=upload_id)
            )
            logger.info("PDF job enqueued job_id=%s document_id=%s user_id=%s", job_id, document_id, uid)
        except Exception as exc:
            if self._is_missing_relation_error(exc):
                logger.warning(
                    "pdf_processing_jobs not available (%s); falling back to in-memory queue for this process",
                    exc,
                )
                return await _memory_pdf_job_queue.run(
                    uid, coro_factory, document_id=document_id, upload_id=upload_id
                )
            raise

        if _should_enqueue_only():
            logger.info(
                "PDF job queued for external worker job_id=%s document_id=%s",
                job_id,
                document_id,
            )
            return {"queued": True, "job_id": job_id}

        claimed = False
        try:
            while not claimed:
                try:
                    await run_blocking(reconcile_stale_pdf_jobs)
                    state = await run_blocking(lambda: self._rpc_claim_sync(job_id, uid))
                except Exception as exc:
                    if self._is_missing_relation_error(exc):
                        logger.warning("PDF queue RPC missing; falling back to in-memory queue")
                        return await _memory_pdf_job_queue.run(
                            uid, coro_factory, document_id=document_id, upload_id=upload_id
                        )
                    raise

                claimed = bool(state.get("claimed"))
                if claimed:
                    logger.info(
                        "PDF job claimed job_id=%s document_id=%s worker=%s reason=%s",
                        job_id,
                        document_id,
                        self._worker,
                        state.get("reason"),
                    )
                    break

                logger.info(
                    "PDF job waiting job_id=%s document_id=%s queue_ahead=%s global_running=%s reason=%s",
                    job_id,
                    document_id,
                    state.get("queue_ahead"),
                    state.get("global_running"),
                    state.get("reason"),
                )

                if on_queue_tick:
                    try:
                        on_queue_tick(state)
                    except Exception:
                        pass

                await asyncio.sleep(self._poll_seconds)

            out = coro_factory()
            if asyncio.iscoroutine(out):
                result = await out
            else:
                result = out
            await run_blocking(lambda: self._rpc_finish_sync(job_id, success=True))
            return result
        except Exception as exc:
            if job_id:
                err_msg = str(exc)[:2000]
                try:
                    await run_blocking(
                        lambda: self._rpc_finish_sync(job_id, success=False, error_message=err_msg)
                    )
                except Exception:
                    pass
                try:
                    await run_blocking(
                        lambda: mark_document_failed_for_job(job_id, err_msg)
                    )
                except Exception:
                    pass
            raise


def _build_pdf_job_queue():
    backend = _queue_backend()
    if backend in ("memory", "local", "process"):
        logger.info("PDF queue backend=memory (per-process semaphores only)")
        return _MemoryPdfJobQueue()
    if backend not in ("postgres", "supabase", "global", "db"):
        logger.warning("Unknown PDF_QUEUE_BACKEND=%r; using postgres", backend)
    logger.info(
        "PDF queue backend=postgres (global) max_concurrent=%s max_per_user=%s",
        _env_int("PDF_JOB_MAX_CONCURRENT", 1),
        _env_int("PDF_JOB_MAX_PER_USER", 1),
    )
    return _PostgresPdfJobQueue()


_memory_pdf_job_queue = _MemoryPdfJobQueue()
pdf_job_queue = _build_pdf_job_queue()


def _list_queued_jobs_sync(limit: int = 20) -> list[Dict[str, Any]]:
    result = (
        get_supabase_client()
        .table("pdf_processing_jobs")
        .select("*")
        .eq("status", "queued")
        .order("created_at")
        .limit(limit)
        .execute()
    )
    return list(result.data or [])


async def claim_next_pdf_job_for_worker() -> Optional[Dict[str, Any]]:
    """Claim the oldest queued PDF job for an external worker process."""
    if _queue_backend() in ("memory", "local", "process"):
        return None

    queue = _PostgresPdfJobQueue()
    await run_blocking(reconcile_stale_pdf_jobs)
    jobs = await run_blocking(lambda: _list_queued_jobs_sync())
    for job in jobs:
        job_id = str(job["id"])
        user_id = str(job["user_id"])
        state = await run_blocking(lambda jid=job_id, uid=user_id: queue._rpc_claim_sync(jid, uid))
        if state.get("claimed"):
            logger.info("PDF worker claimed job_id=%s document_id=%s", job_id, job.get("document_id"))
            return job
    return None


async def finish_pdf_job(job_id: str, *, success: bool, error_message: Optional[str] = None) -> None:
    queue = _PostgresPdfJobQueue()
    await run_blocking(lambda: queue._rpc_finish_sync(job_id, success=success, error_message=error_message))
