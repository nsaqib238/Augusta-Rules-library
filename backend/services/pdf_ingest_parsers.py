"""
CSV parsers for chunk/table ingestion (admin CSV uploads + Modal PDF pipeline exports).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# NCC clause CSVs can have very long text fields (default csv limit is 128KB).
_csv_max = getattr(sys, "maxsize", 2**31 - 1)
csv.field_size_limit(min(_csv_max, 10 * 1024 * 1024))


def _canonical_table_number(raw: str) -> str:
    """Match clause_units sync: one logical table per label (4.3 vs Table 4.3)."""
    normalized = str(raw or "").strip()
    if normalized.lower().startswith("table "):
        normalized = normalized[6:].strip()
    return f"Table {normalized}" if normalized else "Table unknown"


def _csv_clause_body_text(row: Dict[str, Any]) -> str:
    """
    Primary clause text for AS exports (body/body_full handled separately) and NCC exports
    (ncc2022_volume1_full_final-style: full_clause_text, text, unit_label, etc.).
    """
    for key in ("full_clause_text", "text", "raw_text", "content", "body_full", "body"):
        v = (row.get(key) or "").strip()
        if v:
            return v
    title = (row.get("title") or "").strip()
    raw_title = (row.get("raw_title") or "").strip()
    parts = [p for p in (title, raw_title) if p]
    return "\n\n".join(parts).strip()


def _csv_clause_number(row: Dict[str, Any]) -> str:
    """Clause / unit id: AS uses clause_number; NCC uses unit_label, ncc_anchor, anchor_id."""
    for key in ("clause_number", "unit_label", "ncc_anchor", "anchor_id"):
        v = str(row.get(key) or "").strip()
        if v:
            return v
    return ""


def csv_page_number(row: Dict[str, Any]) -> Optional[int]:
    """Support `page_number` and PDF export `page` (e.g. '12-14')."""
    raw = row.get("page_number") or row.get("page") or ""
    if raw is None or raw == "":
        return None
    s = str(raw).strip().split("-")[0].strip()
    try:
        return int(s) if s else None
    except ValueError:
        return None


async def parse_uploaded_chunks_content(
    content: bytes,
    filename: str,
    document_id: str,
    user_id: str,
    *,
    codebook: Optional[str] = None,
    discipline: Optional[str] = None,
) -> List[Dict]:
    """Parse uploaded chunks content and convert to database format."""
    try:
        chunks_data: List[Dict] = []

        if filename.endswith(".json"):
            data = json.loads(content.decode("utf-8"))

            for i, chunk in enumerate(data):
                chunk_data = {
                    "document_id": document_id,
                    "user_id": user_id,
                    "chunk_index": i,
                    "text": chunk.get("text", ""),
                    "clause_number": chunk.get("clause_number"),
                    "page_number": chunk.get("page_number"),
                    "entities": json.dumps(chunk.get("entities", {})),
                    "detailed_analysis": json.dumps(chunk.get("detailed_analysis", {})),
                    "metadata": json.dumps(chunk.get("metadata", {})),
                }
                chunks_data.append(chunk_data)

        elif filename.endswith(".csv"):
            csv_content = content.decode("utf-8-sig")
            reader = csv.DictReader(csv_content.splitlines())

            rows = list(reader)
            if not rows:
                raise Exception("CSV file is empty")

            first_row = rows[0]
            has_rich_body_column = "body" in first_row or "body_full" in first_row

            for i, row in enumerate(rows):
                if has_rich_body_column:
                    text_content = (row.get("body_full") or row.get("body") or "").strip()
                    if not text_content:
                        text_content = _csv_clause_body_text(row)
                    heading = row.get("heading", "")

                    notes = row.get("notes", "[]")
                    exceptions = row.get("exceptions", "[]")

                    try:
                        notes_list = json.loads(notes) if notes else []
                    except Exception:
                        notes_list = [notes] if notes else []

                    try:
                        exceptions_list = json.loads(exceptions) if exceptions else []
                    except Exception:
                        exceptions_list = [exceptions] if exceptions else []

                    enhanced_text = text_content

                    if notes_list and any(notes_list):
                        notes_text = "\n\nNOTES:\n" + "\n".join(
                            [f"- {note}" for note in notes_list if note]
                        )
                        enhanced_text += notes_text

                    if exceptions_list and any(exceptions_list):
                        exceptions_text = "\n\nEXCEPTIONS:\n" + "\n".join(
                            [f"- {exc}" for exc in exceptions_list if exc]
                        )
                        enhanced_text += exceptions_text

                    cn = (row.get("clause_number") or "").strip() or _csv_clause_number(row)
                    if not enhanced_text.strip() and not cn:
                        continue
                    chunk_data = {
                        "document_id": document_id,
                        "user_id": user_id,
                        "chunk_index": i,
                        "text": enhanced_text,
                        "clause_number": cn,
                        "heading": heading,
                        "page_number": csv_page_number(row),
                        "entities": json.dumps(
                            {"notes": notes_list, "exceptions": exceptions_list}
                        ),
                        "detailed_analysis": json.dumps({}),
                        "metadata": json.dumps(
                            {
                                "id": row.get("id", ""),
                                "level": row.get("level", ""),
                                "parent_number": row.get("parent_number", ""),
                                "embeddable": row.get("embeddable", ""),
                            }
                        ),
                    }
                else:
                    plain = _csv_clause_body_text(row)
                    cn = (row.get("clause_number") or "").strip() or _csv_clause_number(row)
                    if not plain.strip() and not cn:
                        continue
                    hd = (row.get("heading") or "").strip() or (row.get("title") or "").strip()
                    chunk_data = {
                        "document_id": document_id,
                        "user_id": user_id,
                        "chunk_index": i,
                        "text": plain,
                        "clause_number": cn or None,
                        "heading": hd or None,
                        "page_number": csv_page_number(row),
                        "entities": row.get("entities", "{}"),
                        "detailed_analysis": row.get("detailed_analysis", "{}"),
                        "metadata": row.get("metadata", "{}"),
                    }

                chunks_data.append(chunk_data)

            if not chunks_data:
                headers = ", ".join(first_row.keys()) if first_row else "(no headers)"
                raise Exception(
                    f"No valid clause rows in CSV. Expected text in columns such as "
                    f"text, full_clause_text, body, or content. Found headers: {headers}"
                )

        else:
            raise Exception("Unsupported file format. Only JSON and CSV files are supported.")

        if codebook:
            for row in chunks_data:
                row["codebook"] = codebook
        if discipline:
            for row in chunks_data:
                row["discipline"] = discipline

        return chunks_data

    except Exception as e:
        logger.exception("Error parsing uploaded chunks content: %s", e)
        raise


async def parse_uploaded_tables_content(
    content: bytes,
    filename: str,
    document_id: str,
    user_id: str,
    default_standard_name: Optional[str] = None,
) -> List[Dict]:
    """Parse tables CSV (admin format or PDF-pipeline export)."""
    try:
        content_str = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content_str))
        rows = list(reader)

        if not rows:
            raise Exception("CSV file is empty")

        tables_data: List[Dict] = []

        for i, row in enumerate(rows):
            table_number = (
                row.get("table_number") or row.get("table_id") or row.get("table_label") or ""
            ).strip()
            title = (row.get("title") or row.get("table_title") or "").strip()
            text = (
                row.get("text") or row.get("table_content") or row.get("table_text") or ""
            ).strip()
            source_clause = row.get("source_clause_number") or row.get("clause_reference") or ""
            if isinstance(source_clause, str) and source_clause.lower().startswith("clause "):
                source_clause = source_clause[7:].strip()

            # When the caller passes default_standard_name (PDF ingest), prefer it over CSV
            # "source"/"standard_name" (e.g. "AS/NZS 3000") so RAG sync (ilike %AS3000%) matches.
            raw_default = (default_standard_name or "").strip()
            from_csv = (row.get("standard_name") or "").strip() or (row.get("source") or "").strip()
            standard_name = raw_default or from_csv or "AS3000"

            table_data = {
                "document_id": document_id,
                "user_id": user_id,
                "table_id": row.get("table_id", f"{document_id}:table:{table_number or i}"),
                "standard_name": standard_name,
                "table_number": table_number,
                "title": title,
                "source_clause_number": source_clause,
                "text": text,
                "notes": json.dumps(json.loads(row.get("notes", "[]")) if row.get("notes") else []),
                "exceptions": json.dumps(
                    json.loads(row.get("exceptions", "[]")) if row.get("exceptions") else []
                ),
                "metadata": json.dumps(json.loads(row.get("metadata", "{}")) if row.get("metadata") else {}),
            }

            if not table_data["table_number"] or not table_data["text"]:
                logger.warning("Skipping table row %s: missing table_number or text", i)
                continue

            tables_data.append(table_data)

        # standard_tables has UNIQUE(document_id, table_number); pipeline may emit duplicates.
        merged: Dict[str, Dict] = {}
        for t in tables_data:
            canon = _canonical_table_number(t["table_number"])
            t["table_number"] = canon
            t["table_id"] = f"{document_id}:table:{canon}"
            if canon not in merged:
                merged[canon] = t
                continue
            prev = merged[canon]
            pt, nt = (prev.get("text") or "").strip(), (t.get("text") or "").strip()
            if nt and nt != pt:
                prev["text"] = (pt + "\n\n" + nt).strip() if pt else nt
            ptit, ntit = (prev.get("title") or "").strip(), (t.get("title") or "").strip()
            if len(ntit) > len(ptit):
                prev["title"] = ntit
            psc, nsc = (prev.get("source_clause_number") or "").strip(), (
                t.get("source_clause_number") or ""
            ).strip()
            if nsc and nsc != psc:
                prev["source_clause_number"] = (psc + "; " + nsc).strip("; ") if psc else nsc

        out = list(merged.values())
        if len(out) < len(tables_data):
            logger.info(
                "Deduplicated tables by canonical table_number: %s -> %s rows",
                len(tables_data),
                len(out),
            )
        logger.info("Parsed %s tables from %s", len(out), filename)
        return out

    except Exception as e:
        logger.error("Error parsing tables CSV: %s", e)
        raise Exception(f"Failed to parse tables CSV: {str(e)}") from e
