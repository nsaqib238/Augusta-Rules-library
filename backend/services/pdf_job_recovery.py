"""
Recover PDF ingest state when jobs fail, workers crash, or queue rows desync from documents.

Keeps documents.status in sync with pdf_processing_jobs so the UI can show Failed
and users can remove + re-upload instead of staying stuck on pdf_processing.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from services.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

_PROCESSING_STATUSES = ("pdf_processing", "admin_processing", "pending_admin_review")
_FAIL_NOTE_PREFIX = "PDF processing failed."


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def mark_document_pdf_failed(document_id: str, reason: str) -> None:
    """Set document to failed if it is still in a processing state."""
    if not document_id:
        return
    msg = (reason or "PDF processing failed.").strip()
    if not msg.lower().startswith("pdf processing failed"):
        msg = f"{_FAIL_NOTE_PREFIX} {msg}"
    msg = msg[:1500]
    try:
        supabase = get_supabase_client()
        supabase.table("documents").update(
            {"status": "failed", "admin_notes": msg}
        ).eq("id", document_id).in_("status", list(_PROCESSING_STATUSES)).execute()
    except Exception as e:
        logger.warning("mark_document_pdf_failed document_id=%s: %s", document_id, e)


def touch_pdf_job_heartbeat(document_id: str) -> None:
    """Refresh updated_at on the active running job for this document (orphan detection)."""
    if not document_id:
        return
    try:
        supabase = get_supabase_client()
        supabase.table("pdf_processing_jobs").update(
            {"updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("document_id", document_id).eq("status", "running").execute()
    except Exception as e:
        logger.debug("touch_pdf_job_heartbeat document_id=%s: %s", document_id, e)


def reconcile_stale_pdf_jobs() -> None:
    """
  Mark orphan/stale running jobs failed and sync documents whose latest job failed.

  Called before claiming queue slots and on app startup so worker OOM/crash does not
  leave documents stuck on pdf_processing for hours.
  """
    orphan_min = _env_int("PDF_JOB_ORPHAN_MINUTES", 15)
    stale_min = _env_int("PDF_JOB_STALE_MINUTES", 90)
    now = datetime.now(timezone.utc)
    orphan_cutoff = (now - timedelta(minutes=orphan_min)).isoformat()
    stale_cutoff = (now - timedelta(minutes=stale_min)).isoformat()

    try:
        supabase = get_supabase_client()
    except Exception as e:
        logger.warning("reconcile_stale_pdf_jobs: no supabase client: %s", e)
        return

    try:
        running = (
            supabase.table("pdf_processing_jobs")
            .select("id, document_id, started_at, updated_at")
            .eq("status", "running")
            .execute()
        )
        for row in running.data or []:
            doc_id = row.get("document_id")
            job_id = row.get("id")
            updated_at = row.get("updated_at") or ""
            started_at = row.get("started_at") or ""
            is_orphan = updated_at < orphan_cutoff
            is_stale = started_at and started_at < stale_cutoff
            if not (is_orphan or is_stale):
                continue
            reason = (
                "Stale PDF job (no heartbeat — server may have restarted). "
                "Remove this file and upload again."
                if is_orphan
                else "PDF processing timed out. Remove this file and upload again."
            )
            try:
                supabase.table("pdf_processing_jobs").update(
                    {
                        "status": "failed",
                        "error_message": reason[:2000],
                        "finished_at": now.isoformat(),
                        "updated_at": now.isoformat(),
                    }
                ).eq("id", job_id).eq("status", "running").execute()
                if doc_id:
                    mark_document_pdf_failed(str(doc_id), reason)
                logger.warning(
                    "[pdf_job_recovery] failed orphan/stale job_id=%s document_id=%s orphan=%s stale=%s",
                    job_id,
                    doc_id,
                    is_orphan,
                    is_stale,
                )
            except Exception as e:
                logger.warning("reconcile_stale_pdf_jobs job_id=%s: %s", job_id, e)

        processing_docs = (
            supabase.table("documents")
            .select("id")
            .in_("status", list(_PROCESSING_STATUSES))
            .execute()
        )
        for doc in processing_docs.data or []:
            doc_id = doc.get("id")
            if not doc_id:
                continue
            latest = (
                supabase.table("pdf_processing_jobs")
                .select("status, error_message")
                .eq("document_id", doc_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if not latest.data:
                continue
            job = latest.data[0]
            if job.get("status") == "failed":
                mark_document_pdf_failed(
                    str(doc_id),
                    job.get("error_message")
                    or "PDF processing failed. Remove this file and upload again.",
                )
    except Exception as e:
        logger.warning("reconcile_stale_pdf_jobs failed: %s", e)


def mark_document_failed_for_job(job_id: str, error_message: Optional[str] = None) -> None:
    """After finish_pdf_processing_job(success=False), sync the document row."""
    if not job_id:
        return
    try:
        supabase = get_supabase_client()
        row = (
            supabase.table("pdf_processing_jobs")
            .select("document_id")
            .eq("id", job_id)
            .limit(1)
            .execute()
        )
        if row.data and row.data[0].get("document_id"):
            mark_document_pdf_failed(
                str(row.data[0]["document_id"]),
                error_message or "PDF processing failed. Remove this file and upload again.",
            )
    except Exception as e:
        logger.warning("mark_document_failed_for_job job_id=%s: %s", job_id, e)
