"""
Run local PDF pipeline and ingest clauses/tables into the database (user upload or admin).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from services.supabase_client import get_supabase_client
from services.pdf_ingest_parsers import (
    parse_uploaded_chunks_content,
    parse_uploaded_tables_content,
)

logger = logging.getLogger(__name__)


class PdfNoStructuredContentError(Exception):
    """Pipeline finished but produced nothing ingestable."""

    USER_MESSAGE = (
        "We could not extract any clauses or tables from this PDF. "
        "Try a text-based PDF from the publisher, or OCR the document first."
    )

    def __init__(self, message: Optional[str] = None):
        super().__init__(message or self.USER_MESSAGE)


class PdfQualityCheckError(Exception):
    """Ingestion outputs look degraded."""

    def __init__(self, message: str):
        super().__init__(message)


_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _pdf_debug_save_outputs_enabled() -> bool:
    return os.getenv("PDF_PIPELINE_DEBUG_SAVE_OUTPUTS", "").lower() in ("1", "true", "yes")


def _pdf_debug_output_root() -> Path:
    raw = (os.getenv("PDF_PIPELINE_DEBUG_OUTPUT_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (_BACKEND_ROOT / "processed_files" / "_debug").resolve()


@contextmanager
def _pdf_ingest_workspace(job_id: str, document_id: str) -> Iterator[Path]:
    if _pdf_debug_save_outputs_enabled():
        root = _pdf_debug_output_root() / document_id / job_id
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        try:
            yield root
        finally:
            logger.info("PDF pipeline debug: outputs kept at %s", root)
    else:
        with tempfile.TemporaryDirectory(prefix=f"pdf_ingest_{job_id}_") as tmp:
            yield Path(tmp)


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _scan_csv_degradation(path: Path, *, label: str) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return

    unknown_hits = len(re.findall(r"\bUnknown\b", text))
    # Count symbol-font / private-use garbage, not legitimate en-dash, degree, etc.
    noise_hits = len(re.findall(r"[\uE000-\uF8FF\uFFF0-\uFFFF]{4,}", text))
    noise_hits += len(re.findall(r"(?:[^\x00-\x7F\n\r\t]){12,}", text))
    unknown_ratio = unknown_hits / max(1, min(len(lines), 50_000))

    max_ratio = _env_float("PDF_INGEST_MAX_UNKNOWN_TOKEN_RATIO", 0.02)
    max_abs = int(os.getenv("PDF_INGEST_MAX_UNKNOWN_TOKENS", "200") or 200)
    max_noise = int(os.getenv("PDF_INGEST_MAX_NOISE_RUNS", "25") or 25)

    if unknown_hits >= max_abs and unknown_ratio >= max_ratio:
        raise PdfQualityCheckError(
            f"{label}: too many 'Unknown' placeholders (hits={unknown_hits}, "
            f"approx_ratio={unknown_ratio:.4f})."
        )
    if noise_hits >= max_noise:
        raise PdfQualityCheckError(
            f"{label}: high non-text/garble signal (noise_hits={noise_hits})."
        )


def _assert_outputs_quality(output_dir: Path) -> None:
    _scan_csv_degradation(output_dir / "clauses.csv", label="clauses.csv")
    _scan_csv_degradation(output_dir / "tables.csv", label="tables.csv")


def delete_existing_chunks_and_tables(supabase, document_id: str) -> None:
    """Remove prior chunks/tables (and embeddings via CASCADE) before re-ingest."""
    from services.chunk_embedding_service import delete_document_embeddings

    delete_document_embeddings(document_id)
    supabase.table("chunks").delete().eq("document_id", document_id).execute()
    supabase.table("standard_tables").delete().eq("document_id", document_id).execute()
    try:
        from services.search_hybrid import invalidate_retrieval_cache_for_document

        invalidate_retrieval_cache_for_document(document_id)
    except Exception:
        pass


async def run_pdf_pipeline_and_ingest_from_path(
    pdf_path: Path,
    document_id: str,
    user_id: str,
    *,
    default_standard_name: Optional[str],
    enable_enhancement: bool = True,
    replace_existing: bool = True,
    admin_notes: str = "",
    update_admin_queue: bool = True,
    mark_processing: bool = False,
    force_modal: bool = False,
    standard_family: Optional[str] = None,
) -> Dict[str, Any]:
    """Run pdf_pipeline PDFProcessor, then insert chunks + standard_tables into the database."""
    from pdf_pipeline.services.pdf_processor import PDFProcessor
    from services.codebooks import resolve_upload_standard_family
    from services.standard_families import normalize_standard_family

    supabase = get_supabase_client()
    job_id = str(uuid.uuid4())
    logger.info(
        "[pdf_ingest] start document_id=%s job_id=%s replace_existing=%s codebook=%r force_modal=%s",
        document_id,
        job_id,
        replace_existing,
        default_standard_name,
        force_modal,
    )

    if mark_processing:
        try:
            supabase.table("documents").update({"status": "pdf_processing"}).eq("id", document_id).execute()
        except Exception as e:
            logger.warning("Could not set status=pdf_processing for document_id=%s: %s", document_id, e)

    with _pdf_ingest_workspace(job_id, document_id) as work_root:
        output_dir = work_root / "out"
        output_dir.mkdir(parents=True, exist_ok=True)

        doc_meta: Dict[str, Any] = {}
        try:
            doc_res = supabase.table("documents").select(
                "codebook, discipline, source, standard_family"
            ).eq("id", document_id).single().execute()
            doc_meta = doc_res.data or {}
        except Exception:
            try:
                doc_res = supabase.table("documents").select(
                    "codebook, discipline, source"
                ).eq("id", document_id).single().execute()
                doc_meta = doc_res.data or {}
            except Exception:
                pass
        ingest_codebook = (default_standard_name or doc_meta.get("codebook") or "").strip() or None
        ingest_discipline = (doc_meta.get("discipline") or "").strip() or None
        parser_family = normalize_standard_family(
            standard_family
            or doc_meta.get("standard_family")
            or resolve_upload_standard_family(
                ingest_codebook or "",
                codebook_label=doc_meta.get("source"),
            )
        )
        logger.info(
            "[pdf_ingest] parser_family=%s document_id=%s codebook=%r",
            parser_family,
            document_id,
            ingest_codebook,
        )

        processor = PDFProcessor()
        pipeline_result = await processor.process_pdf(
            input_path=str(pdf_path),
            output_dir=str(output_dir),
            job_id=job_id,
            enable_enhancement=enable_enhancement,
            force_modal=force_modal,
            standard_family=parser_family,
        )

        _assert_outputs_quality(output_dir)

        clauses_csv = output_dir / "clauses.csv"
        tables_csv = output_dir / "tables.csv"

        chunks_data = []
        tables_data = []

        if clauses_csv.is_file():
            chunks_data = await parse_uploaded_chunks_content(
                clauses_csv.read_bytes(),
                "clauses.csv",
                document_id,
                user_id,
                codebook=ingest_codebook,
                discipline=ingest_discipline,
            )
        if tables_csv.is_file():
            tables_data = await parse_uploaded_tables_content(
                tables_csv.read_bytes(),
                "tables.csv",
                document_id,
                user_id,
                default_standard_name=default_standard_name,
            )

        if not chunks_data and not tables_data:
            raise PdfNoStructuredContentError()

        if replace_existing:
            delete_existing_chunks_and_tables(supabase, document_id)

        chunks_uploaded = 0
        if chunks_data:
            batch_size = 100
            for i in range(0, len(chunks_data), batch_size):
                batch = chunks_data[i : i + batch_size]
                result = supabase.table("chunks").insert(batch).execute()
                if result.data:
                    chunks_uploaded += len(batch)
                else:
                    raise RuntimeError(f"Failed to insert chunks batch {i // batch_size + 1}")

        if chunks_uploaded:
            from services.chunk_embedding_service import sync_document_embeddings

            try:
                embedded = sync_document_embeddings(document_id, user_id)
                logger.info(
                    "[pdf_ingest] embeddings document_id=%s count=%s",
                    document_id,
                    embedded,
                )
            except Exception as e:
                logger.warning(
                    "[pdf_ingest] embedding sync failed document_id=%s: %s",
                    document_id,
                    e,
                )
            try:
                from services.search_hybrid import invalidate_retrieval_cache_for_document

                invalidate_retrieval_cache_for_document(document_id)
            except Exception:
                pass

        tables_uploaded = 0
        if tables_data:
            batch_size = 50
            for i in range(0, len(tables_data), batch_size):
                batch = tables_data[i : i + batch_size]
                result = supabase.table("standard_tables").insert(batch).execute()
                if result.data:
                    tables_uploaded += len(batch)
                else:
                    raise RuntimeError(f"Failed to insert tables batch {i // batch_size + 1}")

        supabase.table("documents").update(
            {"status": "ready_for_search", "chunk_count": chunks_uploaded}
        ).eq("id", document_id).execute()

        notes_msg = (
            f"{admin_notes}\n\nPDF extract+ingest (job {job_id}). "
            f"Chunks: {chunks_uploaded}, tables: {tables_uploaded}. "
            f"Pipeline summary: {pipeline_result.get('summary', {})}"
        )

        if update_admin_queue:
            supabase.table("admin_queue").update(
                {"status": "completed", "admin_notes": notes_msg}
            ).eq("document_id", document_id).execute()

        debug_path = None
        if _pdf_debug_save_outputs_enabled():
            debug_path = str((_pdf_debug_output_root() / document_id / job_id).resolve())

        return {
            "job_id": job_id,
            "pipeline_result": pipeline_result,
            "chunks_count": chunks_uploaded,
            "tables_count": tables_uploaded,
            "admin_notes": notes_msg,
            "debug_output_dir": debug_path,
        }


async def run_word_pipeline_and_ingest_from_path(
    docx_path: Path,
    document_id: str,
    user_id: str,
    *,
    default_standard_name: Optional[str],
    replace_existing: bool = True,
    admin_notes: str = "",
    update_admin_queue: bool = False,
    standard_family: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract clauses/tables from a Word file, then insert like the PDF pipeline."""
    from pdf_pipeline.services.word_processor import process_docx
    from services.codebooks import resolve_upload_standard_family
    from services.standard_families import normalize_standard_family

    supabase = get_supabase_client()
    job_id = str(uuid.uuid4())
    parser_family = normalize_standard_family(
        standard_family
        or resolve_upload_standard_family(default_standard_name or "", codebook_label=default_standard_name)
    )
    logger.info(
        "[word_ingest] start document_id=%s job_id=%s codebook=%r family=%s",
        document_id,
        job_id,
        default_standard_name,
        parser_family,
    )

    with _pdf_ingest_workspace(job_id, document_id) as work_root:
        output_dir = work_root / "out"
        output_dir.mkdir(parents=True, exist_ok=True)
        pipeline_result = process_docx(
            str(docx_path),
            str(output_dir),
            standard_family=parser_family,
            document_title=default_standard_name or "Document",
        )
        _assert_outputs_quality(output_dir)

        clauses_csv = output_dir / "clauses.csv"
        tables_csv = output_dir / "tables.csv"
        chunks_data = []
        tables_data = []
        if clauses_csv.is_file():
            chunks_data = await parse_uploaded_chunks_content(
                clauses_csv.read_bytes(),
                "clauses.csv",
                document_id,
                user_id,
                codebook=default_standard_name,
            )
        if tables_csv.is_file():
            tables_data = await parse_uploaded_tables_content(
                tables_csv.read_bytes(),
                "tables.csv",
                document_id,
                user_id,
                default_standard_name=default_standard_name,
            )
        if not chunks_data and not tables_data:
            raise PdfNoStructuredContentError()

        if replace_existing:
            delete_existing_chunks_and_tables(supabase, document_id)

        chunks_uploaded = 0
        if chunks_data:
            for i in range(0, len(chunks_data), 100):
                batch = chunks_data[i : i + 100]
                result = supabase.table("chunks").insert(batch).execute()
                if not result.data:
                    raise RuntimeError(f"Failed to insert chunks batch {i // 100 + 1}")
                chunks_uploaded += len(batch)

        if chunks_uploaded:
            from services.chunk_embedding_service import sync_document_embeddings

            try:
                sync_document_embeddings(document_id, user_id)
            except Exception as exc:
                logger.warning("[word_ingest] embedding sync failed document_id=%s: %s", document_id, exc)

        tables_uploaded = 0
        if tables_data:
            for i in range(0, len(tables_data), 50):
                batch = tables_data[i : i + 50]
                result = supabase.table("standard_tables").insert(batch).execute()
                if not result.data:
                    raise RuntimeError(f"Failed to insert tables batch {i // 50 + 1}")
                tables_uploaded += len(batch)

        supabase.table("documents").update(
            {"status": "ready_for_search", "chunk_count": chunks_uploaded}
        ).eq("id", document_id).execute()

        notes_msg = (
            f"{admin_notes}\n\nWord extract+ingest (job {job_id}). "
            f"Chunks: {chunks_uploaded}, tables: {tables_uploaded}. "
            f"Pipeline summary: {pipeline_result.get('summary', {})}"
        )
        if update_admin_queue:
            supabase.table("admin_queue").update(
                {"status": "completed", "admin_notes": notes_msg}
            ).eq("document_id", document_id).execute()

        return {
            "job_id": job_id,
            "pipeline_result": pipeline_result,
            "chunks_count": chunks_uploaded,
            "tables_count": tables_uploaded,
            "admin_notes": notes_msg,
        }


async def run_hybrid_word_pdf_ingest_from_paths(
    docx_path: Path,
    pdf_path: Path,
    document_id: str,
    user_id: str,
    *,
    default_standard_name: Optional[str],
    replace_existing: bool = True,
    admin_notes: str = "",
    standard_family: Optional[str] = None,
) -> Dict[str, Any]:
    """Word for clauses; PDF + Modal for tables."""
    from pdf_pipeline.services.word_processor import process_docx
    from pdf_pipeline.services.pdf_processor import PDFProcessor
    from pdf_pipeline.settings import settings as pdf_settings
    from services.codebooks import resolve_upload_standard_family
    from services.standard_families import normalize_standard_family

    supabase = get_supabase_client()
    job_id = str(uuid.uuid4())
    parser_family = normalize_standard_family(
        standard_family
        or resolve_upload_standard_family(default_standard_name or "", codebook_label=default_standard_name)
    )
    has_modal = bool((pdf_settings.modal_endpoint or os.getenv("MODAL_ENDPOINT") or "").strip())
    force_modal = has_modal and not pdf_settings.disable_modal
    logger.info(
        "[hybrid_ingest] document_id=%s word=%s pdf=%s family=%s force_modal=%s",
        document_id,
        docx_path,
        pdf_path,
        parser_family,
        force_modal,
    )

    with _pdf_ingest_workspace(job_id, document_id) as work_root:
        word_dir = work_root / "word"
        pdf_dir = work_root / "pdf"
        word_dir.mkdir(parents=True, exist_ok=True)
        pdf_dir.mkdir(parents=True, exist_ok=True)

        word_result = process_docx(
            str(docx_path),
            str(word_dir),
            standard_family=parser_family,
            document_title=default_standard_name or "Document",
        )
        pdf_result = await PDFProcessor().process_pdf(
            input_path=str(pdf_path),
            output_dir=str(pdf_dir),
            job_id=job_id,
            enable_enhancement=pdf_settings.pdf_pipeline_enhancement,
            force_modal=force_modal,
            standard_family=parser_family,
        )
        _assert_outputs_quality(word_dir)
        _assert_outputs_quality(pdf_dir)

        clauses_csv = word_dir / "clauses.csv"
        tables_csv = pdf_dir / "tables.csv"
        if not tables_csv.is_file():
            tables_csv = word_dir / "tables.csv"

        chunks_data = []
        tables_data = []
        if clauses_csv.is_file():
            chunks_data = await parse_uploaded_chunks_content(
                clauses_csv.read_bytes(),
                "clauses.csv",
                document_id,
                user_id,
                codebook=default_standard_name,
            )
        if tables_csv.is_file():
            tables_data = await parse_uploaded_tables_content(
                tables_csv.read_bytes(),
                "tables.csv",
                document_id,
                user_id,
                default_standard_name=default_standard_name,
            )
        if not chunks_data and not tables_data:
            raise PdfNoStructuredContentError()

        if replace_existing:
            delete_existing_chunks_and_tables(supabase, document_id)

        chunks_uploaded = 0
        if chunks_data:
            for i in range(0, len(chunks_data), 100):
                batch = chunks_data[i : i + 100]
                result = supabase.table("chunks").insert(batch).execute()
                if not result.data:
                    raise RuntimeError(f"Failed to insert chunks batch {i // 100 + 1}")
                chunks_uploaded += len(batch)
            try:
                from services.chunk_embedding_service import sync_document_embeddings

                sync_document_embeddings(document_id, user_id)
            except Exception as exc:
                logger.warning("[hybrid_ingest] embedding sync failed: %s", exc)

        tables_uploaded = 0
        if tables_data:
            for i in range(0, len(tables_data), 50):
                batch = tables_data[i : i + 50]
                result = supabase.table("standard_tables").insert(batch).execute()
                if not result.data:
                    raise RuntimeError(f"Failed to insert tables batch {i // 50 + 1}")
                tables_uploaded += len(batch)

        supabase.table("documents").update(
            {"status": "ready_for_search", "chunk_count": chunks_uploaded}
        ).eq("id", document_id).execute()

        notes_msg = (
            f"{admin_notes}\n\nHybrid Word+PDF ingest (job {job_id}). "
            f"Clauses from Word: {chunks_uploaded}. Tables from Modal/PDF: {tables_uploaded}."
        )
        return {
            "job_id": job_id,
            "pipeline_result": {
                "word": word_result,
                "pdf": pdf_result,
                "summary": {"clauses": chunks_uploaded, "tables": tables_uploaded, "source": "word+pdf"},
            },
            "chunks_count": chunks_uploaded,
            "tables_count": tables_uploaded,
            "admin_notes": notes_msg,
        }
