import json
import csv
import logging
from typing import List
from pathlib import Path
from pdf_pipeline.models.clause import Clause
from pdf_pipeline.models.table import Table

logger = logging.getLogger(__name__)


class OutputGenerator:
    def __init__(self):
        pass
    
    def generate_all(self, clauses: List[Clause], tables: List[Table], output_dir: str, document_title: str = "Document", export_csv: bool = True):
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True, parents=True)
        txt_path = output_path / "normalized_document.txt"
        self.generate_normalized_text(clauses, tables, str(txt_path), document_title)
        clauses_path = output_path / "clauses.json"
        self.generate_clauses_json(clauses, str(clauses_path))
        
        # Generate CSV export if requested
        if export_csv and clauses:
            csv_path = output_path / "clauses.csv"
            try:
                self.generate_clauses_csv(clauses, str(csv_path))
                logger.info(f"✅ Generated clauses CSV: {csv_path} ({len(clauses)} clauses)")
            except Exception as e:
                logger.warning(f"Failed to generate clauses CSV: {e}")
        
        # Generate tables CSV export if requested
        if export_csv and tables:
            tables_csv_path = output_path / "tables.csv"
            try:
                self.generate_tables_csv(tables, str(tables_csv_path), document_title)
                logger.info(f"✅ Generated tables CSV: {tables_csv_path} ({len(tables)} tables)")
            except Exception as e:
                logger.warning(f"Failed to generate tables CSV: {e}")
        
        # Generate YAML directly from table data (NEW APPROACH - no JSON intermediate)
        if tables:
            yaml_path = output_path / "tables.yaml"
            try:
                logger.info("Generating tables YAML directly from extracted data...")
                self.generate_tables_yaml_direct(tables, str(yaml_path), document_title)
                logger.info(f"✅ Generated tables YAML: {yaml_path} ({len(tables)} tables)")
            except Exception as e:
                logger.warning(f"Failed to generate YAML file: {e}")
        
        # Still generate tables.json for backward compatibility
        tables_path = output_path / "tables.json"
        self.generate_tables_json(tables, str(tables_path))
        
        logger.info(f"Generated all output files in {output_dir}")
    
    def generate_normalized_text(self, clauses: List[Clause], tables: List[Table], output_path: str, document_title: str):
        lines = []
        lines.append("=" * 80)
        lines.append(f"DOCUMENT TITLE: {document_title}")
        lines.append("=" * 80)
        lines.append("")
        lines.append("CLAUSES")
        lines.append("=" * 80)
        lines.append("")
        for clause in clauses:
            lines.append("[CLAUSE]")
            lines.append(f"Number: {clause.clause_number}")
            if clause.title:
                lines.append(f"Title: {clause.title}")
            if clause.parent_clause_number:
                lines.append(f"Parent: {clause.parent_clause_number}")
            lines.append(f"Level: {clause.level}")
            lines.append(f"Pages: {clause.page_start}-{clause.page_end}")
            lines.append(f"Confidence: {clause.confidence}")
            lines.append("")
            lines.append("Body:")
            lines.append(clause.body_with_subitems)
            lines.append("")
            if clause.notes:
                lines.append("Notes:")
                for note in clause.notes:
                    lines.append(f"  * {note.type}: {note.text}")
                lines.append("")
            if clause.exceptions:
                lines.append("Exceptions:")
                for exc in clause.exceptions:
                    lines.append(f"  * {exc.type}: {exc.text}")
                lines.append("")
            lines.append("-" * 80)
            lines.append("")
        if tables:
            lines.append("")
            lines.append("TABLES")
            lines.append("=" * 80)
            lines.append("")
            for table in tables:
                lines.append("[TABLE]")
                if table.table_number:
                    lines.append(f"Number: {table.table_number}")
                if table.title:
                    lines.append(f"Title: {table.title}")
                if table.parent_clause_reference:
                    lines.append(f"Parent Clause: {table.parent_clause_reference}")
                lines.append(f"Pages: {table.page_start}-{table.page_end}")
                lines.append(f"Confidence: {table.confidence}")
                lines.append("")
                lines.append(table.normalized_text_representation)
                lines.append("")
                if table.footer_notes:
                    lines.append("Footer Notes:")
                    for note in table.footer_notes:
                        lines.append(f"  * {note}")
                    lines.append("")
                lines.append("-" * 80)
                lines.append("")
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        logger.info(f"Generated normalized text: {output_path}")
    
    def generate_clauses_json(self, clauses: List[Clause], output_path: str):
        clauses_data = [clause.model_dump() for clause in clauses]
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(clauses_data, f, indent=2, ensure_ascii=False)
        logger.info(f"Generated clauses JSON: {output_path} ({len(clauses)} clauses)")
    
    def generate_tables_json(self, tables: List[Table], output_path: str):
        recon_keys = (
            "reconstructed_header_rows",
            "promoted_header_rows",
            "final_columns",
            "header_model",
            "reconstruction_confidence",
            "reconstruction_notes",
        )

        def table_to_json_dict(table: Table) -> dict:
            d = table.model_dump()
            for k in recon_keys:
                if d.get(k) is None:
                    d.pop(k, None)
            return d

        tables_data = [table_to_json_dict(table) for table in tables]
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(tables_data, f, indent=2, ensure_ascii=False)
        logger.info(f"Generated tables JSON: {output_path} ({len(tables)} tables)")
    
    def generate_tables_yaml_direct(self, tables: List[Table], output_path: str, document_title: str = "Document"):
        """
        Generate YAML file directly from table data (with AI-generated descriptions from Modal).
        
        This replaces the old approach of:
        1. Generate tables.json
        2. Read tables.json
        3. Call Gemini to generate descriptions
        4. Write tables.yaml
        
        New approach:
        - Modal already generates yaml_description during extraction (Stage D)
        - We just write it directly to YAML
        """
        from datetime import datetime
        
        lines = []
        lines.append("# AI-Generated Tables as Clauses (RAG-Compatible)")
        lines.append(f"# Source Document: {document_title}")
        lines.append(f"# Generated: {datetime.now().isoformat()}")
        lines.append(f"# Generation Method: Gemini 2.5 Flash (direct extraction)")
        lines.append(f"# Total Tables: {len(tables)}")
        lines.append("")
        lines.append("tables:")
        
        for table in tables:
            table_number = table.table_number or "Unknown"
            title = table.title or "Untitled"
            page = table.page_start
            
            # Get yaml_description from table (generated by Modal Stage D)
            yaml_description = getattr(table, 'yaml_description', None)
            
            # Fallback if yaml_description is missing
            if not yaml_description or yaml_description.strip() == "":
                logger.warning(f"Table {table_number} missing yaml_description, generating fallback")
                yaml_description = self._generate_fallback_description(table)

            # If footer notes exist, append them to the description so RAG retrieval
            # captures critical "NOTES:" content that is often referenced by clauses.
            footer_notes = getattr(table, "footer_notes", None) or []
            if footer_notes:
                notes_text = "\n".join(str(n).strip() for n in footer_notes if str(n).strip())
                if notes_text:
                    yaml_description = (yaml_description.rstrip() + "\n\nNotes:\n" + notes_text).strip()
            
            lines.append(f"  - table_number: {json.dumps(table_number)}")
            lines.append(f"    title: {json.dumps(title)}")
            lines.append(f"    page: {page}")
            lines.append(f"    description: |")
            
            # Write description as multiline block (indented)
            for line in yaml_description.split('\n'):
                lines.append(f"      {line}")
            
            lines.append("")
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        
        logger.info(f"Generated tables YAML: {output_path} ({len(tables)} tables)")
    
    def _generate_fallback_description(self, table: Table) -> str:
        """
        Generate a basic fallback description if yaml_description is missing.
        """
        table_number = table.table_number or "Unknown"
        title = table.title or "Untitled"
        row_count = table.row_count or 0
        notes = table.footer_notes or []
        
        description = f"{table_number} ({title}) specifies technical requirements for AS/NZS 3000 electrical standards. "
        description += f"This table contains {row_count} rows of data."
        
        if notes:
            description += f" Important notes: {' '.join(notes)}"
        
        return description
    
    def generate_clauses_csv(self, clauses: List[Clause], output_path: str, standard_prefix: str = "as3000"):
        """
        Export clauses to CSV format following the reference structure.
        
        Args:
            clauses: List of Clause objects
            output_path: Path to output CSV file
            standard_prefix: Standard identifier prefix (default: "as3000")
        """
        def get_parent_number(clause_number: str) -> str:
            """Extract parent number from clause number."""
            if not clause_number:
                return ""
            parts = clause_number.split('.')
            if len(parts) <= 1:
                return ""
            return '.'.join(parts[:-1])
        
        def format_array_field(field) -> str:
            """Format array/list fields to string."""
            if field is None:
                return ""
            if isinstance(field, list):
                if len(field) == 0:
                    return ""
                # Convert note/exception objects to strings
                items = []
                for item in field:
                    if hasattr(item, 'text'):
                        items.append(str(item.text))
                    else:
                        items.append(str(item))
                return "\n".join(items)
            return str(field)
        
        # Define CSV columns
        fieldnames = [
            'id',
            'clause_number',
            'heading',
            'body',
            'notes',
            'exceptions',
            'level',
            'parent_number',
            'parent_id',
            'page',
            'confidence',
            'appendix_flag',
            'embeddable',
            'is_heading_only',
            'had_duplicate_merge',
            'source_rows'
        ]
        
        csv_rows = []
        for clause in clauses:
            clause_number = clause.clause_number or ""
            parent_number = get_parent_number(clause_number)
            
            # Generate semantic IDs
            clause_id = f"{standard_prefix}:{clause_number}" if clause_number else ""
            parent_id = f"{standard_prefix}:{parent_number}" if parent_number else ""
            
            # Format page range
            if clause.page_start and clause.page_end:
                if clause.page_start == clause.page_end:
                    page_info = str(clause.page_start)
                else:
                    page_info = f"{clause.page_start}-{clause.page_end}"
            else:
                page_info = ""
            
            # Determine if heading only
            body = clause.body_with_subitems or ""
            is_heading = not clause.has_body or len(body.strip()) < 10
            
            row = {
                'id': clause_id,
                'clause_number': clause_number,
                'heading': clause.title or "",
                'body': body,
                'notes': format_array_field(clause.notes),
                'exceptions': format_array_field(clause.exceptions),
                'level': clause.level,
                'parent_number': parent_number,
                'parent_id': parent_id,
                'page': page_info,
                'confidence': clause.confidence,
                'appendix_flag': False,  # Could be enhanced with logic
                'embeddable': True,  # Could be enhanced with logic
                'is_heading_only': is_heading,
                'had_duplicate_merge': False,
                'source_rows': '[]'
            }
            csv_rows.append(row)
        
        # Write CSV
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
            writer.writeheader()
            writer.writerows(csv_rows)
        
        logger.info(f"Generated clauses CSV: {output_path} ({len(csv_rows)} clauses)")
    
    def generate_tables_csv(self, tables: List[Table], output_path: str, document_title: str = "AS/NZS 3000", version: str = "AS3000 2018"):
        """
        Export tables to CSV format following the reference Tables.csv structure.
        
        Args:
            tables: List of Table objects
            output_path: Path to output CSV file
            document_title: Document source identifier (default: "AS/NZS 3000")
            version: Version string (default: "AS3000 2018")
        """
        import csv
        
        def extract_volume_and_section(table_number: str) -> tuple:
            """Extract volume and section code from table number."""
            if not table_number:
                return "Vol1", "", ""
            
            # Remove "Table " prefix if present
            num = table_number.replace("Table ", "").strip()
            
            # Extract section (e.g., "3.1" -> "3", "C10" -> "C10")
            if '.' in num:
                section = num.split('.')[0]
            else:
                section = num
            
            # Determine volume (most tables are Vol1)
            volume = "Vol1"
            
            return volume, section, section
        
        def extract_clause_reference(table: Table) -> str:
            """Extract clause reference from parent_clause_reference or parent_clause_number."""
            if table.parent_clause_reference:
                # Already formatted (e.g., "Clause 3.2")
                if table.parent_clause_reference.startswith("Clause "):
                    return table.parent_clause_reference
                return f"Clause {table.parent_clause_reference}"
            elif table.parent_clause_number:
                return f"Clause {table.parent_clause_number}"
            return ""
        
        def extract_clause_ids(table: Table) -> tuple:
            """Extract first and last clause IDs."""
            clause_ref = table.parent_clause_number or table.parent_clause_reference or ""
            # Remove "Clause " prefix if present
            clause_id = clause_ref.replace("Clause ", "").strip()
            return clause_id, clause_id  # For single table, first = last
        
        # Define CSV columns matching reference Tables.csv
        fieldnames = [
            'volume',
            'part_code',
            'section_code',
            'table_id',
            'unit_type',
            'table_title',
            'table_content',
            'content_part',
            'content_parts_total',
            'called_in',
            'clause_reference',
            'source',
            'context',
            'clause_count',
            'first_clause_id',
            'last_clause_id',
            'archived',
            'state_territory',
            'source_file',
            'version'
        ]
        
        csv_rows = []
        for table in tables:
            table_number = table.table_number or "Unknown"
            volume, part_code, section_code = extract_volume_and_section(table_number)
            clause_reference = extract_clause_reference(table)
            first_clause_id, last_clause_id = extract_clause_ids(table)
            
            # Use yaml_description as table_content (AI-generated prose)
            table_content = table.yaml_description or ""
            if not table_content:
                # Fallback to basic description
                table_content = f"{table_number} ({table.title or 'Untitled'}) contains technical specifications."

            # Append extracted footer notes if present (these are often critical "NOTES:" blocks)
            footer_notes = getattr(table, "footer_notes", None) or []
            if footer_notes:
                notes_text = "\n".join(str(n).strip() for n in footer_notes if str(n).strip())
                if notes_text:
                    # Keep this plain-text and searchable; no markdown.
                    table_content = (table_content.rstrip() + "\n\nNotes:\n" + notes_text).strip()
            
            row = {
                'volume': volume,
                'part_code': part_code,
                'section_code': section_code,
                'table_id': table_number,
                'unit_type': 'table',  # Could be 'figure' if we detect diagrams
                'table_title': table.title or "",
                'table_content': table_content,
                'content_part': 1,
                'content_parts_total': 1,
                'called_in': "",  # Could be enhanced with cross-references
                'clause_reference': clause_reference,
                'source': f"{document_title} Vol1",
                'context': "",  # Additional context if available
                'clause_count': 1 if first_clause_id else 0,
                'first_clause_id': first_clause_id,
                'last_clause_id': last_clause_id,
                'archived': 'false',
                'state_territory': "",
                'source_file': 'tables.json',
                'version': version
            }
            csv_rows.append(row)
        
        # Write CSV
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            writer.writerows(csv_rows)
        
        logger.info(f"Generated tables CSV: {output_path} ({len(csv_rows)} tables)")
