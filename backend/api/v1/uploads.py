from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Query, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import os
import uuid
import tempfile
import logging
import asyncio
from pathlib import Path
import re
import shutil

from services.supabase_storage import SupabaseStorage
from services.admin_service import AdminService
from services.pdf_job_queue import pdf_job_queue
from services.pdf_job_recovery import mark_document_pdf_failed, touch_pdf_job_heartbeat
from services.pdf_ingest_service import (
    PdfNoStructuredContentError,
    PdfQualityCheckError,
    run_pdf_pipeline_and_ingest_from_path,
)
from services.async_utils import run_blocking
from middleware.subscription_check import get_current_user
from services.subscription_service import SubscriptionService
from services.codebooks import resolve_upload_codebook, resolve_upload_standard_family, VALID_DISCIPLINES
from services.standard_families import STANDARD_FAMILIES
from pdf_pipeline.services.pdf_text_preflight import (
    ScannedPdfRejectedError,
    validate_digital_pdf_upload,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_REUPLOAD_HINT = "Remove this file and upload again."


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


async def _run_user_pdf_pipeline_job(
    *,
    supabase_storage: SupabaseStorage,
    local_pdf_path: Path,
    document_id: str,
    user_id: str,
    upload_id: str,
    filename: str,
    effective_codebook: str,
    standard_family: str,
    modal_remote_enabled: bool,
    enhancement: bool,
    mark_processing: bool = True,
) -> None:
    """
    Background job: run full PDF pipeline + ingest. Only marks ready_for_search on full success.
    """
    def _on_queue_tick(state: Dict[str, Any]) -> None:
        ahead = int(state.get("queue_ahead") or 0)
        global_running = int(state.get("global_running") or 0)
        if ahead > 0:
            msg = (
                f"Waiting in server queue (position ~{ahead + 1}; "
                f"{global_running} PDF job(s) running globally)..."
            )
        else:
            msg = (
                f"Waiting for a processing slot ({global_running} PDF job(s) running globally)..."
            )
        _write_progress(upload_id, 52, "queued", msg)

    try:
        _write_progress(upload_id, 52, "queued", "Queued for PDF processing")
        logger.info(
            "PDF background job starting document_id=%s modal_remote=%s enhancement=%s path=%s",
            document_id,
            modal_remote_enabled,
            enhancement,
            local_pdf_path,
        )
        await pdf_job_queue.run(
            user_id,
            lambda: _run_user_pdf_pipeline_job_inner(
                supabase_storage=supabase_storage,
                local_pdf_path=local_pdf_path,
                document_id=document_id,
                user_id=user_id,
                upload_id=upload_id,
                filename=filename,
                effective_codebook=effective_codebook,
                standard_family=standard_family,
                modal_remote_enabled=modal_remote_enabled,
                enhancement=enhancement,
                mark_processing=mark_processing,
            ),
            document_id=document_id,
            upload_id=upload_id,
            on_queue_tick=_on_queue_tick,
        )
    except Exception as e:
        logger.exception("Background PDF job crashed document_id=%s", document_id)
        try:
            await run_blocking(
                lambda: mark_document_pdf_failed(
                    document_id,
                    f"Automatic PDF pipeline failed (background): {str(e)[:1200]}. {_REUPLOAD_HINT}",
                )
            )
        except Exception:
            pass
        _write_progress(
            upload_id,
            100,
            "failed",
            f"Automatic processing failed. {_REUPLOAD_HINT}",
        )


async def _run_user_pdf_pipeline_job_inner(
    *,
    supabase_storage: SupabaseStorage,
    local_pdf_path: Path,
    document_id: str,
    user_id: str,
    upload_id: str,
    filename: str,
    effective_codebook: str,
    standard_family: str,
    modal_remote_enabled: bool,
    enhancement: bool,
    mark_processing: bool = True,
) -> None:
    _write_progress(upload_id, 55, "extracting_pdf", "Running PDF extract + ingest (background)")
    stop_heartbeat = asyncio.Event()
    heartbeat_task = asyncio.create_task(_pdf_job_heartbeat_loop(document_id, stop_heartbeat))
    try:
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

        _write_progress(
            upload_id,
            100,
            "completed",
            (
                f"Document ready for search (chunks={ingest.get('chunks_count', 0)}, "
                f"tables={ingest.get('tables_count', 0)})"
            ),
        )
    except PdfNoStructuredContentError as e:
        logger.warning(
            "Automatic PDF processing: no extractable content document_id=%s filename=%s",
            document_id,
            filename,
        )
        await run_blocking(
            lambda: mark_document_pdf_failed(
                document_id,
                (
                    "Automatic processing found no searchable clauses or tables "
                    "(often a scanned/image-only PDF or unsupported layout). "
                    f"Detail: {str(e)[:900]} {_REUPLOAD_HINT}"
                ),
            )
        )
        _write_progress(upload_id, 100, "failed", f"No searchable content found. {_REUPLOAD_HINT}")
    except PdfQualityCheckError as e:
        logger.warning("Automatic PDF processing: quality check failed document_id=%s: %s", document_id, e)
        await run_blocking(
            lambda: mark_document_pdf_failed(
                document_id,
                f"Automatic PDF pipeline quality check failed: {str(e)[:1200]}. {_REUPLOAD_HINT}",
            )
        )
        _write_progress(
            upload_id,
            100,
            "failed",
            f"Processing failed quality checks. {_REUPLOAD_HINT}",
        )
    except Exception as e:
        logger.exception("Automatic PDF processing failed for document %s", document_id)
        await run_blocking(
            lambda: mark_document_pdf_failed(
                document_id,
                f"Automatic PDF pipeline failed: {str(e)[:1200]}. {_REUPLOAD_HINT}",
            )
        )
        _write_progress(
            upload_id,
            100,
            "failed",
            f"Automatic processing failed. {_REUPLOAD_HINT}",
        )
    finally:
        stop_heartbeat.set()
        heartbeat_task.cancel()
        try:
            if local_pdf_path.exists():
                local_pdf_path.unlink()
        except Exception:
            pass


def _infer_codebook_from_filename(filename: Optional[str], declared: str) -> str:
    """
    If the form still has the default AS3000 but the PDF name clearly indicates AS3017,
    use AS3017 so chunks and clause_units sync to dataset_id as3017_2007 (matches chat Standard).
    """
    decl = (declared or "").strip() or "AS3000"
    if decl.upper() != "AS3000":
        return decl
    fn = filename or ""
    if re.search(r"(?:AS/?NZS?\s*)?3017\b", fn, re.IGNORECASE) or re.search(
        r"\b3017\s*[:_]?\s*2007", fn, re.IGNORECASE
    ):
        return "AS3017"
    return decl


from services.upload_progress import UPLOAD_PROGRESS, progress_path, write_upload_progress

_write_progress = write_upload_progress
_progress_path = progress_path


class UploadResponse(BaseModel):
    document_id: str
    message: str
    status: str
    storage_url: str
    upload_id: Optional[str] = None
    # Remove processing_time, chunks_count, chunks for admin workflow

class ProgressResponse(BaseModel):
    upload_id: str
    progress: int
    status: str
    message: Optional[str] = None

@router.get("/progress", response_model=ProgressResponse)
async def get_upload_progress(
    upload_id: str = Query(...),
    current_user: str = Depends(get_current_user),
):
    # Prefer file-backed progress to avoid cross-process memory issues
    info = None
    try:
        path = _progress_path(upload_id)
        if os.path.exists(path):
            import json
            with open(path, 'r', encoding='utf-8') as f:
                info = json.load(f)
    except Exception:
        info = None
    if info is None:
        info = UPLOAD_PROGRESS.get(upload_id, {"progress": 0, "status": "unknown", "message": ""})
    stored_user = info.get("user_id")
    if stored_user and stored_user != current_user:
        raise HTTPException(status_code=403, detail="Access denied")
    return ProgressResponse(
        upload_id=upload_id,
        progress=int(info.get("progress", 0)),
        status=str(info.get("status", "unknown")),
        message=str(info.get("message", ""))
    )

@router.post("/", response_model=UploadResponse)
async def process_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: Optional[str] = Form(None),  # Deprecated: user identity comes from the bearer token.
    discipline: str = Form('electrical'),  # Discipline selection
    codebook: str = Form('AS3000'),  # Standard / codebook id
    codebook_custom: Optional[str] = Form(None),  # When codebook=OTHER
    codebook_label: Optional[str] = Form(None),  # Optional display name for LLM/search
    standard_family: str = Form('AS_NZS'),  # Parser family: AS_NZS, ISO, IEC, ASTM, NFPA, API, IEEE, NBN
    upload_id: Optional[str] = Form(None),
    current_user: str = Depends(get_current_user),
):
    """
    User PDF upload is disabled in this product.
    """
    raise HTTPException(
        status_code=403,
        detail="PDF upload is not available in this product.",
    )
    try:
        # Prepare progress tracking
        if not upload_id:
            upload_id = str(uuid.uuid4())
        _write_progress(upload_id, 1, "received", "Upload received", user_id=current_user)

        if user_id and user_id != current_user:
            logger.warning(
                "Ignoring upload form user_id=%s because authenticated user is %s",
                user_id,
                current_user,
            )
        user_id = current_user

        # Validate file type
        if not file.filename.endswith('.pdf'):
            raise HTTPException(status_code=400, detail="Only PDF files are allowed")

        subscription_service = SubscriptionService()
        can_upload = await subscription_service.enforce_usage_limits(user_id, 'upload_document')
        if not can_upload:
            access = await subscription_service.check_user_access(user_id)
            max_docs = access.get('max_documents')
            current_docs = access.get('documents_uploaded', 0)
            if max_docs is None:
                error_msg = "Unable to verify upload limit. Please try again."
            else:
                error_msg = (
                    f"Document upload limit reached ({current_docs}/{max_docs} active standards). "
                    "Please delete an existing standard or upgrade your subscription."
                )
            raise HTTPException(status_code=403, detail=error_msg)

        discipline_norm = (discipline or "electrical").strip().lower()
        if discipline_norm not in VALID_DISCIPLINES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid discipline. Choose one of: {', '.join(sorted(VALID_DISCIPLINES))}",
            )

        try:
            effective_codebook, codebook_display = resolve_upload_codebook(
                codebook,
                codebook_custom=codebook_custom,
                codebook_label=codebook_label,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        effective_standard_family = resolve_upload_standard_family(
            effective_codebook,
            standard_family=standard_family,
            codebook_label=codebook_display,
        )
        if effective_standard_family not in STANDARD_FAMILIES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid standard_family. Choose one of: {', '.join(sorted(STANDARD_FAMILIES))}",
            )

        original_filename = file.filename or "upload.pdf"
        
        # Validate file size (80 MiB — large standards e.g. AS/NZS 3000)
        MAX_FILE_SIZE = 80 * 1024 * 1024
        if file.size and file.size > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File size exceeds 80MB limit")
        
        # Initialize database storage
        _write_progress(upload_id, 5, "initializing", "Initializing storage")
        supabase_storage = SupabaseStorage()
        
        # Save uploaded file temporarily
        _write_progress(upload_id, 10, "reading_file", "Reading uploaded file")
        content = await file.read()

        # Validate file size after reading content
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File size exceeds 80MB limit")

        def _write_temp_pdf(buf: bytes) -> str:
            # Write off-thread so a slow disk does not block the event loop.
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tf:
                tf.write(buf)
                return tf.name

        temp_file_path = await run_blocking(_write_temp_pdf, content)
        _write_progress(upload_id, 15, "stored", "File stored temporarily")
        job_pdf_path: Optional[Path] = None

        from pdf_pipeline.settings import settings as pdf_settings

        if pdf_settings.pdf_upload_reject_scanned:
            _write_progress(upload_id, 18, "validating", "Checking PDF text layer")
            try:
                await run_blocking(
                    lambda: validate_digital_pdf_upload(
                        temp_file_path,
                        min_avg_chars_per_page=pdf_settings.pdf_upload_min_avg_chars_per_page,
                        max_sample_pages=pdf_settings.pdf_pipeline_preflight_max_sample_pages,
                    )
                )
            except ScannedPdfRejectedError as e:
                try:
                    os.unlink(temp_file_path)
                except Exception:
                    pass
                _write_progress(upload_id, 100, "failed", str(e))
                raise HTTPException(status_code=400, detail=str(e))
        
        try:
            # Upload file to S3 storage only
            storage_url = None
            if not supabase_storage.s3_storage.s3_client:
                raise HTTPException(status_code=500, detail="S3 storage not available. Please check S3 configuration.")
            
            _write_progress(upload_id, 20, "uploading_s3", "Uploading to S3 storage")
            # boto3 is sync — run it on the threadpool so the event loop keeps
            # serving other requests (auth checks, usage-stats, other uploads).
            storage_url = await run_blocking(
                supabase_storage.s3_storage.upload_file,
                temp_file_path,
                user_id,
                file.filename,
            )
            
            if not storage_url:
                raise HTTPException(status_code=500, detail="Failed to upload file to S3 storage")

            _write_progress(upload_id, 30, "uploaded", "File uploaded to S3 storage successfully")
            
            # Save document metadata to main documents table
            _write_progress(upload_id, 40, "saving_metadata", "Saving document metadata")
            auto_pdf = os.getenv("AUTO_PROCESS_PDF_ON_UPLOAD", "true").lower() in ("1", "true", "yes")

            has_modal_url = bool(os.getenv("MODAL_ENDPOINT", "").strip())
            modal_remote_enabled = has_modal_url and not pdf_settings.disable_modal
            logger.info(
                "Upload PDF pipeline: auto_pdf=%s modal_url=%s disable_modal=%s modal_remote=%s size=%s",
                auto_pdf,
                has_modal_url,
                pdf_settings.disable_modal,
                modal_remote_enabled,
                len(content),
            )
            # Run in-process PDFProcessor when Modal is usable, or when explicitly testing with DISABLE_MODAL (local PyMuPDF clauses).
            run_auto_pdf_pipeline = auto_pdf and len(content) <= pdf_settings.max_file_size and (
                modal_remote_enabled or pdf_settings.disable_modal
            )

            enhancement = pdf_settings.pdf_pipeline_enhancement
            fits_modal_size = len(content) <= pdf_settings.max_file_size

            initial_status = "pdf_processing" if run_auto_pdf_pipeline else "pending_admin_review"
            document_data = {
                "user_id": user_id,
                "filename": original_filename,
                "original_filename": original_filename,
                "codebook": effective_codebook,
                "source": codebook_display,
                "discipline": discipline_norm,
                "standard_family": effective_standard_family,
                "processing_model": "admin",
                "file_size": len(content),
                "storage_url": storage_url,
                "status": initial_status,
            }
            try:
                from services.company_service import get_active_company_id_for_user

                company_id = get_active_company_id_for_user(user_id)
                if company_id:
                    document_data["company_id"] = company_id
            except Exception as company_err:
                logger.warning("Could not attach company_id to upload: %s", company_err)

            try:
                result = await run_blocking(
                    lambda: supabase_storage.supabase.table("documents").insert(document_data).execute()
                )
            except Exception as e:
                err = str(e).lower()
                # Older DBs may lack source / original_filename columns
                if "source" in err or "original_filename" in err or "column" in err:
                    slim = {
                        k: v
                        for k, v in document_data.items()
                        if k not in ("source", "original_filename", "standard_family")
                    }
                    logger.warning("Retrying document insert without source/original_filename: %s", e)
                    result = await run_blocking(
                        lambda: supabase_storage.supabase.table("documents").insert(slim).execute()
                    )
                elif initial_status == "pdf_processing" and (
                    "23514" in str(e)
                    or "documents_status_check" in err
                    or "check constraint" in err
                ):
                    logger.warning(
                        "Insert with status=pdf_processing failed (DB check constraint). "
                        "Apply migration for pdf_processing or rows will use admin_processing. Error: %s",
                        e,
                    )
                    document_data["status"] = "admin_processing"
                    result = await run_blocking(
                        lambda: supabase_storage.supabase.table("documents").insert(document_data).execute()
                    )
                else:
                    raise
            if not result.data:
                raise HTTPException(status_code=500, detail="Failed to save document metadata")
            document_id = result.data[0]["id"]

            if run_auto_pdf_pipeline:
                os.makedirs("processed_files/_incoming", exist_ok=True)
                job_pdf_path = Path("processed_files") / "_incoming" / f"{document_id}.pdf"
                try:
                    await run_blocking(shutil.copyfile, temp_file_path, str(job_pdf_path))
                except Exception as e:
                    await run_blocking(
                        lambda: supabase_storage.supabase.table("documents")
                        .update(
                            {
                                "status": "failed",
                                "admin_notes": f"Failed to stage PDF for background processing: {str(e)[:1500]}",
                            }
                        )
                        .eq("id", document_id)
                        .execute()
                    )
                    _write_progress(upload_id, 100, "failed", "Could not stage PDF for processing.")
                    raise HTTPException(status_code=500, detail="Could not stage PDF for processing. Please try again.")
                try:
                    os.unlink(temp_file_path)
                except Exception:
                    pass

                progress_msg = (
                    "Your upload is saved. Extracting clauses/tables in the background (this can take a long time)..."
                    if modal_remote_enabled
                    else "Your upload is saved. Running local text extraction in the background..."
                )
                _write_progress(upload_id, 45, "processing", progress_msg)
                background_tasks.add_task(
                    _run_user_pdf_pipeline_job,
                    supabase_storage=supabase_storage,
                    local_pdf_path=job_pdf_path,
                    document_id=document_id,
                    user_id=user_id,
                    upload_id=upload_id,
                    filename=file.filename,
                    effective_codebook=effective_codebook,
                    standard_family=effective_standard_family,
                    modal_remote_enabled=modal_remote_enabled,
                    enhancement=enhancement,
                    mark_processing=(document_data.get("status") == "pdf_processing"),
                )
                return UploadResponse(
                    document_id=document_id,
                    message=(
                        "Upload saved. Your PDF is processing in the background — you can keep using the app. "
                        "It will show as ready when ingestion finishes (no partial search results)."
                    ),
                    status="processing",
                    storage_url=storage_url or "",
                    upload_id=upload_id,
                )

            # Legacy: no synchronous Modal run (not configured, oversized PDF, or AUTO_PROCESS_PDF_ON_UPLOAD=false)
            _write_progress(upload_id, 50, "adding_to_queue", "Queued for processing pipeline")
            admin_service = AdminService()
            await admin_service.add_to_admin_queue(
                user_id=user_id,
                document_id=document_id,
                filename=file.filename,
                file_size=len(content),
                storage_url=storage_url,
                priority=1,
                codebook=effective_codebook,
            )
            _write_progress(upload_id, 60, "updating_status", "Updating document status")
            await run_blocking(
                lambda: supabase_storage.supabase.table("documents")
                .update({"status": "pending_admin_review"})
                .eq("id", document_id)
                .execute()
            )

            reason = []
            if not run_auto_pdf_pipeline:
                if not auto_pdf:
                    reason.append("AUTO_PROCESS_PDF_ON_UPLOAD is false")
                elif not fits_modal_size:
                    reason.append(
                        f"file larger than {pdf_settings.max_file_size // (1024 * 1024)}MB pipeline limit"
                    )
                elif not has_modal_url and not pdf_settings.disable_modal:
                    reason.append(
                        "MODAL_ENDPOINT not set (set it for Modal, or DISABLE_MODAL=true for local clause extraction)"
                    )
            suffix = f" ({'; '.join(reason)})" if reason else ""

            _write_progress(upload_id, 95, "finalizing", "Finalizing upload")
            _write_progress(upload_id, 100, "completed", "Upload completed successfully")

            return UploadResponse(
                document_id=document_id,
                message=(
                    "Upload complete. Your document is queued for ingestion"
                    + suffix
                    + "."
                ),
                status="pending_admin_review",
                storage_url=storage_url or "",
                upload_id=upload_id,
            )
            
        finally:
            # Clean up temporary file
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
                
    except HTTPException:
        raise
    except Exception as e:
        try:
            if upload_id:
                _write_progress(upload_id, 100, "failed", str(e))
        except Exception:
            pass
        logger.exception("Upload endpoint failed")
        raise HTTPException(
            status_code=500,
            detail="Something went wrong while processing your upload. Please try again. "
            "If it keeps happening, contact support with your filename and the approximate time.",
        )