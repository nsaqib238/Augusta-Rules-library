"""Shared NCC / SIR library documents — upload once, searchable by all users."""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.chunk_embedding_service import delete_document_embeddings, sync_document_embeddings
from services.codebooks import sanitize_custom_codebook_id
from services.pdf_ingest_parsers import parse_uploaded_chunks_content, parse_uploaded_tables_content
from services.standard_families import infer_standard_family
from services.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

DEFAULT_SIR_LIBRARY: List[Dict[str, Any]] = [
    {"family": "SIR", "codebook": "NSW_SIR_2018", "label": "NSW SIR 2018", "discipline": "electrical", "edition_year": 2018},
    {"family": "SIR", "codebook": "SA_SIR_2025", "label": "South Australia SIR 2025", "discipline": "electrical", "edition_year": 2025},
    {"family": "SIR", "codebook": "TASNETWORK_SIR_V85", "label": "TasNetwork SIR V8-5", "discipline": "electrical", "volume": "V8-5"},
    {"family": "SIR", "codebook": "VIC_SIR_2025", "label": "Victorian SIR 2025", "discipline": "electrical", "edition_year": 2025},
]

DEFAULT_NCC_LIBRARY: List[Dict[str, Any]] = [
    {"family": "NCC", "codebook": "NCC2022_VOL1", "label": "NCC 2022 Vol 1 — Class 2–9", "discipline": "fire", "edition_year": 2022, "volume": "Vol1", "part": "All"},
    {"family": "NCC", "codebook": "NCC2022_VOL2", "label": "NCC 2022 Vol 2 — Class 1 & 10", "discipline": "fire", "edition_year": 2022, "volume": "Vol2", "part": "All"},
    {"family": "NCC", "codebook": "NCC2022_VOL3", "label": "NCC 2022 Vol 3 — Plumbing Code", "discipline": "hydraulics", "edition_year": 2022, "volume": "Vol3", "part": "All"},
]

# Backwards-compatible aliases for modules that import these names
SIR_LIBRARY = DEFAULT_SIR_LIBRARY
NCC_LIBRARY = DEFAULT_NCC_LIBRARY


def _normalize_volume(volume: str) -> str:
    v = (volume or "").strip().upper().replace(" ", "")
    if not v:
        return "VOL1"
    if v.startswith("VOL"):
        suffix = re.sub(r"^VOL", "", v) or "1"
        return f"VOL{suffix}"
    return f"VOL{v}"


def suggest_ncc_codebook(edition_year: int, volume: str) -> str:
    return f"NCC{edition_year}_{_normalize_volume(volume)}"


def default_ncc_discipline(volume: str) -> str:
    vol = _normalize_volume(volume)
    return "hydraulics" if vol == "VOL3" else "fire"


def _fetch_editions(*, family: Optional[str] = None) -> List[Dict[str, Any]]:
    supabase = get_supabase_client()
    query = supabase.table("shared_library_editions").select("*")
    if family:
        query = query.eq("family", family.upper())
    result = query.order("label").execute()
    return list(result.data or [])


def _seed_default_editions() -> None:
    supabase = get_supabase_client()
    for row in DEFAULT_SIR_LIBRARY + DEFAULT_NCC_LIBRARY:
        try:
            supabase.table("shared_library_editions").upsert(row, on_conflict="codebook").execute()
        except Exception as exc:
            logger.warning("Could not seed shared library edition %s: %s", row.get("codebook"), exc)


def list_editions(*, family: Optional[str] = None, seed_if_empty: bool = True) -> List[Dict[str, Any]]:
    rows = _fetch_editions(family=family)
    if not rows and seed_if_empty:
        _seed_default_editions()
        rows = _fetch_editions(family=family)
    return rows


def get_edition(codebook_id: str) -> Optional[Dict[str, Any]]:
    cid = (codebook_id or "").strip().upper()
    if not cid:
        return None
    rows = _fetch_editions()
    for row in rows:
        if (row.get("codebook") or "").upper() == cid:
            return row
    fallback = next(
        (e for e in DEFAULT_SIR_LIBRARY + DEFAULT_NCC_LIBRARY if e["codebook"].upper() == cid),
        None,
    )
    return fallback


def is_shared_library_codebook(codebook_id: str) -> bool:
    return get_edition(codebook_id) is not None


def create_edition(
    *,
    family: str,
    label: str,
    admin_user_id: str,
    codebook: Optional[str] = None,
    discipline: Optional[str] = None,
    edition_year: Optional[int] = None,
    volume: Optional[str] = None,
    part: Optional[str] = None,
    library_document_id: Optional[str] = None,
) -> Dict[str, Any]:
    fam = (family or "").strip().upper()
    if fam not in {"SIR", "NCC", "LIB"}:
        raise ValueError("family must be SIR, NCC, or LIB")

    display = (label or "").strip()
    if not display:
        raise ValueError("label is required")

    if fam == "NCC":
        if edition_year is None:
            raise ValueError("edition_year is required for NCC editions")
        if not volume:
            raise ValueError("volume is required for NCC editions (Vol1, Vol2, Vol3)")
        cid = (codebook or suggest_ncc_codebook(edition_year, volume)).strip().upper()
        disc = (discipline or default_ncc_discipline(volume)).strip().lower()
        part_val = (part or "All").strip() or "All"
        volume_val = volume.strip()
    else:
        cid = (codebook or sanitize_custom_codebook_id(display)).strip().upper()
        disc = (discipline or "electrical").strip().lower()
        part_val = (part or None)
        volume_val = (volume or None)

    if not re.match(r"^[A-Z0-9_\-]{2,64}$", cid):
        raise ValueError("codebook must be 2–64 characters (letters, numbers, underscore, hyphen)")

    if get_edition(cid):
        raise ValueError(f"Codebook id already exists: {cid}")

    row = {
        "family": fam,
        "codebook": cid,
        "label": display,
        "discipline": disc,
        "edition_year": edition_year,
        "volume": volume_val,
        "part": part_val,
        "created_by": admin_user_id,
    }
    if library_document_id:
        row["library_document_id"] = library_document_id
    supabase = get_supabase_client()
    inserted = supabase.table("shared_library_editions").insert(row).execute()
    created = inserted.data[0] if inserted.data else row
    logger.info("Created shared library edition %s (%s)", cid, fam)
    return created


def library_catalog() -> List[Dict[str, Any]]:
    """Status rows for admin UI."""
    supabase = get_supabase_client()
    out: List[Dict[str, Any]] = []
    for entry in list_editions():
        cid = entry["codebook"]
        doc = (
            supabase.table("documents")
            .select("id, status, refined_chunk_count, updated_at")
            .eq("codebook", cid)
            .eq("is_shared_library", True)
            .limit(1)
            .execute()
        )
        row = doc.data[0] if doc.data else None
        chunk_count = 0
        embed_count = 0
        table_count = 0
        if row:
            chunks = (
                supabase.table("chunks")
                .select("id", count="exact")
                .eq("document_id", row["id"])
                .execute()
            )
            chunk_count = getattr(chunks, "count", None) or len(chunks.data or [])
            emb = (
                supabase.table("chunk_embeddings")
                .select("id", count="exact")
                .eq("document_id", row["id"])
                .execute()
            )
            embed_count = getattr(emb, "count", None) or len(emb.data or [])
            tables = (
                supabase.table("standard_tables")
                .select("id", count="exact")
                .eq("document_id", row["id"])
                .execute()
            )
            table_count = getattr(tables, "count", None) or len(tables.data or [])
        out.append(
            {
                "codebook": cid,
                "label": entry["label"],
                "family": entry.get("family"),
                "discipline": entry.get("discipline"),
                "edition_year": entry.get("edition_year"),
                "volume": entry.get("volume"),
                "part": entry.get("part"),
                "library_document_id": entry.get("library_document_id"),
                "document_id": row["id"] if row else None,
                "status": row["status"] if row else None,
                "chunk_count": chunk_count,
                "table_count": table_count,
                "embedding_count": embed_count,
                "ready": bool(row and row.get("status") == "ready_for_search" and chunk_count > 0),
                "processing": bool(row and row.get("status") in {"pdf_processing", "admin_processing"}),
            }
        )
    return out


def get_or_create_library_document(codebook_id: str, admin_user_id: str) -> str:
    """Return shared library document id for this codebook."""
    meta = get_edition(codebook_id)
    if not meta:
        raise ValueError(f"Unknown shared library codebook: {codebook_id}")

    supabase = get_supabase_client()
    existing = (
        supabase.table("documents")
        .select("id")
        .eq("codebook", meta["codebook"])
        .eq("is_shared_library", True)
        .limit(1)
        .execute()
    )
    if existing.data:
        return existing.data[0]["id"]

    doc_id = str(uuid.uuid4())
    row = {
        "id": doc_id,
        "user_id": admin_user_id,
        "filename": f"{meta['label']} (shared library)",
        "original_filename": meta["label"],
        "source": meta["label"],
        "codebook": meta["codebook"],
        "discipline": meta.get("discipline") or "electrical",
        "status": "ready_for_search",
        "admin_status": "completed",
        "is_shared_library": True,
        "processing_model": "admin",
    }
    try:
        supabase.table("documents").insert(row).execute()
    except Exception as exc:
        err = str(exc).lower()
        if "standard_family" in err or "source" in err or "original_filename" in err:
            slim = {k: v for k, v in row.items() if k not in ("source", "original_filename", "standard_family")}
            supabase.table("documents").insert(slim).execute()
        else:
            raise
    logger.info("Created shared library document %s for %s", doc_id, meta["codebook"])
    return doc_id


def library_parser_family(codebook_id: str, explicit: Optional[str] = None) -> str:
    meta = get_edition(codebook_id) or {}
    return infer_standard_family(
        meta.get("codebook") or codebook_id,
        codebook_label=meta.get("label"),
        explicit=explicit,
    )


def mark_library_document_processing(
    document_id: str,
    *,
    filename: Optional[str] = None,
    standard_family: Optional[str] = None,
) -> None:
    supabase = get_supabase_client()
    updates: Dict[str, Any] = {
        "status": "pdf_processing",
        "processing_model": "admin",
        "admin_status": "processing",
    }
    if filename:
        updates["filename"] = filename
        updates["original_filename"] = filename
    if standard_family:
        updates["standard_family"] = standard_family
    try:
        supabase.table("documents").update(updates).eq("id", document_id).execute()
    except Exception as exc:
        err = str(exc).lower()
        if "23514" in str(exc) or "documents_status_check" in err:
            updates["status"] = "admin_processing"
            try:
                supabase.table("documents").update(updates).eq("id", document_id).execute()
                return
            except Exception:
                pass
        slim = {k: v for k, v in updates.items() if k not in ("standard_family", "original_filename")}
        supabase.table("documents").update(slim).eq("id", document_id).execute()


async def ingest_library_source_from_path(
    codebook_id: str,
    admin_user_id: str,
    source_path: Path,
    *,
    filename: Optional[str] = None,
    standard_family: Optional[str] = None,
    replace_existing: bool = True,
    pdf_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Extract Word clauses and/or PDF+Modal tables into a shared library edition."""
    from pdf_pipeline.settings import settings as pdf_settings
    from services.pdf_ingest_service import (
        run_hybrid_word_pdf_ingest_from_paths,
        run_pdf_pipeline_and_ingest_from_path,
        run_word_pipeline_and_ingest_from_path,
    )

    meta = get_edition(codebook_id)
    if not meta:
        raise ValueError(f"Unknown shared library codebook: {codebook_id}")

    doc_id = get_or_create_library_document(codebook_id, admin_user_id)
    parser_family = library_parser_family(codebook_id, standard_family)
    mark_library_document_processing(
        doc_id,
        filename=filename,
        standard_family=parser_family,
    )

    word_path = Path(source_path) if Path(source_path).suffix.lower() == ".docx" else None
    pdf_only = Path(source_path) if Path(source_path).suffix.lower() == ".pdf" else None
    extra_pdf = Path(pdf_path) if pdf_path else None
    table_pdf = extra_pdf or (None if word_path else pdf_only)

    if word_path and table_pdf:
        out = await run_hybrid_word_pdf_ingest_from_paths(
            word_path,
            table_pdf,
            doc_id,
            admin_user_id,
            default_standard_name=meta["codebook"],
            replace_existing=replace_existing,
            admin_notes=f"Library Word clauses + Modal tables for {meta['codebook']}",
            standard_family=parser_family,
        )
    elif word_path:
        out = await run_word_pipeline_and_ingest_from_path(
            word_path,
            doc_id,
            admin_user_id,
            default_standard_name=meta["codebook"],
            replace_existing=replace_existing,
            admin_notes=f"Library Word ingest for {meta['codebook']}",
            update_admin_queue=False,
            standard_family=parser_family,
        )
    else:
        has_modal = bool((pdf_settings.modal_endpoint or os.getenv("MODAL_ENDPOINT") or "").strip())
        force_modal = has_modal and not pdf_settings.disable_modal
        out = await run_pdf_pipeline_and_ingest_from_path(
            Path(source_path),
            doc_id,
            admin_user_id,
            default_standard_name=meta["codebook"],
            enable_enhancement=pdf_settings.pdf_pipeline_enhancement,
            replace_existing=replace_existing,
            admin_notes=f"Library PDF ingest for {meta['codebook']}",
            update_admin_queue=False,
            mark_processing=True,
            force_modal=force_modal,
            standard_family=parser_family,
        )

    supabase = get_supabase_client()
    supabase.table("documents").update(
        {
            "refined_chunk_count": out.get("chunks_count") or 0,
            "chunk_count": out.get("chunks_count") or 0,
            "status": "ready_for_search",
            "admin_status": "completed",
        }
    ).eq("id", doc_id).execute()

    return {
        "document_id": doc_id,
        "codebook": meta["codebook"],
        "clauses_uploaded": out.get("chunks_count") or 0,
        "tables_uploaded": out.get("tables_count") or 0,
        "job_id": out.get("job_id"),
        "standard_family": parser_family,
        "pipeline_result": out.get("pipeline_result"),
    }


async def ingest_library_pdf_from_path(
    codebook_id: str,
    admin_user_id: str,
    pdf_path: Path,
    *,
    filename: Optional[str] = None,
    standard_family: Optional[str] = None,
    replace_existing: bool = True,
) -> Dict[str, Any]:
    return await ingest_library_source_from_path(
        codebook_id,
        admin_user_id,
        pdf_path,
        filename=filename,
        standard_family=standard_family,
        replace_existing=replace_existing,
        pdf_path=None,
    )


async def ingest_clauses_csv(
    codebook_id: str,
    admin_user_id: str,
    content: bytes,
    filename: str,
    *,
    replace: bool = True,
) -> Dict[str, Any]:
    meta = get_edition(codebook_id)
    if not meta:
        raise ValueError(f"Unknown shared library codebook: {codebook_id}")

    doc_id = get_or_create_library_document(codebook_id, admin_user_id)
    supabase = get_supabase_client()

    if replace:
        delete_document_embeddings(doc_id)
        supabase.table("chunks").delete().eq("document_id", doc_id).execute()

    chunks = await parse_uploaded_chunks_content(
        content,
        filename,
        doc_id,
        admin_user_id,
        codebook=meta["codebook"],
        discipline=meta.get("discipline") or "electrical",
    )
    if not chunks:
        raise ValueError("No valid clause rows in CSV")

    for i in range(0, len(chunks), 100):
        supabase.table("chunks").insert(chunks[i : i + 100]).execute()

    supabase.table("documents").update(
        {"refined_chunk_count": len(chunks), "chunk_count": len(chunks), "status": "ready_for_search"}
    ).eq("id", doc_id).execute()

    embedded = sync_document_embeddings(doc_id, admin_user_id)
    return {
        "document_id": doc_id,
        "codebook": meta["codebook"],
        "clauses_uploaded": len(chunks),
        "embeddings_synced": embedded,
    }


async def ingest_tables_csv(
    codebook_id: str,
    admin_user_id: str,
    content: bytes,
    filename: str,
    *,
    replace: bool = True,
) -> Dict[str, Any]:
    meta = get_edition(codebook_id)
    if not meta:
        raise ValueError(f"Unknown shared library codebook: {codebook_id}")

    doc_id = get_or_create_library_document(codebook_id, admin_user_id)
    supabase = get_supabase_client()

    if replace:
        supabase.table("standard_tables").delete().eq("document_id", doc_id).execute()

    tables = await parse_uploaded_tables_content(content, filename, doc_id, admin_user_id)
    if not tables:
        raise ValueError("No valid table rows in CSV")

    for i in range(0, len(tables), 50):
        supabase.table("standard_tables").insert(tables[i : i + 50]).execute()

    return {
        "document_id": doc_id,
        "codebook": meta["codebook"],
        "tables_uploaded": len(tables),
    }


def _library_document_id(codebook_id: str) -> Optional[str]:
    meta = get_edition(codebook_id)
    if not meta:
        raise ValueError(f"Unknown shared library codebook: {codebook_id}")
    supabase = get_supabase_client()
    existing = (
        supabase.table("documents")
        .select("id")
        .eq("codebook", meta["codebook"])
        .eq("is_shared_library", True)
        .limit(1)
        .execute()
    )
    if not existing.data:
        return None
    return existing.data[0]["id"]


def _fetch_all_rows(table: str, document_id: str, order_col: str) -> List[Dict[str, Any]]:
    supabase = get_supabase_client()
    rows: List[Dict[str, Any]] = []
    page = 1000
    start = 0
    while True:
        result = (
            supabase.table(table)
            .select("*")
            .eq("document_id", document_id)
            .order(order_col)
            .range(start, start + page - 1)
            .execute()
        )
        batch = list(result.data or [])
        rows.extend(batch)
        if len(batch) < page:
            break
        start += page
    return rows


def _json_cell(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            return stripped
        return value
    return str(value)


def export_library_clauses_csv(codebook_id: str) -> Tuple[bytes, str]:
    meta = get_edition(codebook_id)
    if not meta:
        raise ValueError(f"Unknown shared library codebook: {codebook_id}")
    doc_id = _library_document_id(codebook_id)
    if not doc_id:
        raise ValueError("No ingested edition yet — upload a PDF or clause CSV first")

    rows = _fetch_all_rows("chunks", doc_id, "chunk_index")
    if not rows:
        raise ValueError("No clauses stored for this edition")

    fieldnames = [
        "id",
        "clause_number",
        "heading",
        "body",
        "notes",
        "exceptions",
        "level",
        "parent_number",
        "page",
        "codebook",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore", quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    for row in rows:
        stored_meta = row.get("metadata")
        try:
            meta_obj = json.loads(stored_meta) if isinstance(stored_meta, str) and stored_meta else (stored_meta or {})
        except Exception:
            meta_obj = {}
        if not isinstance(meta_obj, dict):
            meta_obj = {}
        ent_raw = row.get("entities")
        try:
            ent_obj = json.loads(ent_raw) if isinstance(ent_raw, str) and ent_raw else (ent_raw or {})
        except Exception:
            ent_obj = {}
        if not isinstance(ent_obj, dict):
            ent_obj = {}
        writer.writerow(
            {
                "id": meta_obj.get("id") or "",
                "clause_number": row.get("clause_number") or "",
                "heading": row.get("heading") or "",
                "body": row.get("text") or "",
                "notes": _json_cell(ent_obj.get("notes")),
                "exceptions": _json_cell(ent_obj.get("exceptions")),
                "level": meta_obj.get("level") or "",
                "parent_number": meta_obj.get("parent_number") or "",
                "page": row.get("page_number") or "",
                "codebook": row.get("codebook") or meta["codebook"],
            }
        )
    filename = f"{meta['codebook']}_clauses.csv"
    return buf.getvalue().encode("utf-8-sig"), filename


def export_library_tables_csv(codebook_id: str) -> Tuple[bytes, str]:
    meta = get_edition(codebook_id)
    if not meta:
        raise ValueError(f"Unknown shared library codebook: {codebook_id}")
    doc_id = _library_document_id(codebook_id)
    if not doc_id:
        raise ValueError("No ingested edition yet — upload a PDF or tables CSV first")

    rows = _fetch_all_rows("standard_tables", doc_id, "table_number")
    if not rows:
        raise ValueError("No tables stored for this edition")

    fieldnames = [
        "table_id",
        "table_number",
        "table_title",
        "table_content",
        "clause_reference",
        "source",
        "notes",
        "exceptions",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore", quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "table_id": row.get("table_id") or "",
                "table_number": row.get("table_number") or "",
                "table_title": row.get("title") or "",
                "table_content": row.get("text") or "",
                "clause_reference": row.get("source_clause_number") or "",
                "source": row.get("standard_name") or meta["codebook"],
                "notes": _json_cell(row.get("notes")),
                "exceptions": _json_cell(row.get("exceptions")),
            }
        )
    filename = f"{meta['codebook']}_tables.csv"
    return buf.getvalue().encode("utf-8-sig"), filename


def sync_library_embeddings(codebook_id: str, admin_user_id: str) -> Dict[str, Any]:
    meta = get_edition(codebook_id)
    if not meta:
        raise ValueError(f"Unknown shared library codebook: {codebook_id}")
    doc_id = get_or_create_library_document(codebook_id, admin_user_id)
    count = sync_document_embeddings(doc_id, admin_user_id)
    return {"document_id": doc_id, "codebook": meta["codebook"], "embeddings_synced": count}


def clear_library(codebook_id: str) -> Dict[str, Any]:
    meta = get_edition(codebook_id)
    if not meta:
        raise ValueError(f"Unknown shared library codebook: {codebook_id}")

    supabase = get_supabase_client()
    docs = (
        supabase.table("documents")
        .select("id")
        .eq("codebook", meta["codebook"])
        .eq("is_shared_library", True)
        .execute()
    )
    doc_ids = [d["id"] for d in (docs.data or [])]
    deleted_chunks = 0
    deleted_tables = 0
    for did in doc_ids:
        delete_document_embeddings(did)
        chk = supabase.table("chunks").delete().eq("document_id", did).execute()
        deleted_chunks += len(chk.data or [])
        tbl = supabase.table("standard_tables").delete().eq("document_id", did).execute()
        deleted_tables += len(tbl.data or [])
        supabase.table("documents").delete().eq("id", did).execute()

    return {
        "codebook": meta["codebook"],
        "deleted_documents": len(doc_ids),
        "deleted_chunks": deleted_chunks,
        "deleted_tables": deleted_tables,
    }


def shared_library_document_ids(codebook_id: str) -> List[str]:
    """Document ids for a shared codebook (0 or 1)."""
    if not is_shared_library_codebook(codebook_id):
        return []
    meta = get_edition(codebook_id)
    if not meta:
        return []
    supabase = get_supabase_client()
    docs = (
        supabase.table("documents")
        .select("id")
        .eq("codebook", meta["codebook"])
        .eq("is_shared_library", True)
        .eq("status", "ready_for_search")
        .execute()
    )
    return [d["id"] for d in (docs.data or [])]
