"""Admin API — country → type → catalog document library."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from middleware.subscription_check import check_admin_access
from services.library_catalog_service import (
    catalog_tree,
    create_country,
    create_document,
    create_type,
    get_country,
    get_document,
    infer_edition_family,
    list_documents,
    list_types,
    update_document,
    update_type,
)
from services.shared_library_service import (
    clear_library,
    create_edition,
    export_library_clauses_csv,
    export_library_tables_csv,
    get_edition,
    get_or_create_library_document,
    ingest_clauses_csv,
    ingest_library_source_from_path,
    ingest_tables_csv,
    library_parser_family,
    mark_library_document_processing,
    sync_library_embeddings,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class CreateCountryRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=2)
    name: str = Field(..., min_length=2, max_length=80)
    is_active: bool = True


class CreateTypeRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    slug: Optional[str] = Field(None, max_length=80)
    sort_order: Optional[int] = None


class PatchTypeRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    slug: Optional[str] = None
    sort_order: Optional[int] = None


class CreateDocumentRequest(BaseModel):
    country_code: str = Field(..., min_length=2, max_length=2)
    document_type_id: str
    title: str = Field(..., min_length=2, max_length=240)
    discipline: str = Field("Electrical", max_length=64)
    publisher: Optional[str] = Field(None, max_length=160)
    priority: str = Field("medium", max_length=16)
    slug: Optional[str] = None


class PatchDocumentRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=2, max_length=240)
    discipline: Optional[str] = None
    publisher: Optional[str] = None
    priority: Optional[str] = None
    document_type_id: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class CreateEditionRequest(BaseModel):
    label: str = Field(..., min_length=2, max_length=200)
    codebook: Optional[str] = Field(None, max_length=64)
    edition_year: Optional[int] = None
    volume: Optional[str] = None
    part: Optional[str] = None
    family: Optional[str] = None


def _country_or_404(code: str) -> dict:
    row = get_country(code)
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown country: {code}")
    return row


@router.get("/tree")
async def get_library_tree(current_admin: str = Depends(check_admin_access)):
    try:
        return catalog_tree()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("library tree failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/countries")
async def get_countries(current_admin: str = Depends(check_admin_access)):
    tree = catalog_tree()
    return tree["countries"]


@router.post("/countries")
async def post_country(
    request: CreateCountryRequest,
    current_admin: str = Depends(check_admin_access),
):
    try:
        return {"ok": True, **create_country(request.code, request.name, is_active=request.is_active)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/countries/{code}/types")
async def get_country_types(code: str, current_admin: str = Depends(check_admin_access)):
    country = _country_or_404(code)
    return list_types(country["id"])


@router.post("/countries/{code}/types")
async def post_country_type(
    code: str,
    request: CreateTypeRequest,
    current_admin: str = Depends(check_admin_access),
):
    country = _country_or_404(code)
    try:
        return {
            "ok": True,
            **create_type(country["id"], request.name, slug=request.slug, sort_order=request.sort_order),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/types/{type_id}")
async def patch_type(
    type_id: str,
    request: PatchTypeRequest,
    current_admin: str = Depends(check_admin_access),
):
    try:
        return {"ok": True, **update_type(type_id, request.model_dump(exclude_unset=True))}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/documents")
async def get_documents(
    country: Optional[str] = Query(None),
    type_id: Optional[str] = Query(None),
    current_admin: str = Depends(check_admin_access),
):
    country_id = None
    if country:
        country_id = _country_or_404(country)["id"]
    return list_documents(country_id=country_id, type_id=type_id)


@router.post("/documents")
async def post_document(
    request: CreateDocumentRequest,
    current_admin: str = Depends(check_admin_access),
):
    country = _country_or_404(request.country_code)
    try:
        row = create_document(
            country_id=country["id"],
            document_type_id=request.document_type_id,
            title=request.title,
            discipline=request.discipline,
            publisher=request.publisher,
            priority=request.priority,
            slug=request.slug,
            admin_user_id=current_admin,
        )
        return {"ok": True, **row}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("create catalog document failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/documents/{document_id}")
async def get_one_document(document_id: str, current_admin: str = Depends(check_admin_access)):
    row = get_document(document_id)
    if not row:
        raise HTTPException(status_code=404, detail="Catalog document not found")
    tree = catalog_tree()
    editions = [e for e in tree["editions"] if e.get("library_document_id") == document_id]
    return {**row, "editions": editions}


@router.patch("/documents/{document_id}")
async def patch_document(
    document_id: str,
    request: PatchDocumentRequest,
    current_admin: str = Depends(check_admin_access),
):
    try:
        return {"ok": True, **update_document(document_id, request.model_dump(exclude_unset=True))}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/documents/{document_id}/editions")
async def post_document_edition(
    document_id: str,
    request: CreateEditionRequest,
    current_admin: str = Depends(check_admin_access),
):
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Catalog document not found")
    country = get_country_by_id(doc["country_id"])
    type_slug = None
    if country:
        for t in list_types(country["id"]):
            if t["id"] == doc["document_type_id"]:
                type_slug = t.get("slug")
                break
    family = (request.family or infer_edition_family(type_slug, doc.get("title") or "")).upper()
    if family == "NCC" and (request.edition_year is None or not request.volume):
        family = "LIB"
    try:
        row = create_edition(
            family=family,
            label=request.label,
            admin_user_id=current_admin,
            codebook=request.codebook,
            discipline=_map_edition_discipline(doc.get("discipline")),
            edition_year=request.edition_year,
            volume=request.volume,
            part=request.part,
            library_document_id=document_id,
        )
        return {"ok": True, **row}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _map_edition_discipline(value: Optional[str]) -> str:
    key = (value or "").split("/")[0].strip().lower()
    mapping = {
        "electrical": "electrical",
        "hydraulic": "hydraulics",
        "hydraulics": "hydraulics",
        "plumbing": "hydraulics",
        "fire": "fire",
        "mechanical": "mechanical",
    }
    return mapping.get(key, "electrical")


def get_country_by_id(country_id: str) -> Optional[dict]:
    from services.library_catalog_service import list_countries

    for row in list_countries():
        if row.get("id") == country_id:
            return row
    return None


def _require_edition(codebook: str) -> dict:
    meta = get_edition(codebook.strip())
    if not meta:
        raise HTTPException(status_code=400, detail=f"Unknown edition: {codebook}")
    return meta


async def _save_upload(upload: UploadFile, dest: Path, max_bytes: int) -> int:
    total_written = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as buffer:
        while True:
            piece = await upload.read(1024 * 1024)
            if not piece:
                break
            total_written += len(piece)
            if total_written > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds maximum size of {max_bytes // (1024 * 1024)}MB",
                )
            buffer.write(piece)
    if total_written == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    return total_written


async def _run_library_pdf_job(
    codebook: str,
    admin_user_id: str,
    source_path: Path,
    filename: str,
    standard_family: Optional[str],
    replace_existing: bool,
    document_id: str,
    extra_pdf_path: Optional[Path] = None,
) -> None:
    from services.pdf_job_queue import pdf_job_queue
    from services.pdf_job_recovery import mark_document_pdf_failed

    try:
        await pdf_job_queue.run(
            admin_user_id,
            lambda: ingest_library_source_from_path(
                codebook,
                admin_user_id,
                source_path,
                filename=filename,
                standard_family=standard_family,
                replace_existing=replace_existing,
                pdf_path=extra_pdf_path,
            ),
            document_id=document_id,
        )
    except Exception as exc:
        logger.exception("Library ingest failed codebook=%s document_id=%s", codebook, document_id)
        try:
            mark_document_pdf_failed(document_id, str(exc)[:1200])
        except Exception:
            pass
    finally:
        for path in (source_path, extra_pdf_path):
            if not path:
                continue
            try:
                os.unlink(path)
            except Exception:
                pass


@router.post("/editions/{codebook}/upload-pdf")
async def upload_edition_pdf(
    codebook: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    pdf_file: Optional[UploadFile] = File(None),
    standard_family: Optional[str] = Form(None),
    replace_existing: str = Form("true"),
    current_admin: str = Depends(check_admin_access),
):
    """Word for clauses (preferred). Optional PDF is sent to Modal for tables."""
    meta = _require_edition(codebook)
    name = (file.filename or "").lower()
    suffix = Path(name).suffix
    if suffix not in {".pdf", ".docx"}:
        raise HTTPException(status_code=400, detail="Upload a .docx (clauses) and/or a .pdf (Modal tables)")

    extra_name = (pdf_file.filename or "").lower() if pdf_file and pdf_file.filename else ""
    if extra_name and not extra_name.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="The tables file must be a PDF")

    from pdf_pipeline.settings import settings as pdf_settings

    max_bytes = pdf_settings.max_file_size
    do_replace = (replace_existing or "true").lower() in ("true", "1", "yes")
    parser_family = library_parser_family(meta["codebook"], standard_family)
    doc_id = get_or_create_library_document(meta["codebook"], current_admin)

    incoming = Path("processed_files") / "_incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    source_path = incoming / f"{doc_id}{suffix}"
    extra_pdf_path = incoming / f"{doc_id}_tables.pdf" if extra_name else None

    try:
        await _save_upload(file, source_path, max_bytes)
        if pdf_file and extra_pdf_path:
            await _save_upload(pdf_file, extra_pdf_path, max_bytes)

        mark_library_document_processing(
            doc_id,
            filename=file.filename,
            standard_family=parser_family,
        )
        background_tasks.add_task(
            _run_library_pdf_job,
            meta["codebook"],
            current_admin,
            source_path,
            file.filename,
            parser_family,
            do_replace,
            doc_id,
            extra_pdf_path,
        )
        if suffix == ".docx" and extra_pdf_path:
            message = (
                "Word accepted for clauses. PDF queued for Modal table extraction. "
                "The edition will show Ready when both finish."
            )
        elif suffix == ".docx":
            message = "Word accepted. Extracting clauses and Word tables. Add a PDF if you want Modal tables."
        else:
            message = "PDF accepted. Extracting clauses with regex and tables with Modal."
        return {
            "ok": True,
            "status": "processing",
            "document_id": doc_id,
            "codebook": meta["codebook"],
            "standard_family": parser_family,
            "message": message,
        }
    except HTTPException:
        for path in (source_path, extra_pdf_path):
            if path:
                try:
                    os.unlink(path)
                except Exception:
                    pass
        raise
    except Exception as exc:
        for path in (source_path, extra_pdf_path):
            if path:
                try:
                    os.unlink(path)
                except Exception:
                    pass
        logger.exception("library upload failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/editions/{codebook}/upload-clauses")
async def upload_edition_clauses(
    codebook: str,
    file: UploadFile = File(...),
    current_admin: str = Depends(check_admin_access),
):
    meta = _require_edition(codebook)
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")
    content = await file.read()
    try:
        result = await ingest_clauses_csv(meta["codebook"], current_admin, content, file.filename)
        return {"ok": True, **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("library clause upload failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/editions/{codebook}/upload-tables")
async def upload_edition_tables(
    codebook: str,
    file: UploadFile = File(...),
    current_admin: str = Depends(check_admin_access),
):
    meta = _require_edition(codebook)
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")
    content = await file.read()
    try:
        result = await ingest_tables_csv(meta["codebook"], current_admin, content, file.filename)
        return {"ok": True, **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/editions/{codebook}/download-clauses")
async def download_edition_clauses(
    codebook: str,
    current_admin: str = Depends(check_admin_access),
):
    meta = _require_edition(codebook)
    try:
        content, filename = export_library_clauses_csv(meta["codebook"])
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/editions/{codebook}/download-tables")
async def download_edition_tables(
    codebook: str,
    current_admin: str = Depends(check_admin_access),
):
    meta = _require_edition(codebook)
    try:
        content, filename = export_library_tables_csv(meta["codebook"])
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/editions/{codebook}/sync-embeddings")
async def sync_edition_embeddings(
    codebook: str,
    current_admin: str = Depends(check_admin_access),
):
    meta = _require_edition(codebook)
    try:
        return {"ok": True, **sync_library_embeddings(meta["codebook"], current_admin)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/editions/{codebook}")
async def clear_edition_data(
    codebook: str,
    current_admin: str = Depends(check_admin_access),
):
    meta = _require_edition(codebook)
    try:
        return {"ok": True, **clear_library(meta["codebook"])}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
