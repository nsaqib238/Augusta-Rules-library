"""
Table Normalization Service
============================

Provides utilities for normalizing table IDs, resolving parent clauses,
and improving table metadata quality.

Key Functions:
- normalize_table_id(): Clean table numbers to canonical format
- resolve_parent_clause(): Find specific governing clause
- merge_continued_tables(): Combine multi-page tables
"""

import re
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def normalize_table_id(table_number: str) -> Tuple[str, str]:
    """
    Normalize table ID to canonical format.
    
    Removes prefixes like "TABLE", "Table", and extracts clean identifier.
    
    Examples:
        "TABLE 4.1" → ("4.1", "Table 4.1")
        "Table 3.2" → ("3.2", "Table 3.2")
        "4.3" → ("4.3", "Table 4.3")
        "C10" → ("C10", "Table C10")
        "D12(A)" → ("D12(A)", "Table D12(A)")
    
    Args:
        table_number: Raw table number from extraction
        
    Returns:
        Tuple of (canonical_id, formatted_label)
        - canonical_id: Clean identifier (e.g., "4.1", "C10")
        - formatted_label: Display label (e.g., "Table 4.1")
    """
    if not table_number:
        return ("Unknown", "Table Unknown")
    
    # Remove common prefixes (case-insensitive)
    cleaned = re.sub(r'^(TABLE|Table|table)\s*', '', table_number.strip())
    cleaned = re.sub(r"^([A-Z])\.l$", r"\1.1", cleaned, flags=re.IGNORECASE)

    # Full dotted ids: 5.1.5, 7.4, J.1, L.8.2.2, M.1, C10, D12(A)
    match = re.search(
        r"(?:Appendix|Annex)\s+([A-Z](?:\.\d+)*)"
        r"|([A-Z]\.\d+(?:\.\d+)*)"
        r"|([A-Z]?\d+(?:\.\d+)+)"
        r"|([A-Z]?\d+[A-Z]?(?:\([A-Z]\))?)",
        cleaned,
        re.IGNORECASE,
    )

    if match:
        canonical_id = next(g for g in match.groups() if g)
    else:
        canonical_id = cleaned if cleaned else "Unknown"
    
    # Build formatted label
    formatted_label = f"Table {canonical_id}"
    
    return (canonical_id, formatted_label)


def resolve_parent_clause(
    table_number: str,
    table_title: str = None,
    page_number: int = None,
    clause_list: List[Dict] = None
) -> Optional[str]:
    """
    Resolve the most specific governing clause for a table.
    
    Strategy:
    1. Try to match table number to clause number pattern
       - Table 4.1 → Look for Clause 4.1 or section 4
    2. Try to find clause that references this table
    3. Use section-level clause as fallback
    
    Examples:
        Table 4.1 → "4.1" (specific clause) or "4" (section)
        Table C10 → "C" (appendix section)
        Table 3.7 → "3.7" or "3"
    
    Args:
        table_number: Table number (e.g., "4.1", "Table 3.2")
        table_title: Optional table title for context
        page_number: Optional page number for proximity matching
        clause_list: Optional list of clauses to search
        
    Returns:
        Most specific governing clause reference, or None
    """
    if not table_number:
        return None
    
    # Normalize table ID first
    canonical_id, _ = normalize_table_id(table_number)
    
    # Extract section from table number
    # Examples: "4.1" → "4", "3.7" → "3", "C10" → "C"
    section_match = re.match(r'([A-Z]?\d+)', canonical_id, re.IGNORECASE)
    
    if section_match:
        section = section_match.group(1)
    else:
        section = canonical_id
    
    # If clause list provided, search for matching or referencing clause
    if clause_list:
        # Try exact match first (e.g., Table 4.1 → Clause 4.1)
        exact_match = _find_clause_by_number(clause_list, canonical_id)
        if exact_match:
            return exact_match.get("clause_number")
        
        # Try to find clause that mentions this table
        referencing_clause = _find_clause_referencing_table(clause_list, canonical_id)
        if referencing_clause:
            return referencing_clause.get("clause_number")
        
        # Try section-level match (e.g., Table 4.1 → Clause 4)
        section_match = _find_clause_by_number(clause_list, section)
        if section_match:
            return section_match.get("clause_number")
    
    # Fallback: Return section-level reference
    # Prefer specific clause notation (e.g., "4.1" over "4")
    if "." in canonical_id or "(" in canonical_id:
        return canonical_id  # Already specific
    else:
        return section  # Section level


def _find_clause_by_number(clause_list: List[Dict], clause_number: str) -> Optional[Dict]:
    """Find clause by exact number match."""
    for clause in clause_list:
        if clause.get("clause_number") == clause_number:
            return clause
    return None


def _find_clause_referencing_table(clause_list: List[Dict], table_number: str) -> Optional[Dict]:
    """Find clause that references this table in its text."""
    patterns = [
        f"Table {table_number}",
        f"table {table_number}",
        f"TABLE {table_number}",
    ]
    
    for clause in clause_list:
        text = clause.get("text", "")
        if any(pattern in text for pattern in patterns):
            return clause
    
    return None


def _table_sort_key(t: Dict) -> tuple:
    """Order tables by page, then vertical position (top-first) for same-page slices."""
    p = t.get("page_start") or t.get("page") or 0
    bbox = t.get("bbox") or {}
    y0 = 0
    if isinstance(bbox, dict):
        y0 = bbox.get("y0", 0) or 0
    return (p, float(y0))


def _append_continuation_slice(current: Dict, incoming: Dict, page: int) -> None:
    """Append data rows, typed rows, and footer notes from a continuation page/slice."""
    new_data_rows = incoming.get("data_rows") or []
    current.setdefault("data_rows", []).extend(new_data_rows)

    inc_rows = incoming.get("rows") or []
    if inc_rows:
        current.setdefault("rows", []).extend(inc_rows)

    current_notes = current.setdefault("footer_notes", [])
    for note in incoming.get("footer_notes") or []:
        if note not in current_notes:
            current_notes.append(note)

    inc_yaml = (incoming.get("yaml_description") or "").strip()
    if inc_yaml:
        cur_yaml = (current.get("yaml_description") or "").strip()
        if not cur_yaml:
            current["yaml_description"] = inc_yaml
        elif inc_yaml not in cur_yaml:
            current["yaml_description"] = f"{cur_yaml}\n\n--- continued ---\n\n{inc_yaml}"

    cur_start = current.get("page_start") or 0
    cur_end = current.get("page_end") or cur_start
    inc_end = incoming.get("page_end") or page
    current["page_end"] = max(cur_end, inc_end, page)
    current["page_start"] = min(cur_start, page) if cur_start else page
    current["is_multipage"] = True


def merge_continued_tables(tables: List[Dict]) -> List[Dict]:
    """
    Merge tables that continue across multiple pages.
    
    Detects continuation patterns:
    - Table number contains "(continued)", "(cont.)", "continued"
    - Same canonical table number on the next page (native captions often strip "(continued)")
    - Explicit "continues_from_previous" flag
    - Same page, same id, when continues_from_previous is set
    
    Strategy:
    - Combine data_rows and header_rows
    - Merge footer_notes
    - Update page_start and page_end
    - Keep first table's metadata as base
    
    Args:
        tables: List of table dictionaries
        
    Returns:
        List of tables with continuations merged
    """
    if not tables:
        return []
    
    sorted_tables = sorted(tables, key=_table_sort_key)
    
    merged = []
    current = None
    
    for table in sorted_tables:
        table_number = table.get("table_number", "") or ""
        page = int(table.get("page_start") or table.get("page") or 0)
        
        canonical_id, _ = normalize_table_id(table_number)
        
        is_continuation = (
            "(continued)" in table_number.lower() or
            "(cont.)" in table_number.lower() or
            "continued" in table_number.lower() or
            table.get("continues_from_previous", False)
        )
        
        should_merge = False
        if current and canonical_id not in ("", "Unknown"):
            cur_num = str(current.get("table_number", "") or "")
            if cur_num.upper().startswith("MODAL_P") or str(table_number).upper().startswith("MODAL_P"):
                should_merge = False
            else:
                current_canonical_id, _ = normalize_table_id(cur_num)
                cleaned_id = re.sub(
                    r"\s*\(continued\)|\(cont\.\)|continued",
                    "",
                    canonical_id,
                    flags=re.IGNORECASE,
                ).strip()

                if is_continuation and cleaned_id == current_canonical_id:
                    should_merge = True
                elif (
                    not is_continuation
                    and canonical_id == current_canonical_id
                ):
                    cur_end = current.get("page_end") or current.get("page_start") or 0
                    if page and cur_end and page == cur_end + 1:
                        should_merge = True
                    elif page and cur_end and page == cur_end and table.get("continues_from_previous"):
                        should_merge = True

        if should_merge:
            logger.info(
                "Merging continuation slice: table %s page %s into table starting page %s",
                table_number,
                page,
                current.get("page_start"),
            )
            _append_continuation_slice(current, table, page)
            continue
        
        if current:
            merged.append(current)
        
        current = table.copy()
        current["table_number"] = canonical_id
        current["page_start"] = page
        current["page_end"] = int(table.get("page_end") or page)
    
    if current:
        merged.append(current)
    
    logger.info(f"Table merging: {len(sorted_tables)} → {len(merged)} tables (merged {len(sorted_tables) - len(merged)} continuations)")
    
    return merged


def enhance_table_metadata(table: Dict, clause_list: List[Dict] = None) -> Dict:
    """
    Enhance table metadata with normalized IDs and parent clauses.
    
    Applies:
    - Table ID normalization
    - Parent clause resolution
    - Label formatting
    
    Args:
        table: Table dictionary to enhance
        clause_list: Optional clause list for parent resolution
        
    Returns:
        Enhanced table dictionary (modified in place)
    """
    table_number = table.get("table_number", "")
    
    # Normalize table ID
    canonical_id, formatted_label = normalize_table_id(table_number)
    table["table_number"] = canonical_id
    table["table_label"] = formatted_label
    
    # Resolve parent clause if not already set
    if not table.get("parent_clause_reference"):
        parent_clause = resolve_parent_clause(
            table_number=canonical_id,
            table_title=table.get("title"),
            page_number=table.get("page_start"),
            clause_list=clause_list
        )
        
        if parent_clause:
            table["parent_clause_reference"] = parent_clause
            logger.debug(f"Resolved parent clause for Table {canonical_id}: {parent_clause}")
    
    return table


def batch_enhance_tables(tables: List[Dict], clause_list: List[Dict] = None) -> List[Dict]:
    """
    Batch enhance all tables with normalization and parent clause resolution.
    
    Also merges continued tables before enhancement.
    
    Args:
        tables: List of table dictionaries
        clause_list: Optional clause list for parent resolution
        
    Returns:
        List of enhanced tables with normalized metadata
    """
    # First, merge continued tables
    merged_tables = merge_continued_tables(tables)
    
    # Then enhance each table
    enhanced = []
    for table in merged_tables:
        enhanced_table = enhance_table_metadata(table, clause_list)
        enhanced.append(enhanced_table)
    
    logger.info(f"Enhanced {len(enhanced)} tables with normalized metadata")
    
    return enhanced


# ============================================================================
# Validation and Quality Checks
# ============================================================================

def validate_semantic_description(description: str) -> Dict:
    """
    Validate that a table description meets semantic criteria.
    
    Checks for:
    - Forbidden row dump patterns
    - Minimum description length
    - Semantic relationship patterns
    
    Args:
        description: Generated description text
        
    Returns:
        {
            "valid": bool,
            "issues": List[str],
            "has_semantic_patterns": bool,
            "word_count": int
        }
    """
    issues = []
    
    if not description or not description.strip():
        issues.append("Description is empty")
        return {
            "valid": False,
            "issues": issues,
            "has_semantic_patterns": False,
            "word_count": 0
        }
    
    # Check for forbidden patterns
    forbidden_patterns = [
        (r'\bRow \d+:', "Contains 'Row N:' pattern"),
        (r'The table contains \d+ rows', "Contains row count pattern"),
        (r'The table columns are:', "Describes column layout"),
        (r'\|\s*\|', "Contains pipe separators (table dump)"),
    ]
    
    for pattern, message in forbidden_patterns:
        if re.search(pattern, description):
            issues.append(message)
    
    # Check for semantic patterns (positive signals)
    semantic_patterns = [
        r'\bFor\b.*\bthe\b',  # "For X, the Y is Z"
        r'\bspecifies?\b',     # "This table specifies..."
        r'\bdefines?\b',       # "This table defines..."
        r'\brequires?\b',      # "requires", "requirement"
    ]
    
    has_semantic = any(re.search(pattern, description, re.IGNORECASE) for pattern in semantic_patterns)
    
    # Check minimum length (should be substantive)
    word_count = len(description.split())
    if word_count < 30:
        issues.append(f"Description too short ({word_count} words, minimum 30)")
    
    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "has_semantic_patterns": has_semantic,
        "word_count": word_count
    }
