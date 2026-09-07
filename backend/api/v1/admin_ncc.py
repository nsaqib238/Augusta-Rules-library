"""
Admin API — shared NCC library (CSV upload + embeddings, searchable by all users).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from middleware.subscription_check import check_admin_access
from services.shared_library_service import (
    clear_library,
    create_edition,
    get_edition,
    ingest_clauses_csv,
    ingest_tables_csv,
    library_catalog,
    list_editions,
    sync_library_embeddings,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class CreateEditionRequest(BaseModel):
    label: str = Field(..., min_length=2, max_length=200)
    codebook: Optional[str] = Field(None, max_length=64)
    discipline: Optional[str] = None
    edition_year: int = Field(..., ge=1990, le=2100)
    volume: str = Field(..., min_length=2, max_length=16)
    part: Optional[str] = Field("All", max_length=64)


def _require_ncc_edition(codebook: str) -> dict:
    meta = get_edition(codebook.strip())
    if not meta or meta.get("family") != "NCC":
        raise HTTPException(status_code=400, detail=f"Unknown NCC codebook: {codebook}")
    return meta


@router.get("/editions")
async def get_ncc_editions(current_admin: str = Depends(check_admin_access)):
    catalog = library_catalog()
    return [r for r in catalog if r.get("family") == "NCC"]


@router.post("/editions")
async def create_ncc_edition(
    request: CreateEditionRequest,
    current_admin: str = Depends(check_admin_access),
):
    try:
        row = create_edition(
            family="NCC",
            label=request.label,
            admin_user_id=current_admin,
            codebook=request.codebook,
            discipline=request.discipline,
            edition_year=request.edition_year,
            volume=request.volume,
            part=request.part,
        )
        return {"ok": True, **row}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/upload-clauses")
async def upload_ncc_clauses(
    codebook: str = Form(..., description="e.g. NCC2022_VOL1"),
    file: UploadFile = File(...),
    current_admin: str = Depends(check_admin_access),
):
    _require_ncc_edition(codebook)
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")
    content = await file.read()
    try:
        meta = _require_ncc_edition(codebook)
        result = await ingest_clauses_csv(meta["codebook"], current_admin, content, file.filename)
        return {"ok": True, **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("NCC clause upload failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/upload-tables")
async def upload_ncc_tables(
    codebook: str = Form(...),
    file: UploadFile = File(...),
    current_admin: str = Depends(check_admin_access),
):
    _require_ncc_edition(codebook)
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")
    content = await file.read()
    try:
        meta = _require_ncc_edition(codebook)
        result = await ingest_tables_csv(meta["codebook"], current_admin, content, file.filename)
        return {"ok": True, **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sync-embeddings")
async def sync_ncc_embeddings(
    codebook: str = Form(...),
    current_admin: str = Depends(check_admin_access),
):
    meta = _require_ncc_edition(codebook)
    try:
        return {"ok": True, **sync_library_embeddings(meta["codebook"], current_admin)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/clear")
async def clear_ncc_data(
    codebook: str = Query(...),
    current_admin: str = Depends(check_admin_access),
):
    meta = _require_ncc_edition(codebook)
    try:
        return {"ok": True, **clear_library(meta["codebook"])}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
