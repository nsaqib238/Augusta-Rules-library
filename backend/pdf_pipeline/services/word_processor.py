"""
Word (.docx) extract — same clause/table CSVs as the PDF pipeline.

Word keeps heading and table order. That is why the old NCC Word→CSV run was
accurate. This path is family-generic (AS/NZS, IEC, SIR, network rules, etc.).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from pdf_pipeline.models.clause import Clause
from pdf_pipeline.models.table import Table, TableRow
from pdf_pipeline.services.clause_parser import ClauseParser
from pdf_pipeline.services.output_generator import OutputGenerator
from pdf_pipeline.services.pdf_processor import PDFProcessor
from pdf_pipeline.services.standard_clause_patterns import (
    extract_table_caption_id,
    match_clause_header,
    sanitize_pdf_text,
)
from services.standard_families import DEFAULT_STANDARD_FAMILY, normalize_standard_family

logger = logging.getLogger(__name__)


def _clean_cell(value: str) -> str:
    text = sanitize_pdf_text(str(value or ""))
    text = text.replace("\r", " ").replace("\n", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def _iter_blocks(doc):
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph

    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, doc)
        elif isinstance(child, CT_Tbl):
            yield DocxTable(child, doc)


def _table_rows(table) -> List[List[str]]:
    rows: List[List[str]] = []
    for row in table.rows:
        rows.append([_clean_cell(cell.text) for cell in row.cells])
    return rows


def _table_as_text(rows: List[List[str]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    lines = []
    for row in rows:
        cells = [row[i] if i < len(row) else "" for i in range(width)]
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def _table_as_prose(table_id: str, title: str, rows: List[List[str]]) -> str:
    if not rows:
        return f"{table_id} {title}".strip()
    header = rows[0]
    body = rows[1:] or []
    parts = [f"{table_id}: {title}".strip(": ")]
    for row in body:
        bits = []
        for i, cell in enumerate(row):
            if not cell:
                continue
            label = header[i] if i < len(header) and header[i] else f"col{i + 1}"
            bits.append(f"{label}: {cell}")
        if bits:
            parts.append("; ".join(bits))
    return "\n".join(parts)


def process_docx(
    input_path: str,
    output_dir: str,
    *,
    standard_family: str = DEFAULT_STANDARD_FAMILY,
    document_title: str = "Document",
) -> Dict[str, Any]:
    try:
        from docx import Document
        from docx.table import Table as DocxTable
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise RuntimeError("python-docx is required for Word upload. pip install python-docx") from exc

    family = normalize_standard_family(standard_family)
    doc = Document(input_path)
    para_lines: List[str] = []
    tables: List[Table] = []
    last_caption: Optional[str] = None
    last_table_id: Optional[str] = None
    last_clause: Optional[str] = None

    for block in _iter_blocks(doc):
        if isinstance(block, Paragraph):
            text = _clean_cell(block.text)
            if not text:
                continue
            caption_id = extract_table_caption_id(text)
            if caption_id:
                last_caption = text
                last_table_id = caption_id
            matched = match_clause_header(text, family)
            if matched:
                last_clause = matched[0]
            para_lines.append(text)
            continue

        if isinstance(block, DocxTable):
            rows = _table_rows(block)
            nonempty = [r for r in rows if any(c.strip() for c in r)]
            if not nonempty:
                continue
            table_id = last_table_id or (f"Table {last_clause or 'unnumbered'}-{len(tables) + 1}")
            title = ""
            if last_caption:
                title = re.sub(r"(?i)^table\s+\S+\s*[-–—:]?\s*", "", last_caption).strip()
            prose = _table_as_prose(table_id, title, nonempty)
            tables.append(
                Table(
                    table_id=f"word:{len(tables) + 1}:{table_id}",
                    table_number=table_id,
                    title=title or table_id,
                    parent_clause_number=last_clause,
                    parent_clause_reference=f"Clause {last_clause}" if last_clause else None,
                    page_start=1,
                    page_end=1,
                    header_rows=[TableRow(cells=nonempty[0], is_header=True)] if nonempty else [],
                    data_rows=[TableRow(cells=r) for r in nonempty[1:]],
                    normalized_text_representation=_table_as_text(nonempty),
                    yaml_description=prose,
                    confidence="high",
                    source_method="word_docx",
                )
            )
            last_caption = None
            last_table_id = None

    raw_text = "\n".join(para_lines)
    processor = PDFProcessor()
    normalized = processor._format_normalized_document(raw_text, standard_family=family)
    clause_dicts = ClauseParser().parse_from_text(normalized)
    clauses: List[Clause] = []
    for row in clause_dicts:
        try:
            row["page_start"] = int(str(row.get("page_start") or "1").split("-")[0])
            row["page_end"] = int(str(row.get("page_end") or row["page_start"]).split("-")[-1])
        except Exception:
            row["page_start"] = 1
            row["page_end"] = 1
        clauses.append(Clause(**row))

    OutputGenerator().generate_all(clauses, tables, output_dir, document_title)
    logger.info(
        "Word extract family=%s clauses=%s tables=%s path=%s",
        family,
        len(clauses),
        len(tables),
        input_path,
    )
    return {
        "summary": {
            "clauses": len(clauses),
            "tables": len(tables),
            "standard_family": family,
            "source": "docx",
        }
    }
