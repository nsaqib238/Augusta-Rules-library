"""
Simplified Table Processor - Uses Modal.com Complete Data
=========================================================
Modal.com provides complete table extraction, backend just validates.
"""

import logging
from typing import List, Dict, Any, Optional
from pdf_pipeline.models.table import Table

logger = logging.getLogger(__name__)


class TableProcessor:
    """Process tables from Modal.com (complete data, no extraction needed)"""

    def __init__(self):
        self.tables: List[Table] = []

    def process_tables_from_modal(
        self,
        modal_tables: List[Dict],
        clauses: List[Any] = None
    ) -> List[Table]:
        """
        Process tables from Modal.com complete extraction.
        
        Modal provides:
        - Complete table content (headers, data rows)
        - Table numbers and titles
        - Structure metadata
        
        Backend just:
        - Converts to Table objects
        - Links to parent clauses (if provided)
        - Validates data quality

        Args:
            modal_tables: Complete tables from Modal.com
            clauses: Optional list of clauses for parent linking

        Returns:
            List of Table objects
        """
        logger.info(f"Processing {len(modal_tables)} tables from Modal.com")

        if not modal_tables:
            logger.warning("No tables provided from Modal.com")
            return []

        # Convert Modal format to Table objects
        self.tables = self._convert_dicts_to_table_objects(modal_tables)

        # Link tables to parent clauses if provided
        if clauses:
            self._link_tables_to_clauses(clauses)
            logger.info("✅ Linked tables to parent clauses")

        logger.info(f"✅ Processed {len(self.tables)} tables from Modal.com")
        return self.tables

    def _convert_dicts_to_table_objects(self, table_dicts: List[Dict]) -> List[Table]:
        """Convert table dicts to Table model objects."""
        tables = []
        
        for table_dict in table_dicts:
            try:
                table = Table(**table_dict)
                tables.append(table)
            except Exception as e:
                logger.error(f"Error converting table to object: {e}")
                logger.debug(f"Table dict: {table_dict}")
                continue

        return tables

    def _link_tables_to_clauses(self, clauses: List[Any]):
        """Link tables to their parent clauses based on page location and table numbers."""
        if not clauses:
            return

        # Build clause lookup by page AND by number prefix
        clause_by_page = {}
        clause_by_number = {}
        
        for clause in clauses:
            # Index by page
            for page in range(clause.page_start, clause.page_end + 1):
                if page not in clause_by_page:
                    clause_by_page[page] = []
                clause_by_page[page].append(clause)
            
            # Index by number for quick lookup
            if clause.clause_number:
                clause_by_number[clause.clause_number] = clause

        # Link each table to nearest clause
        linked_count = 0
        for table in self.tables:
            page = table.page_start
            
            best_clause = None
            
            # STRATEGY 1: Exact or prefix match by number
            if table.table_number and not table.table_number.startswith("MODAL_P"):
                # Try exact match first
                if table.table_number in clause_by_number:
                    best_clause = clause_by_number[table.table_number]
                else:
                    # Try prefix match (e.g., table 3.6.1 → clause 3.6)
                    parts = table.table_number.split(".")
                    for i in range(len(parts), 0, -1):
                        prefix = ".".join(parts[:i])
                        if prefix in clause_by_number:
                            best_clause = clause_by_number[prefix]
                            break
            
            # STRATEGY 2: Search on same page
            if not best_clause and page in clause_by_page:
                page_clauses = clause_by_page[page]
                
                # Prefer deeper (more specific) clauses
                if page_clauses:
                    best_clause = max(page_clauses, key=lambda c: c.level)
            
            # STRATEGY 3: Search nearby pages (±1 page)
            if not best_clause:
                for nearby_page in [page - 1, page + 1]:
                    if nearby_page in clause_by_page:
                        nearby_clauses = clause_by_page[nearby_page]
                        if nearby_clauses:
                            best_clause = max(nearby_clauses, key=lambda c: c.level)
                            break
            
            if best_clause:
                table.parent_clause_id = best_clause.clause_id
                table.parent_clause_number = best_clause.clause_number
                linked_count += 1
        
        orphan_count = len(self.tables) - linked_count
        if orphan_count > 0:
            logger.warning(f"⚠️ {orphan_count} tables without clause links ({orphan_count/len(self.tables)*100:.1f}%)")
        logger.info(f"✅ Linked {linked_count}/{len(self.tables)} tables to clauses ({linked_count/len(self.tables)*100:.1f}%)")
