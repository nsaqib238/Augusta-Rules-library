"""
Post-process Modal table extraction: repair ids, drop false-positive regions.

Modal often returns page-based fallback ids (e.g. 35.1) and bbox hits on glossary
clauses, figures, and prose blocks. This module runs on the backend after HTTP response.
"""
from __future__ import annotations

import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# AS/NZS table numbers: 1.8, 5.1.5, 7.4, J.1, L.8.2.2, M.1
_TABLE_NUM_PATTERNS = [
    re.compile(
        r"\bTable\s+((?:[A-Z]\.)?(?:\d+(?:\.\d+)+|[A-Z]\d*(?:\.\d+)*))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bTABLE\s+((?:[A-Z]\.)?(?:\d+(?:\.\d+)+|[A-Z]\d*(?:\.\d+)*))\b",
    ),
]

_FIGURE_RE = re.compile(r"\bFigure\s+[\w][\w.\(\)]*", re.IGNORECASE)
_BIBLIOGRAPHY_RE = re.compile(r"\bBibliography\b", re.IGNORECASE)
_DESIGNER_STMT_RE = re.compile(r"Designer['\u2019]?s\s+statement", re.IGNORECASE)
_GLOSSARY_CLAUSE_RE = re.compile(r"^\d+\.\d+\.\d+\s", re.MULTILINE)
_FALLBACK_ID_RE = re.compile(r"^(\d+)\.(\d+)$")


def _table_page(table: Dict[str, Any]) -> int:
    try:
        return int(table.get("page_start") or table.get("page") or 0)
    except (TypeError, ValueError):
        return 0


def _combined_text(table: Dict[str, Any]) -> str:
    parts = [
        str(table.get("table_number") or ""),
        str(table.get("title") or ""),
        str(table.get("yaml_description") or ""),
    ]
    meta = table.get("metadata")
    if isinstance(meta, dict):
        parts.append(str(meta.get("table_number") or ""))
        parts.append(str(meta.get("caption") or ""))
    return "\n".join(p for p in parts if p)


def _fix_ocr_table_token(raw: str) -> str:
    """Common OCR: Table J.l → J.1"""
    s = (raw or "").strip()
    s = re.sub(r"^([A-Z])\.l$", r"\1.1", s, flags=re.IGNORECASE)
    s = re.sub(r"^([A-Z])\.l\.(\d+)$", r"\1.1.\2", s, flags=re.IGNORECASE)
    return s


def extract_table_number_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    best: Optional[str] = None
    best_len = 0
    for pat in _TABLE_NUM_PATTERNS:
        for m in pat.finditer(text):
            candidate = _fix_ocr_table_token(m.group(1))
            if len(candidate) > best_len:
                best = candidate
                best_len = len(candidate)
    return best


def is_modal_fallback_table_id(table_number: str, page: int) -> bool:
    """
    Modal fallback ids look like '{page}.{index}' e.g. page 35 → 35.1, 101 → 101.2.
    """
    m = _FALLBACK_ID_RE.match(str(table_number or "").strip())
    if not m:
        return False
    try:
        return int(m.group(1)) == int(page) and page > 0
    except ValueError:
        return False


def repair_modal_table(table: Dict[str, Any]) -> Dict[str, Any]:
    """Try to replace fallback / missing table numbers from yaml, title, metadata."""
    out = dict(table)
    page = _table_page(out)
    current = str(out.get("table_number") or "").strip()
    current = _fix_ocr_table_token(current)

    extracted = extract_table_number_from_text(_combined_text(out))
    if extracted:
        if (
            not current
            or current.lower() in ("unknown", "none", "null")
            or is_modal_fallback_table_id(current, page)
            or (current != extracted and len(extracted) >= len(current))
        ):
            if current != extracted:
                logger.info(
                    "Repaired table id page %s: %r → %r",
                    page,
                    current or "(empty)",
                    extracted,
                )
            out["table_number"] = extracted
            out["table_id_repaired"] = True
    elif current:
        out["table_number"] = current

    return out


def _row_cell_texts(table: Dict[str, Any]) -> List[str]:
    texts: List[str] = []
    for key in ("header_rows", "data_rows"):
        for row in table.get(key) or []:
            cells = row if isinstance(row, list) else []
            texts.extend(str(c).strip() for c in cells if str(c).strip())
    return texts


def _looks_like_glossary_block(table: Dict[str, Any]) -> bool:
    """Definition lists (1.4.16 term — description) mis-detected as tables."""
    cells = _row_cell_texts(table)
    if len(cells) < 4:
        return False
    clause_like = sum(1 for c in cells if re.match(r"^\d+\.\d+\.\d+\b", c))
    if clause_like >= max(2, len(cells) // 4):
        return True
    combined = " ".join(cells[:20])
    if _GLOSSARY_CLAUSE_RE.search(combined) and table.get("column_count", 3) <= 3:
        return True
    return False


def should_drop_modal_table(table: Dict[str, Any]) -> Tuple[bool, str]:
    page = _table_page(table)
    table_num = str(table.get("table_number") or "").strip()
    blob = _combined_text(table)

    data_rows = table.get("data_rows") or []
    if len(data_rows) == 0:
        return True, "no_data_rows"

    if _FIGURE_RE.search(blob) and not extract_table_number_from_text(blob):
        return True, "figure_not_table"

    if _BIBLIOGRAPHY_RE.search(blob):
        return True, "bibliography"

    if _DESIGNER_STMT_RE.search(blob):
        return True, "form_not_table"

    if is_modal_fallback_table_id(table_num, page) and not table.get("table_id_repaired"):
        yaml = str(table.get("yaml_description") or "")
        if not extract_table_number_from_text(yaml):
            return True, "fallback_id_no_real_table"

    if _looks_like_glossary_block(table):
        return True, "glossary_not_table"

    return False, ""


def repair_and_filter_modal_tables(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    repaired = [repair_modal_table(t) for t in (tables or [])]
    kept: List[Dict[str, Any]] = []
    dropped = 0
    for table in repaired:
        drop, reason = should_drop_modal_table(table)
        if drop:
            dropped += 1
            logger.info(
                "Dropped Modal table page %s id=%r: %s",
                _table_page(table),
                table.get("table_number"),
                reason,
            )
            continue
        kept.append(table)
    if dropped:
        logger.info(
            "Modal table repair: %s → %s tables (%s dropped)",
            len(tables or []),
            len(kept),
            dropped,
        )
    return kept
