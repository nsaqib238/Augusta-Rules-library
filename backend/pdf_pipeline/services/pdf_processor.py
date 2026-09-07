"""
Simplified PDF Processor - Uses Modal.com for All Extraction
============================================================
Modal.com extracts both tables and clauses, backend validates and saves.
"""

import logging
from pathlib import Path
from typing import Dict, Any

from pdf_pipeline.settings import settings
from pdf_pipeline.services.modal_service import ModalService
from pdf_pipeline.services.pdf_text_preflight import sample_text_layer_stats
from pdf_pipeline.services.table_processor import TableProcessor
from pdf_pipeline.services.validator import Validator
from pdf_pipeline.services.output_generator import OutputGenerator
from pdf_pipeline.services.clause_parser import ClauseParser
from pdf_pipeline.models.clause import Clause
from pdf_pipeline.services.standard_clause_patterns import (
    clause_level_and_parent,
    match_clause_header,
    normalize_raw_text,
    sanitize_pdf_text,
)
from services.standard_families import DEFAULT_STANDARD_FAMILY, normalize_standard_family

logger = logging.getLogger(__name__)


class PDFProcessor:
    """Simplified PDF processor using Modal.com for extraction"""

    @staticmethod
    def _normalize_table_number_label(table_number: str | None) -> str | None:
        """
        Modal / caption parsers sometimes return 'Table 3.2', 'TABLE 3.2', or '3.2'.
        Normalize to a bare identifier like '3.2' for anchoring in page text.
        """
        import re

        if not table_number:
            return None
        t = str(table_number).strip()
        t = re.sub(r"(?is)^table\s+", "", t).strip()
        return t or None

    @staticmethod
    def _extract_footer_notes_from_page_text(
        page_text: str,
        table_number: str | None,
        table_title: str | None,
    ) -> list[str]:
        """
        Best-effort extraction of table footer notes (e.g. "NOTES:" blocks) from
        the page text.

        Many standards render table notes below the grid and they often won't be
        captured by table-structure models. This heuristic tries to pull those
        notes back in so they can be saved to `Table.footer_notes`.
        """
        import re

        if not page_text or not page_text.strip():
            return []

        text = page_text.replace("\r\n", "\n").replace("\r", "\n")

        # Anchor near the table to avoid stealing other tables' notes.
        anchors: list[str] = []
        num = PDFProcessor._normalize_table_number_label(table_number)
        if num:
            # Standards PDFs vary spacing: "TABLE 3.2", "Table 3.2", sometimes "TABLE3.2"
            anchors.extend(
                [
                    rf"\bTABLE\s+{re.escape(num)}\b",
                    rf"\bTable\s+{re.escape(num)}\b",
                    rf"\bTABLE{re.escape(num)}\b",
                    rf"\bTable{re.escape(num)}\b",
                ]
            )
        if table_title:
            title = str(table_title).strip()
            if title and len(title) >= 8:
                anchors.append(re.escape(title[:80]))

        start_idx = 0
        anchor_found = False
        for pat in anchors:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                start_idx = m.start()
                anchor_found = True
                break

        # If we expected to find an anchor but didn't, do NOT guess.
        # This prevents Table 3.1 accidentally picking up Table 3.2 notes.
        if anchors and not anchor_found:
            return []

        # Limit scan to this table region only: stop at the next TABLE heading.
        # This avoids attaching the next table's NOTES block to the current table.
        end_idx = len(text)
        next_table = re.search(r"(?im)^\s*(?:TABLE|Table)\s+\S+", text[start_idx + 1 :])
        if next_table:
            end_idx = start_idx + 1 + next_table.start()
        segment = text[start_idx:end_idx]

        # Find a NOTES block after the anchor.
        # PyMuPDF text often does NOT preserve line breaks the way the printed page looks,
        # so avoid requiring "^" line-start. Also handle "NOTE" singular headings.
        notes_match = re.search(r"(?is)\bNOTES?\b\s*[:\-–—]?\s*", segment)
        if not notes_match:
            # Some tables only publish an Exceptions block (no explicit NOTES heading).
            exc_match = re.search(r"(?is)\bEXCEPTIONS?\b", segment)
            if not exc_match:
                return []
            exc_tail = segment[exc_match.start() :]
            lines = [ln.strip() for ln in exc_tail.split("\n") if ln.strip()]
            extracted: list[str] = []
            extracted.append(lines[0].strip())
            # Parse remainder like normal items
            current: list[str] = []

            def flush_current():
                nonlocal current
                if current:
                    joined = " ".join([s for s in current if s]).strip()
                    if joined:
                        extracted.append(joined)
                    current = []

            item_start = re.compile(
                r"^\s*(?:\(\d{1,3}\)\s*|\d{1,3}\s*[\.\):]\s*|\([a-z]\)\s*|[a-z]\s*[\.\):]\s*)",
                re.IGNORECASE,
            )
            for ln in lines[1:]:
                if item_start.match(ln):
                    flush_current()
                    current.append(ln)
                elif current:
                    current.append(ln)
                elif len(ln) <= 260 and re.search(r"[A-Za-z]", ln):
                    current.append(ln)
            flush_current()
            return PDFProcessor._dedupe_note_lines(extracted)

        nm = notes_match
        line_end = segment.find("\n", nm.start())
        if line_end < 0:
            line_end = len(segment)
        after_on_line = segment[nm.end() : line_end].strip()
        rest = segment[line_end + 1 :]
        lines = [ln.strip() for ln in rest.split("\n")]
        if after_on_line:
            lines.insert(0, after_on_line)

        # Consume NOTES and optional EXCEPTIONS blocks until a hard stop.
        extracted: list[str] = []
        current: list[str] = []

        def flush_current():
            nonlocal current
            if current:
                joined = " ".join([s for s in current if s]).strip()
                if joined:
                    extracted.append(joined)
                current = []

        hard_stop_heading = re.compile(
            r"^(TABLE\s+\S+|FIGURE\s+\S+|CLAUSE\s+\S+|APPENDIX\b|NOTE\b(?!S)|SOURCE\b)$",
            re.IGNORECASE,
        )
        # Note/exception items can be "1." / "1)" / or just "1 <text>" in OCR.
        # Numbered notes: "1.", "1)", "(1)", "1 " ... plus lettered "(a)", "a."
        item_start = re.compile(
            r"^\s*(?:\(\d{1,3}\)\s*|\d{1,3}\s*[\.\):]\s*|\([a-z]\)\s*|[a-z]\s*[\.\):]\s*)",
            re.IGNORECASE,
        )
        # "EXCEPTIONS", "EXCEPTIONS:", "EXCEPTIONS TO TABLE 3.2 ...", etc.
        exceptions_heading = re.compile(r"^\s*EXCEPTIONS?\b.*$", re.IGNORECASE)
        copyrightish = re.compile(r"^\s*COPYRIGHT\b", re.IGNORECASE)

        for ln in lines:
            if not ln:
                continue

            if hard_stop_heading.match(ln):
                # stop if we already started collecting notes
                if extracted or current:
                    break
            if copyrightish.match(ln):
                if extracted or current:
                    break

            # Switch to exceptions mode if we hit an Exceptions heading
            if exceptions_heading.match(ln):
                flush_current()
                # Keep the real heading text (it often includes "TO TABLE ...")
                extracted.append(ln.strip())
                continue

            m = item_start.match(ln)
            if m:
                flush_current()
                current.append(ln)
                continue

            # Continuation line: attach to current note only if we already started one.
            if current:
                # Skip common filler lines that are not part of the notes
                if ln.lower().startswith("refer to manufacturer"):
                    current.append(ln)
                else:
                    current.append(ln)
            else:
                # If the first line isn't numbered, treat as an unnumbered note/exception only
                # when it looks like a sentence.
                if len(ln) <= 260 and re.search(r"[A-Za-z]", ln):
                    current.append(ln)

            # Safety: don't slurp the whole page
            if len(extracted) >= 60:
                break

        flush_current()

        return PDFProcessor._dedupe_note_lines(extracted)

    @staticmethod
    def _dedupe_note_lines(extracted: list[str]) -> list[str]:
        import re

        cleaned: list[str] = []
        seen: set[str] = set()
        for n in extracted:
            n2 = re.sub(r"\s+", " ", str(n)).strip()
            if not n2:
                continue
            key = n2.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(n2)
        return cleaned

    @staticmethod
    def _pymupdf_plaintext(input_path: str) -> str:
        """Concatenate page text from a PDF (PyMuPDF). Used when Modal is disabled or returns no text."""
        import fitz

        doc = fitz.open(input_path)
        try:
            pages_text = []
            for page_num in range(len(doc)):
                page = doc[page_num]
                pages_text.append(page.get_text() or "")
            return sanitize_pdf_text("\n\n".join(pages_text))
        finally:
            doc.close()

    def __init__(self):
        self.modal_service = ModalService()
        self.table_processor = TableProcessor()
        self.validator = Validator()
        self.output_generator = OutputGenerator()
        self.clause_parser = ClauseParser()

    async def process_pdf(
        self,
        input_path: str,
        output_dir: str,
        job_id: str,
        enable_enhancement: bool = True,
        *,
        force_modal: bool = False,
        standard_family: str = DEFAULT_STANDARD_FAMILY,
    ) -> Dict[str, Any]:
        """
        Complete PDF processing pipeline.

        When Modal is configured and ``DISABLE_MODAL`` is not set: tables (and optional
        normalized text) come from Modal; clauses are still parsed locally from text.

        When Modal is skipped (no ``MODAL_ENDPOINT`` or ``DISABLE_MODAL=true``): text is
        taken from the PDF via PyMuPDF; ``tables`` outputs are empty but ``clauses.csv``
        can still be exercised end-to-end.

        Args:
            input_path: Path to input PDF
            output_dir: Directory for output files
            job_id: Unique job identifier
            enable_enhancement: Enable Real-ESRGAN 2x enhancement (default: True)

        Returns:
            Processing result summary
        """
        logger.info(f"Starting PDF processing pipeline (Job ID: {job_id})")
        parser_family = normalize_standard_family(standard_family)
        logger.info("Clause parser family: %s", parser_family)
        result = {
            "job_id": job_id,
            "steps": {}
        }

        try:
            use_modal = self.modal_service.is_available()
            logger.info(
                "PDF pipeline job=%s use_modal=%s force_modal=%s preflight=%s endpoint=%s disable_modal=%s",
                job_id,
                use_modal,
                force_modal,
                settings.pdf_pipeline_text_preflight,
                settings.modal_endpoint or "(not set)",
                settings.disable_modal,
            )

            skip_modal_preflight = False
            preflight_stats = None
            if use_modal and settings.pdf_pipeline_text_preflight and not force_modal:
                preflight_stats = sample_text_layer_stats(
                    input_path,
                    max_sample_pages=settings.pdf_pipeline_preflight_max_sample_pages,
                )
                if preflight_stats is not None:
                    thr = settings.pdf_pipeline_preflight_skip_modal_below_avg_chars_per_page
                    if preflight_stats.avg_chars_per_page < thr:
                        skip_modal_preflight = True
                        logger.info(
                            "Text preflight: avg_chars_per_page=%.1f (sample %s/%s pages, %s chars) "
                            "is below threshold %.1f — skipping Modal to save resources",
                            preflight_stats.avg_chars_per_page,
                            preflight_stats.pages_sampled,
                            preflight_stats.total_pages,
                            preflight_stats.total_chars,
                            thr,
                        )
                    else:
                        logger.info(
                            "Text preflight: avg_chars_per_page=%.1f >= %.1f — proceeding with Modal",
                            preflight_stats.avg_chars_per_page,
                            thr,
                        )
                else:
                    logger.info("Text preflight: inconclusive (open/read failed) — proceeding with Modal")
            elif use_modal and force_modal and settings.pdf_pipeline_text_preflight:
                logger.info(
                    "Text preflight enabled but force_modal=true — always calling Modal for table extraction"
                )

            if use_modal and not skip_modal_preflight:
                # Step 1: Extract TABLES ONLY from Modal.com (clauses handled locally)
                logger.info("Step 1: Extracting tables via Modal.com...")
                extraction_result = self.modal_service.extract_tables_only(
                    Path(input_path),
                    filename=Path(input_path).name,
                    enable_enhancement=enable_enhancement,
                )

                # DEBUG: Log what Modal actually returned
                logger.info(f"Modal extraction result keys: {list(extraction_result.keys())}")
                logger.info(f"Modal success: {extraction_result.get('success')}")
                logger.info(f"Modal table_count: {extraction_result.get('table_count')}")
                logger.info(f"Modal clause_count: {extraction_result.get('clause_count')}")
                logger.info(f"Modal tables list length: {len(extraction_result.get('tables', []))}")
                logger.info(f"Modal clauses list length: {len(extraction_result.get('clauses', []))}")
                if extraction_result.get("error"):
                    logger.error(f"Modal returned error: {extraction_result.get('error')}")

                if not extraction_result.get("success"):
                    error_msg = extraction_result.get("error", "Unknown error")
                    raise Exception(f"Modal.com extraction failed: {error_msg}")

                result["steps"]["modal_extraction"] = {
                    "status": "success",
                    "table_count": extraction_result.get("table_count", 0),
                    "clause_count": extraction_result.get("clause_count", 0),
                    "processing_time": extraction_result.get("processing_time", 0),
                    "cost_estimate": extraction_result.get("cost_estimate", 0),
                }
                if preflight_stats is not None:
                    result["steps"]["modal_extraction"]["preflight"] = {
                        "avg_chars_per_page": round(preflight_stats.avg_chars_per_page, 2),
                        "pages_sampled": preflight_stats.pages_sampled,
                    }

                logger.info(
                    f"✅ Modal.com extracted {extraction_result.get('table_count', 0)} tables, "
                    f"{extraction_result.get('clause_count', 0)} clauses "
                    f"(${extraction_result.get('cost_estimate', 0):.3f})"
                )

                modal_tables = extraction_result.get("tables", [])
                modal_normalized_text = extraction_result.get("normalized_text", "")
                table_dicts = self.modal_service.convert_tables_to_objects(modal_tables)
            elif skip_modal_preflight:
                extraction_result = {
                    "success": True,
                    "table_count": 0,
                    "clause_count": 0,
                    "processing_time": 0,
                    "cost_estimate": 0,
                    "tables": [],
                    "normalized_text": "",
                }
                step = {
                    "status": "skipped",
                    "reason": "text_preflight_low_text_layer",
                    "table_count": 0,
                    "clause_count": 0,
                    "processing_time": 0,
                    "cost_estimate": 0,
                }
                if preflight_stats is not None:
                    step["preflight"] = {
                        "avg_chars_per_page": round(preflight_stats.avg_chars_per_page, 2),
                        "pages_sampled": preflight_stats.pages_sampled,
                        "total_pages": preflight_stats.total_pages,
                        "total_chars": preflight_stats.total_chars,
                        "threshold_avg_chars_per_page": settings.pdf_pipeline_preflight_skip_modal_below_avg_chars_per_page,
                    }
                result["steps"]["modal_extraction"] = step
                modal_tables = []
                modal_normalized_text = ""
                table_dicts = []
                logger.info("Step 1: Modal skipped after text preflight (no remote table extraction).")
                logger.warning(
                    "Modal NOT called — text preflight avg_chars/page below threshold. "
                    "Set PDF_PIPELINE_TEXT_PREFLIGHT=false or upload with force_modal to use Modal."
                )
            else:
                skip_reason = (
                    "DISABLE_MODAL=true in environment"
                    if settings.disable_modal
                    else "MODAL_ENDPOINT not set"
                )
                logger.info(
                    "Step 1: Skipping Modal (%s); using local PyMuPDF text for clauses (no remote tables).",
                    skip_reason,
                )
                extraction_result = {
                    "success": True,
                    "table_count": 0,
                    "clause_count": 0,
                    "processing_time": 0,
                    "cost_estimate": 0,
                    "tables": [],
                    "normalized_text": "",
                }
                result["steps"]["modal_extraction"] = {
                    "status": "skipped",
                    "reason": skip_reason,
                    "table_count": 0,
                    "clause_count": 0,
                    "processing_time": 0,
                    "cost_estimate": 0,
                }
                modal_tables = []
                modal_normalized_text = ""
                table_dicts = []

            # Step 2: Table dicts from Modal (or empty); clause text filled below
            logger.info("Step 2: Preparing table objects and clause source text...")
            
            # Step 2: Parse clauses locally from normalized text
            logger.info("Step 2: Parsing clauses from normalized text (local parser)...")
            logger.info(f"   📄 Processing {len(modal_normalized_text):,} characters of text...")
            if not modal_normalized_text:
                logger.warning("No normalized text from Modal; extracting plain text from PDF (PyMuPDF)...")
                modal_normalized_text = self._pymupdf_plaintext(input_path)
            else:
                modal_normalized_text = sanitize_pdf_text(modal_normalized_text)
            
            # Create normalized_document.txt format
            logger.info("   🔄 Formatting normalized document with clause markers...")
            normalized_content = self._format_normalized_document(
                modal_normalized_text,
                standard_family=parser_family,
            )
            logger.info("   ✅ Document formatted")
            
            # Parse clauses
            logger.info("   🔍 Parsing clauses with regex pattern matching...")
            clause_dicts = self.clause_parser.parse_from_text(normalized_content)
            logger.info(f"   ✅ Parsed {len(clause_dicts)} clause dictionaries")
            logger.info("   🔄 Converting to Clause objects...")
            clauses = [Clause(**c) for c in clause_dicts]
            logger.info(f"   ✅ Created {len(clauses)} Clause objects")
            
            # Process tables (with clause linking)
            logger.info(f"   🔗 Linking {len(table_dicts)} tables to clauses...")
            tables = self.table_processor.process_tables_from_modal(
                table_dicts,
                clauses=clauses
            )
            logger.info(f"   ✅ Linked {len(tables)} tables")

            # Step 2b: Best-effort capture of table footer notes (NOTES:) from page text
            try:
                import fitz

                doc = fitz.open(input_path)
                try:
                    # Process tables in page order so we can cap scanning at the next table start.
                    tables_sorted = sorted(
                        tables,
                        key=lambda t: (getattr(t, "page_start", 0) or 0, getattr(t, "table_number", "") or ""),
                    )
                    for table in tables:
                        existing = list(getattr(table, "footer_notes", None) or [])
                        page_num = getattr(table, "page_start", None)
                        if not page_num or page_num < 1 or page_num > len(doc):
                            continue
                        # Notes can spill to the next page; scan a small window.
                        page_end = getattr(table, "page_end", page_num) or page_num
                        # Notes often continue on following pages; keep this bounded to avoid cross-table bleed.
                        scan_end = min(len(doc), max(page_end, page_num) + 5)

                        # Cap scan at the next table's starting page (prevents stealing).
                        try:
                            idx = tables_sorted.index(table)
                        except ValueError:
                            idx = -1
                        if idx >= 0 and idx + 1 < len(tables_sorted):
                            next_page = getattr(tables_sorted[idx + 1], "page_start", None)
                            if next_page and next_page > page_num:
                                scan_end = min(scan_end, next_page)

                        page_text_parts: list[str] = []
                        for p in range(page_num, scan_end + 1):
                            try:
                                page_text_parts.append(doc[p - 1].get_text() or "")
                            except Exception:
                                continue
                        page_text = "\n".join(t for t in page_text_parts if t)
                        extracted_notes = self._extract_footer_notes_from_page_text(
                            page_text=page_text,
                            table_number=getattr(table, "table_number", None),
                            table_title=getattr(table, "title", None),
                        )
                        if extracted_notes:
                            merged: list[str] = []
                            seen = set()
                            for note in (existing + extracted_notes):
                                n = str(note).strip()
                                if not n:
                                    continue
                                # Drop bare "NOTES:" tokens (placeholders)
                                if n.lower() in {"notes", "notes:", "note", "note:"}:
                                    continue
                                key = n.lower()
                                if key in seen:
                                    continue
                                seen.add(key)
                                merged.append(n)
                            table.footer_notes = merged
                finally:
                    doc.close()
            except Exception as e:
                logger.info(f"Footer-notes enrichment skipped (non-fatal): {e}")

            result["steps"]["conversion"] = {
                "status": "success",
                "tables_converted": len(tables),
                "clauses_converted": len(clauses),
            }

            logger.info(f"✅ Converted {len(tables)} tables, {len(clauses)} clauses")

            # Step 3: Validate
            logger.info("Step 3: Validating results...")
            clause_issues = self.validator.validate_clauses(clauses)
            table_issues = self.validator.validate_tables(tables)
            validation_summary = self.validator.get_summary()

            result["steps"]["validation"] = {
                "status": "success",
                "summary": validation_summary,
                "issues": [issue.model_dump() for issue in self.validator.issues]
            }

            logger.info(f"Validation completed: {validation_summary}")

            # Step 4: Generate outputs
            logger.info("Step 4: Generating output files...")
            document_title = self._extract_document_title(clauses)
            self.output_generator.generate_all(clauses, tables, output_dir, document_title)

            result["steps"]["output_generation"] = {
                "status": "success",
                "files": [
                    "normalized_document.txt",
                    "clauses.json",
                    "tables.json",
                    "tables.yaml"
                ]
            }

            # Summary
            result["summary"] = {
                "total_clauses": len(clauses),
                "total_tables": len(tables),
                "validation_issues": validation_summary,
                "document_title": document_title,
                "extraction_cost": extraction_result.get("cost_estimate", 0),
                "extraction_time": extraction_result.get("processing_time", 0),
            }

            logger.info(f"✅ PDF processing completed successfully (Job ID: {job_id})")
            return result

        except Exception as e:
            logger.error(f"Error in PDF processing pipeline: {e}", exc_info=True)
            result["error"] = str(e)
            result["status"] = "failed"
            raise

    async def process_pdf_tables_only(
        self, 
        input_path: str, 
        output_dir: str, 
        job_id: str,
        enable_enhancement: bool = True
    ) -> Dict[str, Any]:
        """
        Process tables only (skip clauses).

        Args:
            input_path: Path to input PDF
            output_dir: Directory for output files
            job_id: Unique job identifier
            enable_enhancement: Enable Real-ESRGAN 2x enhancement (default: True)

        Returns:
            Processing result summary
        """
        logger.info(f"Starting tables-only processing (Job ID: {job_id})")
        result = {
            "job_id": job_id,
            "mode": "tables_only",
            "steps": {},
        }

        try:
            if not self.modal_service.is_available():
                raise ValueError(
                    "Tables-only pipeline requires Modal. "
                    "Set MODAL_ENDPOINT and unset DISABLE_MODAL, "
                    "or use full process_pdf with DISABLE_MODAL=true to test clauses locally (tables will be empty)."
                )

            # Extract via Modal.com
            extraction_result = self.modal_service.extract_tables_only(
                Path(input_path),
                filename=Path(input_path).name,
                enable_enhancement=enable_enhancement
            )

            if not extraction_result.get("success"):
                raise Exception(f"Modal.com extraction failed: {extraction_result.get('error')}")

            # Convert and process tables only
            modal_tables = extraction_result.get("tables", [])
            table_dicts = self.modal_service.convert_tables_to_objects(modal_tables)
            tables = self.table_processor.process_tables_from_modal(table_dicts, clauses=[])

            result["steps"]["modal_extraction"] = {
                "status": "success",
                "table_count": len(tables),
            }

            # Validate tables
            self.validator.issues = []
            table_issues = self.validator.validate_tables(tables)
            validation_summary = self.validator.get_summary()

            result["steps"]["validation"] = {
                "status": "success",
                "summary": validation_summary,
                "issues": [issue.model_dump() for issue in table_issues],
            }

            # Generate tables.json only
            output_path = Path(output_dir)
            output_path.mkdir(exist_ok=True, parents=True)
            tables_path = output_path / "tables.json"
            self.output_generator.generate_tables_json(tables, str(tables_path))
            
            # Also generate YAML file for RAG compatibility
            yaml_path = output_path / "tables.yaml"
            try:
                # Try AI-powered converter first, fallback to basic if unavailable
                if self.output_generator.ai_yaml_converter:
                    logger.info("Using AI-powered YAML generation (Gemini 1.5 Flash)...")
                    self.output_generator.ai_yaml_converter.convert_tables_to_yaml(
                        tables_json_path=str(tables_path),
                        output_yaml_path=str(yaml_path)
                    )
                    logger.info(f"✅ Generated AI-powered tables YAML: {yaml_path}")
                else:
                    logger.info("Using basic YAML generation...")
                    self.output_generator.yaml_converter.convert_tables_to_yaml(
                        tables_json_path=str(tables_path),
                        output_yaml_path=str(yaml_path)
                    )
                    logger.info(f"Generated tables YAML: {yaml_path}")
                result["steps"]["output_generation"] = {
                    "status": "success",
                    "files": ["tables.json", "tables.yaml"],
                }
            except Exception as e:
                logger.warning(f"Failed to generate YAML file: {e}")
                result["steps"]["output_generation"] = {
                    "status": "success",
                    "files": ["tables.json"],
                }

            result["summary"] = {
                "total_clauses": 0,
                "total_tables": len(tables),
                "validation_issues": validation_summary,
                "document_title": None,
            }

            logger.info(f"✅ Tables-only processing completed (Job ID: {job_id}, {len(tables)} tables)")
            return result

        except Exception as e:
            logger.error(f"Error in tables-only processing: {e}", exc_info=True)
            result["error"] = str(e)
            result["status"] = "failed"
            raise

    def _extract_document_title(self, clauses: list) -> str:
        """Extract document title from first top-level clause or default."""
        if clauses:
            # Try to find title from first clause
            for clause in clauses:
                if clause.level == 1 and clause.title:
                    return clause.title
            
            # Fallback to first clause with title
            for clause in clauses:
                if clause.title:
                    return clause.title

        return "Technical Standard Document"

    @staticmethod
    def _line_looks_like_table_grid_line(line: str) -> bool:
        """
        Heuristic: PDF table rows often land in plain text as TSV, pipe grids, or
        wide-spaced numeric columns. Those should not become clause body in clauses.csv.
        """
        import re

        s = line.strip()
        if len(s) < 2:
            return False
        # Multi-column TSV (very common in standards PDF text dumps)
        if s.count("\t") >= 2:
            return True
        # Pipe tables / markdown-style rows
        if s.count("|") >= 3:
            return True
        if "|" in s and re.match(r"^[\|\s\-\:\.]+$", s):
            return True
        # Wide gaps (3+ spaces) + numeric / unit heavy => layout columns
        if len(s) >= 16 and re.search(r"\S\s{3,}\S", s):
            digitish = sum(
                ch.isdigit() or ch in ".,-–—°%×x*µ" for ch in s
            )
            if digitish / len(s) >= 0.38:
                return True
        # Mostly digits/symbols/units, almost no alphabetic words (dimension grids)
        if len(s) >= 10:
            letters = sum(ch.isalpha() for ch in s)
            if letters < 5 and sum(ch.isdigit() for ch in s) >= 4:
                if re.match(r"^[\d\s\.\,\-\–\/%°NnAaWwVvMm\(\)]+$", s):
                    return True
        return False

    def _format_normalized_document(
        self,
        raw_text: str,
        *,
        standard_family: str = DEFAULT_STANDARD_FAMILY,
    ) -> str:
        """
        Format raw PDF text into normalized_document.txt format with clause markers.
        Uses family-specific regex (AS/NZS, ISO, IEC, ASTM, NFPA, API, IEEE).
        """
        family = normalize_standard_family(standard_family)
        formatted_lines = []

        formatted_lines.append("=" * 80)
        formatted_lines.append("DOCUMENT TITLE: Technical Standard")
        formatted_lines.append("=" * 80)
        formatted_lines.append("")
        formatted_lines.append("CLAUSES")
        formatted_lines.append("=" * 80)
        formatted_lines.append("")

        raw_text = normalize_raw_text(raw_text, family)
        lines = raw_text.split("\n")

        logger.info("   DEBUG: Total lines to process: %s (family=%s)", len(lines), family)
        logger.info("   DEBUG: First 10 lines:")
        for i, line in enumerate(lines[:10]):
            logger.info("   Line %s: %s", i, line[:100])

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            if not line:
                i += 1
                continue

            matched = match_clause_header(line, family)
            if matched:
                number, title = matched
                level, parent = clause_level_and_parent(number, family)

                body_lines = []
                j = i + 1
                max_scan = min(len(lines), i + 800)
                while j < max_scan:
                    next_line = lines[j].strip()
                    m_next = match_clause_header(next_line, family) if next_line else None
                    if m_next and next_line.count("\t") >= 2:
                        j += 1
                        continue
                    if m_next:
                        break
                    if next_line and not self._line_looks_like_table_grid_line(next_line):
                        body_lines.append(next_line)
                    j += 1

                body = " ".join(body_lines)

                formatted_lines.append("[CLAUSE]")
                formatted_lines.append(f"Number: {number}")
                formatted_lines.append(f"Title: {title}")
                if parent:
                    formatted_lines.append(f"Parent: {parent}")
                formatted_lines.append(f"Level: {level}")
                formatted_lines.append(f"Pages: {i // 50 + 1}-{i // 50 + 1}")
                formatted_lines.append("Confidence: high")
                formatted_lines.append("")
                formatted_lines.append("Body:")
                formatted_lines.append(body)
                formatted_lines.append("")
                formatted_lines.append("-" * 80)
                formatted_lines.append("")

                logger.info("   DEBUG: Matched clause %s: %s", number, title[:50])
                i = j
            else:
                i += 1

        return "\n".join(formatted_lines)
