"""
Async ask job queue (Stage S3b).

POST /query/ask can return 202 + job_id; workers in the API lifespan pop jobs,
run the full ask pipeline, and store the result for GET /query/ask/jobs/{id}.

Redis-backed when REDIS_URL is set (required for multi-worker uvicorn so any
worker can serve the poll). Falls back to an in-process queue for local dev.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from typing import Any, Dict, Optional

from services.redis_client import get_redis_client

logger = logging.getLogger(__name__)

ASK_JOB_TTL_SECONDS = max(300, int(os.getenv("ASK_JOB_TTL_SECONDS", "3600")))
ASK_JOB_WORKER_CONCURRENCY = max(1, int(os.getenv("ASK_JOB_WORKER_CONCURRENCY", "4")))
ASK_JOB_SLOT_WAIT_SECONDS = max(10, int(os.getenv("ASK_JOB_SLOT_WAIT_SECONDS", "600")))

_QUEUE_KEY = "askjob:queue"
_JOB_KEY_PREFIX = "askjob:job:"

_memory_lock = threading.Lock()
_memory_jobs: Dict[str, Dict[str, Any]] = {}
_memory_queue: deque[str] = deque()

_workers_started = False
_stop_event = threading.Event()


def _env_truthy(name: str, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def ask_async_enabled() -> bool:
    return _env_truthy("ASK_ASYNC_ENABLED", default=False)


def ask_async_default() -> bool:
    return _env_truthy("ASK_ASYNC_DEFAULT", default=False)


def ask_job_workers_enabled() -> bool:
    return _env_truthy("ASK_JOB_WORKER_ENABLED", default=False)


def _job_key(job_id: str) -> str:
    return f"{_JOB_KEY_PREFIX}{job_id}"


def _save_job(job: Dict[str, Any]) -> None:
    client = get_redis_client()
    if client is not None:
        try:
            client.setex(_job_key(job["job_id"]), ASK_JOB_TTL_SECONDS, json.dumps(job, default=str))
            return
        except Exception as exc:
            logger.warning("Redis save ask job failed: %s", exc)
    with _memory_lock:
        _memory_jobs[job["job_id"]] = job


def get_ask_job(job_id: str) -> Optional[Dict[str, Any]]:
    client = get_redis_client()
    if client is not None:
        try:
            raw = client.get(_job_key(job_id))
            if raw:
                return json.loads(raw)
        except Exception as exc:
            logger.warning("Redis load ask job failed: %s", exc)
    with _memory_lock:
        return _memory_jobs.get(job_id)


def _queue_length() -> int:
    client = get_redis_client()
    if client is not None:
        try:
            return int(client.llen(_QUEUE_KEY))
        except Exception:
            pass
    with _memory_lock:
        return len(_memory_queue)


def _enqueue_job(user_id: str, payload: Dict[str, Any], job_type: str) -> Dict[str, Any]:
    """Create a queued job and push it onto the shared queue."""
    job_id = uuid.uuid4().hex
    job: Dict[str, Any] = {
        "job_id": job_id,
        "user_id": user_id,
        "job_type": job_type,
        "status": "queued",
        "payload": payload,
        "created_at": time.time(),
        "queue_position": _queue_length() + 1,
    }
    _save_job(job)

    client = get_redis_client()
    pushed = False
    if client is not None:
        try:
            client.rpush(_QUEUE_KEY, job_id)
            pushed = True
        except Exception as exc:
            logger.warning("Redis enqueue ask job failed: %s", exc)
    if not pushed:
        with _memory_lock:
            _memory_queue.append(job_id)
    return job


def enqueue_ask_job(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Queue a standard single-question ask."""
    return _enqueue_job(user_id, payload, "ask")


def enqueue_design_compliance_job(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Queue a long-running multi-question design compliance report."""
    return _enqueue_job(user_id, payload, "design_compliance")


def _pop_job_id(timeout_seconds: int = 2) -> Optional[str]:
    client = get_redis_client()
    if client is not None:
        try:
            item = client.blpop(_QUEUE_KEY, timeout=timeout_seconds)
            if item:
                return item[1]
            return None
        except Exception as exc:
            logger.warning("Redis pop ask job failed: %s", exc)
    with _memory_lock:
        if _memory_queue:
            return _memory_queue.popleft()
    time.sleep(min(timeout_seconds, 1))
    return None


def _run_job(job_id: str) -> None:
    from services.ask_pipeline import increment_question_usage_sync, run_ask_pipeline
    from services.ask_rate_limit import acquire_ask_slot, release_ask_slot

    job = get_ask_job(job_id)
    if not job or job.get("status") != "queued":
        return

    user_id = job["user_id"]
    payload = job.get("payload") or {}

    # Respect the same global/per-user inflight limits as the sync path.
    slot_deadline = time.monotonic() + ASK_JOB_SLOT_WAIT_SECONDS
    got_slot = False
    while time.monotonic() < slot_deadline and not _stop_event.is_set():
        if acquire_ask_slot(user_id):
            got_slot = True
            break
        time.sleep(1.0)
    if not got_slot:
        job.update(
            status="error",
            error="The server is busy and your question could not start in time. Please try again.",
            finished_at=time.time(),
        )
        _save_job(job)
        return

    job.update(status="running", started_at=time.time(), queue_position=0)
    _save_job(job)
    try:
        if job.get("job_type") == "design_compliance":
            from services.design_compliance_service import DesignDocument, run_report

            result = run_report(
                user_id=user_id,
                discipline=payload["discipline"],
                question=payload["question"],
                documents=[
                    DesignDocument.model_validate(document)
                    for document in payload.get("documents") or []
                ],
                plan=payload["plan"],
                scoping_answers=payload.get("scoping_answers") or {},
            )
        else:
            result = run_ask_pipeline(user_id, payload)
            try:
                from services.answer_cache import store_cached_answer

                store_cached_answer(payload, result)
            except Exception as cache_exc:
                logger.warning("Ask job answer cache store failed: %s", cache_exc)
        increment_question_usage_sync(user_id)
        job.update(status="done", result=result, finished_at=time.time())
    except ValueError as exc:
        job.update(status="error", error=str(exc), finished_at=time.time())
    except Exception as exc:
        logger.exception("Ask job %s failed", job_id)
        error_text = f"Ask failed: {exc}"
        if job.get("job_type") == "design_compliance":
            error_text = (
                f"{error_text} The report may be too large — deselect some codes "
                f"(aim for 3–5), shorten the brief, and click Plan this review again."
            )
        job.update(status="error", error=error_text, finished_at=time.time())
    finally:
        release_ask_slot(user_id)
    _save_job(job)


def _worker_loop(worker_index: int) -> None:
    logger.info("Ask job worker %s started", worker_index)
    while not _stop_event.is_set():
        try:
            job_id = _pop_job_id()
            if job_id:
                _run_job(job_id)
        except Exception as exc:
            logger.exception("Ask job worker %s loop error: %s", worker_index, exc)
            time.sleep(1.0)
    logger.info("Ask job worker %s stopped", worker_index)


def start_ask_job_workers() -> int:
    """Start worker threads when ASK_JOB_WORKER_ENABLED=true. Returns thread count."""
    global _workers_started
    if _workers_started:
        return 0
    if not ask_job_workers_enabled():
        return 0
    _stop_event.clear()
    for i in range(ASK_JOB_WORKER_CONCURRENCY):
        threading.Thread(target=_worker_loop, args=(i + 1,), name=f"ask-job-{i + 1}", daemon=True).start()
    _workers_started = True
    logger.info("Started %s ask job worker(s)", ASK_JOB_WORKER_CONCURRENCY)
    return ASK_JOB_WORKER_CONCURRENCY


def stop_ask_job_workers() -> None:
    global _workers_started
    _stop_event.set()
    _workers_started = False


def job_status_payload(job: Dict[str, Any]) -> Dict[str, Any]:
    """Public shape for GET /query/ask/jobs/{id} (payload/user internals omitted)."""
    out: Dict[str, Any] = {
        "job_id": job["job_id"],
        "status": job.get("status"),
        "created_at": job.get("created_at"),
    }
    if job.get("status") == "queued":
        out["queue_position"] = job.get("queue_position")
    if job.get("started_at"):
        out["started_at"] = job["started_at"]
    if job.get("finished_at"):
        out["finished_at"] = job["finished_at"]
    if job.get("status") == "done":
        out["result"] = job.get("result")
    if job.get("status") == "error":
        out["error"] = job.get("error") or "Ask failed"
    return out
