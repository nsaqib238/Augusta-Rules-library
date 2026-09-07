from fastapi import APIRouter, HTTPException, Depends, Query, Body, UploadFile, File, Form
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Set, Tuple
import requests
import io
import json
import csv
import os
import uuid
import tempfile
import logging
import re
from pathlib import Path
from services.supabase_client import get_supabase_client
from services.supabase_storage import SupabaseStorage
from services.schema_manager import SchemaManager
from services.admin_service import AdminService
from services.pdf_ingest_parsers import parse_uploaded_chunks_content, parse_uploaded_tables_content
from services.pdf_ingest_service import run_pdf_pipeline_and_ingest_from_path
from middleware.subscription_check import check_admin_access

router = APIRouter()

logger = logging.getLogger(__name__)

# Pydantic models
class ChunkData(BaseModel):
    text: str
    clause_number: Optional[str] = None
    page_number: Optional[int] = None
    entities: Optional[List[str]] = []
    detailed_analysis: Optional[Dict] = {}
    metadata: Optional[Dict] = {}

class UploadChunksRequest(BaseModel):
    user_id: str
    document_id: str
    chunks: List[ChunkData]
    admin_notes: Optional[str] = ""

class AdminStatsResponse(BaseModel):
    total_users: int
    total_documents: int
    pending_documents: int
    completed_documents: int
    total_chunks: int


class OrphanStorageDeleteRequest(BaseModel):
    bucket: str = Field(..., description="Supabase Storage bucket name")
    path: str = Field(..., description="Object path within bucket (no leading slash)")


class OrphanChunksDeleteRequest(BaseModel):
    document_id: str = Field(..., description="document_id whose orphan chunks should be deleted")


def _extract_supabase_object_bucket_path(storage_url: str) -> Optional[Tuple[str, str]]:
    """
    Extract (bucket, path) from Supabase Storage object URLs.
    Supports /storage/v1/object/public/... and /storage/v1/object/sign/...
    Returns None for non-object URLs (e.g. S3 gateway URLs).
    """
    u = (storage_url or "").strip()
    if not u or "/storage/v1/object/" not in u:
        return None
    try:
        after = u.split("/storage/v1/object/", 1)[1]
        after = after.split("?", 1)[0]
        parts = [p for p in after.split("/") if p]
        if len(parts) >= 3 and parts[0] in ("public", "sign", "authenticated"):
            bucket = parts[1]
            path = "/".join(parts[2:])
            return (bucket, path) if bucket and path else None
        if len(parts) >= 2:
            bucket = parts[0]
            path = "/".join(parts[1:])
            return (bucket, path) if bucket and path else None
        return None
    except Exception:
        return None


def _list_storage_objects_recursive(
    supabase,
    *,
    bucket: str,
    prefix: str,
    max_depth: int = 4,
    limit_per_dir: int = 200,
) -> List[Dict[str, Any]]:
    """
    Best-effort recursive listing for Supabase Storage prefixes.
    Supabase list() is directory-based; items without `id` are treated as folders.
    """
    out: List[Dict[str, Any]] = []
    seen_dirs: Set[str] = set()

    def walk(path: str, depth: int) -> None:
        if depth > max_depth:
            return
        p = (path or "").strip().strip("/")
        if p in seen_dirs:
            return
        seen_dirs.add(p)
        try:
            items = supabase.storage.from_(bucket).list(p, {"limit": limit_per_dir, "offset": 0})
        except Exception:
            return
        if not isinstance(items, list):
            return
        for it in items:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "").strip()
            if not name:
                continue
            child_path = f"{p}/{name}".strip("/") if p else name
            if it.get("id"):
                row = dict(it)
                row["path"] = child_path
                out.append(row)
            else:
                walk(child_path, depth + 1)

    walk(prefix, 0)
    return out

class UserDocumentInfo(BaseModel):
    user_id: str
    document_id: str
    filename: str
    status: str
    admin_status: str
    created_at: str

class AdminQueueItem(BaseModel):
    id: str
    user_id: str
    document_id: str
    filename: str
    file_size: Optional[int] = None
    storage_url: Optional[str] = None
    priority: int = 1
    admin_notes: Optional[str] = None
    assigned_admin_id: Optional[str] = None
    status: str
    created_at: str
    updated_at: str

class DocumentRegistryOwner(BaseModel):
    id: Optional[str]
    email: Optional[str]
    full_name: Optional[str]
    role: Optional[str]

class DocumentRegistryQueue(BaseModel):
    status: Optional[str]
    priority: Optional[int]
    admin_notes: Optional[str]
    assigned_admin_id: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]

class DocumentRegistryVersion(BaseModel):
    document_id: str
    created_at: Optional[str]
    status: Optional[str]
    admin_status: Optional[str]
    is_current: bool

class DocumentRegistryEntry(BaseModel):
    id: str
    user_id: Optional[str]
    filename: Optional[str]
    codebook: Optional[str]
    discipline: Optional[str]
    status: Optional[str]
    admin_status: Optional[str]
    chunk_count: Optional[int]
    refined_chunk_count: Optional[int]
    file_size: Optional[int]
    storage_url: Optional[str]
    processing_model: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    admin_processed_at: Optional[str]
    owner: Optional[DocumentRegistryOwner]
    queue: Optional[DocumentRegistryQueue]
    duplicate_key: Optional[str]
    duplicate_count: int
    versions: List[DocumentRegistryVersion]

class DocumentRegistrySummary(BaseModel):
    total_documents: int
    unique_users: int
    ready_documents: int
    pending_documents: int
    duplicate_groups: int
    total_duplicates: int

class DocumentRegistryResponse(BaseModel):
    summary: DocumentRegistrySummary
    documents: List[DocumentRegistryEntry]

class AdminOverviewStats(BaseModel):
    total_users: int
    total_documents: int
    total_chunks: int
    active_users: int
    ready_documents: int
    pending_documents: int

class AdminOverviewUser(BaseModel):
    id: str
    email: Optional[str]
    full_name: Optional[str]
    role: Optional[str]
    created_at: Optional[str]
    last_activity: Optional[str]
    document_count: int
    chunk_count: int
    statuses: List[str]

class AdminOverviewResponse(BaseModel):
    stats: AdminOverviewStats
    users: List[AdminOverviewUser]

class AdminUserOption(BaseModel):
    id: str
    email: Optional[str]
    full_name: Optional[str]
    role: Optional[str]
    created_at: Optional[str]

# AdminCompanyMembership removed - company features no longer supported

class AdminUserDatabaseResponse(BaseModel):
    profile: Optional[Dict[str, Any]] = None
    # Company fields removed - kept for backward compatibility but always empty
    memberships: List[Dict[str, Any]] = Field(default_factory=list)
    company_employees: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    company_invitations: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    documents: List[Dict[str, Any]] = Field(default_factory=list)
    subscriptions: List[Dict[str, Any]] = Field(default_factory=list)
    admin_queue: List[Dict[str, Any]] = Field(default_factory=list)
    invitations: List[Dict[str, Any]] = Field(default_factory=list)
    stats: Dict[str, Any] = Field(default_factory=dict)

@router.get("/orphans/summary")
async def get_orphans_summary(
    limit: int = Query(200, ge=1, le=1000),
    current_admin: str = Depends(check_admin_access),
):
    """
    Admin: find orphaned artifacts.

    - Storage uploads: objects under documents/uploads/* not referenced by any documents.storage_url
      (only for Supabase object URLs; S3 gateway URLs can't be reliably cross-checked here).
    - Benchmark artifacts: objects under <BENCHMARK_SUPABASE_BUCKET>/benchmarks/*/benchmark_qna.json
      that do not correspond to an existing (user_id, document_id) in documents.
    - Chunks: rows in chunks whose document_id is missing from documents.
    """
    supabase = get_supabase_client()

    # 1) Document references
    docs_result = supabase.table("documents").select("id,user_id,storage_url").limit(10000).execute()
    docs = list(docs_result.data or [])

    existing_doc_ids: Set[str] = set()
    expected_benchmark_paths: Set[str] = set()
    referenced_storage_paths: Set[Tuple[str, str]] = set()

    for d in docs:
        did = str(d.get("id") or "").strip()
        uid = str(d.get("user_id") or "").strip()
        if did:
            existing_doc_ids.add(did)
        if uid and did:
            expected_benchmark_paths.add(f"benchmarks/{uid}/{did}/benchmark_qna.json")
        bu = _extract_supabase_object_bucket_path(str(d.get("storage_url") or ""))
        if bu:
            referenced_storage_paths.add(bu)

    notes: List[str] = []
    if not referenced_storage_paths:
        notes.append(
            "No Supabase object URLs found in documents.storage_url; upload orphan detection may be incomplete if you use S3 gateway URLs."
        )

    # 2) Storage: uploads/
    storage_orphans: List[Dict[str, Any]] = []
    uploads_bucket = "documents"
    upload_objects = _list_storage_objects_recursive(
        supabase, bucket=uploads_bucket, prefix="uploads", max_depth=6, limit_per_dir=200
    )
    for it in upload_objects:
        path = str(it.get("path") or "").strip()
        if not path:
            continue
        referenced = (uploads_bucket, path) in referenced_storage_paths
        if referenced_storage_paths and referenced:
            continue
        storage_orphans.append(
            {
                "bucket": uploads_bucket,
                "path": path,
                "name": it.get("name"),
                "size": it.get("metadata", {}).get("size") if isinstance(it.get("metadata"), dict) else it.get("size"),
                "updated_at": it.get("updated_at") or it.get("created_at"),
                "referenced": referenced if referenced_storage_paths else None,
                "note": None if referenced_storage_paths else "reference unknown (storage_url not in Supabase object URL form)",
            }
        )

    # 3) Storage: benchmarks/
    benchmark_bucket = (os.getenv("BENCHMARK_SUPABASE_BUCKET") or "documents").strip() or "documents"
    benchmark_orphans: List[Dict[str, Any]] = []
    bench_objects = _list_storage_objects_recursive(
        supabase, bucket=benchmark_bucket, prefix="benchmarks", max_depth=6, limit_per_dir=200
    )
    for it in bench_objects:
        path = str(it.get("path") or "").strip()
        if not path or not path.endswith("/benchmark_qna.json"):
            continue
        if path in expected_benchmark_paths:
            continue
        benchmark_orphans.append(
            {
                "bucket": benchmark_bucket,
                "path": path,
                "name": it.get("name"),
                "size": it.get("metadata", {}).get("size") if isinstance(it.get("metadata"), dict) else it.get("size"),
                "updated_at": it.get("updated_at") or it.get("created_at"),
                "referenced": False,
                "note": "no matching documents row (user_id, document_id)",
            }
        )

    # 4) DB: orphan chunks
    chunk_result = supabase.table("chunks").select("document_id").limit(50000).execute()
    chunk_rows = list(chunk_result.data or [])
    counts: Dict[str, int] = {}
    for r in chunk_rows:
        did = str(r.get("document_id") or "").strip()
        if did:
            counts[did] = counts.get(did, 0) + 1
    chunk_orphans: List[Dict[str, Any]] = []
    for did, cnt in counts.items():
        if did not in existing_doc_ids:
            chunk_orphans.append({"document_id": did, "chunks_count": cnt})
    chunk_orphans.sort(key=lambda x: -int(x.get("chunks_count") or 0))

    return {
        "storage_orphans": storage_orphans[:limit],
        "benchmark_orphans": benchmark_orphans[:limit],
        "chunk_orphans": chunk_orphans[:limit],
        "notes": notes,
    }


@router.post("/orphans/storage/delete")
async def delete_orphan_storage_object(
    request: OrphanStorageDeleteRequest,
    current_admin: str = Depends(check_admin_access),
):
    supabase = get_supabase_client()
    bucket = (request.bucket or "").strip()
    path = (request.path or "").strip().lstrip("/")
    if not bucket or not path:
        raise HTTPException(status_code=400, detail="bucket and path required")
    try:
        supabase.storage.from_(bucket).remove([path])
        return {"ok": True, "bucket": bucket, "path": path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Storage delete failed: {e}")


@router.post("/orphans/chunks/delete")
async def delete_orphan_chunks(
    request: OrphanChunksDeleteRequest,
    current_admin: str = Depends(check_admin_access),
):
    supabase = get_supabase_client()
    did = (request.document_id or "").strip()
    if not did:
        raise HTTPException(status_code=400, detail="document_id required")
    try:
        res = supabase.table("chunks").delete().eq("document_id", did).execute()
        deleted = len(res.data) if res.data else 0
        return {"ok": True, "document_id": did, "deleted": deleted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chunk delete failed: {e}")

@router.get("/pending-documents", response_model=List[AdminQueueItem])
async def get_pending_documents(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_admin: str = Depends(check_admin_access)
):
    """Get all pending documents from admin queue"""
    try:
        admin_service = AdminService()
        
        # Get pending documents from admin queue
        pending_docs = await admin_service.get_pending_documents(None, limit, offset)
        
        return [AdminQueueItem(**doc) for doc in pending_docs]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get pending documents: {str(e)}")

@router.get("/download-pdf/{document_id}")
async def download_pdf(
    document_id: str,
    current_admin: str = Depends(check_admin_access)
):
    """Download PDF file for admin review"""
    try:
        supabase = get_supabase_client()
        
        # Get document info from admin queue
        queue_result = supabase.table('admin_queue').select('storage_url, filename').eq('document_id', document_id).single().execute()
        
        if not queue_result.data:
            raise HTTPException(status_code=404, detail="Document not found in admin queue")
        
        storage_url = queue_result.data['storage_url']
        filename = queue_result.data['filename']
        
        # Generate signed URL for secure access
        from services.supabase_storage import SupabaseStorage
        storage_service = SupabaseStorage()
        
        signed_url = storage_service.s3_storage.generate_signed_url(storage_url, expiration=3600)  # 1 hour expiration
        
        if not signed_url:
            raise HTTPException(status_code=500, detail="Failed to generate signed URL for PDF access")
        
        return {
            "download_url": signed_url,
            "filename": filename,
            "expires_in": 3600,
            "message": "Signed URL generated successfully. URL expires in 1 hour."
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get download URL: {str(e)}")

@router.get("/proxy-pdf/{document_id}")
async def proxy_pdf(
    document_id: str,
    current_admin: str = Depends(check_admin_access)
):
    """Proxy PDF file for admin review - streams file directly"""
    try:
        supabase = get_supabase_client()
        
        # Get document info from admin queue
        queue_result = supabase.table('admin_queue').select('storage_url, filename').eq('document_id', document_id).single().execute()
        
        if not queue_result.data:
            raise HTTPException(status_code=404, detail="Document not found in admin queue")
        
        storage_url = queue_result.data['storage_url']
        filename = queue_result.data['filename']
        
        # Extract file path from storage URL for Supabase storage API
        # URL format: https://project.supabase.co/storage/v1/s3/bucket/path
        parts = storage_url.split('/storage/v1/s3/')
        if len(parts) != 2:
            raise HTTPException(status_code=500, detail="Invalid storage URL format")
        
        # Remove bucket name from path (documents/user_id/filename.pdf -> user_id/filename.pdf)
        path_with_bucket = parts[1]
        if path_with_bucket.startswith('documents/'):
            file_path = path_with_bucket[10:]  # Remove 'documents/' prefix
        else:
            file_path = path_with_bucket
        
        # Use Supabase storage API with service role key
        try:
            # Download file using Supabase storage API
            response = supabase.storage.from_('documents').download(file_path)
            
            if not response:
                raise HTTPException(status_code=404, detail="File not found in storage")
            
            # Create a streaming response
            def generate():
                # Convert bytes to chunks
                chunk_size = 8192
                for i in range(0, len(response), chunk_size):
                    yield response[i:i + chunk_size]
            
            return StreamingResponse(
                generate(),
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f"inline; filename={filename}",
                    "Content-Type": "application/pdf"
                }
            )
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to download file from database storage: {str(e)}")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to proxy PDF: {str(e)}")

@router.post("/upload-processed-chunks-file")
async def upload_processed_chunks_file(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    document_id: str = Form(...),
    admin_notes: str = Form(""),
    current_admin: str = Depends(check_admin_access)
):
    """Upload processed chunks from uploaded file"""
    try:
        # Validate file type
        if not file.filename.endswith(('.json', '.csv')):
            raise HTTPException(status_code=400, detail="Only JSON and CSV files are supported")
        
        # Read file content
        content = await file.read()
        
        # Parse chunks from file content
        chunks_data = await parse_uploaded_chunks_content(content, file.filename, document_id, user_id)
        
        if not chunks_data:
            raise HTTPException(status_code=400, detail="No valid chunks found in file")
        
        # Insert chunks into main chunks table
        supabase = get_supabase_client()
        
        # Upload chunks in batches to avoid timeout
        batch_size = 100
        total_uploaded = 0
        
        for i in range(0, len(chunks_data), batch_size):
            batch = chunks_data[i:i + batch_size]
            
            try:
                result = supabase.table('chunks').insert(batch).execute()
                if result.data:
                    total_uploaded += len(batch)
                else:
                    raise Exception(f"Failed to insert batch {i//batch_size + 1}")
                    
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to insert chunks batch: {str(e)}")

        from services.chunk_embedding_service import sync_document_embeddings

        try:
            sync_document_embeddings(document_id, user_id)
        except Exception as e:
            logger.warning("Embedding sync after admin chunk upload failed: %s", e)

        # Update document status
        supabase.table('documents').update({
            "status": "ready_for_search",
            "chunk_count": total_uploaded
        }).eq('id', document_id).execute()
        
        supabase.table('admin_queue').update({
            "status": "completed",
            "admin_notes": f"{admin_notes}\n\nProcessed chunks uploaded from file: {file.filename}. {total_uploaded} chunks added."
        }).eq('document_id', document_id).execute()
        
        return {
            "message": f"Successfully uploaded {total_uploaded} chunks from file",
            "chunks_count": total_uploaded,
            "document_id": document_id,
            "user_id": user_id,
            "filename": file.filename,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload processed chunks file: {str(e)}")


@router.post("/documents/{document_id}/sync-embeddings")
async def admin_sync_document_embeddings(
    document_id: str,
    user_id: str = Query(..., description="Owner user_id for the document"),
    current_admin: str = Depends(check_admin_access),
):
    """Backfill chunk_embeddings for an existing document (after patch or re-ingest)."""
    from services.chunk_embedding_service import sync_document_embeddings

    try:
        count = sync_document_embeddings(document_id, user_id)
        return {
            "document_id": document_id,
            "user_id": user_id,
            "embeddings_upserted": count,
        }
    except Exception as e:
        logger.exception("sync-embeddings failed for %s", document_id)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/upload-tables-file")
async def upload_tables_file(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    document_id: str = Form(...),
    admin_notes: str = Form(""),
    current_admin: str = Depends(check_admin_access)
):
    """Upload processed tables from uploaded CSV file"""
    try:
        # Validate file type
        if not file.filename.endswith('.csv'):
            raise HTTPException(status_code=400, detail="Only CSV files are supported for tables")
        
        # Read file content
        content = await file.read()
        
        # Parse tables from file content
        tables_data = await parse_uploaded_tables_content(content, file.filename, document_id, user_id)
        
        if not tables_data:
            raise HTTPException(status_code=400, detail="No valid tables found in file")
        
        # Insert tables into standard_tables table
        supabase = get_supabase_client()
        
        # Upload tables in batches to avoid timeout
        batch_size = 50
        total_uploaded = 0
        
        for i in range(0, len(tables_data), batch_size):
            batch = tables_data[i:i + batch_size]
            
            try:
                result = supabase.table('standard_tables').insert(batch).execute()
                if result.data:
                    total_uploaded += len(batch)
                else:
                    raise Exception(f"Failed to insert table batch {i//batch_size + 1}")
                    
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to insert tables batch: {str(e)}")
        
        # Update admin queue status
        supabase.table('admin_queue').update({
            "status": "completed",
            "admin_notes": f"{admin_notes}\n\nProcessed tables uploaded from file: {file.filename}. {total_uploaded} tables added."
        }).eq('document_id', document_id).execute()
        
        return {
            "message": f"Successfully uploaded {total_uploaded} tables from file",
            "tables_count": total_uploaded,
            "document_id": document_id,
            "user_id": user_id,
            "filename": file.filename
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload tables file: {str(e)}")


@router.post("/upload-pdf-extract-and-ingest")
async def upload_pdf_extract_and_ingest(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    document_id: str = Form(...),
    admin_notes: str = Form(""),
    enable_enhancement: Optional[str] = Form(None),
    replace_existing: str = Form("true"),
    current_admin: str = Depends(check_admin_access),
):
    """
    Run the PDF pipeline (local PyMuPDF), generate clauses.csv + tables.csv in-process,
    then ingest them like the separate CSV uploads.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    from pdf_pipeline.settings import settings as pdf_settings

    if enable_enhancement is not None and str(enable_enhancement).strip() != "":
        enable_enh_bool = str(enable_enhancement).lower() in ("true", "1", "yes")
    else:
        enable_enh_bool = pdf_settings.pdf_pipeline_enhancement
    do_replace = replace_existing.lower() in ("true", "1", "yes")
    max_bytes = pdf_settings.max_file_size

    supabase = get_supabase_client()
    doc_result = supabase.table("documents").select("id, codebook").eq("id", document_id).single().execute()
    if not doc_result.data:
        raise HTTPException(status_code=404, detail="Document not found")
    default_standard = (doc_result.data.get("codebook") or "").strip() or None

    ingest_job_id = str(uuid.uuid4())

    try:
        with tempfile.TemporaryDirectory(prefix=f"pdf_ingest_{ingest_job_id}_") as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / f"{ingest_job_id}.pdf"

            total_written = 0
            chunk_size = 1024 * 1024
            with open(pdf_path, "wb") as buffer:
                while True:
                    piece = await file.read(chunk_size)
                    if not piece:
                        break
                    total_written += len(piece)
                    if total_written > max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"File exceeds maximum size of {max_bytes // (1024 * 1024)}MB",
                        )
                    buffer.write(piece)

            from pdf_pipeline.settings import settings as pdf_settings
            import os
            has_modal = bool((pdf_settings.modal_endpoint or os.getenv("MODAL_ENDPOINT") or "").strip())
            force_modal = has_modal and not pdf_settings.disable_modal

            out = await run_pdf_pipeline_and_ingest_from_path(
                pdf_path,
                document_id,
                user_id,
                default_standard_name=default_standard,
                enable_enhancement=enable_enh_bool,
                replace_existing=do_replace,
                admin_notes=admin_notes,
                update_admin_queue=True,
                force_modal=force_modal,
            )

            return {
                "message": f"Ingested {out['chunks_count']} chunks and {out['tables_count']} tables from PDF.",
                "job_id": out["job_id"],
                "chunks_count": out["chunks_count"],
                "tables_count": out["tables_count"],
                "document_id": document_id,
                "user_id": user_id,
                "pipeline_result": out["pipeline_result"],
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("upload_pdf_extract_and_ingest failed")
        raise HTTPException(status_code=500, detail=f"PDF extract and ingest failed: {str(e)}")


@router.post("/upload-processed-chunks")
async def upload_processed_chunks(
    user_id: str = Body(...),
    document_id: str = Body(...),
    processed_file_path: str = Body(...),
    current_admin: str = Depends(check_admin_access)
):
    """Upload processed chunks from file to user's schema"""
    try:
        schema_manager = SchemaManager()
        
        # Check if user schema exists
        if not await schema_manager.schema_exists(user_id):
            raise HTTPException(status_code=404, detail="User schema not found")
        
        # Parse the processed chunks file
        chunks_data = await parse_processed_chunks_file(processed_file_path, document_id)
        
        if not chunks_data:
            raise HTTPException(status_code=400, detail="No valid chunks found in file")
        
        # Insert chunks into user's schema
        success = await schema_manager.insert_user_chunks(user_id, chunks_data)
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to insert chunks into user schema")
        
        # Update document status to ready_for_search
        await schema_manager.update_user_document(user_id, document_id, {
            "status": "ready_for_search",
            "chunk_count": len(chunks_data),
            "admin_status": "completed"
        })
        
        # Update admin queue status
        supabase = get_supabase_client()
        supabase.table('admin_queue').update({
            "status": "completed",
            "admin_notes": f"Processed chunks uploaded successfully. {len(chunks_data)} chunks added."
        }).eq('document_id', document_id).execute()
        
        return {
            "message": f"Successfully uploaded {len(chunks_data)} chunks to user schema",
            "chunks_count": len(chunks_data),
            "document_id": document_id,
            "user_id": user_id
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload processed chunks: {str(e)}")

async def parse_processed_chunks_file(file_path: str, document_id: str) -> List[Dict]:
    """Parse processed chunks file and convert to database format"""
    try:
        if not os.path.exists(file_path):
            raise Exception(f"File not found: {file_path}")
        
        chunks_data = []
        
        # Try to parse as JSON first
        if file_path.endswith('.json'):
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            for i, chunk in enumerate(data):
                chunk_data = {
                    "document_id": document_id,
                    "chunk_index": i,
                    "text": chunk.get("text", ""),
                    "clause_number": chunk.get("clause_number"),
                    "page_number": chunk.get("page_number"),
                    "entities": json.dumps(chunk.get("entities", {})),
                    "detailed_analysis": json.dumps(chunk.get("detailed_analysis", {})),
                    "metadata": json.dumps(chunk.get("metadata", {}))
                }
                chunks_data.append(chunk_data)
        
        # Try to parse as CSV
        elif file_path.endswith('.csv'):
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                
            if not rows:
                raise Exception("CSV file is empty")
            
            first_row = rows[0]
            has_body_column = 'body' in first_row or 'body_full' in first_row
            
            for i, row in enumerate(rows):
                if has_body_column:
                    # Clause format - use body or body_full for text
                    text_content = row.get("body_full", "") or row.get("body", "")
                    heading = row.get("heading", "")
                    
                    # Parse notes and exceptions if they're JSON arrays
                    notes = row.get("notes", "[]")
                    exceptions = row.get("exceptions", "[]")
                    
                    try:
                        notes_list = json.loads(notes) if notes else []
                    except:
                        notes_list = [notes] if notes else []
                    
                    try:
                        exceptions_list = json.loads(exceptions) if exceptions else []
                    except:
                        exceptions_list = [exceptions] if exceptions else []
                    
                    # ENHANCEMENT: Append notes and exceptions to text for searchability and LLM context
                    enhanced_text = text_content
                    
                    if notes_list and any(notes_list):
                        notes_text = "\n\nNOTES:\n" + "\n".join([f"- {note}" for note in notes_list if note])
                        enhanced_text += notes_text
                    
                    if exceptions_list and any(exceptions_list):
                        exceptions_text = "\n\nEXCEPTIONS:\n" + "\n".join([f"- {exc}" for exc in exceptions_list if exc])
                        enhanced_text += exceptions_text
                    
                    chunk_data = {
                        "document_id": document_id,
                        "chunk_index": i,
                        "text": enhanced_text,  # Now includes notes and exceptions
                        "clause_number": row.get("clause_number", ""),
                        "heading": heading,
                        "page_number": int(row.get("page_number", 0)) if row.get("page_number") else None,
                        "entities": json.dumps({
                            "notes": notes_list,
                            "exceptions": exceptions_list
                        }),
                        "detailed_analysis": json.dumps({}),
                        "metadata": json.dumps({
                            "id": row.get("id", ""),
                            "level": row.get("level", ""),
                            "parent_number": row.get("parent_number", ""),
                            "embeddable": row.get("embeddable", "")
                        })
                    }
                else:
                    # Standard format
                    chunk_data = {
                        "document_id": document_id,
                        "chunk_index": i,
                        "text": row.get("text", ""),
                        "clause_number": row.get("clause_number"),
                        "heading": row.get("heading"),
                        "page_number": int(row.get("page_number", 0)) if row.get("page_number") else None,
                        "entities": row.get("entities", "{}"),
                        "detailed_analysis": row.get("detailed_analysis", "{}"),
                        "metadata": row.get("metadata", "{}")
                    }
                
                chunks_data.append(chunk_data)
        
        else:
            raise Exception("Unsupported file format. Only JSON and CSV files are supported.")
        
        return chunks_data
        
    except Exception as e:
        print(f"Error parsing processed chunks file: {e}")
        import traceback
        traceback.print_exc()
        return []

@router.get("/user-documents/{user_id}", response_model=List[UserDocumentInfo])
async def get_user_documents(
    user_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_admin: str = Depends(check_admin_access)
):
    """Get all documents for a specific user from their schema"""
    try:
        schema_manager = SchemaManager()
        
        # Check if user schema exists
        if not await schema_manager.schema_exists(user_id):
            raise HTTPException(status_code=404, detail="User schema not found")
        
        # Get documents from user's schema
        documents = await schema_manager.get_user_documents(user_id, limit, offset)
        
        return [UserDocumentInfo(**doc) for doc in documents]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get user documents: {str(e)}")

@router.get("/users", response_model=List[Dict])
async def get_all_users_with_documents(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_admin: str = Depends(check_admin_access)
):
    """Get all users with their document counts and details"""
    try:
        schema_manager = SchemaManager()
        
        # Get Supabase client
        supabase = get_supabase_client()
        
        # Plan display names mapping
        PLAN_DISPLAY_NAMES = {
            'sole_trader_free': 'Sole Trader',
            'individual_monthly': 'Individual',
            'professional': 'Professional'
        }
        
        # Get all users from profiles table
        profiles_result = supabase.table('profiles').select('*').order('created_at', desc=True).execute()
        profiles = profiles_result.data if profiles_result.data else []
        
        # Get subscription information (all subscriptions are now individual)
        subscriptions_result = supabase.table('user_subscriptions').select('user_id, plan_name, status, subscription_type').eq('subscription_type', 'individual').execute()
        subscriptions = subscriptions_result.data if subscriptions_result.data else []
        subscription_map = {}
        
        # Map individual subscriptions
        for sub in subscriptions:
            user_id = sub.get('user_id')
            if user_id:
                plan_name = sub.get('plan_name', '')
                subscription_map[user_id] = {
                    'plan_name': plan_name,
                    'plan_display_name': PLAN_DISPLAY_NAMES.get(plan_name, plan_name.replace('_', ' ').title()) if plan_name else None,
                    'status': sub.get('status')
                }
        
        # Get all documents with user info
        documents_result = supabase.table('documents').select('*').order('created_at', desc=True).execute()
        documents = documents_result.data if documents_result.data else []
        
        # Create user map with document counts
        user_map = {}
        
        # Add users from profiles
        for profile in profiles:
            user_id = profile['id']
            subscription_info = subscription_map.get(user_id, {})
            user_map[user_id] = {
                'id': user_id,
                'email': profile.get('email', ''),
                'full_name': profile.get('full_name', ''),
                'role': profile.get('role', 'user'),
                'created_at': profile.get('created_at', ''),
                'document_count': 0,
                'total_size': 0,
                'subscription_type': subscription_info.get('plan_display_name') or subscription_info.get('plan_name') or 'No Subscription',
                'documents': []
            }
        
        # Add users from documents who might not have profiles
        for doc in documents:
            user_id = doc['user_id']
            if user_id not in user_map:
                subscription_info = subscription_map.get(user_id, {})
                user_map[user_id] = {
                    'id': user_id,
                    'email': f'User ID: {user_id[:8]}...',
                    'full_name': f'Document Uploader ({user_id[:8]})',
                    'role': 'user',
                    'created_at': doc.get('created_at', ''),
                    'document_count': 0,
                    'total_size': 0,
                    'subscription_type': subscription_info.get('plan_display_name') or subscription_info.get('plan_name') or 'No Subscription',
                    'documents': []
                }
            
            # Add document info
            user_map[user_id]['document_count'] += 1
            user_map[user_id]['total_size'] += doc.get('file_size', 0)
            user_map[user_id]['documents'].append({
                'id': doc['id'],
                'filename': doc.get('filename', ''),
                'codebook': doc.get('codebook', ''),
                'status': doc.get('status', ''),
                'file_size': doc.get('file_size', 0),
                'created_at': doc.get('created_at', ''),
                'storage_url': doc.get('storage_url', '')
            })
        
        # Convert to list and sort by document count
        users = list(user_map.values())
        users.sort(key=lambda x: x['document_count'], reverse=True)
        
        # Apply pagination
        paginated_users = users[offset:offset + limit]
        
        return paginated_users
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get users: {str(e)}")

@router.delete("/user-documents/{user_id}/{document_id}")
async def delete_user_document(
    user_id: str,
    document_id: str,
    current_admin: str = Depends(check_admin_access)
):
    """Delete a specific user document and all its chunks"""
    try:
        schema_manager = SchemaManager()
        
        # Check if user schema exists
        if not await schema_manager.schema_exists(user_id):
            raise HTTPException(status_code=404, detail="User schema not found")
        
        # Get document info before deletion
        document_info = await schema_manager.get_user_document(user_id, document_id)
        if not document_info:
            raise HTTPException(status_code=404, detail="Document not found")
        
        # Delete from user's schema
        await schema_manager.delete_user_document(user_id, document_id)
        
        supabase = get_supabase_client()

        # Also delete from main chunks/admin queue + documents tables
        # (Some deployments may not have ON DELETE CASCADE from documents -> chunks.)
        try:
            supabase.table("chunks").delete().eq("document_id", document_id).execute()
        except Exception as e:
            logger.warning("Failed to delete chunks for document %s: %s", document_id, e)
        try:
            supabase.table("admin_queue").delete().eq("document_id", document_id).execute()
        except Exception as e:
            logger.warning("Failed to delete admin_queue for document %s: %s", document_id, e)

        supabase = get_supabase_client()
        supabase.table('documents').delete().eq('id', document_id).execute()
        
        # Delete from storage if URL exists
        if document_info.get('storage_url'):
            try:
                SupabaseStorage().delete_file_from_storage(document_info["storage_url"])
            except Exception as e:
                logger.warning(f"Failed to delete file from storage: {e}")

        SupabaseStorage().delete_document_benchmark_qna(user_id, document_id)
        
        return {
            "message": f"Document '{document_info.get('filename', '')}' deleted successfully",
            "document_id": document_id,
            "user_id": user_id
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {str(e)}")

@router.delete("/user-documents/{user_id}")
async def delete_all_user_documents(
    user_id: str,
    current_admin: str = Depends(check_admin_access)
):
    """Delete all documents for a specific user"""
    try:
        schema_manager = SchemaManager()
        
        # Check if user schema exists
        if not await schema_manager.schema_exists(user_id):
            raise HTTPException(status_code=404, detail="User schema not found")
        
        # Get all user documents
        documents = await schema_manager.get_user_documents(user_id, limit=1000, offset=0)
        
        deleted_count = 0
        for doc in documents:
            try:
                # Delete from user's schema
                await schema_manager.delete_user_document(user_id, doc['id'])
                
                supabase = get_supabase_client()

                # Delete from main chunks/admin queue + documents tables
                # (Some deployments may not have ON DELETE CASCADE from documents -> chunks.)
                try:
                    supabase.table("chunks").delete().eq("document_id", doc["id"]).execute()
                except Exception as e:
                    logger.warning("Failed to delete chunks for document %s: %s", doc.get("id"), e)
                try:
                    supabase.table("admin_queue").delete().eq("document_id", doc["id"]).execute()
                except Exception as e:
                    logger.warning("Failed to delete admin_queue for document %s: %s", doc.get("id"), e)
                supabase.table('documents').delete().eq('id', doc['id']).execute()
                
                # Delete from storage if URL exists
                if doc.get('storage_url'):
                    try:
                        SupabaseStorage().delete_file_from_storage(doc["storage_url"])
                    except Exception as e:
                        logger.warning(f"Failed to delete file from storage: {e}")

                SupabaseStorage().delete_document_benchmark_qna(user_id, doc['id'])
                
                deleted_count += 1
            except Exception as e:
                logger.error(f"Failed to delete document {doc['id']}: {e}")
        
        return {
            "message": f"Deleted {deleted_count} documents for user",
            "user_id": user_id,
            "deleted_count": deleted_count
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete user documents: {str(e)}")

@router.get("/user-documents/{user_id}/{document_id}/chunks")
async def get_user_document_chunks(
    user_id: str,
    document_id: str,
    current_admin: str = Depends(check_admin_access)
):
    """Get chunks for a specific user's document from their schema"""
    try:
        schema_manager = SchemaManager()
        
        # Check if user schema exists
        if not await schema_manager.schema_exists(user_id):
            raise HTTPException(status_code=404, detail="User schema not found")
        
        # Get chunks from user's schema
        chunks = await schema_manager.get_user_chunks(user_id, document_id)
        
        return {
            "user_id": user_id,
            "document_id": document_id,
            "chunks": chunks,
            "total_chunks": len(chunks)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get document chunks: {str(e)}")

@router.post("/upload-chunks")
async def upload_chunks_to_user(
    request: UploadChunksRequest,
    current_admin: str = Depends(check_admin_access)
):
    """Upload admin-refined chunks to a specific user's schema"""
    try:
        schema_manager = SchemaManager()
        admin_service = AdminService()
        
        # Check if user schema exists
        if not await schema_manager.schema_exists(request.user_id):
            raise HTTPException(status_code=404, detail="User schema not found")
        
        # Validate document exists in user's schema
        user_docs = await schema_manager.get_user_documents(request.user_id, 1000, 0)
        document_exists = any(doc['id'] == request.document_id for doc in user_docs)
        
        if not document_exists:
            raise HTTPException(status_code=404, detail="Document not found in user's schema")
        
        # Prepare chunks data
        chunks_data = []
        for i, chunk in enumerate(request.chunks):
            chunks_data.append({
                'document_id': request.document_id,
                'chunk_index': i,
                'text': chunk.text,
                'clause_number': chunk.clause_number,
                'page_number': chunk.page_number,
                'entities': chunk.entities or [],
                'detailed_analysis': chunk.detailed_analysis or {},
                'metadata': chunk.metadata or {}
            })
        
        # Upload chunks using admin service
        result = await admin_service.upload_refined_chunks(
            user_id=request.user_id,
            document_id=request.document_id,
            chunks=chunks_data,
            admin_user_id=current_admin,
            admin_notes=request.admin_notes
        )
        
        if result and result.get("status") == "success":
            return {
                "message": "Chunks uploaded successfully",
                "user_id": request.user_id,
                "document_id": request.document_id,
                "chunks_uploaded": result.get("chunks_uploaded", len(chunks_data))
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to upload chunks")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload chunks: {str(e)}")

@router.post("/assign-document/{queue_id}")
async def assign_document_to_admin(
    queue_id: str,
    current_admin: str = Depends(check_admin_access)
):
    """Assign a document from admin queue to current admin"""
    try:
        admin_service = AdminService()
        success = await admin_service.assign_document_to_admin(queue_id, current_admin)
        
        if success:
            return {"message": "Document assigned successfully", "queue_id": queue_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to assign document")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to assign document: {str(e)}")

@router.get("/stats", response_model=AdminStatsResponse)
async def get_admin_stats(current_admin: str = Depends(check_admin_access)):
    """Get admin statistics across all user schemas"""
    try:
        admin_service = AdminService()
        stats = await admin_service.get_admin_stats()
        
        return AdminStatsResponse(**stats)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get admin stats: {str(e)}")

@router.get("/document-registry", response_model=DocumentRegistryResponse)
async def get_document_registry(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_admin: str = Depends(check_admin_access),
):
    """Return document registry entries with metadata, queue status, and duplicate insights."""
    try:
        admin_service = AdminService()
        payload = await admin_service.get_document_registry(limit=limit, offset=offset)
        return DocumentRegistryResponse(**payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch document registry: {str(e)}")

@router.get("/overview", response_model=AdminOverviewResponse)
async def get_admin_overview(current_admin: str = Depends(check_admin_access)):
    """Return Supabase auth user list with document/chunk stats."""
    try:
        admin_service = AdminService()
        payload = await admin_service.get_project_overview()
        return AdminOverviewResponse(**payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch admin overview: {str(e)}")

@router.get("/user-schemas")
async def get_all_user_schemas(current_admin: str = Depends(check_admin_access)):
    """Get list of all user schemas (admin only)"""
    try:
        schema_manager = SchemaManager()
        schemas = await schema_manager.get_all_user_schemas()
        
        return {
            "total_schemas": len(schemas),
            "schemas": schemas
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get user schemas: {str(e)}")

@router.get("/user-schema-stats/{user_id}")
async def get_user_schema_stats(
    user_id: str,
    current_admin: str = Depends(check_admin_access)
):
    """Get statistics for a specific user's schema"""
    try:
        schema_manager = SchemaManager()
        
        # Check if user schema exists
        if not await schema_manager.schema_exists(user_id):
            raise HTTPException(status_code=404, detail="User schema not found")
        
        stats = await schema_manager.get_user_stats(user_id)
        
        return {
            "user_id": user_id,
            "schema_exists": True,
            "stats": stats
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get user schema stats: {str(e)}")

@router.post("/create-user-schema/{user_id}")
async def create_user_schema_admin(
    user_id: str,
    current_admin: str = Depends(check_admin_access)
):
    """Create schema for a specific user (admin only)"""
    try:
        from uuid import UUID
        try:
            UUID(user_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid user_id") from exc

        schema_manager = SchemaManager()
        success = await schema_manager.create_user_schema(user_id)
        
        if success:
            return {"message": "User schema created successfully", "user_id": user_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to create user schema")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create user schema: {str(e)}")

@router.delete("/delete-document/{document_id}")
async def delete_document_admin(
    document_id: str,
    current_admin: str = Depends(check_admin_access)
):
    """Delete a document and its chunks from the system (admin only)"""
    try:
        supabase = get_supabase_client()

        doc_pick = supabase.table("documents").select("user_id, storage_url").eq("id", document_id).execute()
        if doc_pick.data:
            uid = doc_pick.data[0].get("user_id")
            if uid:
                SupabaseStorage().delete_document_benchmark_qna(uid, document_id)
            # Best-effort storage cleanup (supports Supabase object URLs + S3 gateway URLs)
            storage_url = doc_pick.data[0].get("storage_url")
            if storage_url:
                try:
                    SupabaseStorage().delete_file_from_storage(str(storage_url))
                except Exception as e:
                    logger.warning("Failed to delete file from storage (admin delete-document): %s", e)
        
        # Delete chunks first
        chunks_result = supabase.table('chunks').delete().eq('document_id', document_id).execute()
        
        # Delete from admin queue
        queue_result = supabase.table('admin_queue').delete().eq('document_id', document_id).execute()
        
        # Delete document
        doc_result = supabase.table('documents').delete().eq('id', document_id).execute()
        
        return {
            "message": "Document deleted successfully",
            "document_id": document_id,
            "chunks_deleted": len(chunks_result.data) if chunks_result.data else 0
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {str(e)}")

@router.delete("/delete-user-schema/{user_id}")
async def delete_user_schema_admin(
    user_id: str,
    current_admin: str = Depends(check_admin_access)
):
    """Delete entire schema for a specific user (admin only)"""
    try:
        schema_manager = SchemaManager()
        success = await schema_manager.delete_user_schema(user_id)
        
        if success:
            return {"message": "User schema deleted successfully", "user_id": user_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete user schema")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete user schema: {str(e)}")


# ===========================================
# SUBSCRIPTION MANAGEMENT ENDPOINTS
# ===========================================

class SubscriptionInfo(BaseModel):
    subscription_id: str
    stripe_subscription_id: Optional[str]
    database_id: Optional[str]
    user_id: Optional[str]
    user_email: Optional[str]
    user_name: Optional[str]
    company_id: Optional[str]
    company_name: Optional[str]
    subscription_type: Optional[str]
    status: str
    plan_name: Optional[str]
    product_name: Optional[str]
    price_amount: Optional[int]
    billing_interval: Optional[str]
    current_period_start: Optional[str]
    current_period_end: Optional[str]
    cancel_at_period_end: Optional[bool]
    canceled_at: Optional[str]
    max_documents: Optional[int]
    max_questions: Optional[int]
    documents_uploaded: int
    questions_asked: int
    created_at: Optional[str]
    source_table: Optional[str]


@router.get("/subscriptions", response_model=List[SubscriptionInfo])
async def get_all_subscriptions(
    status: Optional[str] = Query(None, description="Filter by status (active, trialing, canceled, etc.)"),
    subscription_type: Optional[str] = Query(None, description="Filter by type (individual, company)"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_admin: str = Depends(check_admin_access)
):
    """
    Get all subscriptions for admin management
    Uses the subscription_overview view to get unified data from both tables
    """
    try:
        supabase = get_supabase_client()
        
        # Query the subscription_overview view
        query = supabase.table('subscription_overview').select('*')
        
        # Apply filters
        if status:
            query = query.eq('status', status)
        if subscription_type:
            query = query.eq('subscription_type', subscription_type)
        
        # Order by created_at descending (newest first)
        query = query.order('created_at', desc=True)
        
        # Apply pagination
        query = query.range(offset, offset + limit - 1)
        
        result = query.execute()
        
        subscriptions = []
        for sub in result.data or []:
            subscriptions.append(SubscriptionInfo(
                subscription_id=sub.get('subscription_id', ''),
                stripe_subscription_id=sub.get('stripe_subscription_id'),
                database_id=sub.get('database_id'),
                user_id=str(sub.get('user_id')) if sub.get('user_id') else None,
                user_email=None,  # Will be populated from subscription_details view
                user_name=None,
                company_id=None,  # Company subscriptions removed
                company_name=None,  # Company subscriptions removed
                subscription_type=sub.get('subscription_type'),
                status=sub.get('status', 'unknown'),
                plan_name=sub.get('plan_name'),
                product_name=sub.get('product_name'),
                price_amount=sub.get('price_amount'),
                billing_interval=sub.get('billing_interval'),
                current_period_start=sub.get('current_period_start'),
                current_period_end=sub.get('current_period_end'),
                cancel_at_period_end=sub.get('cancel_at_period_end'),
                canceled_at=sub.get('canceled_at'),
                max_documents=sub.get('max_documents'),
                max_questions=sub.get('max_questions'),
                documents_uploaded=sub.get('documents_uploaded', 0),
                questions_asked=sub.get('questions_asked', 0),
                created_at=sub.get('created_at'),
                source_table=sub.get('source_table')
            ))
        
        # Enrich subscriptions with user details from subscription_details view
        if subscriptions:
            user_ids = [s.user_id for s in subscriptions if s.user_id]
            
            if user_ids:
                details_query = supabase.table('subscription_details').select('*')
                details_query = details_query.in_('user_id', user_ids)
                details_result = details_query.execute()
                
                # Create lookup map
                details_map = {}
                for detail in details_result.data or []:
                    key = detail.get('subscription_id')
                    if key:
                        details_map[key] = detail
                
                # Enrich subscriptions with user details
                for sub in subscriptions:
                    detail = details_map.get(sub.subscription_id)
                    if detail:
                        sub.user_email = detail.get('user_email')
                        sub.user_name = detail.get('user_name')
        
        return subscriptions
        
    except Exception as e:
        logger.error(f"Failed to get subscriptions: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get subscriptions: {str(e)}")


@router.get("/subscriptions/stats", response_model=Dict)
async def get_subscription_stats(
    current_admin: str = Depends(check_admin_access)
):
    """
    Get subscription statistics for admin dashboard
    """
    try:
        supabase = get_supabase_client()
        
        # Get all subscriptions from overview
        result = supabase.table('subscription_overview').select('status, subscription_type').execute()
        
        subscriptions = result.data or []
        
        stats = {
            'total': len(subscriptions),
            'by_status': {},
            'by_type': {
                'individual': 0,
                'company': 0
            },
            'active': 0,
            'trialing': 0,
            'canceled': 0,
            'past_due': 0
        }
        
        for sub in subscriptions:
            status = sub.get('status', 'unknown')
            sub_type = sub.get('subscription_type', 'unknown')
            
            # Count by status
            stats['by_status'][status] = stats['by_status'].get(status, 0) + 1
            
            # Count by type
            if sub_type in ['individual', 'company']:
                stats['by_type'][sub_type] = stats['by_type'].get(sub_type, 0) + 1
            
            # Count specific statuses
            if status == 'active':
                stats['active'] += 1
            elif status == 'trialing':
                stats['trialing'] += 1
            elif status == 'canceled':
                stats['canceled'] += 1
            elif status == 'past_due':
                stats['past_due'] += 1
        
        return stats
        
    except Exception as e:
        logger.error(f"Failed to get subscription stats: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get subscription stats: {str(e)}")


@router.get("/users/options", response_model=List[AdminUserOption])
async def get_admin_user_options(
    search: Optional[str] = None,
    limit: int = Query(250, ge=1, le=500),
    current_admin: str = Depends(check_admin_access)
):
    """Fetch lightweight list of users for admin dropdowns."""
    try:
        supabase = get_supabase_client()
        query = supabase.table('profiles')\
            .select('id, email, full_name, role, created_at')\
            .order('created_at', desc=True)\
            .limit(limit)
        
        result = query.execute()
        users = result.data or []
        
        if search:
            search_lower = search.lower()
            users = [
                user for user in users
                if search_lower in (user.get('email') or '').lower()
                or search_lower in (user.get('full_name') or '').lower()
            ]
        
        return users
    except Exception as e:
        logger.error(f"Failed to fetch admin user options: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch user list")


@router.get("/users/{user_id}/database", response_model=AdminUserDatabaseResponse)
async def get_admin_user_database(
    user_id: str,
    current_admin: str = Depends(check_admin_access)
):
    """Fetch comprehensive details about a specific user."""
    try:
        supabase = get_supabase_client()
        
        profile_result = supabase.table('profiles')\
            .select('*')\
            .eq('id', user_id)\
            .single()\
            .execute()
        
        profile = profile_result.data
        if not profile:
            raise HTTPException(status_code=404, detail="User profile not found")
        
        documents_result = supabase.table('documents')\
            .select('id, filename, codebook, discipline, status, admin_status, file_size, created_at, updated_at, admin_processed_at')\
            .eq('user_id', user_id)\
            .order('created_at', desc=True)\
            .limit(200)\
            .execute()
        documents = documents_result.data or []
        
        # Company features removed - all subscriptions are now individual
        memberships: List[Dict[str, Any]] = []
        company_employees: Dict[str, List[Dict[str, Any]]] = {}
        company_invitations: Dict[str, List[Dict[str, Any]]] = {}
        
        # Get subscription data from database
        subscriptions_result = supabase.table('user_subscriptions')\
            .select('id, subscription_type, plan_name, plan_price, currency, status, current_period_start, current_period_end, stripe_customer_id, stripe_subscription_id, created_at, updated_at')\
            .eq('user_id', user_id)\
            .order('created_at', desc=True)\
            .execute()
        subscriptions = subscriptions_result.data or []
        profile['subscription_type'] = profile.get('account_type') or profile.get('subscription_type') or '—'
        profile['subscription_status'] = profile.get('subscription_status') or 'active'
        
        admin_queue_result = supabase.table('admin_queue')\
            .select('id, document_id, filename, status, priority, created_at, updated_at, codebook')\
            .eq('user_id', user_id)\
            .order('created_at', desc=True)\
            .execute()
        admin_queue = admin_queue_result.data or []
        
        # Company invitations removed
        invitations: List[Dict[str, Any]] = []
        
        stats = {
            "total_documents": len(documents),
            "ready_documents": sum(1 for doc in documents if doc.get('status') == 'ready_for_search'),
            "pending_documents": sum(1 for doc in documents if doc.get('status') == 'pending_admin_review'),
            "company_memberships": 0,
            "subscriptions": len(subscriptions),
            "admin_queue_items": len(admin_queue)
        }
        
        return AdminUserDatabaseResponse(
            profile=profile,
            memberships=[],  # Company memberships removed
            company_employees=company_employees,
            company_invitations=company_invitations,
            documents=documents,
            subscriptions=subscriptions,
            admin_queue=admin_queue,
            invitations=invitations,
            stats=stats
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch admin database view for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch user details")