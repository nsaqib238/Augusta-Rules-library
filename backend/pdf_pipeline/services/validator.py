"""
Validation module for quality checks
"""
import logging
import re
from typing import List, Dict
from collections import Counter
from pdf_pipeline.models.clause import Clause, ValidationIssue
from pdf_pipeline.models.table import Table

logger = logging.getLogger(__name__)


class Validator:
    """Validate extracted clauses and tables for quality issues"""
    
    def __init__(self):
        self.issues: List[ValidationIssue] = []
    
    def validate_clauses(self, clauses: List[Clause]) -> List[ValidationIssue]:
        """
        Validate clause structure and hierarchy
        
        Args:
            clauses: List of clauses to validate
            
        Returns:
            List of validation issues
        """
        self.issues = []
        
        # Build clause number map
        clause_map = {c.clause_number: c for c in clauses}
        
        # Run global validation checks first
        self._check_single_digit_contamination(clauses)
        self._check_toc_contamination(clauses)
        self._check_page_attribution(clauses)
        self._check_has_parent_consistency(clauses)
        self._check_hierarchical_coverage(clauses)
        
        # Per-clause validation
        for clause in clauses:
            # Check for empty body
            if not clause.body_with_subitems.strip():
                self.issues.append(ValidationIssue(
                    type="empty_body",
                    severity="warning",
                    clause_id=clause.clause_id,
                    message=f"Clause {clause.clause_number} has empty body"
                ))
                clause.has_body = False
            
            # Check for missing parent
            if clause.parent_clause_number:
                if clause.parent_clause_number not in clause_map:
                    self.issues.append(ValidationIssue(
                        type="missing_parent",
                        severity="error",
                        clause_id=clause.clause_id,
                        message=f"Clause {clause.clause_number} references non-existent parent {clause.parent_clause_number}"
                    ))
                    clause.has_parent = False
            
            # Check for orphan notes
            if clause.notes and not clause.body_with_subitems.strip():
                self.issues.append(ValidationIssue(
                    type="orphan_note",
                    severity="warning",
                    clause_id=clause.clause_id,
                    message=f"Clause {clause.clause_number} has notes but no body"
                ))
                clause.is_orphan_note = True
            
            # Check for TOC-style content in body
            if self._has_toc_patterns(clause.body_with_subitems):
                self.issues.append(ValidationIssue(
                    type="toc_in_body",
                    severity="error",
                    clause_id=clause.clause_id,
                    message=f"Clause {clause.clause_number} body contains TOC-style content (dot leaders)"
                ))
        
        # Check for duplicate clause numbers
        clause_numbers = [c.clause_number for c in clauses]
        duplicates = set([num for num in clause_numbers if clause_numbers.count(num) > 1])
        
        for dup_num in duplicates:
            self.issues.append(ValidationIssue(
                type="duplicate_number",
                severity="error",
                message=f"Duplicate clause number: {dup_num}"
            ))
        
        logger.info(f"Validation found {len(self.issues)} issues in clauses")
        return self.issues
    
    def validate_tables(self, tables: List[Table]) -> List[ValidationIssue]:
        """
        Validate table structure
        
        Args:
            tables: List of tables to validate
            
        Returns:
            List of validation issues
        """
        table_issues = []
        
        numbers_seen: Dict[str, int] = {}
        for table in tables:
            # Check for missing headers
            if not table.header_rows:
                table_issues.append(ValidationIssue(
                    type="missing_headers",
                    severity="warning",
                    message=f"Table {table.table_number or table.table_id} has no header rows"
                ))
                table.has_headers = False

            # Check for empty data
            if not table.data_rows:
                table_issues.append(ValidationIssue(
                    type="empty_table",
                    severity="warning",
                    message=f"Table {table.table_number or table.table_id} has no data rows"
                ))

            if table.title and self._table_title_looks_like_clause_body(table.title):
                snippet = table.title if len(table.title) <= 100 else table.title[:97] + "..."
                table_issues.append(ValidationIssue(
                    type="suspicious_table_title",
                    severity="warning",
                    message=f"Table {table.table_number or table.table_id} title may include clause prose: {snippet}",
                ))

            if table.table_number:
                numbers_seen[table.table_number] = numbers_seen.get(table.table_number, 0) + 1

        for num, count in numbers_seen.items():
            if count > 1:
                table_issues.append(ValidationIssue(
                    type="duplicate_table_number",
                    severity="warning",
                    message=f"Table number {num} appears {count} times in output (check merge/dedup)",
                ))

        self.issues.extend(table_issues)
        logger.info(f"Validation found {len(table_issues)} issues in tables")
        return table_issues

    def _table_title_looks_like_clause_body(self, title: str) -> bool:
        t = title.strip()
        if len(t) > 100:
            return True
        if re.search(r"\b(shall|must not|must)\b", t, re.IGNORECASE):
            return True
        if re.search(r"\bin accordance with\b", t, re.IGNORECASE):
            return True
        return bool(re.match(r"^\d+(?:\.\d+)+\s+\S+", t))

    def _check_single_digit_contamination(self, clauses: List[Clause]):
        """Check if too many single-digit clause numbers exist (likely page numbers)"""
        if not clauses:
            return
        
        single_digit_clauses = [c for c in clauses if re.match(r'^\d{1,2}$', c.clause_number)]
        single_digit_ratio = len(single_digit_clauses) / len(clauses)
        
        if single_digit_ratio > 0.3:  # More than 30% are single digits
            self.issues.append(ValidationIssue(
                type="single_digit_contamination",
                severity="error",
                message=f"Too many single-digit clause numbers ({len(single_digit_clauses)}/{len(clauses)}). "
                        f"Likely parsing page numbers as clauses."
            ))
        
        # Check for suspicious titles (standard names)
        for clause in single_digit_clauses:
            if clause.title and re.match(r'(AS/NZS|ISO|IEC|BS|EN)\s+\d+', clause.title, re.IGNORECASE):
                self.issues.append(ValidationIssue(
                    type="header_as_clause",
                    severity="error",
                    clause_id=clause.clause_id,
                    message=f"Clause {clause.clause_number} has standard title '{clause.title}' - likely a header/footer"
                ))
    
    def _check_toc_contamination(self, clauses: List[Clause]):
        """Check for TOC-style patterns in clause bodies"""
        toc_pattern = re.compile(r'\.{3,}.*\d+\s*$', re.MULTILINE)
        
        toc_contaminated = 0
        for clause in clauses:
            body = clause.body_with_subitems
            if toc_pattern.search(body):
                toc_contaminated += 1
        
        if toc_contaminated > 0:
            self.issues.append(ValidationIssue(
                type="toc_contamination",
                severity="error",
                message=f"{toc_contaminated} clause(s) contain TOC-style dot leaders. "
                        f"Table of Contents not properly excluded."
            ))
    
    def _check_page_attribution(self, clauses: List[Clause]):
        """Check if page numbers are realistic"""
        if not clauses:
            return
        
        # Check if too many clauses default to page 1
        page_one_clauses = [c for c in clauses if c.page_start == 1 and c.page_end == 1]
        page_one_ratio = len(page_one_clauses) / len(clauses)
        
        if page_one_ratio > 0.7 and len(clauses) > 5:  # More than 70% on page 1
            self.issues.append(ValidationIssue(
                type="poor_page_tracking",
                severity="warning",
                message=f"{len(page_one_clauses)}/{len(clauses)} clauses attributed to page 1. "
                        f"Page tracking may not be working correctly."
            ))
    
    def _check_has_parent_consistency(self, clauses: List[Clause]):
        """Check for logical inconsistencies in has_parent field"""
        for clause in clauses:
            # has_parent=True but no parent fields set
            if clause.has_parent and not clause.parent_clause_id and not clause.parent_clause_number:
                self.issues.append(ValidationIssue(
                    type="inconsistent_parent",
                    severity="error",
                    clause_id=clause.clause_id,
                    message=f"Clause {clause.clause_number}: has_parent=True but parent fields are null"
                ))
            
            # has_parent=False but parent fields are set
            if not clause.has_parent and (clause.parent_clause_id or clause.parent_clause_number):
                self.issues.append(ValidationIssue(
                    type="inconsistent_parent",
                    severity="error",
                    clause_id=clause.clause_id,
                    message=f"Clause {clause.clause_number}: has_parent=False but parent fields are set"
                ))
    
    def _check_hierarchical_coverage(self, clauses: List[Clause]):
        """Check if we have proper hierarchical clause coverage (X.X format)"""
        if not clauses:
            return
        
        # Count clauses with hierarchical numbering (X.X, X.X.X)
        hierarchical_clauses = [c for c in clauses if '.' in c.clause_number]
        hierarchical_ratio = len(hierarchical_clauses) / len(clauses)
        
        if hierarchical_ratio < 0.3 and len(clauses) > 10:  # Less than 30% hierarchical
            self.issues.append(ValidationIssue(
                type="poor_hierarchy",
                severity="warning",
                message=f"Only {len(hierarchical_clauses)}/{len(clauses)} clauses have hierarchical numbering. "
                        f"Expected more X.X.X style clauses in a structured document."
            ))
        
        # Check if first clause looks reasonable
        if clauses:
            first_clause = clauses[0]
            if not re.match(r'^(1|1\.1|Appendix)', first_clause.clause_number, re.IGNORECASE):
                self.issues.append(ValidationIssue(
                    type="unexpected_first_clause",
                    severity="warning",
                    message=f"First clause is '{first_clause.clause_number}', expected '1' or '1.1'"
                ))
    
    def _has_toc_patterns(self, text: str) -> bool:
        """Check if text contains TOC-style patterns"""
        toc_patterns = [
            re.compile(r'\.{3,}'),  # Dot leaders
            re.compile(r'^\d+\.\d+\s+[A-Z\s]+\.{3,}\s*\d+$', re.MULTILINE),  # 1.1 SCOPE ... 33
        ]
        
        for pattern in toc_patterns:
            if pattern.search(text):
                return True
        return False
    
    def get_summary(self) -> Dict[str, int]:
        """Get summary of validation results"""
        summary = {
            "total_issues": len(self.issues),
            "errors": sum(1 for i in self.issues if i.severity == "error"),
            "warnings": sum(1 for i in self.issues if i.severity == "warning"),
            "info": sum(1 for i in self.issues if i.severity == "info")
        }
        return summary
