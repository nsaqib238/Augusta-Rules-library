"""PDF job execution for external worker processes."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from pdf_pipeline.settings import settings as pdf_settings
from services.async_utils import run_blocking
from services.pdf_ingest_service import (
    PdfNoStructuredContentError,
    PdfQualityCheckError,
    run_pdf_pipeline_and_ingest_from_path,
)
from services.pdf_job_recovery import mark_document_pdf_failed, touch_pdf_job_heartbeat
from services.supabase_client import get_supabase_client
from services.supabase_storage import SupabaseStorage
from services.upload_progress import write_upload_progress

logger = logging.getLogger(__name__)

_REUPLOAD_HINT = "Remove this file and upload again."
_INCOMING_DIR = Path("processed_files") / "_incoming"


async def _pdf_job_heartbeat_loop(document_id: str, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await run_blocking(lambda: touch_pdf_job_heartbeat(document_id))
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            continue


def _modal_remote_enabled() -> bool:
    has_modal_url = bool(os.getenv("MODAL_ENDPOINT", "").strip())
    return has_modal_url and not pdf_settings.disable_modal


def _stage_pdf_path(document_id: str) -> Path:
    return _INCOMING_DIR / f"{document_id}.pdf"


def _download_pdf_to_path(storage_url: str, dest: Path) -> None:
    storage = SupabaseStorage()
    signed_url = storage.s3_storage.generate_signed_url(storage_url, expiration=3600)
    if not signed_url:
        raise RuntimeError("Failed to generate signed URL for PDF download")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        response = client.get(signed_url)
        response.raise_for_status()
        dest.write_bytes(response.content)


def _resolve_local_pdf_path(document_id: str, storage_url: Optional[str]) -> Path:
    staged = _stage_pdf_path(document_id)
    if staged.is_file() and staged.stat().st_size > 0:
        return staged
    if not storage_url:
        raise FileNotFoundError(f"No staged PDF and no storage_url for document {document_id}")
    _download_pdf_to_path(storage_url, staged)
    return staged


def _load_document(document_id: str) -> Dict[str, Any]:
    result = (
        get_supabase_client()
        .table("documents")
        .select("id, user_id, filename, codebook, standard_family, storage_url, status")
        .eq("id", document_id)
        .single()
        .execute()
    )
    if not result.data:
        raise ValueError(f"Document not found: {document_id}")
    return result.data


async def process_pdf_job(job: Dict[str, Any]) -> None:
    """Run full PDF pipeline for a claimed pdf_processing_jobs row."""
    document_id = str(job["document_id"])
    user_id = str(job["user_id"])
    upload_id = str(job.get("upload_id") or document_id)
    job_id = str(job.get("id") or "")

    document = await run_blocking(lambda: _load_document(document_id))
    filename = document.get("filename") or document.get("original_filename") or "upload.pdf"
    effective_codebook = (document.get("codebook") or "AS3000").strip()
    standard_family = (document.get("standard_family") or "").strip()
    storage_url = document.get("storage_url")
    modal_remote_enabled = _modal_remote_enabled()
    enhancement = pdf_settings.pdf_pipeline_enhancement
    mark_processing = document.get("status") == "pdf_processing"

    write_upload_progress(upload_id, 52, "queued", "Claimed by PDF worker", user_id=user_id)
    local_pdf_path = await run_blocking(lambda: _resolve_local_pdf_path(document_id, storage_url))

    supabase_storage = SupabaseStorage()
    stop_heartbeat = asyncio.Event()
    heartbeat_task = asyncio.create_task(_pdf_job_heartbeat_loop(document_id, stop_heartbeat))
    try:
        write_upload_progress(upload_id, 55, "extracting_pdf", "Running PDF extract + ingest (background)", user_id=user_id)
        ingest = await run_pdf_pipeline_and_ingest_from_path(
            local_pdf_path,
            document_id,
            user_id,
            default_standard_name=effective_codebook,
            enable_enhancement=enhancement,
            replace_existing=True,
            admin_notes="User upload — automatic PDF pipeline",
            update_admin_queue=False,
            mark_processing=mark_processing,
            force_modal=modal_remote_enabled,
            standard_family=standard_family,
        )
        if modal_remote_enabled:
            await run_blocking(
                lambda: supabase_storage.supabase.table("documents")
                .update({"processing_model": "modal_pdf_pipeline"})
                .eq("id", document_id)
                .execute()
            )
        write_upload_progress(
            upload_id,
            100,
            "completed",
            (
                f"Document ready for search (chunks={ingest.get('chunks_count', 0)}, "
                f"tables={ingest.get('tables_count', 0)})"
            ),
            user_id=user_id,
        )
        logger.info("PDF worker completed job_id=%s document_id=%s", job_id, document_id)
    except PdfNoStructuredContentError as exc:
        logger.warning("PDF worker: no structured content document_id=%s", document_id)
        await run_blocking(
            lambda: mark_document_pdf_failed(
                document_id,
                (
                    "Automatic processing found no searchable clauses or tables "
                    "(often a scanned/image-only PDF or unsupported layout). "
                    f"Detail: {str(exc)[:900]} {_REUPLOAD_HINT}"
                ),
            )
        )
        write_upload_progress(upload_id, 100, "failed", f"No searchable content found. {_REUPLOAD_HINT}", user_id=user_id)
        raise
    except PdfQualityCheckError as exc:
        logger.warning("PDF worker: quality check failed document_id=%s: %s", document_id, exc)
        await run_blocking(
            lambda: mark_document_pdf_failed(
                document_id,
                f"Automatic PDF pipeline quality check failed: {str(exc)[:1200]}. {_REUPLOAD_HINT}",
            )
        )
        write_upload_progress(upload_id, 100, "failed", f"Processing failed quality checks. {_REUPLOAD_HINT}", user_id=user_id)
        raise
    except Exception as exc:
        logger.exception("PDF worker failed job_id=%s document_id=%s", job_id, document_id)
        await run_blocking(
            lambda: mark_document_pdf_failed(
                document_id,
                f"Automatic PDF pipeline failed (worker): {str(exc)[:1200]}. {_REUPLOAD_HINT}",
            )
        )
        write_upload_progress(upload_id, 100, "failed", f"Automatic processing failed. {_REUPLOAD_HINT}", user_id=user_id)
        raise
    finally:
        stop_heartbeat.set()
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        if local_pdf_path.is_file() and str(local_pdf_path).startswith(str(_INCOMING_DIR)):
            try:
                local_pdf_path.unlink(missing_ok=True)
            except Exception:
                pass
