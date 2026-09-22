"""
Admin API — leftover SIR editions can be listed and cleared.
New SIR ingest is disabled; this product hosts NCC only.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from middleware.subscription_check import check_admin_access
from services.shared_library_service import (
    clear_library,
    get_edition,
    library_catalog,
)

router = APIRouter()

_NCC_ONLY = "This library hosts NCC editions only"


class CreateEditionRequest(BaseModel):
    label: str = Field(..., min_length=2, max_length=200)
    codebook: Optional[str] = Field(None, max_length=64)
    discipline: Optional[str] = Field("electrical", max_length=32)
    edition_year: Optional[int] = None
    volume: Optional[str] = None
    part: Optional[str] = None


def _require_sir_edition(codebook: str) -> dict:
    meta = get_edition(codebook.strip())
    if not meta or meta.get("family") != "SIR":
        raise HTTPException(status_code=400, detail=f"Unknown SIR codebook: {codebook}")
    return meta


@router.get("/edition-metadata")
async def get_sir_edition_metadata(current_admin: str = Depends(check_admin_access)):
    catalog = library_catalog()
    return [r for r in catalog if r.get("family") == "SIR"]


@router.get("/editions")
async def get_sir_editions(current_admin: str = Depends(check_admin_access)):
    catalog = library_catalog()
    return [r for r in catalog if r.get("family") == "SIR"]


@router.post("/editions")
async def create_sir_edition(
    request: CreateEditionRequest,
    current_admin: str = Depends(check_admin_access),
):
    raise HTTPException(status_code=400, detail=_NCC_ONLY)


@router.post("/upload-clauses")
async def upload_sir_clauses(
    codebook: str = Form(...),
    file: UploadFile = File(...),
    current_admin: str = Depends(check_admin_access),
):
    raise HTTPException(status_code=400, detail=_NCC_ONLY)


@router.post("/upload-tables")
async def upload_sir_tables(
    codebook: str = Form(...),
    file: UploadFile = File(...),
    current_admin: str = Depends(check_admin_access),
):
    raise HTTPException(status_code=400, detail=_NCC_ONLY)


@router.post("/sync-embeddings")
async def sync_sir_embeddings(
    codebook: str = Form(...),
    current_admin: str = Depends(check_admin_access),
):
    raise HTTPException(status_code=400, detail=_NCC_ONLY)


@router.delete("/clear")
async def clear_sir_data(
    codebook: str = Query(...),
    current_admin: str = Depends(check_admin_access),
):
    meta = _require_sir_edition(codebook)
    try:
        return {"ok": True, **clear_library(meta["codebook"])}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
